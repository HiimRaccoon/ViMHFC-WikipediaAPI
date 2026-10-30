"""Bounded official dump transfers. Partial files are never trusted inputs."""
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import time

import httpx
from vimfc.domain import Incompatible

BASE='https://dumps.wikimedia.org'
TABLES=('page','linktarget','categorylinks')


def hashes(path):
    sha1=hashlib.sha1();sha256=hashlib.sha256();size=0
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            size+=len(chunk);sha1.update(chunk);sha256.update(chunk)
    return {'size':size,'sha1':sha1.hexdigest(),'sha256':sha256.hexdigest()}


def atomic_json(path,value):
    path=Path(path);temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,sort_keys=True);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def publish_new(path,data):
    """No-clobber publication; retry after a crash verifies existing bytes."""
    path=Path(path)
    if path.exists():
        if path.read_bytes()!=data:raise Incompatible('Conflicting published artifact')
        return
    temp=path.with_suffix(path.suffix+'.staged')
    with temp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.link(temp,path);temp.unlink()


@contextmanager
def exclusive(path):
    """OS lock releases on process death; no stale lease deletion or retry loop."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0);f.write(b'0');f.flush();f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as e:raise Incompatible('Dump acquisition already locked') from e
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f,fcntl.LOCK_UN)


def select_artifacts(status,checksums,date):
    if not re.fullmatch(r'20\d{6}',date):raise Incompatible('Pin an exact dump date')
    expected={}
    for line in checksums.splitlines():
        if not line.strip():continue
        m=re.fullmatch(r'([a-f0-9]{40})\s+\*?([^\s/]+)',line)
        if not m:raise Incompatible('Invalid official checksum line')
        expected.setdefault(m[2],set()).add(m[1])
    result=[]
    for table in TABLES:
        name=f'viwiki-{date}-{table}.sql.gz';found=[]
        for job in status['jobs'].values():
            if name in job.get('files',{}):found.append((job,job['files'][name]))
        if len(found)!=1:raise Incompatible('Missing or ambiguous metadata dump')
        job,item=found[0];url=f'/viwiki/{date}/{name}'
        if (job['status']!='done' or item['url']!=url or expected.get(name)!={item.get('sha1')}
            or not re.fullmatch('[a-f0-9]{40}',item.get('sha1',''))
            or type(item['size']) is not int or not 0<item['size']<=200_000_000):
            raise Incompatible('Unfinished/unverified/nonofficial or oversized dump')
        result.append({'table':table,'name':name,'url':BASE+url,'size':item['size'],'sha1':item['sha1']})
    if sum(r['size'] for r in result)>500_000_000:raise Incompatible('Metadata download budget exceeded')
    return result


class Downloader:
    def __init__(self,root,client,*,sleep=time.sleep,clock=time.time,max_attempts=12,max_bytes=1_200_000_000):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.client=client
        self.sleep=sleep;self.clock=clock;self.max_attempts=max_attempts;self.max_bytes=max_bytes
        self.state_path=self.root/'download-state.json'
        self.state=json.loads(self.state_path.read_text(encoding='utf-8')) if self.state_path.exists() else {
            'attempts':0,'bytes':0,'reserved_bytes':0,'retry_at':0,'next_start':0,'receipts':[]}

    def save(self):atomic_json(self.state_path,self.state)

    def fetch(self,spec):
        if (spec['url']!=BASE+'/viwiki/20261001/'+spec['name'] or '/' in spec['name']
            or not re.fullmatch(r'viwiki-20261001-(page|categorylinks|linktarget)\.sql\.gz',spec['name'])):
            raise Incompatible('Unapproved dump URL')
        target=self.root/spec['name'];part=self.root/(spec['name']+'.part')
        def verify(path):
            h=hashes(path)
            if h['size']!=spec['size'] or h['sha1']!=spec['sha1']:raise Incompatible('Dump checksum/size mismatch')
            return h
        if target.exists():verify(target);return target
        for retry in range(3):
            if part.exists() and part.stat().st_size==spec['size']:
                verify(part);os.link(part,target);part.unlink();return target
            offset=part.stat().st_size if part.exists() else 0
            if offset>spec['size']:raise Incompatible('Oversized partial dump')
            if (self.state['attempts']>=self.max_attempts
                or self.state.get('reserved_bytes',self.state['bytes'])+spec['size']>self.max_bytes):
                raise Incompatible('Dump request/byte budget exhausted')
            delay=max(self.state['retry_at'],self.state['next_start'])-self.clock()
            if delay>120:raise Incompatible('Dump host cooldown exceeds wait budget')
            if delay>0:self.sleep(delay)
            # Reserve a complete response before I/O, including the 200/ignored
            # Range case. Never refund after crashes, errors or partial resumes.
            self.state['attempts']+=1;self.state['next_start']=self.clock()+1
            self.state['reserved_bytes']=self.state.get('reserved_bytes',self.state['bytes'])+spec['size'];self.save()
            receipt={'attempt':self.state['attempts'],'url':spec['url'],'offset':offset,'started_at':self.clock(),'bytes':0}
            headers={'Accept-Encoding':'identity'}
            if offset:headers['Range']=f'bytes={offset}-'
            retryable=False
            try:
                with self.client.stream('GET',spec['url'],headers=headers,follow_redirects=False) as response:
                    receipt.update(status=response.status_code,headers={k:v for k,v in response.headers.items()
                        if k.lower() in ('content-length','content-range','retry-after','etag','last-modified','server','date')})
                    if response.status_code in (429,500,502,503,504):
                        wait=5*2**retry*(1+random.random()/4);after=response.headers.get('retry-after')
                        if after:
                            try:seconds=float(after)
                            except ValueError:seconds=parsedate_to_datetime(after).timestamp()-self.clock()
                            if not math.isfinite(seconds):raise Incompatible('Invalid Retry-After')
                            wait=max(wait,seconds)
                        self.state['retry_at']=self.clock()+wait;retryable=True
                        raise httpx.TransportError(f'Dump HTTP {response.status_code}')
                    if response.status_code not in (200,206):raise Incompatible(f'Dump HTTP {response.status_code}')
                    if response.headers.get('content-encoding','identity')!='identity':raise Incompatible('Unexpected wire encoding')
                    if response.status_code==206:
                        expected=f'bytes {offset}-{spec["size"]-1}/{spec["size"]}'
                        if response.headers.get('content-range')!=expected:raise Incompatible('Invalid resumed range')
                    else:offset=0
                    with part.open('ab' if offset else 'wb') as f:
                        for chunk in response.iter_bytes(1024*256):
                            receipt['bytes']+=len(chunk);self.state['bytes']+=len(chunk)
                            if self.state['bytes']>self.max_bytes or f.tell()+len(chunk)>spec['size']:
                                raise Incompatible('Dump byte/size budget exceeded')
                            f.write(chunk)
                        f.flush();os.fsync(f.fileno())
                verify(part);os.link(part,target);part.unlink();self.state['retry_at']=0
                return target
            except httpx.TransportError as error:
                receipt['error']=str(error)
                if not retryable:self.state['retry_at']=self.clock()+5*2**retry
                if retry==2:raise Incompatible('Bounded dump retries exhausted') from error
            except BaseException as error:
                receipt['error']=str(error);receipt['error_type']=type(error).__name__
                raise
            finally:
                receipt['ended_at']=self.clock();self.state['receipts'].append(receipt);self.save()
        raise Incompatible('Bounded dump transfer stopped')
