"""Bounded root forests over checked historical first paragraphs."""
from contextlib import closing
from copy import deepcopy
from fractions import Fraction
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from vimfc.domain import GROUPS,Incompatible,SourceUnavailable,canonical,digest,now
from vimfc.source.identities import normalize_title
from vimfc.storage.pipeline_store import PipelineStore
from .fast20k import load,restore,checkpoint,ambiguous_move
from .incremental_collection import failure_kind,_publish_json
from vimfc.source.dump_download import exclusive

POLICY=dict(version='btt-forest-1',max_depth=3,max_children=2,probe_limit=8,
            score='distinct-retained-first-paragraph-targets',roots='soft-quota-degree-1')


def titles(article):
    return sorted({normalize_title(l['target_title']) for l in article['extraction']['links']
        if l['scope']=='retained_lead_prose' and l.get('target_title')})


def prepare(bundle,workspace,durable,source,profile,*,fixture=False):
    """Fork a stopped checkpoint. Originals stay read-only; spending is carried upward."""
    from .fast20k import validate_profile
    validate_profile(profile,fixture=fixture)
    workspace,durable,source=map(lambda p:Path(p).resolve(),(workspace,durable,source))
    if durable.exists() or workspace.exists() or durable.is_relative_to(source) or workspace.is_relative_to(source):
        raise Incompatible('BTT requires separate new workspace and durable directory')
    parent=load(source/'latest.json');old=load(source/parent['directory']/'workspace.json')
    if old['binding_id']!=digest({k:v for k,v in old.items() if k!='binding_id'}) or parent['binding_id']!=old['binding_id']:
        raise Incompatible('BTT source header binding differs')
    if old.get('fixture')!=fixture or old['profile'].get('selection'):
        raise Incompatible('BTT import requires a compatible stopped seed campaign')
    if parent['progress']['stop_reason']!='CHECKPOINT_STOP':raise Incompatible('Source campaign must be checkpoint-stopped')
    source_budget=load(source/'http-budget.json') if (source/'http-budget.json').exists() else None
    if source_budget and source_budget['binding']!=digest(old):raise Incompatible('Source budget binding differs')
    restore(bundle,workspace,source,old['profile'])
    with PipelineStore(workspace/'pipeline.sqlite') as store:
        if store.db.execute("SELECT value FROM fast_control WHERE key='binding'").fetchone()[0]!=canonical(old):
            raise Incompatible('BTT source database/header binding differs')
        previous=old['context']['run_id'];cfg=deepcopy(store.run(previous)['config'])
        if store.db.execute("SELECT 1 FROM fast_control WHERE key='fatal'").fetchone():raise Incompatible('Source fatal must be reviewed')
        cfg.update(version='btt-collection-1',fast20k=profile)
        run=store.create(old['context']['snapshot'],cfg,reuse_run=previous)
        with store.transaction():
            for row in store.db.execute("SELECT identity,stage,dependency_hash,result_json,result_hash FROM stage_results WHERE run_id=? AND stage IN ('acquire','verify_namespace') AND status='completed'",(previous,)).fetchall():
                value=json.loads(row[3])
                if digest(value)!=row[4]:raise Incompatible('BTT source stage checksum differs')
                store.complete(run,row[0],row[1],row[2],value)
            # The 100k frozen list is a root reservoir, not 100k already admitted work items.
            store.db.execute("DELETE FROM fast_work WHERE status='pending' AND tries=0")
            for page,body,checksum in store.db.execute('SELECT page_id,record_json,record_hash FROM fast_work WHERE record_json IS NOT NULL').fetchall():
                a=json.loads(body)
                if digest(a)!=checksum:raise Incompatible('BTT imported record checksum differs')
                a.update(run_id=run,config_hash=digest(cfg),reused_record=dict(run_id=previous,sha256=checksum))
                store.db.execute('UPDATE fast_work SET record_json=?,record_hash=? WHERE page_id=?',(canonical(a),digest(a),page))
            header={**old,'profile':profile,'context':{**old['context'],'run_id':run,'config_hash':digest(cfg)},
                'parent_checkpoint':dict(binding_id=old['binding_id'],db_sha256=parent['db_sha256'],run_id=previous)}
            header.pop('binding_id');header['binding_id']=digest(header)
            store.db.execute("UPDATE fast_control SET value=? WHERE key='binding'",(canonical(header),))
            local=int(store.db.execute("SELECT value FROM fast_control WHERE key='attempts'").fetchone()[0])
            spent=max(local,source_budget['attempts'] if source_budget else 0)
            store.db.execute("UPDATE fast_control SET value=? WHERE key='attempts'",(str(spent),))
        Forest.create(store)
        forest=Forest(store,header)
        with store.transaction():
            for page, in store.db.execute('SELECT page_id FROM fast_work WHERE eligible=1').fetchall():forest.refresh(page)
        durable.mkdir(parents=True)
        receipt={**(source_budget or {}),'binding':digest(header),'attempts':spent,
                 'last_request':float(store.db.execute("SELECT value FROM fast_control WHERE key='last_request'").fetchone()[0]),
                 'parent_budget_sha256':digest(source_budget),'parent_binding':old['binding_id']}
        if source_budget:receipt['last_request']=max(receipt['last_request'],source_budget['last_request'])
        _publish_json(durable/'http-budget.json',receipt)
        _publish_json(workspace/'workspace.json',header)
        checkpoint(store,workspace,durable,header,'CHECKPOINT_STOP')
    return header


