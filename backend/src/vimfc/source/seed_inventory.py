"""Resumable listing identities, never historical classification/evidence."""
from collections import Counter
from copy import deepcopy
import json
import sqlite3
from datetime import datetime,timezone,timedelta
import os
import random
import threading
import time
import uuid
import psutil
from vimfc.domain import Incompatible, SourceUnavailable, canonical, digest, now, utc, at_or_before
from vimfc.pipeline.discovery_archive import STREAM_STRATA

VERSION = 'seed-only-2'
OPERATIONAL_POLICY={'version':'inventory-host-1','pacing_seconds':1,'backoff_base_seconds':5,
    'backoff_cap_seconds':300,'jitter_fraction':0.25,'lease_seconds':300,'http_attempts_per_query':1,
    'stop_on_any_source_failure':True}


class InventoryBudgetExhausted(Incompatible):
    code='inventory_request_budget_exhausted'


def inventory_policy(config, *, target=80000, max_requests=800):
    if not 60000 <= target <= 100000 or not 1 <= max_requests <= 800:
        raise Incompatible('Inventory target60000..100000; finite request budget1..800')
    roots = {s['id']:s['title'] for s in config['streams'] if s['kind']=='categorymembers'}
    if set(roots) != set(STREAM_STRATA):
        raise Incompatible('Eight configured category streams required')
    return {'version':VERSION, 'snapshot':'2026-10-25T03:00:00Z', 'target':target,
            'max_requests':max_requests, 'roots':roots, 'quotas':config['quotas'],
            'seed':'seed-only-wide-1', 'category_depth':3, 'categories_per_stratum':128,
            'exploration_percent':20, 'listing_limit':500}


