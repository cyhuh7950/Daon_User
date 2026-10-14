import importlib.util
import io
import pathlib
import unittest
import wave
import threading
import urllib.request
import urllib.error
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('asr_server',pathlib.Path(__file__).parents[1]/'server.py')
server=importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

def wav(seconds=1,rate=16000):
    output=io.BytesIO()
    with wave.open(output,'wb') as writer:
        writer.setnchannels(1);writer.setsampwidth(2);writer.setframerate(rate)
        writer.writeframes(b'\0\0'*(rate*seconds))
    return output.getvalue()

class AudioBoundaryTests(unittest.TestCase):
    def test_pcm_duration_and_invalid_headers(self):
        self.assertEqual(server.validate_wav(wav()),1000)
        for value in (b'not-wav',wav()[:-1],wav(rate=8000),wav(seconds=121)):
            with self.subTest(value=len(value)),self.assertRaises(ValueError):server.validate_wav(value)
    def test_timestamp_normalization_never_clips_or_merges(self):
        values=server.normalize_segments([(0,.5,'한글 test')],1000)
        self.assertEqual(values,[{'segment_id':'s:1','start_ms':0,'end_ms':500,'text':'한글 test'}])
        for items in ([(-1,.5,'bad')],[(0,2,'bad')],[(0,.8,'a'),(.5,1,'b')],[(0,.5,'')]):
            with self.subTest(items=items),self.assertRaises(ValueError):server.normalize_segments(items,1000)

class InferenceBoundaryTests(unittest.TestCase):
    def test_inference_word_alignment_emits_primitive_timestamps_for_strict_normalization(self):
        class EngineTimestamp(float):
            pass
        requests=[]
        class Engine:
            def __init__(self,*args,**kwargs):pass
            def transcribe(self,audio,**kwargs):
                requests.append((audio.read(),kwargs))
                return iter([SimpleNamespace(start=EngineTimestamp(.25),end=EngineTimestamp(.75),text='한글 test')]),None
        class Pipe:
            def __init__(self,raw):self.inputs=iter([raw]);self.sent=[]
            def recv(self):
                try:return next(self.inputs)
                except StopIteration:raise EOFError
            def send(self,value):self.sent.append(value)
        raw=wav();pipe=Pipe(raw)
        with patch.dict(sys.modules,{'faster_whisper':SimpleNamespace(WhisperModel=Engine)}):
            server.inference_process(pipe,'/fixture/model')
        self.assertEqual(pipe.sent[0],('ready',None))
        self.assertEqual(pipe.sent[1][0],'ok')
        self.assertEqual(len(pipe.sent),2)
        self.assertEqual(requests[0][0],raw)
        with self.subTest(boundary='word_alignment'):
            self.assertEqual(requests[0][1],dict(beam_size=1,temperature=0,
                condition_on_previous_text=False,vad_filter=False,word_timestamps=True))
        with self.subTest(boundary='primitive_timestamps_and_normalized_result'):
            result=pipe.sent[1][1]
            self.assertIs(type(result[0][0]),float)
            self.assertIs(type(result[0][1]),float)
            self.assertEqual(server.normalize_segments(result,server.validate_wav(raw)),
                [{'segment_id':'s:1','start_ms':250,'end_ms':750,'text':'한글 test'}])

class HttpBoundaryTests(unittest.TestCase):
    def test_busy_request_is_rejected_and_timeout_is_not_success(self):
        entered=threading.Event();release=threading.Event()
        class Engine:
            def ready(self):return True
            def transcribe(self,raw):
                entered.set();release.wait(2);raise TimeoutError('ASR_TIMEOUT')
        http=server.create_server(Engine(),'a'*64,('127.0.0.1',0))
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(http.server_port);statuses=[]
        def request():
            try:
                urllib.request.urlopen(urllib.request.Request(base+'/v1/transcribe',data=wav(),headers={'Content-Type':'audio/wav'}),timeout=4).close()
            except urllib.error.HTTPError as error:
                statuses.append((error.code,json.load(error)['error']));error.close()
        first=threading.Thread(target=request)
        try:
            first.start();self.assertTrue(entered.wait(1));request()
            self.assertEqual(statuses,[(503,'ASR_BUSY')])
            release.set();first.join(3)
            self.assertEqual(statuses,[(503,'ASR_BUSY'),(504,'ASR_TIMEOUT')])
        finally:release.set();first.join(3);http.shutdown();http.server_close();thread.join(2)
    def test_worker_deadline_terminates_child(self):
        class Connection:
            closed=False
            def send(self,raw):pass
            def poll(self,seconds):self.seconds=seconds;return False
            def close(self):self.closed=True
        class Process:
            alive=True
            def is_alive(self):return self.alive
            def terminate(self):self.alive=False
            def join(self,timeout):self.timeout=timeout
        worker=object.__new__(server.ModelWorker);worker.connection=Connection();worker.process=Process()
        with self.assertRaises(TimeoutError):worker.transcribe(b'input')
        self.assertEqual(worker.connection.seconds,120);self.assertTrue(worker.connection.closed);self.assertFalse(worker.ready())
    def test_actual_http_metadata_and_invalid_audio_call_zero(self):
        class Engine:
            calls=0
            def ready(self):return True
            def transcribe(self,raw):self.calls+=1;raise AssertionError('INVALID_AUDIO_CALLED_MODEL')
        engine=Engine();http=server.create_server(engine,'a'*64,('127.0.0.1',0))
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(http.server_port)
        try:
            with urllib.request.urlopen(base+'/v1/metadata') as response:
                value=json.load(response);self.assertEqual(value['model_digest'],'a'*64);self.assertEqual(value['capability'],'speech_to_text')
            for content_type,body,status in [('audio/wav',b'bad-wav-content'+b'\0'*40,422),('application/json',b'{}',400),('audio/wav',b'',400)]:
                request=urllib.request.Request(base+'/v1/transcribe',data=body,headers={'Content-Type':content_type})
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
                self.assertEqual(error.exception.code,status)
                error.exception.close()
            self.assertEqual(engine.calls,0)
        finally:http.shutdown();http.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main()
