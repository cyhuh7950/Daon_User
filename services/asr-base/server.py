"""내부 CPU ASR. 원본·전사는 저장하거나 로그에 남기지 않는다."""
import hashlib
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID='faster-whisper-base'
REVISION='ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66'
MAX_BYTES=25*1024*1024

def validate_wav(content):
    if not 44<=len(content)<=MAX_BYTES or content[:4]!=b'RIFF' or content[8:12]!=b'WAVE' or struct.unpack_from('<I',content,4)[0]+8!=len(content):raise ValueError('INVALID_WAV')
    offset=12;count=0;metadata=0;fmt=None;data=None
    while offset<len(content):
        count+=1
        if count>64 or offset+8>len(content):raise ValueError('INVALID_WAV')
        kind=content[offset:offset+4];size=struct.unpack_from('<I',content,offset+4)[0];start=offset+8;end=start+size
        if end+(size%2)>len(content):raise ValueError('INVALID_WAV')
        if kind==b'fmt ':
            if fmt is not None or size!=16:raise ValueError('INVALID_WAV')
            fmt=struct.unpack_from('<HHIIHH',content,start)
        elif kind==b'data':
            if data is not None:raise ValueError('INVALID_WAV')
            data=size
        elif kind in (b'JUNK',b'LIST'):
            metadata+=size
            if metadata>65536 or (kind==b'LIST' and content[start:start+4]!=b'INFO'):raise ValueError('INVALID_WAV')
        else:raise ValueError('INVALID_WAV')
        offset=end+(size%2)
    if fmt is None or data is None:raise ValueError('INVALID_WAV')
    encoding,channels,rate,byte_rate,align,bits=fmt
    if encoding!=1 or channels not in (1,2) or rate not in (16000,22050,24000,44100,48000) or bits!=16 or align!=channels*2 or byte_rate!=rate*align or data%align:raise ValueError('INVALID_WAV')
    frames=data//align
    if not rate<=frames<=120*rate:raise ValueError('INVALID_WAV')
    return frames*1000//rate

def normalize_segments(items,duration_ms):
    output=[];previous=0;total=0
    for ordinal,(start,end,text) in enumerate(items,1):
        if ordinal>64 or type(start) not in (int,float) or type(end) not in (int,float) or not math.isfinite(start) or not math.isfinite(end):raise ValueError('INVALID_TRANSCRIPT')
        start_ms=math.floor(start*1000);end_ms=math.ceil(end*1000)
        if not previous<=start_ms<end_ms<=duration_ms or not isinstance(text,str) or not text.strip() or len(text)>2000:raise ValueError('INVALID_TRANSCRIPT')
        total+=len(text)
        if total>16000:raise ValueError('INVALID_TRANSCRIPT')
        output.append(dict(segment_id=f's:{ordinal}',start_ms=start_ms,end_ms=end_ms,text=text));previous=end_ms
    if not output:raise ValueError('EMPTY_TRANSCRIPT')
    return output

def inference_process(connection,model_path):
    try:
        from faster_whisper import WhisperModel
        model=WhisperModel(model_path,device='cpu',compute_type='int8',cpu_threads=1,num_workers=1,local_files_only=True)
        connection.send(('ready',None))
        while True:
            raw=connection.recv()
            try:
                segments,_=model.transcribe(io.BytesIO(raw),beam_size=1,temperature=0,condition_on_previous_text=False,vad_filter=False,word_timestamps=True)
                result=[(float(s.start),float(s.end),s.text) for s in segments]
                connection.send(('ok',result))
            except Exception:connection.send(('error',None))
    except (EOFError,BrokenPipeError):pass
    except Exception:
        try:connection.send(('error',None))
        except Exception:pass

class ModelWorker:
    def __init__(self,path):
        context=multiprocessing.get_context('spawn');self.connection,child=context.Pipe()
        self.process=context.Process(target=inference_process,args=(child,path),daemon=True);self.process.start();child.close()
        if not self.connection.poll(120) or self.connection.recv()[0]!='ready':
            self.close();raise RuntimeError('MODEL_NOT_READY')
    def ready(self):return self.process.is_alive()
    def transcribe(self,raw):
        if not self.ready():raise RuntimeError('MODEL_NOT_READY')
        self.connection.send(raw)
        if not self.connection.poll(120):
            self.close();raise TimeoutError('ASR_TIMEOUT')
        status,result=self.connection.recv()
        if status!='ok':raise RuntimeError('ASR_FAILED')
        return result
    def close(self):
        if self.process.is_alive():self.process.terminate()
        self.process.join(timeout=3);self.connection.close()

def create_server(model,digest,address=('0.0.0.0',8080)):
    gate=threading.BoundedSemaphore(1)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def setup(self):
            super().setup();self.connection.settimeout(5)
        def reply(self,status,value):
            raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Connection','close');self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if self.path=='/health/live':return self.reply(200,{'status':'live'})
            if self.path=='/health/ready':return self.reply(200 if model.ready() else 503,{'status':'ready' if model.ready() else 'unavailable'})
            if self.path=='/v1/metadata':return self.reply(200 if model.ready() else 503,dict(model_id=MODEL_ID,model_digest=digest,revision=REVISION,capability='speech_to_text',runtime='cpu-int8-local'))
            self.reply(404,{'error':'NOT_FOUND'})
        def do_POST(self):
            if self.path!='/v1/transcribe':return self.reply(404,{'error':'NOT_FOUND'})
            if self.headers.get('Transfer-Encoding') is not None or self.headers.get('Content-Type')!='audio/wav':return self.reply(400,{'error':'INVALID_AUDIO_REQUEST'})
            lengths=self.headers.get_all('Content-Length',[])
            if len(lengths)!=1 or not lengths[0].isdigit() or not 44<=int(lengths[0])<=MAX_BYTES:return self.reply(400,{'error':'INVALID_AUDIO_REQUEST'})
            if not gate.acquire(blocking=False):return self.reply(503,{'error':'ASR_BUSY'})
            try:
                raw=self.rfile.read(int(lengths[0]))
                duration=validate_wav(raw)
                if not model.ready():return self.reply(503,{'error':'MODEL_NOT_READY'})
                segments=normalize_segments(model.transcribe(raw),duration)
                value=dict(model_id=MODEL_ID,model_digest=digest,source_sha256=hashlib.sha256(raw).hexdigest(),segments=segments)
                if len(json.dumps(value,ensure_ascii=False).encode())>65536:raise ValueError('INVALID_TRANSCRIPT')
                self.reply(200,value)
            except TimeoutError:self.reply(504,{'error':'ASR_TIMEOUT'})
            except ValueError:self.reply(422,{'error':'INVALID_AUDIO_OR_TRANSCRIPT'})
            except Exception:self.reply(503,{'error':'ASR_UNAVAILABLE'})
            finally:gate.release()
    return ThreadingHTTPServer(address,Handler)

if __name__=='__main__':
    path=Path('/opt/asr-model');manifest=json.loads((path/'manifest.json').read_text())
    assert manifest['revision']==REVISION
    for name,digest in manifest['files'].items():assert hashlib.sha256((path/name).read_bytes()).hexdigest()==digest
    worker=ModelWorker(str(path))
    try:create_server(worker,manifest['files']['model.bin']).serve_forever()
    finally:worker.close()
