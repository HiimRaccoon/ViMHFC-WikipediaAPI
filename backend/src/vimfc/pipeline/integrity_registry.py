"""Anchored operational diagnostics, never a trusted-source or approval shortcut."""
from collections import Counter
from dataclasses import asdict
import hashlib,json,os,sqlite3
from pathlib import Path
from vimfc.domain import Incompatible,SourceRevisionRef,SourceUnavailable,canonical,digest
from vimfc.source.integrity_diagnostics import inspect_revision_response,_unique_object
from .quarantine import ReviewedIntegrityQuarantine

FORMAT='vimfc-integrity-registry-1'
ANCHOR='integrity_registry_anchor'
UNKNOWN='UNKNOWN_INTEGRITY'
REFUSAL='REVIEWED_REFUSAL'
RECOVERY='VERIFIED_RECOVERY'
OTHER={'SOURCE_UNAVAILABLE','SEMANTIC_UNRESOLVED','MISSING_HISTORICAL_DEPENDENCY'}


class IntegrityRegistry:
    def __init__(self,path,store,run_id,*,create=False):
        self.path=Path(path).resolve();self.store=store;self.run_id=run_id;self.run=store.run(run_id)
        self.header={'format':FORMAT,'run_id':run_id,'config_hash':self.run['config_hash'],'snapshot':self.run['snapshot']}
        self.registry_id=digest(self.header);self.artifacts=self.path.with_name(self.path.stem+'-artifacts')
        self.last_response=None;self.root_article=None
        anchors=self._anchors()
        if not self.path.exists() and (not create or anchors):raise Incompatible('Required integrity registry is missing')
        new=not self.path.exists();self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,isolation_level=None);self.db.row_factory=sqlite3.Row
        if new:
            self.db.executescript('''CREATE TABLE metadata(record_json TEXT NOT NULL);
            CREATE TABLE events(sequence INTEGER PRIMARY KEY,event_hash TEXT UNIQUE NOT NULL,record_json TEXT NOT NULL);
            CREATE TRIGGER immutable_event_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'immutable registry event');END;
            CREATE TRIGGER immutable_event_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'immutable registry event');END;''')
            self.db.execute('INSERT INTO metadata VALUES (?)',(canonical(self.header),))
        try:self.verify()
        except BaseException:self.close();raise

    def __enter__(self):return self
    def __exit__(self,*a):self.close()
    def close(self):
        if self.db is not None:self.db.close();self.db=None
    def _anchors(self):
        return [json.loads(r[0]) for r in self.store.db.execute('SELECT record_json FROM run_events WHERE run_id=? AND stage=? ORDER BY event_id',(self.run_id,ANCHOR))]
    def _put(self,kind,body):
        sha=hashlib.sha256(body).hexdigest();path=self.artifacts/kind/sha;path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            if path.read_bytes()!=body:raise Incompatible('Conflicting diagnostic artifact')
        else:
            with path.open('xb') as f:f.write(body);f.flush();os.fsync(f.fileno())
        return {'path':str(path),'sha256':sha}
    @staticmethod
    def _read_artifact(ref):
        data=Path(ref['path']).read_bytes()
        if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise Incompatible('Integrity registry evidence checksum mismatch')
        return data
    def _external(self,path):
        path=Path(path).resolve();return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    def _inspect(self,response,ref):
        payload=json.loads(response.decode('utf-8'),object_pairs_hook=_unique_object)
        pages=[p for p in payload['query']['pages'] if p['pageid']==ref.page_id and p['title']==ref.title]
        if len(pages)!=1:raise ValueError('Exact response page not unique')
        page=pages[0];revisions=[r for r in page['revisions'] if r['revid']==ref.revision_id]
        if len(revisions)!=1:raise ValueError('Exact response revision not unique')
        projected=canonical({'query':{'pages':[{**page,'revisions':revisions}]}}).encode('utf-8')
        return inspect_revision_response(projected,ref,expected_response_sha256=hashlib.sha256(projected).hexdigest())
    def _member(self,artifact,review_id):
        self._read_artifact(artifact)
        gate=ReviewedIntegrityQuarantine.from_file(artifact['path'],self.run)
        if review_id not in gate.review_ids:raise Incompatible('Refusal component absent from reviewed approval')
        return gate.members[review_id] if hasattr(gate,'members') else gate

    def verify(self):
        try:
            rows=self.db.execute('SELECT record_json FROM metadata').fetchall()
            if len(rows)!=1 or json.loads(rows[0][0])!=self.header:raise Incompatible('Integrity registry scope/header changed')
            anchors=self._anchors();events=list(self.db.execute('SELECT * FROM events ORDER BY sequence'))
            if len(events)!=len(anchors):raise Incompatible('Registry/anchor length mismatch; recovery review required')
            previous=self.registry_id;observations={}
            for index,row in enumerate(events,1):
                record=json.loads(row['record_json']);expected={'registry_id':self.registry_id,'sequence':index,'head_hash':row['event_hash']}
                if (row['sequence']!=index or record['sequence']!=index or record['format']!=FORMAT
                    or record['previous_hash']!=previous or digest(record)!=row['event_hash'] or anchors[index-1]!=expected):
                    raise Incompatible('Integrity registry hash chain/anchor changed')
                previous=row['event_hash'];event=record['event'];p=event['payload']
                if event['kind']=='OBSERVATION':
                    identity=digest(p)
                    if identity in observations or p['state'] not in OTHER|{UNKNOWN}:raise Incompatible('Invalid duplicate/state observation')
                    if p['kind']=='mismatch':
                        ref=SourceRevisionRef(**p['source_ref']);inspection=self._inspect(self._read_artifact(p['response']),ref)
                        if inspection!=p['inspection'] or inspection['status']!='source_representation_mismatch':raise Incompatible('Registry mismatch evidence differs')
                    elif p['kind']!='status':raise Incompatible('Unknown observation kind')
                    if p['kind']=='status' and p['details'].get('response_artifact'):
                        self._read_artifact(p['details']['response_artifact'])
                    self._read_artifact(p['failure_receipt'])
                    observations[identity]={**p,'state':p['state'],'review_id':None}
                elif event['kind']=='DISPOSITION':
                    obs=observations[p['observation_id']]
                    if obs['state']!=UNKNOWN or obs['kind']!='mismatch':raise Incompatible('Invalid/conflicting disposition transition')
                    if not self._read_artifact(p['authorization']).strip():raise Incompatible('Review authorization is empty')
                    if p['state']==REFUSAL:
                        member=self._member(p['approval_file'],p['review_id']);entry=member.entry;i=obs['inspection'];ref=obs['source_ref']
                        if (any(member.source_ref[k]!=ref[k] for k in member.source_ref)
                            or entry['reported_sha1']!=i['reported_revision_sha1'] or entry['slot_sha1']!=i['reported_slot_sha1']
                            or entry['computed_sha1']!=i['content_sha1'] or entry['content_sha256']!=i['content_sha256']):
                            raise Incompatible('Refusal does not bind exact observation')
                    elif p['state']==RECOVERY:
                        recovered=self._inspect(self._read_artifact(p['recovery_response']),SourceRevisionRef(**obs['source_ref']))
                        if recovered['status']!='metadata_hashes_match' or recovered['content_sha1']!=obs['inspection']['reported_revision_sha1']:
                            raise Incompatible('Verified recovery lacks original source-hash proof')
                    else:raise Incompatible('Unknown disposition state')
                    obs.update(state=p['state'],review_id=p.get('review_id'))
                else:raise Incompatible('Unknown registry event kind')
            return observations
        except Incompatible:raise
        except (OSError,sqlite3.Error,ValueError,TypeError,KeyError,AttributeError) as e:raise Incompatible('Invalid integrity registry/evidence: '+str(e)) from e

    def _append(self,kind,payload):
        self.verify();event={'kind':kind,'payload':payload}
        for row in self.db.execute('SELECT record_json FROM events'):
            if json.loads(row[0])['event']==event:return
        row=self.db.execute('SELECT sequence,event_hash FROM events ORDER BY sequence DESC LIMIT 1').fetchone()
        number=row[0]+1 if row else 1;previous=row[1] if row else self.registry_id
        record={'format':FORMAT,'sequence':number,'previous_hash':previous,'event':event};head=digest(record)
        # Sidecar and pipeline DB cannot commit atomically. An unanchored tail
        # after a crash deliberately fails closed; never silently truncate it.
        self.db.execute('INSERT INTO events VALUES (?,?,?)',(number,head,canonical(record)))
        with self.store.transaction():self.store.event(self.run_id,ANCHOR,{'registry_id':self.registry_id,'sequence':number,'head_hash':head})
        self.verify()

    def observe_mismatch(self,response,ref,provenance):
        try:
            inspection=self._inspect(response,ref)
            if ref.snapshot!=self.header['snapshot'] or inspection['status']!='source_representation_mismatch':raise ValueError('Not a scoped mismatch')
            p={'kind':'mismatch','state':UNKNOWN,'source_ref':asdict(ref),'inspection':inspection,
               'response':self._put('responses',response),'provenance':provenance,
               'failure_receipt':self._put('failures',canonical(provenance).encode())}
            self._append('OBSERVATION',p);return digest(p)
        except Incompatible:raise
        except (ValueError,TypeError,KeyError,OSError) as e:raise Incompatible('Invalid exact mismatch observation: '+str(e)) from e
    def observe_status(self,state,details):
        if state not in OTHER|{UNKNOWN}:raise Incompatible('Status observation cannot authorize a disposition')
        p={'kind':'status','state':state,'source_ref':details.get('source_ref'),'details':details,
           'failure_receipt':self._put('failures',canonical(details).encode())}
        self._append('OBSERVATION',p);return digest(p)
    def _transition(self,obs,state,authorization,**proof):
        try:
            current=self.verify()[obs]
            payload={'observation_id':obs,'state':state,'authorization':self._external(authorization),**proof}
            if current['state']!=UNKNOWN:
                if any(json.loads(r[0])['event']=={'kind':'DISPOSITION','payload':payload} for r in self.db.execute('SELECT record_json FROM events')):return
                raise Incompatible('Conflicting registry disposition')
            # Validate against a temporary event without ever committing an
            # invalid disposition: exact proof checks are repeated by verify.
            if not self._read_artifact(payload['authorization']).strip():raise Incompatible('Missing explicit authorization')
            if state==REFUSAL:
                m=self._member(payload['approval_file'],payload['review_id']);r=current['source_ref'];i=current['inspection']
                if (any(m.source_ref[k]!=r[k] for k in m.source_ref) or m.entry['reported_sha1']!=i['reported_revision_sha1']
                    or m.entry['slot_sha1']!=i['reported_slot_sha1'] or m.entry['computed_sha1']!=i['content_sha1']
                    or m.entry['content_sha256']!=i['content_sha256']):raise Incompatible('Wrong exact refusal scope')
            elif state==RECOVERY:
                i=self._inspect(self._read_artifact(payload['recovery_response']),SourceRevisionRef(**current['source_ref']))
                if i['status']!='metadata_hashes_match' or i['content_sha1']!=current['inspection']['reported_revision_sha1']:raise Incompatible('No original source-hash recovery proof')
            else:raise Incompatible('Unknown disposition')
            self._append('DISPOSITION',payload)
        except Incompatible:raise
        except (OSError,ValueError,TypeError,KeyError) as e:raise Incompatible('Invalid review authorization/proof: '+str(e)) from e
    def review_refusal(self,obs,approval_file,authorization):
        try:
            gate=ReviewedIntegrityQuarantine.from_file(approval_file,self.run);r=self.verify()[obs]['source_ref']
            members=gate.members.values() if hasattr(gate,'members') else [gate]
            matching=[m for m in members if m.source_ref['revision_id']==r['revision_id'] and m.source_ref['site']==r['site']]
            if len(matching)!=1:raise Incompatible('No exact reviewed refusal component')
            self._transition(obs,REFUSAL,authorization,approval_file=self._external(approval_file),review_id=matching[0].review_id)
        except (OSError,ValueError,TypeError,KeyError) as e:raise Incompatible('Invalid refusal input: '+str(e)) from e
    def review_recovery(self,obs,response_file,authorization):
        self._transition(obs,RECOVERY,authorization,recovery_response=self._external(response_file))

    def check(self,ref,review=None):
        for identity,obs in self.verify().items():
            expected=obs.get('source_ref')
            if not expected or (ref.site,ref.revision_id,ref.snapshot)!=(expected['site'],expected['revision_id'],expected['snapshot']):continue
            if (ref.identity not in (expected['identity'],str(expected['page_id'])) or
                any(getattr(ref,k)!=expected[k] for k in ('page_id','title','revision_timestamp'))):raise Incompatible('Known blocker identity metadata conflicts')
            if obs['state']==UNKNOWN:raise SourceUnavailable('Known unresolved integrity blocker',code='source_hash_mismatch',details={'registry_preflight':True,'observation_id':identity,'source_ref':expected})
            if obs['state']==REFUSAL and (review is None or obs['review_id'] not in review.review_ids):raise Incompatible('Explicit exact refusal approval required by registry')
    def preflight(self,review):
        for identity,obs in self.verify().items():
            if obs['state']==UNKNOWN:raise SourceUnavailable('Known unresolved integrity blocker',code='source_hash_mismatch',details={'registry_preflight':True,'observation_id':identity})
            if obs.get('source_ref'):self.check(SourceRevisionRef(**obs['source_ref']),review)
    def audit(self):
        observations=self.verify();last=self.db.execute('SELECT sequence,event_hash FROM events ORDER BY sequence DESC LIMIT 1').fetchone()
        return {'format':FORMAT,'registry_id':self.registry_id,'event_count':last[0] if last else 0,'head_hash':last[1] if last else self.registry_id,
            'states':dict(sorted(Counter(o['state'] for o in observations.values()).items())),'observations':observations,'trusted_evidence_published':False,'progression_authority':False}
    def set_root(self,candidate):self.root_article={'page_id':candidate['page_id'],'title':candidate.get('title')}
    def capture_response(self,response):
        body=response.read();self.last_response={'body':body,'http':{'url':str(response.request.url),'status':response.status_code,
            'headers':{k:v for k,v in response.headers.items() if k in ('date','content-type','content-encoding','retry-after','x-request-id','server')},
            'body_sha256':hashlib.sha256(body).hexdigest(),'body_scope':'decoded HTTP response; untrusted diagnostic'}}
    def capture_error(self,error,*,invocation,charged_attempts,cap):
        if error.details.get('registry_preflight'):return
        d=error.details;provenance={'original_failure':{'code':error.code,'message':str(error),'details':d},'root_article':self.root_article,
            'dependency_path':None,'run_id':self.run_id,'config_hash':self.header['config_hash'],'invocation_id':invocation,
            'request_cost':{'physical_attempts':charged_attempts,'invocation_cap':cap},'http':self.last_response['http'] if self.last_response else None}
        if error.code=='source_hash_mismatch' and d.get('site')=='wikidata' and self.last_response:
            try:
                payload=json.loads(self.last_response['body'],object_pairs_hook=_unique_object)
                page,=[p for p in payload['query']['pages'] if p['pageid']==d['page_id']]
                title=page['title']
                if d['identity'] not in (title,str(d['page_id'])):raise ValueError('Captured identity conflict')
                ref=SourceRevisionRef(d['site'],title,d['page_id'],title,d['revision_id'],d['revision_timestamp'],d['snapshot'])
                inspected=self._inspect(self.last_response['body'],ref)
                if (inspected['status']!='source_representation_mismatch' or inspected['content_sha1']!=d.get('computed_sha1')
                    or inspected['reported_revision_sha1']!=d.get('reported_sha1') or inspected['reported_slot_sha1']!=d.get('slot_sha1')):
                    raise ValueError('Last response does not prove this original hash failure')
            except (ValueError,KeyError,TypeError,AttributeError,UnicodeError) as inspection_error:
                # Incomplete metadata cannot prove an exact reviewed mismatch,
                # but must still leave a durable unknown blocker and the body.
                provenance.update(capture_validation_error=str(inspection_error),
                    response_artifact=self._put('responses',self.last_response['body']),
                    response_relation='last HTTP response; strict exact mismatch proof unavailable')
                self.observe_status(UNKNOWN,provenance)
            else:self.observe_mismatch(self.last_response['body'],ref,provenance)
        else:
            if self.last_response:
                provenance.update(response_artifact=self._put('responses',self.last_response['body']),response_relation='last HTTP response; not proven to be the failing input')
            self.observe_status(UNKNOWN,provenance)
