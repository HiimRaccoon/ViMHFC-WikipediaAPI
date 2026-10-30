from dataclasses import asdict,replace
from collections import Counter
import hashlib
import json
from functools import wraps
from vimfc.domain import SourceRevisionRef, RawRevision, SourceUnavailable, canonical, utc, at_or_before,now


def guarded_validation(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        try:return method(self,*args,**kwargs)
        except SourceUnavailable as error:
            if self.integrity_guard:self.integrity_guard(error)
            raise
        except (ValueError,TypeError,KeyError,OSError,AttributeError) as error:
            if self.integrity_guard:
                self.integrity_guard(SourceUnavailable('Invalid historical source input',code='invalid_historical_cache',details={'cause':str(error)}))
            raise
    return call


class RevisionSource:
    def __init__(self, client, store, cache, batch_size=20,*,integrity_guard=None):
        self.client, self.store, self.cache = client,store,cache
        self.integrity_guard=integrity_guard
        self.reviewed_quarantine=None
        self.integrity_registry=None
        self.batch_size = min(20,max(1,batch_size))
        self.cache_hits = 0
        self.cache_lookups = 0
        self.metrics=Counter()
        if hasattr(client,'cooldown_store') and client.cooldown_store is None:
            client.cooldown_store=store

    def _cached_failure(self,site,identity,snapshot,operation):
        row=self.store.db.execute('SELECT error_json,retry_at,retryable FROM source_failures WHERE site=? AND identity=? AND snapshot=? AND operation=?',(site,str(identity),snapshot,operation)).fetchone()
        if row and (not row['retryable'] or row['retry_at'] and not at_or_before(row['retry_at'],now())):
            self.metrics['failure_cache_hits']+=1
            value=json.loads(row['error_json'])
            raise SourceUnavailable(value['message'],retryable=bool(row['retryable']),code=value['code'],retry_at=row['retry_at'],details={**value.get('details',{}),'cached_failure':True})

    def _remember_failure(self,site,identity,snapshot,operation,error):
        value={'message':str(error),'code':error.code,'details':error.details}
        self.store.db.execute('INSERT INTO source_failures VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(site,identity,snapshot,operation) DO UPDATE SET error_json=excluded.error_json,retry_at=excluded.retry_at,retryable=excluded.retryable,recorded_at=excluded.recorded_at',
            (site,str(identity),snapshot,operation,canonical(value),error.retry_at,int(error.retryable),now()))

    @guarded_validation
    def select_revision(self, site, identity, snapshot):
        snapshot, identity = utc(snapshot), str(identity)
        self.cache_lookups += 1
        row = self.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',(site,identity,snapshot)).fetchone()
        if row:
            self.cache_hits += 1
            ref=SourceRevisionRef(**json.loads(row[0]))
            if self.integrity_registry:self.integrity_registry.check(ref,self.reviewed_quarantine)
            if self.reviewed_quarantine:self.reviewed_quarantine.check(ref)
            return ref
        selector = {'pageids':identity} if identity.isdecimal() else {'titles':identity}
        self._cached_failure(site,identity,snapshot,'select')
        try:
            payload = self.client.query(site,{'action':'query','prop':'revisions',**selector,'rvstart':snapshot,'rvdir':'older','rvlimit':1,'rvprop':'ids|timestamp','rvslots':'main','curtimestamp':1})
        except SourceUnavailable as error:
            self._remember_failure(site,identity,snapshot,'select',error)
            raise
        pages = payload.get('query',{}).get('pages',[])
        if len(pages) != 1 or pages[0].get('missing') or not pages[0].get('revisions'):
            error=SourceUnavailable(f'No historical revision for {site}:{identity}',code='history_unavailable',details={'site':site,'identity':identity,'snapshot':snapshot})
            self._remember_failure(site,identity,snapshot,'select',error)
            raise error
        page, revision = pages[0],pages[0]['revisions'][0]
        if not at_or_before(revision['timestamp'],snapshot):
            raise SourceUnavailable('Source returned a future revision')
        ref = SourceRevisionRef(site,identity,page['pageid'],page['title'],revision['revid'],utc(revision['timestamp']),snapshot,payload.get('curtimestamp'))
        if self.integrity_registry:self.integrity_registry.check(ref,self.reviewed_quarantine)
        if self.reviewed_quarantine:self.reviewed_quarantine.check(ref)
        self.store.db.execute('INSERT OR IGNORE INTO snapshot_revision_refs VALUES (?,?,?,?)',(site,identity,snapshot,canonical(ref)))
        canonical_ref=replace(ref,identity=str(ref.page_id))
        self.store.db.execute('INSERT OR IGNORE INTO snapshot_revision_refs VALUES (?,?,?,?)',(site,str(ref.page_id),snapshot,canonical(canonical_ref)))
        self.store.db.execute('DELETE FROM source_failures WHERE site=? AND identity=? AND snapshot=? AND operation=?',(site,identity,snapshot,'select'))
        return ref

    @guarded_validation
    def fetch_revisions(self, site, refs, *, recover_missing=False):
        if self.integrity_registry:
            for ref in refs:
                if ref.site==site:self.integrity_registry.check(ref,self.reviewed_quarantine)
        if self.reviewed_quarantine:
            for ref in refs:
                if ref.site==site:self.reviewed_quarantine.check(ref)
        outputs, missing = {},[]
        for ref in refs:
            if ref.site != site or not at_or_before(ref.revision_timestamp,ref.snapshot):
                raise SourceUnavailable('Revision source/cutoff mismatch',code='invalid_historical_cache' if recover_missing else 'source_unavailable',details={'qid':ref.identity,'revision_id':ref.revision_id})
            self.cache_lookups += 1
            row = self.store.db.execute('SELECT record_json FROM source_revisions WHERE site=? AND revision_id=?',(site,ref.revision_id)).fetchone()
            if row is None:
                # Blob publication can precede its SQLite reference when a
                # process stops. Discover and validate that durable orphan.
                try:orphans=self.cache.find(site,ref.revision_id)
                except (ValueError,TypeError,KeyError,OSError) as error:
                    if recover_missing:raise SourceUnavailable('Invalid orphaned raw artifact',code='invalid_historical_cache',details={'qid':ref.identity,'revision_id':ref.revision_id}) from error
                    raise
                if len(orphans)>1:
                    raise SourceUnavailable('Conflicting raw objects for one revision',code='invalid_historical_cache' if recover_missing else 'source_unavailable',details={'qid':ref.identity,'revision_id':ref.revision_id})
                if orphans:
                    cache_ref,raw=orphans[0]
                    try:
                        encoded=raw['content'].encode('utf-8')
                        if raw['page_id']!=ref.page_id or raw['timestamp']!=ref.revision_timestamp or hashlib.sha256(encoded).hexdigest()!=raw['local_sha256'] or hashlib.sha1(encoded).hexdigest()!=raw['source_sha1']:
                            raise ValueError('Orphaned raw revision identity/hash mismatch')
                    except (ValueError,TypeError,KeyError,AttributeError) as error:
                        raise SourceUnavailable('Orphaned raw revision identity/hash mismatch',code='invalid_historical_cache' if recover_missing else 'source_unavailable',details={'qid':ref.identity,'revision_id':ref.revision_id}) from error
                    self.store.db.execute('INSERT OR IGNORE INTO source_revisions VALUES (?,?,?)',(site,ref.revision_id,canonical({'cache_ref':cache_ref})))
                    row=(canonical({'cache_ref':cache_ref}),)
            if row:
                try:
                    value = json.loads(row[0])
                    raw = self.cache.get(value['cache_ref'])
                    cached_identity=(raw['page_id'],raw['timestamp'])
                    cached_output=RawRevision(ref,raw['content'],raw['content_model'],raw['source_sha1'],raw['local_sha256'],value['cache_ref'])
                except (ValueError,TypeError,KeyError,OSError) as error:
                    # A pinned manifest can restore an absent physical object by
                    # exact revision ID. Corrupt existing objects are not bypassed.
                    if recover_missing and isinstance(error.__cause__,FileNotFoundError):
                        self._cached_failure(site,str(ref.revision_id),ref.snapshot,'content')
                        missing.append(ref)
                        continue
                    if recover_missing:raise SourceUnavailable('Invalid registered raw artifact',code='invalid_historical_cache',details={'qid':ref.identity,'revision_id':ref.revision_id}) from error
                    raise
                if cached_identity != (ref.page_id,ref.revision_timestamp):
                    raise SourceUnavailable('Cached revision identity mismatch',code='invalid_historical_cache' if recover_missing else 'source_unavailable',details={'qid':ref.identity,'revision_id':ref.revision_id})
                outputs[ref.revision_id] = cached_output
                self.cache_hits += 1
            else:
                self._cached_failure(site,str(ref.revision_id),ref.snapshot,'content')
                missing.append(ref)
        for offset in range(0,len(missing),self.batch_size):
            batch=missing[offset:offset+self.batch_size]
            try:
                self._fetch_batch(site,batch,outputs)
            except SourceUnavailable as error:
                for ref in batch:
                    if ref.revision_id not in outputs and (error.retryable or ref.revision_id in error.details.get('unavailable_revision_ids',[])):
                        self._remember_failure(site,str(ref.revision_id),ref.snapshot,'content',error)
                raise
        return [outputs[ref.revision_id] for ref in refs]

    def _fetch_batch(self, site, refs, outputs):
        try:
            payload = self.client.query(site,{'action':'query','prop':'revisions','revids':'|'.join(str(r.revision_id) for r in refs),'rvprop':'ids|timestamp|content|contentmodel|sha1|slotsha1','rvslots':'main'})
        except SourceUnavailable as error:
            error.details.setdefault('requested_revision_ids',[r.revision_id for r in refs])
            if len(refs)>1 and any(k in str(error) for k in ('413','414','toomanyvalues','response')):
                middle = len(refs)//2
                self._fetch_batch(site,refs[:middle],outputs)
                self._fetch_batch(site,refs[middle:],outputs)
                return
            raise
        expected = {r.revision_id:r for r in refs}
        for page in payload.get('query',{}).get('pages',[]):
            for revision in page.get('revisions',[]):
                ref = expected.get(revision['revid'])
                if ref is None or page['pageid'] != ref.page_id or utc(revision['timestamp']) != ref.revision_timestamp:
                    raise SourceUnavailable('Returned revision identity mismatch')
                slot = revision.get('slots',{}).get('main',{})
                content = slot.get('content')
                if content is None:
                    continue
                sha1 = hashlib.sha1(content.encode('utf-8')).hexdigest()
                if revision.get('sha1') != sha1:
                    raise SourceUnavailable('Source SHA-1 mismatch',code='source_hash_mismatch',details={
                        'site':site,'identity':ref.identity,'qid':ref.identity if site=='wikidata' else None,
                        'page_id':ref.page_id,'revision_id':ref.revision_id,'revision_timestamp':ref.revision_timestamp,
                        'snapshot':ref.snapshot,'reported_sha1':revision.get('sha1'),'computed_sha1':sha1,
                        'content_sha256':hashlib.sha256(content.encode('utf-8')).hexdigest(),
                        'utf8_length':len(content.encode('utf-8')),'slot_sha1':slot.get('sha1'),
                        'content_model':slot.get('contentmodel',revision.get('contentmodel')),
                        'content_format':slot.get('contentformat',revision.get('contentformat')),
                        'hash_validation_basis':'revision_sha1',
                        'revision_sha1_matches_content':revision.get('sha1')==sha1,
                        'slot_sha1_matches_content':slot['sha1']==sha1 if 'sha1' in slot else None,
                        'reason':'missing_reported_sha1' if revision.get('sha1') is None else 'hash_mismatch'})
                value = {'page_id':ref.page_id,'timestamp':ref.revision_timestamp,'content':content,'content_model':slot.get('contentmodel',revision.get('contentmodel')),'source_sha1':sha1,'local_sha256':hashlib.sha256(content.encode('utf-8')).hexdigest()}
                cache_ref = self.cache.put(site,ref.revision_id,value)
                self.store.db.execute('INSERT OR IGNORE INTO source_revisions VALUES (?,?,?)',(site,ref.revision_id,canonical({'cache_ref':cache_ref})))
                outputs[ref.revision_id] = RawRevision(ref,content,value['content_model'],sha1,value['local_sha256'],cache_ref)
        missing=[r.revision_id for r in refs if r.revision_id not in outputs]
        if missing:
            raise SourceUnavailable('Some requested historical revisions are unavailable',code='historical_content_unavailable',details={'site':site,'unavailable_revision_ids':missing})
