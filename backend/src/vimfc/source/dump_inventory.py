"""Transactional metadata join and deterministic Seed-only discovery inventory."""
from collections import Counter,deque
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
from vimfc.domain import Incompatible,canonical,digest
from vimfc.pipeline.discovery_archive import STREAM_STRATA
from vimfc.pipeline.discovery_policy import seal_inventory
from .dump_download import hashes
from .dump_sql import sql_rows

VERSION='dump-inventory-1'
SNAPSHOT='2026-10-25T03:00:00Z'
REQUIRED={'page':{'page_id','page_namespace','page_title','page_is_redirect'},
          'linktarget':{'lt_id','lt_namespace','lt_title'},
          'categorylinks':{'cl_from','cl_type','cl_target_id'}}


class DumpInventory:
    def __init__(self,path,config):
        self.config=config;self.path=Path(path);self.db=sqlite3.connect(path,timeout=0)
        self.db.execute('PRAGMA cache_size=-32768');self.db.execute('PRAGMA temp_store=FILE')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS imported(name TEXT PRIMARY KEY,record TEXT);
        CREATE TABLE IF NOT EXISTS pages(id INTEGER PRIMARY KEY,ns INTEGER,title TEXT,redirect INTEGER,UNIQUE(ns,title));
        CREATE TABLE IF NOT EXISTS targets(id INTEGER PRIMARY KEY,title TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS links(page INTEGER,target INTEGER,kind TEXT,PRIMARY KEY(page,target));
        CREATE TABLE IF NOT EXISTS hints(page INTEGER,group_name TEXT,path TEXT,PRIMARY KEY(page,group_name));
        ''')
        settings={'version':VERSION,'snapshot':SNAPSHOT,'quotas':config['quotas'],'config_hash':digest(config),
            'roots':{STREAM_STRATA[r['id']]:r['title'].split(':',1)[1].replace(' ','_') for r in config['streams'] if r['id'] in STREAM_STRATA},
            'depth':3,'max_categories':128,'seed':'seed-only-wide-1','exploration_percent':20,
            'semantic_rules':{k:digest(config[k]) for k in ('classification','extraction','ranking')}}
        row=self.db.execute("SELECT value FROM settings WHERE key='policy'").fetchone()
        if row and row[0]!=canonical(settings):self.db.close();raise Incompatible('Dump inventory configuration changed')
        self.policy=settings
        with self.db:self.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',('policy',canonical(settings)))

    def __enter__(self):return self
    def __exit__(self,*args):self.db.close()

    def import_file(self,table,path,spec):
        if table not in REQUIRED or spec['table']!=table:raise Incompatible('Unsupported metadata table')
        observed=hashes(path)
        if observed['size']!=spec['size'] or observed['sha1']!=spec['sha1']:raise Incompatible('Unverified dump input')
        identity={'spec':spec,'hashes':observed}
        prior=self.db.execute('SELECT record FROM imported WHERE name=?',(table,)).fetchone()
        if prior:
            if json.loads(prior[0])['input']!=identity:raise Incompatible('Imported source changed')
            return json.loads(prior[0])
        schema,rows=sql_rows(path,table)
        if not REQUIRED[table]<=set(schema):rows.close();raise Incompatible('Missing required metadata columns')
        idx={k:schema.index(k) for k in REQUIRED[table]};batch=[];count=0;duplicates=0;ns_counts=Counter()
        def text(value):
            if not isinstance(value,bytes):raise Incompatible('Invalid metadata string')
            result=value.decode('utf-8')
            if not result or '\0' in result:raise Incompatible('Empty/NUL metadata title')
            return result
        def integer(value,positive=False):
            if type(value) is not int or (positive and value<=0):raise Incompatible('Invalid metadata ID/namespace')
            return value
        sql={'page':'INSERT INTO pages VALUES (?,?,?,?)','linktarget':'INSERT INTO targets VALUES (?,?)',
             'categorylinks':'INSERT INTO links VALUES (?,?,?)'}[table]
        def flush():
            nonlocal duplicates
            # SAVEPOINT isolates duplicate handling without losing prior batches.
            self.db.execute('SAVEPOINT batch')
            try:self.db.executemany(sql,batch)
            except sqlite3.IntegrityError:
                self.db.execute('ROLLBACK TO batch')
                for record in batch:
                    if table=='page':old=self.db.execute('SELECT * FROM pages WHERE id=?',(record[0],)).fetchone()
                    elif table=='linktarget':old=self.db.execute('SELECT * FROM targets WHERE id=?',(record[0],)).fetchone()
                    else:old=self.db.execute('SELECT * FROM links WHERE page=? AND target=?',record[:2]).fetchone()
                    if old is None:self.db.execute(sql,record)
                    elif tuple(old)!=record:raise Incompatible('Conflicting metadata identity/title')
                    else:duplicates+=1
            self.db.execute('RELEASE batch');batch.clear()
        try:
            self.db.execute('BEGIN IMMEDIATE')
            for row in rows:
                count+=1
                if table=='page':
                    page=integer(row[idx['page_id']],True);ns=integer(row[idx['page_namespace']]);redirect=row[idx['page_is_redirect']]
                    if redirect not in (0,1):raise Incompatible('Invalid redirect flag')
                    title=text(row[idx['page_title']]);ns_counts[ns]+=1
                    record=(page,ns,title,redirect)
                elif table=='linktarget':
                    target=integer(row[idx['lt_id']],True);ns=integer(row[idx['lt_namespace']])
                    if ns!=14:continue
                    record=(target,text(row[idx['lt_title']]))
                else:
                    page=integer(row[idx['cl_from']],True);target=integer(row[idx['cl_target_id']],True);kind=text(row[idx['cl_type']])
                    if kind not in ('page','subcat','file'):raise Incompatible('Unknown category kind')
                    if kind=='file':continue
                    record=(page,target,kind)
                batch.append(record)
                if len(batch)>=5000:flush()
            if batch:flush()
            record={'input':identity,'schema':schema,'rows':count,'duplicates':duplicates,'namespace_counts':dict(ns_counts)}
            self.db.execute('INSERT INTO imported VALUES (?,?)',(table,canonical(record)));self.db.commit()
            return record
        except BaseException:
            self.db.rollback();raise
        finally:rows.close()

    def summary(self):
        return {'pages':self.db.execute('SELECT count(*) FROM pages').fetchone()[0],
            'targets':self.db.execute('SELECT count(*) FROM targets').fetchone()[0],
            'links':self.db.execute('SELECT count(*) FROM links').fetchone()[0],
            'imports':[json.loads(r[0]) for r in self.db.execute('SELECT record FROM imported ORDER BY name')]}

    def build_hints(self):
        if self.db.execute('SELECT count(*) FROM imported').fetchone()[0]!=3:raise Incompatible('Incomplete metadata sources')
        with self.db:
            self.db.execute('CREATE INDEX IF NOT EXISTS links_target ON links(target,kind,page)')
            self.db.execute('DELETE FROM hints')
            coverage={}
            for group,root in sorted(self.policy['roots'].items()):
                queue=deque([(root,[root])]);seen={root};visited=0;missing=[]
                while queue:
                    name,path=queue.popleft();target=self.db.execute('SELECT id FROM targets WHERE title=?',(name,)).fetchone()
                    if not target:missing.append(name);continue
                    visited+=1
                    for page, in self.db.execute('SELECT l.page FROM links l JOIN pages p ON p.id=l.page WHERE l.target=? AND l.kind=\'page\' AND p.ns=0 AND p.redirect=0 ORDER BY l.page',(target[0],)):
                        self.db.execute('INSERT OR IGNORE INTO hints VALUES (?,?,?)',(page,group,canonical(path)))
                    if len(path)-1>=self.policy['depth']:continue
                    for title, in self.db.execute('SELECT p.title FROM links l JOIN pages p ON p.id=l.page WHERE l.target=? AND l.kind=\'subcat\' AND p.ns=14 AND p.redirect=0 ORDER BY p.title',(target[0],)):
                        if title in seen or len(seen)>=self.policy['max_categories']:continue
                        seen.add(title);queue.append((title,path+[title]))
                coverage[group]={'categories':visited,'missing_targets':missing,'hinted_pages':self.db.execute('SELECT count(*) FROM hints WHERE group_name=?',(group,)).fetchone()[0]}
        return coverage

    def inventory(self,*,target=100000,minimum=60000):
        if not 1<=minimum<=target<=100000:raise Incompatible('Inventory bounds')
        coverage=self.build_hints()
        available=self.db.execute('SELECT count(*) FROM pages WHERE ns=0 AND redirect=0').fetchone()[0]
        if available<minimum:raise Incompatible('Insufficient verified identities')
        target=min(target,available)
        # Disk-backed hash ordering over the entire universe; only bounded ID pools in RAM.
        self.db.create_function('seed_rank',1,lambda p:digest([self.policy['seed'],p]),deterministic=True)
        self.db.execute('CREATE TEMP TABLE ranked AS SELECT id,seed_rank(id) rank FROM pages WHERE ns=0 AND redirect=0')
        self.db.execute('CREATE INDEX temp.ranked_order ON ranked(rank,id)')
        pools={g:deque(r[0] for r in self.db.execute('SELECT r.id FROM ranked r JOIN hints h ON h.page=r.id WHERE h.group_name=? ORDER BY r.rank,r.id LIMIT ?',(g,target))) for g in self.policy['quotas']}
        unknown=deque(r[0] for r in self.db.execute('SELECT r.id FROM ranked r WHERE NOT EXISTS(SELECT 1 FROM hints h WHERE h.page=r.id) ORDER BY rank,id LIMIT ?',(target,)))
        fallback=deque(r[0] for r in self.db.execute('SELECT id FROM ranked ORDER BY rank,id LIMIT ?',(target,)))
        selected=set();served=Counter()
        def take(q):
            while q and q[0] in selected:q.popleft()
            return q.popleft() if q else None
        for i in range(target):
            page=take(unknown) if i%5==4 else None
            if page is None:
                for q in pools.values():
                    while q and q[0] in selected:q.popleft()
                groups=[g for g,q in pools.items() if q]
                if groups:
                    group=min(groups,key=lambda g:(Fraction(served[g],self.policy['quotas'][g]),g));page=take(pools[group]);served[group]+=1
                else:page=take(unknown)
            if page is None:page=take(fallback)
            if page is None:raise Incompatible('Sampling pool exhausted unexpectedly')
            selected.add(page)
        self.db.execute('DROP TABLE ranked');self.db.commit()
        imports={name:json.loads(record) for name,record in self.db.execute('SELECT name,record FROM imported ORDER BY name')}
        source={'version':VERSION,'wiki':'viwiki','dump_date':'20261001','policy':self.policy,
            'config_hash':self.policy['config_hash'],'policy_hash':digest(self.policy),
            'artifacts':imports,'metadata_is_historical_evidence':False,'universe_nonredirect_ns0':available,'coverage':coverage}
        source_id=digest(source);seeds=[];counts=Counter()
        for page in sorted(selected):
            title=self.db.execute('SELECT title FROM pages WHERE id=?',(page,)).fetchone()[0].replace('_',' ')
            hints=list(self.db.execute('SELECT group_name,path FROM hints WHERE page=? ORDER BY group_name',(page,)))
            groups=[r[0] for r in hints];counts.update(groups)
            origin={'kind':'seed','source_version':VERSION,'source_manifest_id':source_id,'dump_page_id':page,
                'page_file_sha256':imports['page']['input']['hashes']['sha256'],'metadata_scope':'discovery_time_only',
                'category_paths':{g:json.loads(path) for g,path in hints}}
            seeds.append({'page_id':page,'title':title,'namespace':0,'strata':groups,'origins':[origin],
                'namespace_basis':'dump_metadata_only','candidate':{'page_id':page,'title':title,
                    'discovery_origin':'stratified-seed-only','discovery_metadata_only':True,'discovery_namespace_status':'unverified'}})
        source.update(selected_unique=len(seeds),strata_membership=dict(counts),unassigned=sum(not r['strata'] for r in seeds),
            provenance_manifest_id=source_id)
        return seal_inventory({'format':'vimfc-discovery-inventory-1','snapshot':SNAPSHOT,
            'semantic_rules':self.policy['semantic_rules'],'source':source,'seeds':seeds,'neighbors':[]})
