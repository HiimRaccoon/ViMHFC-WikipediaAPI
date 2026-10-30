"""Groq-only durable reservations/cache and shared-host pacing; no corpus writes."""
from contextlib import contextmanager
import json,os,sqlite3,time,uuid
from pathlib import Path
import psutil
from vimfc.domain import canonical,digest
from .provider import parse_reset_duration

FORMAT='vimfc-groq-ledger-1'

class Pause(RuntimeError):
 def __init__(self,code,retry_at=None):super().__init__(code);self.code=code;self.retry_at=retry_at

class ExperimentStore:
 def __init__(self,path,scope,limits,*,clock=time.time):
  self.path=Path(path).resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
  self.scope=scope;self.scope_id=digest(scope);self.limits=limits;self.clock=clock
  if self.path.exists() and self.path.stat().st_size:
   with sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True) as existing:
    tables={r[0] for r in existing.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables!={'metadata','scopes','state','attempts','results'}:raise ValueError('Refusing foreign or incomplete experiment database')
    rows=existing.execute('SELECT format FROM metadata').fetchall()
    if rows!=[(FORMAT,)]:raise ValueError('Refusing foreign experiment database format')
  self.db=sqlite3.connect(self.path,isolation_level=None,timeout=5);self.db.row_factory=sqlite3.Row
  self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
  self.db.executescript('''CREATE TABLE IF NOT EXISTS metadata(format TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS scopes(scope_id TEXT PRIMARY KEY,record_json TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,record_json TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,scope_id TEXT NOT NULL,request_key TEXT NOT NULL,model_id TEXT NOT NULL,endpoint TEXT NOT NULL,invocation_id TEXT NOT NULL,created_at REAL NOT NULL,reserved_tokens INTEGER NOT NULL,charged_tokens INTEGER NOT NULL,status TEXT NOT NULL,receipt_json TEXT,receipt_hash TEXT);
  CREATE INDEX IF NOT EXISTS attempts_key ON attempts(request_key);
  CREATE INDEX IF NOT EXISTS attempts_time ON attempts(created_at);
  CREATE TABLE IF NOT EXISTS results(request_key TEXT PRIMARY KEY,record_json TEXT NOT NULL,record_hash TEXT NOT NULL);
  ''')
  formats=self.db.execute('SELECT format FROM metadata').fetchall()
  if not formats:self.db.execute('INSERT INTO metadata VALUES (?)',(FORMAT,))
  elif len(formats)!=1 or formats[0][0]!=FORMAT:raise ValueError('Unsupported Groq ledger')
  previous=self.db.execute('SELECT record_json FROM scopes WHERE scope_id=?',(self.scope_id,)).fetchone()
  if previous and json.loads(previous[0])!=scope:raise ValueError('Experiment scope mismatch')
  self.db.execute('INSERT OR IGNORE INTO scopes VALUES (?,?)',(self.scope_id,canonical(scope)))
 def __enter__(self):return self
 def __exit__(self,*a):self.db.close()
 def get(self,key,default=None):
  r=self.db.execute('SELECT record_json FROM state WHERE key=?',(key,)).fetchone()
  return json.loads(r[0]) if r else default
 def put(self,key,value):self.db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(key,canonical(value)))
 @contextmanager
 def transaction(self):
  self.db.execute('BEGIN IMMEDIATE')
  try:yield
  except BaseException:self.db.rollback();raise
  else:self.db.commit()
 @contextmanager
 def lease(self):
  token=str(uuid.uuid4());identity={'pid':os.getpid(),'created':psutil.Process().create_time(),'token':token}
  with self.transaction():
   prior=self.get('lease')
   if prior:
    try:alive=psutil.Process(prior['pid']).create_time()==prior['created']
    except psutil.NoSuchProcess:alive=False
    except psutil.Error:alive=True
    if alive:raise Pause('worker_lease_active')
   self.put('lease',identity)
  try:yield
  finally:
   with self.transaction():
    if (self.get('lease') or {}).get('token')==token:self.db.execute("DELETE FROM state WHERE key='lease'")
 def cached(self,key):
  r=self.db.execute('SELECT record_json,record_hash FROM results WHERE request_key=?',(key,)).fetchone()
  if not r:return None
  obj=json.loads(r[0])
  if digest(obj)!=r[1] or obj.get('request_key')!=key:raise ValueError('Groq cached result checksum mismatch')
  return obj
 def save_result(self,key,result):
  with self.transaction():
   old=self.cached(key)
   if old is not None and old!=result:raise ValueError('Conflicting immutable Groq result')
   self.db.execute('INSERT OR IGNORE INTO results VALUES (?,?,?)',(key,canonical(result),digest(result)))
 def attempts_for(self,key):return self.db.execute('SELECT count(*) FROM attempts WHERE request_key=?',(key,)).fetchone()[0]
 def successful_response(self,key):
  row=self.db.execute("SELECT * FROM attempts WHERE request_key=? AND endpoint='chat' AND status='succeeded' ORDER BY id DESC LIMIT 1",(key,)).fetchone()
  if row is None:return None
  receipt=json.loads(row['receipt_json'])
  if digest(receipt)!=row['receipt_hash']:raise ValueError('Persisted HTTP response checksum mismatch')
  if 'body' not in receipt:raise Pause('successful_response_requires_manual_reconciliation')
  return {'attempt_id':row['id'],'invocation_id':row['invocation_id'],'response':receipt}
 def pending(self):return [dict(r) for r in self.db.execute("SELECT * FROM attempts WHERE status IN ('reserved','uncertain')")]
 def cooldown(self,seconds,reason):
  with self.transaction():self.put('cooldown',{'until':max(self.clock()+max(0,seconds),(self.get('cooldown') or {}).get('until',0)),'reason':reason})
 def headers(self,headers):
  safe={k.lower():v for k,v in headers.items()}
  with self.transaction():
   state=self.get('rate_headers',{})
   for key in ('requests','tokens'):
    for prefix in ('limit','remaining'):
     value=safe.get(f'x-ratelimit-{prefix}-{key}')
     if value is not None:
      try:n=int(value)
      except (ValueError,TypeError):continue
      if n>=0:state[f'{prefix}_{key}']=n
    reset=safe.get(f'x-ratelimit-reset-{key}')
    if reset is not None:
     seconds=parse_reset_duration(reset)
     if seconds is not None:state[f'reset_{key}']=self.clock()+seconds
   self.put('rate_headers',state)
 def reserve(self,key,tokens,model_id,endpoint,invocation):
  if type(tokens) is not int or tokens<0:raise ValueError('Invalid token reservation')
  now=self.clock();lim=self.limits
  with self.transaction():
   if self.get('usage_reservation_overrun'):raise Pause('actual_usage_exceeded_reservation_requires_review')
   if self.db.execute("SELECT 1 FROM attempts WHERE request_key=? AND endpoint='chat' AND status='succeeded'",(key,)).fetchone():raise Pause('successful_request_requires_replay')
   if self.db.execute("SELECT 1 FROM attempts WHERE request_key=? AND status IN ('reserved','uncertain')",(key,)).fetchone():raise Pause('uncertain_request_requires_review')
   if self.attempts_for(key)>=lim.get('max_attempts',2):raise Pause('request_retry_limit')
   count,total=self.db.execute('SELECT count(*),coalesce(sum(charged_tokens),0) FROM attempts WHERE scope_id=?',(self.scope_id,)).fetchone()
   if count>=lim['max_requests']:raise Pause('request_budget_exhausted')
   if total+tokens>lim['max_total_tokens']:raise Pause('token_budget_exhausted')
   cooldown=self.get('cooldown',{})
   if now<cooldown.get('until',0):raise Pause('host_cooldown',cooldown['until'])
   rate=self.get('rate_headers',{})
   rpd=min(lim['rpd'],rate.get('limit_requests',lim['rpd']));tpm=min(lim['tpm'],rate.get('limit_tokens',lim['tpm']))
   if tokens>tpm:raise Pause('single_request_token_limit')
   if now<rate.get('reset_requests',now) and rate.get('remaining_requests',1)==0:raise Pause('provider_daily_limit',rate['reset_requests'])
   if now<rate.get('reset_tokens',now) and rate.get('remaining_tokens',tokens)<tokens:raise Pause('provider_token_limit',rate['reset_tokens'])
   if rate.get('remaining_requests')==0 and not rate.get('reset_requests'):raise Pause('provider_daily_reset_unknown')
   if tokens and rate.get('remaining_tokens')==0 and not rate.get('reset_tokens'):raise Pause('provider_token_reset_unknown')
   # Aggregate every scope/model conservatively. Other clients/org usage is
   # unknown; response headers may reduce these bounds, never enlarge them.
   for seconds,max_requests,max_tokens,name in ((60,lim['rpm'],tpm,'minute'),(86400,rpd,lim['tpd'],'day')):
    rows=list(self.db.execute('SELECT created_at,charged_tokens FROM attempts WHERE created_at>? ORDER BY created_at',(now-seconds,)))
    if len(rows)>=max_requests or sum(r[1] for r in rows)+tokens>max_tokens:
     raise Pause(name+'_rate_limit',rows[0][0]+seconds if rows else now+seconds)
   next_start=self.get('next_start',0)
   if now<next_start:raise Pause('request_pacing',next_start)
   self.put('next_start',now+60/lim['rpm'])
   cur=self.db.execute('INSERT INTO attempts(scope_id,request_key,model_id,endpoint,invocation_id,created_at,reserved_tokens,charged_tokens,status) VALUES (?,?,?,?,?,?,?,?,?)',
    (self.scope_id,key,model_id,endpoint,invocation,now,tokens,tokens,'reserved'))
   return cur.lastrowid
 def finish(self,attempt,receipt,*,tokens=None,error=False,uncertain=False):
  exceeded=False
  with self.transaction():
   row=self.db.execute('SELECT * FROM attempts WHERE id=?',(attempt,)).fetchone()
   if row is None or row['status']!='reserved':raise ValueError('Attempt already finalized or absent')
   if tokens is not None and (type(tokens) is not int or tokens<0):raise ValueError('Invalid actual token usage')
   charged=row['reserved_tokens'] if tokens is None else tokens
   self.db.execute('UPDATE attempts SET status=?,charged_tokens=?,receipt_json=?,receipt_hash=? WHERE id=?',
    ('uncertain' if uncertain else 'error' if error else 'succeeded',charged,canonical(receipt),digest(receipt),attempt))
   exceeded=tokens is not None and tokens>row['reserved_tokens']
   if exceeded:self.put('usage_reservation_overrun',{'attempt_id':attempt,'reserved_tokens':row['reserved_tokens'],'actual_tokens':tokens})
  if exceeded:raise Pause('actual_usage_exceeded_reservation')
 def summary(self):
  rows=list(self.db.execute('SELECT * FROM attempts WHERE scope_id=? ORDER BY id',(self.scope_id,)))
  return {'physical_attempts':len(rows),'charged_tokens':sum(r['charged_tokens'] for r in rows),'reserved_tokens':sum(r['reserved_tokens'] for r in rows),
   'successful_http':sum(r['status']=='succeeded' for r in rows),'error_attempts':sum(r['status']=='error' for r in rows),
   'uncertain_attempts':sum(r['status'] in ('reserved','uncertain') for r in rows),'scope_id':self.scope_id}
 def results(self):return [self.cached(r[0]) for r in self.db.execute('SELECT request_key FROM results ORDER BY request_key')]
