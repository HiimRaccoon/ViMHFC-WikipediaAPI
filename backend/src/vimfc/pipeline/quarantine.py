"""An explicit reviewed refusal of source input, never an alternate trust path."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from vimfc.domain import Incompatible,SourceRevisionRef,digest,utc


FORMAT='vimfc-reviewed-integrity-quarantine-1'
SET_FORMAT='vimfc-reviewed-integrity-quarantine-set-1'
STATUS='quarantined_integrity'


class QuarantinedIntegrity(Incompatible):
    def __init__(self,review_id,source_ref):
        super().__init__('Reviewed integrity quarantine: source revision is not evidence')
        self.review_id,self.source_ref=review_id,source_ref


def active_approvals(store,run_id):
    rows=store.db.execute("SELECT record_json FROM run_events WHERE run_id=? AND stage='reviewed_integrity_policy'",(run_id,)).fetchall()
    records={}
    for row in rows:
        event=json.loads(row[0]);identity=event['review_id'];approval=event['approval']
        if identity!=digest(approval) or identity in records:raise Incompatible('Invalid/duplicate immutable review activation')
        records[identity]=approval
    return records


def require_review(store,run_id,review):
    active=set(active_approvals(store,run_id))
    quarantined=store.db.execute("SELECT 1 FROM candidate_work WHERE run_id=? AND status=? LIMIT 1",(run_id,STATUS)).fetchone()
    if (active or quarantined) and (review is None or active!=review.review_ids):
        raise Incompatible('Explicit matching reviewed quarantine is required for this run')


class ReviewedIntegrityQuarantine:
    def __init__(self,record):
        self.record=record
        self.review_id=digest(record)
        self.review_ids={self.review_id}
        self.entry=record['revisions'][0]
        self.source_ref={k:self.entry[k] for k in ('site','identity','page_id','title','revision_id','revision_timestamp','snapshot')}

    @classmethod
    def from_file(cls,path,run):
        path=Path(path).resolve()
        try:
            record=json.loads(path.read_text(encoding='utf-8-sig'))
            if record.get('format')==SET_FORMAT:
                if (not isinstance(record.get('approvals'),list) or not 2<=len(record['approvals'])<=8
                    or record!=refusal_set_record(record['approvals'])):
                    raise ValueError('Invalid/noncanonical refusal set')
                members=[cls._from_record(r,path,run) for r in record['approvals']]
                return ReviewedIntegrityQuarantineSet(record,members)
            return cls._from_record(record,path,run)
        except (ValueError,TypeError,KeyError,IndexError,OSError,AttributeError) as error:
            raise Incompatible('Invalid reviewed integrity quarantine/evidence: '+str(error)) from error

    @classmethod
    def _from_record(cls,record,path,run):
        try:
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
            # Legacy records may predate RCA pointers. If a pointer is supplied,
            # it must bind the actual artifact as well as the captured response.
            review=record['review']
            if review.get('rca_path') or review.get('rca_sha256'):
                rca=(path.parent/review['rca_path']).resolve().read_bytes()
                if hashlib.sha256(rca).hexdigest()!=review['rca_sha256']:raise ValueError('Reviewed RCA artifact changed')
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
        return self._restore_record(store,page_id,record)

    def _restore_record(self,store,page_id,record):
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


def refusal_set_record(approvals):
    """Canonical envelope; component records and their historical IDs never change."""
    if len(approvals)<2:raise ValueError('A refusal set requires at least two reviewed components')
    first=approvals[0]
    return {'format':SET_FORMAT,'run_id':first['run_id'],'config_hash':first['config_hash'],
        'snapshot':first['snapshot'],'approvals':sorted(approvals,key=digest)}


def active_review(store,run_id):
    approvals=list(active_approvals(store,run_id).values())
    if not approvals:return None
    members=[ReviewedIntegrityQuarantine(record) for record in approvals]
    return members[0] if len(members)==1 else ReviewedIntegrityQuarantineSet(refusal_set_record(approvals),members)


class ReviewedIntegrityQuarantineSet:
    """Additive, explicit refusal policy. No single component can downgrade it."""
    def __init__(self,record,members):
        self.record=record;self.review_id=digest(record)
        self.members={m.review_id:m for m in members};self.review_ids=set(self.members)
        refs={(m.source_ref['site'],m.source_ref['revision_id'],m.source_ref['snapshot']) for m in members}
        if len(self.members)!=len(members) or len(refs)!=len(members):raise Incompatible('Duplicate refusal component/revision')
        scope=tuple(record[k] for k in ('run_id','config_hash','snapshot'))
        if any(tuple(m.record[k] for k in ('run_id','config_hash','snapshot'))!=scope for m in members):raise Incompatible('Mixed refusal scopes')

    def bind(self,store):
        run_id=self.record['run_id']
        with store.transaction():
            active=active_approvals(store,run_id)
            if not set(active)<=self.review_ids:raise Incompatible('Refusal addition cannot remove an active review')
            if any(active[k]!=self.members[k].record for k in active):raise Incompatible('Refusal addition changed an old approval')
            # Append individual approval events; preserve existing policy/case IDs.
            for identity in sorted(self.review_ids-set(active)):
                store.event(run_id,'reviewed_integrity_policy',{'review_id':identity,'approval':self.members[identity].record})
            require_review(store,run_id,self)

    def check(self,ref):
        for member in self.members.values():member.check(ref)

    def check_cached_selection(self,source,qid,snapshot):
        for member in self.members.values():member.check_cached_selection(source,qid,snapshot)

    def check_manifest(self,manifest):
        for member in self.members.values():member.check_manifest(manifest)

    def candidate_record(self,store,page_id,error):
        require_review(store,self.record['run_id'],self)
        member=self.members.get(error.review_id)
        if member is None:raise Incompatible('Unreviewed quarantine exception')
        return member.candidate_record(store,page_id,error)

    def restore_candidate(self,store,page_id,record):
        require_review(store,self.record['run_id'],self)
        identity=record.get('error_details',{}).get('review_id');member=self.members.get(identity)
        if member is None:raise Incompatible('Quarantine record outside active refusal set')
        return member._restore_record(store,page_id,record)

    def audit(self,records):
        cases=[r for r in records if r.get('status')==STATUS]
        if any(r.get('error_details',{}).get('review_id') not in self.review_ids for r in cases):raise Incompatible('Unreviewed case in audit')
        return {'policy_version':SET_FORMAT,'review_id':self.review_id,'component_review_ids':sorted(self.review_ids),
            'review':self.record,'quarantined_count':len(cases),'articles':cases,'integrity_complete':not cases,
            'quarantined_source_content_trusted':False,'human_pilot_acceptance':False}
