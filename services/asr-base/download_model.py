"""고정 revision 공식 모델 네 파일만 설치하고 LFS 해시를 확인한다."""
import hashlib,json,os
from pathlib import Path
import urllib.request

REVISION='ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66'
FILES=('config.json','model.bin','tokenizer.json','vocabulary.txt')
target=Path(os.environ['ASR_MODEL_PATH']);target.mkdir(parents=True,exist_ok=False)
with urllib.request.urlopen('https://huggingface.co/api/models/Systran/faster-whisper-base/revision/'+REVISION+'?blobs=true',timeout=30) as response:metadata=json.load(response)
assert metadata['sha']==REVISION
entries={v['rfilename']:v for v in metadata['siblings']};hashes={};total=0
for name in FILES:
    with urllib.request.urlopen('https://huggingface.co/Systran/faster-whisper-base/resolve/'+REVISION+'/'+name,timeout=60) as response:
        with (target/name).open('xb') as stream:
            digest=hashlib.sha256();size=0
            while True:
                part=response.read(1024*1024)
                if not part:break
                size+=len(part);total+=len(part)
                if size>200*1024*1024 or total>300*1024*1024:raise ValueError('MODEL_DOWNLOAD_BOUND')
                stream.write(part);digest.update(part)
    assert size==entries[name]['size']
    if 'lfs' in entries[name]:assert digest.hexdigest()==entries[name]['lfs']['sha256']
    hashes[name]=digest.hexdigest()
(target/'manifest.json').write_text(json.dumps(dict(revision=REVISION,files=hashes),sort_keys=True),encoding='utf-8')
print('MODEL_REVISION='+REVISION+'; MODEL_SHA256='+hashes['model.bin']+'; MODEL_BYTES='+str(total))
