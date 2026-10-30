"""Fetch only two pinned official tokenizer files, never weights or API keys."""
import hashlib,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend/src'))
import httpx
from vimfc.experiments.groq_classification.token_estimation import DEFAULT_MANIFEST,FILE_HASHES,MODEL,REVISION,QwenTokenEstimator

def main():
 root=DEFAULT_MANIFEST.parent;root.mkdir(parents=True,exist_ok=True);attempts=0;downloaded=0;records={}
 with httpx.Client(timeout=60,follow_redirects=True,trust_env=False) as client:
  for name,expected in FILE_HASHES.items():
   target=root/name;url=f'https://huggingface.co/Qwen/Qwen3.8-27B/resolve/{REVISION}/{name}'
   if not target.exists():
    for retry in range(2):
     attempts+=1;temp=target.with_name(target.name+'.part')
     try:
      with client.stream('GET',url) as r:
       r.raise_for_status();size=0
       with temp.open('wb') as f:
        for block in r.iter_bytes(65536):
         size+=len(block)
         if size>16*1024*1024:raise ValueError('Tokenizer file exceeds download cap')
         f.write(block)
        f.flush();os.fsync(f.fileno())
      if hashlib.sha256(temp.read_bytes()).hexdigest()!=expected:raise ValueError('Official tokenizer checksum mismatch')
      os.replace(temp,target);downloaded+=size;break
     except httpx.HTTPError:
      if retry==1:raise
      time.sleep(2)
   data=target.read_bytes()
   if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Existing tokenizer checksum mismatch')
   records[name]={'sha256':expected,'bytes':len(data),'url':url}
 manifest={'format':'vimfc-groq-tokenizer-1','model_id':MODEL,'repository':'Qwen/Qwen3.8-27B','revision':REVISION,'files':records}
 if not DEFAULT_MANIFEST.exists():
  temp=DEFAULT_MANIFEST.with_suffix('.json.part')
  with temp.open('x',encoding='utf-8') as f:json.dump(manifest,f,indent=2);f.flush();os.fsync(f.fileno())
  os.replace(temp,DEFAULT_MANIFEST)
 QwenTokenEstimator(DEFAULT_MANIFEST)
 print(json.dumps({'status':'TOKENIZER_READY','manifest':str(DEFAULT_MANIFEST),'download_attempts':attempts,'downloaded_bytes':downloaded}))

if __name__=='__main__':main()