class Forest:
    @staticmethod
    def create(store):
        store.db.executescript('''
          CREATE TABLE btt_pool(page_id INTEGER PRIMARY KEY,title_key TEXT,degree INTEGER,entity_group TEXT);
          CREATE INDEX btt_titles ON btt_pool(title_key);
          CREATE INDEX btt_degree ON btt_pool(entity_group,degree DESC,page_id);
          CREATE TABLE btt_members(page_id INTEGER PRIMARY KEY,root_id INTEGER,parent_id INTEGER,depth INTEGER CHECK(depth BETWEEN 1 AND 3),ordinal INTEGER UNIQUE);
          CREATE TABLE btt_roots(page_id INTEGER PRIMARY KEY,status TEXT,entity_group TEXT);
          CREATE TABLE btt_expand(page_id INTEGER PRIMARY KEY,root_id INTEGER,depth INTEGER,status TEXT);
          CREATE TABLE btt_links(parent_id INTEGER,title_key TEXT,position INTEGER,status TEXT,target_id INTEGER,error_json TEXT,PRIMARY KEY(parent_id,title_key));
          CREATE TABLE btt_lookups(title_key TEXT PRIMARY KEY,status TEXT,target_id INTEGER,error_json TEXT);
          CREATE TABLE btt_retry(title_key TEXT PRIMARY KEY,tries INTEGER,retry_at TEXT,error_json TEXT);
          CREATE TABLE btt_candidates(page_id INTEGER PRIMARY KEY,record_json TEXT NOT NULL);
        ''')

    def __init__(self,store,header):
        self.store,self.db,self.header=store,store.db,header
        self.profile=header['profile'];self.run=header['context']['run_id'];self.reason='CANDIDATE_EXHAUSTED'

    def count(self):return self.db.execute('SELECT count(*) FROM btt_members').fetchone()[0]

    def candidate(self,page):
        row=self.db.execute('SELECT record_json FROM btt_candidates WHERE page_id=?',(page,)).fetchone()
        if not row:row=self.db.execute('SELECT record_json FROM candidates WHERE run_id=? AND canonical_page_id=?',(self.run,page)).fetchone()
        if not row:raise Incompatible('BTT candidate provenance missing')
        return json.loads(row[0])

    def refresh(self,page):
        row=self.db.execute('SELECT record_json,record_hash FROM fast_work WHERE page_id=? AND eligible=1',(page,)).fetchone()
        if not row:return
        a=json.loads(row[0])
        if digest(a)!=row[1]:raise Incompatible('BTT pool checksum differs')
        group=a['classification'].get('primary_entity_type')
        q=self.db.execute('SELECT record_json FROM fast_qwen WHERE page_id=?',(page,)).fetchone()
        if not group and q and q[0]:group=json.loads(q[0]).get('suggested_primary_entity_type')
        self.db.execute('INSERT OR REPLACE INTO btt_pool VALUES (?,?,?,?)',
            (page,normalize_title(a['title']),len(titles(a)),group if group in GROUPS else 'Unknown'))

    def admit(self,page,title,parent=None):
        if type(page) is not int or page<=0:raise Incompatible('Invalid BTT page identity')
        if self.db.execute('SELECT 1 FROM fast_work WHERE page_id=?',(page,)).fetchone():return True
        if self.db.execute('SELECT count(*) FROM fast_work').fetchone()[0]>=self.profile['max_candidates']:return False
        root=self.db.execute('SELECT record_json FROM candidates WHERE run_id=? AND canonical_page_id=?',(self.run,page)).fetchone()
        a=json.loads(root[0]) if root else dict(page_id=page,title=title,discovery_metadata_only=True,
            discovery_origins=[dict(kind='retained_first_paragraph_link',parent_page_id=parent)],expected_strata=[])
        with self.store.transaction():
            self.db.execute('INSERT OR IGNORE INTO btt_candidates VALUES (?,?)',(page,canonical(a)))
            ordinal=self.db.execute('SELECT coalesce(max(ordinal),-1)+1 FROM fast_work').fetchone()[0]
            self.db.execute('INSERT INTO fast_work(page_id,ordinal) VALUES (?,?)',(page,ordinal))
        return True

    def work(self,page):
        row=self.db.execute('SELECT status,tries,retry_at FROM fast_work WHERE page_id=?',(page,)).fetchone()
        if row and (row[0]=='pending' or row[0]=='deferred' and row[1]<3 and (not row[2] or row[2]<=now())):return (page,row[1])

    def add(self,page,root,parent,depth):
        if self.count()>=self.profile['target']:return
        self.db.execute('INSERT OR IGNORE INTO btt_members VALUES (?,?,?,?,?)',(page,root,parent,depth,self.count()))
        if depth<3:self.db.execute("INSERT OR IGNORE INTO btt_expand VALUES (?,?,?,'pending')",(page,root,depth))

    def choose_root(self):
        counts=dict(self.db.execute("SELECT entity_group,count(*) FROM btt_roots WHERE status='selected' GROUP BY entity_group"))
        available={r[0] for r in self.db.execute('SELECT DISTINCT p.entity_group FROM btt_pool p LEFT JOIN btt_roots r USING(page_id) LEFT JOIN btt_members m USING(page_id) WHERE r.page_id IS NULL AND m.page_id IS NULL AND p.degree>0')}
        if available:
            quotas=self.store.run(self.run)['config']['quotas']
            group=min(available,key=lambda g:(Fraction(counts.get(g,0),quotas.get(g,max(1,self.profile['target']//5))),g))
            page=self.db.execute('SELECT p.page_id FROM btt_pool p LEFT JOIN btt_roots r USING(page_id) LEFT JOIN btt_members m USING(page_id) WHERE r.page_id IS NULL AND m.page_id IS NULL AND p.degree>0 AND p.entity_group=? ORDER BY p.degree DESC,p.page_id LIMIT 1',(group,)).fetchone()[0]
            self.db.execute("INSERT INTO btt_roots VALUES (?,'active',?)",(page,group));return page
        row=self.db.execute("SELECT c.canonical_page_id,c.record_json FROM candidates c LEFT JOIN btt_roots r ON r.page_id=c.canonical_page_id LEFT JOIN fast_work w ON w.page_id=c.canonical_page_id WHERE c.run_id=? AND r.page_id IS NULL AND (w.page_id IS NULL OR w.status='pending') ORDER BY c.discovery_order LIMIT 1",(self.run,)).fetchone()
        if row:
            page=row[0]
            if not self.admit(page,json.loads(row[1])['title']):return None
            self.db.execute("INSERT INTO btt_roots VALUES (?,'active','Unknown')",(page,));return page

    def plan_links(self,page):
        a=json.loads(self.db.execute('SELECT record_json FROM fast_work WHERE page_id=?',(page,)).fetchone()[0])
        choices=[]
        for title in titles(a):
            if title.isdecimal():continue  # Untyped numeric lookup can alias a page ID.
            known=self.db.execute('SELECT page_id,degree FROM btt_pool WHERE title_key=?',(title,)).fetchall()
            archived=self.db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',(self.header['context'].get('snapshot','2026-10-25T03:00:00Z'),title)).fetchone()
            if known and archived:
                decision=json.loads(archived[0])
                if decision.get('status')!='resolved' or len(known)!=1 or decision.get('page_id')!=known[0][0]:continue
            available=[r for r in known if r[0]!=page and not self.db.execute('SELECT 1 FROM btt_members WHERE page_id=?',(r[0],)).fetchone()]
            if len(known)==1 and available:choices.append((0,-known[0][1],title,known[0][0]))
            elif not known:choices.append((1,0,title,None))
        choices.sort()
        known=[c for c in choices if c[0]==0]
        # Score all cached eligible targets. Only probe missing targets if fewer than two are available.
        considered=known if len(known)>=2 else (known+[c for c in choices if c[0]==1][:max(0,POLICY['probe_limit']-len(known))])
        for i,(_,_,title,target) in enumerate(considered):
            self.db.execute('INSERT INTO btt_links VALUES (?,?,?,?,?,NULL)',(page,title,i,'resolved' if target else 'pending',target))

    def next(self,provider,stop):
        while self.count()<self.profile['target']:
            if stop():self.reason='CHECKPOINT_STOP';return None
            expansion=self.db.execute("SELECT page_id,root_id,depth FROM btt_expand WHERE status='pending' ORDER BY depth,page_id LIMIT 1").fetchone()
            root_trial=self.db.execute("SELECT page_id FROM btt_roots WHERE status='active' LIMIT 1").fetchone()
            if expansion:page,root,depth=expansion
            else:
                page=root_trial[0] if root_trial else self.choose_root()
                if page is None:self.reason='CANDIDATE_EXHAUSTED';return None
                work=self.work(page)
                if work:return work
                pool=self.db.execute('SELECT degree,entity_group FROM btt_pool WHERE page_id=?',(page,)).fetchone()
                if not pool or not pool[0]:
                    self.db.execute("UPDATE btt_roots SET status='unusable' WHERE page_id=?",(page,));continue
                root,depth=page,1
                self.db.execute("UPDATE btt_roots SET entity_group=? WHERE page_id=?",(pool[1],page))
            planned=self.db.execute('SELECT 1 FROM btt_links WHERE parent_id=? LIMIT 1',(page,)).fetchone()
            if not planned:
                with self.store.transaction():self.plan_links(page)
            for title,status,target in self.db.execute('SELECT title_key,status,target_id FROM btt_links WHERE parent_id=? ORDER BY position',(page,)).fetchall():
                if stop():self.reason='CHECKPOINT_STOP';return None
                if status=='pending':
                    lookup=self.db.execute('SELECT status,target_id,error_json FROM btt_lookups WHERE title_key=?',(title,)).fetchone()
                    if not lookup:
                        total=self.db.execute('SELECT count(*) FROM fast_work').fetchone()[0]+self.db.execute('SELECT count(*) FROM btt_lookups').fetchone()[0]
                        if total+2>self.profile['max_candidates']:
                            lookup=('unavailable',None,canonical(dict(reason='candidate_limit')))
                        else:
                            try:
                                resolved=provider.identities.resolve(title)
                                target=resolved.get('page_id') if resolved.get('status')=='resolved' else None
                                lookup=('resolved' if target else 'unavailable',target,canonical(resolved.get('reason')))
                            except SourceUnavailable as error:
                                cause=error
                                while cause.__cause__ is not None:cause=cause.__cause__
                                if failure_kind(error)=='integrity_fatal' and not ambiguous_move(cause):raise
                                if error.retryable:
                                    old=self.db.execute('SELECT tries FROM btt_retry WHERE title_key=?',(title,)).fetchone()
                                    tries=(old[0] if old else 0)+1
                                    deadline=error.retry_at or (datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat().replace('+00:00','Z')
                                    self.db.execute('INSERT OR REPLACE INTO btt_retry VALUES (?,?,?,?)',(title,tries,deadline,canonical(dict(message=str(error),code=error.code))))
                                    if tries<3:
                                        self.db.execute("INSERT INTO api_cooldowns VALUES ('viwiki',?) ON CONFLICT(site) DO UPDATE SET retry_at=max(retry_at,excluded.retry_at)",(deadline,))
                                        self.reason='SOURCE_COOLDOWN';return None
                                lookup=('unavailable',None,canonical(dict(message=str(error),code=error.code)))
                            self.db.execute('INSERT INTO btt_lookups VALUES (?,?,?,?)',(title,*lookup))
                    status,target,error=lookup
                    self.db.execute('UPDATE btt_links SET status=?,target_id=?,error_json=? WHERE parent_id=? AND title_key=?',(status,target,error,page,title))
                if status=='resolved' and target and target!=page:
                    if self.db.execute('SELECT 1 FROM btt_members WHERE page_id=?',(target,)).fetchone():continue
                    if not self.admit(target,title,page):continue
                    work=self.work(target)
                    if work:return work
            options=self.db.execute('SELECT DISTINCT p.page_id,p.degree FROM btt_links l JOIN btt_pool p ON p.page_id=l.target_id LEFT JOIN btt_members m ON m.page_id=p.page_id WHERE l.parent_id=? AND l.status=\'resolved\' AND m.page_id IS NULL AND p.page_id!=? ORDER BY p.degree DESC,p.page_id',(page,page)).fetchall()[:2]
            with self.store.transaction():
                if not expansion:
                    if options:
                        self.add(page,root,None,1)
                        self.db.execute("UPDATE btt_roots SET status='selected' WHERE page_id=?",(page,))
                    else:self.db.execute("UPDATE btt_roots SET status='no_eligible_child' WHERE page_id=?",(page,))
                if expansion or options:
                    for child,_ in options:self.add(child,root,page,depth+1)
                self.db.execute("UPDATE btt_expand SET status='done' WHERE page_id=?",(page,))
        self.reason='TARGET_REACHED';return None


def validate_forest(db,target):
    rows=db.execute('SELECT page_id,root_id,parent_id,depth,ordinal FROM btt_members ORDER BY ordinal').fetchall()
    if len(rows)!=target:raise Incompatible('BTT selected count differs')
    by_id={r[0]:r for r in rows}
    if len(by_id)!=len(rows):raise Incompatible('BTT duplicate selection')
    for page,root,parent,depth,_ in rows:
        if depth==1:
            if root!=page or parent is not None:raise Incompatible('BTT root differs')
        elif parent not in by_id or by_id[parent][1]!=root or by_id[parent][3]+1!=depth:
            raise Incompatible('BTT parent/depth differs')
        if not 1<=depth<=3:raise Incompatible('BTT depth exceeded')
        if db.execute('SELECT count(*) FROM btt_members WHERE parent_id=?',(page,)).fetchone()[0]>2:raise Incompatible('BTT branching exceeded')
        if parent is not None:
            parent_article=json.loads(db.execute('SELECT record_json FROM fast_work WHERE page_id=?',(parent,)).fetchone()[0])
            matched={r[0] for r in db.execute("SELECT title_key FROM btt_links WHERE parent_id=? AND target_id=? AND status='resolved'",(parent,page))}
            if not matched.intersection(titles(parent_article)):raise Incompatible('BTT retained link provenance missing')
    return [dict(page_id=r[0],root_id=r[1],parent_id=r[2],depth=r[3],ordinal=r[4]) for r in rows]
