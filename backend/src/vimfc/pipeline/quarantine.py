"""An explicit reviewed refusal of source input, never an alternate trust path."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from vimfc.domain import Incompatible,SourceRevisionRef,digest,utc


FORMAT='vimfc-reviewed-integrity-quarantine-1'
STATUS='quarantined_integrity'


class QuarantinedIntegrity(Incompatible):
    def __init__(self,review_id,source_ref):
        super().__init__('Reviewed integrity quarantine: source revision is not evidence')
        self.review_id,self.source_ref=review_id,source_ref


def require_review(store,run_id,review):
    rows=store.db.execute("SELECT record_json FROM run_events WHERE run_id=? AND stage='reviewed_integrity_policy'",(run_id,)).fetchall()
    active={json.loads(r[0])['review_id'] for r in rows}
    quarantined=store.db.execute("SELECT 1 FROM candidate_work WHERE run_id=? AND status=? LIMIT 1",(run_id,STATUS)).fetchone()
    if (active or quarantined) and (review is None or active!={review.review_id}):
        raise Incompatible('Explicit matching reviewed quarantine is required for this run')


class ReviewedIntegrityQuarantine:
    def __init__(self,record):
        self.record=record
        self.review_id=digest(record)
        self.entry=record['revisions'][0]
        self.source_ref={k:self.entry[k] for k in ('site','identity','page_id','title','revision_id','revision_timestamp','snapshot')}

    @classmethod
    def from_file(cls,path,run):
        path=Path(path).resolve()
        try:
            record=json.loads(path.read_text(encoding='utf-8-sig'))
            if (record['format']!=FORMAT or record['run_id']!=run['pipeline_run_id']
                or record['snapshot']!=run['snapshot'] or record['config_hash']!=run['config_hash']
                or len(record['revisions'])!=1 or not record['review']['reviewed_by'] or not record['review']['reason']):
                raise ValueError('Wrong reviewed scope')
            entry=record['revisions'][0]
            if entry['site']!='wikidata' or entry['snapshot']!=record['snapshot']:
                raise ValueError('Wrong reviewed source/snapshot')
            evidence_path=(path.parent/entry['evidence_path']).resolve()
            evidence=evidence_path.read_bytes()
            if hashlib.sha256(evidence).hexdigest()!=entry['evidence_sha256']:
                raise ValueError('Reviewed response artifact changed')
            payload=json.loads(evidence)
            pages=payload['query']['pages']
            if len(pages)!=1 or len(pages[0]['revisions'])!=1:raise ValueError('Expected one captured revision')
            page=pages[0];rev=page['revisions'][0];slot=rev['slots']['main'];encoded=slot['content'].encode('utf-8')
            computed=hashlib.sha1(encoded).hexdigest()
            if (page['pageid']!=entry['page_id'] or page['title']!=entry['identity'] or page['title']!=entry['title']
                or rev['revid']!=entry['revision_id'] or utc(rev['timestamp'])!=entry['revision_timestamp']
                or slot['contentmodel']!='wikibase-item' or slot['contentformat']!='application/json'
                or rev['sha1']!=entry['reported_sha1'] or slot['sha1']!=entry['slot_sha1']
                or computed!=entry['computed_sha1'] or computed==rev['sha1'] or computed==slot['sha1']
                or hashlib.sha256(encoded).hexdigest()!=entry['content_sha256'] or len(encoded)!=entry['utf8_length']):
                raise ValueError('Response does not prove the exact reviewed mismatch')
            if utc(entry['revision_timestamp'])>utc(entry['snapshot']):raise ValueError('Future reviewed revision')
        except (ValueError,TypeError,KeyError,IndexError,OSError,AttributeError) as error:
            raise Incompatible('Invalid reviewed integrity quarantine/evidence: '+str(error)) from error
        return cls(record)

    def bind(self,store):
        run_id=self.record['run_id']
        with store.transaction():
            require_review(store,run_id,self)
            row=store.db.execute("SELECT 1 FROM run_events WHERE run_id=? AND stage='reviewed_integrity_policy'",(run_id,)).fetchone()
            if not row:store.event(run_id,'reviewed_integrity_policy',{'review_id':self.review_id,'approval':self.record})

    def check(self,ref):
        expected=self.source_ref
        if (ref.site,ref.revision_id,ref.snapshot)!=(expected['site'],expected['revision_id'],expected['snapshot']):return
        if (ref.identity not in (expected['identity'],str(expected['page_id']))
            or any(getattr(ref,k)!=expected[k] for k in ('page_id','title','revision_timestamp'))):
            raise Incompatible('Reviewed revision has conflicting identity metadata; integrity stop')
        raise QuarantinedIntegrity(self.review_id,dict(expected))

    def check_cached_selection(self,source,qid,snapshot):
        row=source.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',('wikidata',qid,snapshot)).fetchone()
        if row:self.check(SourceRevisionRef(**json.loads(row[0])))

    def check_manifest(self,manifest):
        for node in manifest['nodes']:
            self.check(SourceRevisionRef('wikidata',node['qid'],node['page_id'],node['qid'],node['revision_id'],node['revision_timestamp'],manifest['snapshot']))

    def _candidate_record(self,page_id):
        case_id=digest({'review_id':self.review_id,'page_id':page_id,'source_ref':self.source_ref})
        details={'review_id':self.review_id,'case_id':case_id,'source_ref':self.source_ref,
            **{k:self.entry[k] for k in ('reported_sha1','slot_sha1','computed_sha1','content_sha256','evidence_sha256')}}
        record={'page_id':page_id,'status':STATUS,'error_code':'reviewed_integrity_quarantine',
            'error':'Reviewed source hash mismatch; candidate supplies no corpus evidence',
            'retryable':False,'retry_at':None,'case_id':case_id,'error_details':details}
        return record

    def restore_candidate(self,store,page_id,record):
        """Validate immutable refusal evidence; never claim a stage or write queue metadata."""
        require_review(store,self.record['run_id'],self)
        expected=self._candidate_record(page_id)
        if record!=expected:raise Incompatible('Quarantine queue does not match reviewed refusal')
        receipt=store.db.execute("SELECT record_json FROM run_events WHERE run_id=? AND stage='integrity_quarantine' AND json_extract(record_json,'$.case_id')=?",(self.record['run_id'],expected['case_id'])).fetchall()
        if len(receipt)!=1 or json.loads(receipt[0][0])!=expected:
            raise Incompatible('Missing or conflicting immutable quarantine receipt')
        return record

    def candidate_record(self,store,page_id,error):
        if error.review_id!=self.review_id or error.source_ref!=self.source_ref:
            raise Incompatible('Unreviewed quarantine exception')
        record=self._candidate_record(page_id);case_id=record['case_id']
        with store.transaction():
            previous=store.db.execute("SELECT record_json FROM run_events WHERE run_id=? AND stage='integrity_quarantine' AND json_extract(record_json,'$.case_id')=?",(self.record['run_id'],case_id)).fetchone()
            if previous and json.loads(previous[0])!=record:raise Incompatible('Conflicting immutable quarantine receipt')
            if not previous:store.event(self.record['run_id'],'integrity_quarantine',record)
        return record

    def audit(self,records):
        cases=[r for r in records if r.get('status')==STATUS]
        return {'policy_version':FORMAT,'review_id':self.review_id,'review':self.record,
            'quarantined_count':len(cases),'articles':cases,'integrity_complete':not cases,
            'quarantined_source_content_trusted':False,'human_pilot_acceptance':False}