class SeedInventory:
    def __init__(self, path, policy,*,clock=None,sleep=time.sleep,jitter=None):
        self.policy = policy
        self.clock=clock or (lambda:datetime.now(timezone.utc));self.sleep=sleep;self.jitter=jitter or random.random
        self.owner=str(uuid.uuid4());self._step_lock=threading.Lock()
        self._lease_depth=0
        self.pacing_wait_seconds=0
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone()
        row=self.db.execute("SELECT value FROM metadata WHERE key='policy'").fetchone() if exists else None
        if row and json.loads(row[0])!=policy:
            self.db.close();raise Incompatible('Inventory checkpoint policy changed')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS streams(stream TEXT PRIMARY KEY, state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS pages(page_id INTEGER PRIMARY KEY, title TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS origins(page_id INTEGER, stream TEXT, response_hash TEXT,
              category TEXT, PRIMARY KEY(page_id,stream,response_hash));
          CREATE TABLE IF NOT EXISTS responses(hash TEXT PRIMARY KEY, record TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,stream TEXT,started_at TEXT,
              ended_at TEXT,error TEXT);
          CREATE TABLE IF NOT EXISTS attempt_receipts(attempt_id INTEGER PRIMARY KEY, hash TEXT NOT NULL,record TEXT NOT NULL);
        ''')
        row = self.db.execute("SELECT value FROM metadata WHERE key='policy'").fetchone()
        if row and json.loads(row[0]) != policy:
            self.db.close(); raise Incompatible('Inventory checkpoint policy changed')
        if not row:
            with self.db:
                self.db.execute('INSERT INTO metadata VALUES (?,?)', ('policy',canonical(policy)))
                self.db.execute('INSERT INTO metadata VALUES (?,?)', ('created_at',now()))
                for stream,root in policy['roots'].items():
                    state = {'queue':[[root,0]], 'index':0, 'seen':[root], 'continuation':{}, 'exhausted':False}
                    self.db.execute('INSERT INTO streams VALUES (?,?)', (stream,canonical(state)))
                self.db.execute('INSERT INTO streams VALUES (?,?)', ('exploration',canonical({'continuation':{},'exhausted':False})))

    def __enter__(self): return self
    def __exit__(self,*args):
        while self._lease_depth:self.release_host()
        self.db.close()
    def count(self): return self.db.execute('SELECT count(*) FROM pages').fetchone()[0]
    def state(self,stream):
        return json.loads(self.db.execute('SELECT state FROM streams WHERE stream=?',(stream,)).fetchone()[0])

    def stamp(self):return self.clock().isoformat().replace('+00:00','Z')

    def claim_host(self):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute("SELECT value FROM metadata WHERE key='host_lease'").fetchone()
            if row:
                lease=json.loads(row[0]);alive=False
                try:alive=psutil.Process(lease['pid']).create_time()==lease['process_started']
                except psutil.AccessDenied:alive=True
                except psutil.Error:pass
                if lease['owner']!=self.owner and (alive or not at_or_before(lease['until'],self.stamp())):
                    raise SourceUnavailable('Inventory host lease is active; no request sent',retryable=True,code='inventory_lease',retry_at=lease['until'])
            lease={'owner':self.owner,'pid':os.getpid(),'process_started':psutil.Process().create_time(),
                'until':(self.clock()+timedelta(seconds=OPERATIONAL_POLICY['lease_seconds'])).isoformat().replace('+00:00','Z')}
            self.db.execute("INSERT INTO metadata VALUES ('host_lease',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(lease),))
        self._lease_depth+=1

    def release_host(self):
        if self._lease_depth>1:self._lease_depth-=1;return
        self._lease_depth=0
        row=self.db.execute("SELECT value FROM metadata WHERE key='host_lease'").fetchone()
        if row and json.loads(row[0])['owner']==self.owner:
            with self.db:self.db.execute("DELETE FROM metadata WHERE key='host_lease'")

    def _host_failure(self,error):
        row=self.db.execute("SELECT value FROM metadata WHERE key='host_state'").fetchone()
        prior=json.loads(row[0]) if row else {}
        streak=prior.get('failure_streak',0)+1
        base=min(OPERATIONAL_POLICY['backoff_cap_seconds'],OPERATIONAL_POLICY['backoff_base_seconds']*2**min(streak-1,10))
        delay=min(OPERATIONAL_POLICY['backoff_cap_seconds'],base*(1+OPERATIONAL_POLICY['jitter_fraction']*max(0,min(1,self.jitter()))))
        deadline=(self.clock()+timedelta(seconds=delay)).isoformat().replace('+00:00','Z')
        for candidate in (getattr(error,'retry_at',None),prior.get('retry_at')):
            if candidate and not at_or_before(candidate,deadline):deadline=candidate
        state={'state':'open','site':'viwiki','failure_streak':streak,'retry_at':deadline,
            'backoff_seconds':delay,'reason':getattr(error,'code',type(error).__name__),'operational_policy':OPERATIONAL_POLICY['version']}
        self.db.execute("INSERT INTO metadata VALUES ('host_state',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(state),))
        self.db.execute("INSERT INTO metadata VALUES ('viwiki_cooldown',?) ON CONFLICT(key) DO UPDATE SET value=max(value,excluded.value)",(deadline,))
        if isinstance(error,SourceUnavailable):error.retry_at=deadline
        return state

    def step(self,client,stream):
        with self._step_lock:
            if self.db.execute("SELECT 1 FROM metadata WHERE key='frozen'").fetchone():
                raise Incompatible('Frozen inventory is immutable; use a new inventory for extension')
            self.claim_host()
            try:return self._step(client,stream)
            finally:self.release_host()

    def _step(self,client,stream):
        if self.db.execute("SELECT 1 FROM metadata WHERE key='frozen'").fetchone():
            raise Incompatible('Frozen inventory is immutable; use a new inventory for extension')
        if self.db.execute('SELECT count(*) FROM attempts').fetchone()[0] >= self.policy['max_requests']:
            raise InventoryBudgetExhausted('Inventory request budget exhausted; checkpoint retained')
        cooldown=self.db.execute("SELECT value FROM metadata WHERE key='viwiki_cooldown'").fetchone()
        if cooldown and not at_or_before(cooldown[0],self.stamp()):
            raise SourceUnavailable('Inventory host cooldown; no request sent',code='host_cooldown',retryable=True,retry_at=cooldown[0])
        pace=self.db.execute("SELECT value FROM metadata WHERE key='host_next_start'").fetchone()
        if pace:
            seconds=max(0,(datetime.fromisoformat(pace[0].replace('Z','+00:00'))-self.clock()).total_seconds())
            if seconds>60:raise SourceUnavailable('Inventory pacing deadline exceeds wait budget',code='host_cooldown',retryable=True,retry_at=pace[0])
            if seconds:self.sleep(seconds);self.pacing_wait_seconds+=seconds
        state = self.state(stream)
        if state['exhausted']: return False
        category = None
        params = {'action':'query', 'curtimestamp':1}
        if stream == 'exploration':
            key = 'allpages'
            params.update(list=key,apnamespace=0,apfilterredir='nonredirects',aplimit=self.policy['listing_limit'])
        else:
            category,depth = state['queue'][state['index']]
            key = 'categorymembers'
            params.update(list=key,cmtitle=category,cmnamespace='0|14',cmtype='page|subcat',
                          cmprop='ids|title|type',cmlimit=self.policy['listing_limit'])
        params.update(state['continuation'])
        # Charge before I/O: a crash cannot reset the finite request budget.
        with self.db:
            attempt = self.db.execute('INSERT INTO attempts(stream,started_at) VALUES (?,?)',(stream,self.stamp())).lastrowid
            next_start=(self.clock()+timedelta(seconds=OPERATIONAL_POLICY['pacing_seconds'])).isoformat().replace('+00:00','Z')
            self.db.execute("INSERT INTO metadata VALUES ('host_next_start',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(next_start,))
        try:
            payload = client.query('viwiki',params,attempts=1)
            timestamp = utc(payload['curtimestamp'])
            rows = payload['query'][key]
            if not isinstance(rows,list): raise Incompatible('Invalid listing rows')
            continuation = payload.get('continue') or {}
            if continuation and (not isinstance(continuation,dict) or continuation == state['continuation']):
                raise Incompatible('Listing cursor did not advance')
            if continuation and self.db.execute('SELECT 1 FROM metadata WHERE key=?',('cursor:'+digest([stream,category,continuation]),)).fetchone():
                raise Incompatible('Listing cursor cycle')
            envelope = {'site':'viwiki','params':params,'payload':payload,'observation_only':True}
            identity = digest(envelope)
            # Validate the whole response before publishing any identities/state.
            with self.db:
                for row in rows:
                    page,title,ns = row.get('pageid'),row.get('title'),row.get('ns')
                    if type(page) is not int or page<=0 or not isinstance(title,str) or not title.strip():
                        raise Incompatible('Invalid listing identity')
                    if ns == 14 and stream != 'exploration' and row.get('type')=='subcat':
                        if (title not in state['seen'] and depth < self.policy['category_depth']
                                and len(state['queue']) < self.policy['categories_per_stratum']):
                            state['seen'].append(title); state['queue'].append([title,depth+1])
                        continue
                    if type(ns) is not int or ns!=0 or row.get('type','page')!='page':
                        raise Incompatible('Non-main namespace in page listing')
                    old = self.db.execute('SELECT title FROM pages WHERE page_id=?',(page,)).fetchone()
                    if old and old[0]!=title: raise Incompatible('Conflicting discovery page titles; review source consistency')
                    if not old and self.count()>=self.policy['target']:continue
                    self.db.execute('INSERT OR IGNORE INTO pages VALUES (?,?)',(page,title))
                    self.db.execute('INSERT OR IGNORE INTO origins VALUES (?,?,?,?)',(page,stream,identity,category))
                self.db.execute('INSERT OR IGNORE INTO responses VALUES (?,?)',(identity,canonical(envelope)))
                if continuation:
                    self.db.execute('INSERT INTO metadata VALUES (?,?)',('cursor:'+digest([stream,category,continuation]),timestamp))
                state['continuation']=continuation
                if not continuation:
                    if stream=='exploration': state['exhausted']=True
                    else:
                        state['index']+=1
                        state['exhausted']=state['index']>=len(state['queue'])
                self.db.execute('UPDATE streams SET state=? WHERE stream=?',(canonical(state),stream))
                ended=self.stamp()
                self.db.execute('UPDATE attempts SET ended_at=? WHERE id=?',(ended,attempt))
                host={'state':'closed','site':'viwiki','failure_streak':0,'retry_at':None,'operational_policy':OPERATIONAL_POLICY['version']}
                self.db.execute("INSERT INTO metadata VALUES ('host_state',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(host),))
                receipt={'attempt_id':attempt,'stream':stream,'ended_at':ended,'response_hash':identity,
                    'http':getattr(client,'last_response_details',{}),'host_state':host,'error':None}
                self.db.execute('INSERT INTO attempt_receipts VALUES (?,?,?)',(attempt,digest(receipt),canonical(receipt)))
            return True
        except BaseException as error:
            with self.db:
                ended=self.stamp();self.db.execute('UPDATE attempts SET ended_at=?,error=? WHERE id=?',(ended,str(error),attempt))
                host=self._host_failure(error)
                receipt={'attempt_id':attempt,'stream':stream,'ended_at':ended,'cursor_before':params,
                    'http':getattr(client,'last_response_details',{}),'host_state':host,
                    'error':{'type':type(error).__name__,'code':getattr(error,'code',None),'message':str(error),
                        'retry_at':getattr(error,'retry_at',None),'details':getattr(error,'details',{})}}
                self.db.execute('INSERT INTO attempt_receipts VALUES (?,?,?)',(attempt,digest(receipt),canonical(receipt)))
            if isinstance(error,(KeyError,TypeError,ValueError)):
                raise Incompatible('Malformed listing response; no identities published') from error
            raise

    def collect(self,client,progress=None):
        streams=list(self.policy['roots'])
        while self.count() < self.policy['target']:
            counts=dict(self.db.execute('SELECT stream,count(DISTINCT page_id) FROM origins GROUP BY stream'))
            available=[s for s in streams if not self.state(s)['exhausted']]
            target=self.policy['target']; percent=self.policy['exploration_percent']
            options=[s for s in available if counts.get(s,0) < target*(100-percent)*self.policy['quotas'][STREAM_STRATA[s]]/2000000]
            if options:
                stream=min(options,key=lambda s:(counts.get(s,0)/self.policy['quotas'][STREAM_STRATA[s]],s))
                # One fifth of listing calls explores the namespace from the outset.
                calls=self.db.execute('SELECT count(*) FROM attempts').fetchone()[0]
                if calls%5==4 and not self.state('exploration')['exhausted']:stream='exploration'
            elif not self.state('exploration')['exhausted']:stream='exploration'
            elif available:stream=min(available,key=lambda s:(counts.get(s,0),s))
            else:break
            self.step(client,stream)
            if progress:progress({'event':'inventory_listing','stream':stream,'unique':self.count(),
                'requests':self.db.execute('SELECT count(*) FROM attempts').fetchone()[0]})
        return self.count()

    def freeze(self,config,*,commit=True):
        with self._step_lock:
            self.claim_host()
            try:return self._freeze(config,commit=commit)
            finally:self.release_host()

    def _freeze(self,config,*,commit=True):
        for _,identity,encoded in self.db.execute('SELECT * FROM attempt_receipts'):
            if digest(json.loads(encoded))!=identity:raise Incompatible('Attempt receipt checksum mismatch')
        observed={}
        for identity,encoded in self.db.execute('SELECT * FROM responses'):
            envelope=json.loads(encoded)
            if digest(envelope)!=identity or envelope.get('site')!='viwiki' or envelope.get('observation_only') is not True:
                raise Incompatible('Listing response checksum/scope mismatch')
            key=envelope['params']['list']
            for row in envelope['payload']['query'][key]:
                if row['ns']==0:observed[(identity,row['pageid'])]=row['title']
        origins={}
        for row in self.db.execute('SELECT * FROM origins ORDER BY page_id,stream,response_hash'):
            title=self.db.execute('SELECT title FROM pages WHERE page_id=?',(row['page_id'],)).fetchone()
            if not title or observed.get((row['response_hash'],row['page_id']))!=title[0]:
                raise Incompatible('Listing identity lacks matching captured provenance')
            origins.setdefault(row['page_id'],[]).append({'kind':'seed','stream':row['stream'],
                'listing_response_hash':row['response_hash'],'category':row['category'],
                'metadata_scope':'discovery_time_only'})
        seeds=[]; strata=Counter()
        for page,title in self.db.execute('SELECT * FROM pages ORDER BY page_id'):
            if page not in origins:raise Incompatible('Listing identity has no discovery origin')
            groups=sorted({STREAM_STRATA[o['stream']] for o in origins[page] if o['stream'] in STREAM_STRATA})
            strata.update(groups)
            seeds.append({'page_id':page,'title':title,'namespace':0,'strata':groups,'origins':origins[page],
                'namespace_basis':'discovery_time_listing_only','candidate':{'page_id':page,'title':title,
                    'discovery_origin':'stratified-seed-only','discovery_metadata_only':True,
                    'discovery_namespace_status':'unverified'}})
        observations=self.db.execute('SELECT count(*) FROM origins').fetchone()[0]
        responses=[r[0] for r in self.db.execute('SELECT hash FROM responses ORDER BY hash')]
        body={'format':'vimfc-discovery-inventory-1','snapshot':self.policy['snapshot'],
            'semantic_rules':{k:digest(config[k]) for k in ('classification','extraction','ranking')},
            'source':{'version':VERSION,'policy':self.policy,'policy_hash':digest(self.policy),
                'created_at':self.db.execute("SELECT value FROM metadata WHERE key='created_at'").fetchone()[0],
                'response_manifest_id':digest(responses),'response_hashes':responses,
                'physical_requests':self.db.execute('SELECT count(*) FROM attempts').fetchone()[0],
                'unique_candidates':len(seeds),'observations':observations,'duplicate_observations':observations-len(seeds),
                'strata_membership':dict(strata),'unassigned':sum(not r['strata'] for r in seeds),
                'target_reached':len(seeds)>=self.policy['target'],
                'metadata_is_historical_evidence':False},'seeds':seeds,'neighbors':[]}
        result={**body,'inventory_id':digest(body)}
        prior=self.db.execute("SELECT value FROM metadata WHERE key='frozen'").fetchone()
        if prior and prior[0]!=result['inventory_id']:raise Incompatible('Frozen inventory checksum changed')
        if commit:
            with self.db:self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)',('frozen',result['inventory_id']))
        return result
