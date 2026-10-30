"""Explicit experimental permission, separate from source/integrity acceptance."""
import json
from collections import Counter
from vimfc.domain import Incompatible, digest


def validate_progression(store,run_id,size,prior,permit):
    run=store.run(run_id);config=run['config'];candidates=store.candidates(run_id)
    if (size!=1000 or config.get('discovery',{}).get('version')!='seed-only-2'
            or config['discovery'].get('variant')!='seed-only' or len(candidates)!=1000
            or permit.get('format')!='vimfc-exploratory-progression-1'
            or permit.get('run_id')!=run_id or permit.get('config_hash')!=run['config_hash']
            or permit.get('snapshot')!=run['snapshot'] or permit.get('cohort_hash')!=digest(candidates)
            or permit.get('from_size')!=100 or permit.get('to_size')!=1000 or not permit.get('authorization')
            or not prior or not prior.get('completed') or prior.get('size')!=100
            or prior.get('run_id')!=run_id or prior.get('snapshot')!=run['snapshot']
            or prior.get('config_hash')!=run['config_hash'] or prior.get('cohort_hash')!=digest(candidates[:100])
            or permit.get('prior_report_hash')!=digest(prior)):
        raise Incompatible('Exploratory progression scope/report identity mismatch')
    replay=permit.get('replay',{})
    if (replay.get('report_hash')!=digest(prior) or replay.get('errors')!=[]
            or replay.get('validated_process_count')!=prior['work_counts']['processed']
            or prior.get('pinned_transient_manifest_count') or prior.get('source_failure_count')):
        raise Incompatible('Exploratory progression requires verified replay and no transient/source failures')
    records=prior.get('work_audit',{}).get('articles',[])
    if len(records)!=100 or {r['page_id'] for r in records}!={c['page_id'] for c in candidates[:100]}:
        raise Incompatible('Incomplete prefix work audit')
    allowed_hints=set(permit.get('allowed_hint_pages',[]));actual_hints=set();quarantined=[]
    observed=Counter()
    for row in records:
        if row.get('status')=='deferred' and row.get('error_code')=='missing_item_hint':
            actual_hints.add(row['page_id']);observed['deferred']+=1
        elif row.get('status')=='quarantined_integrity':
            quarantined.append(row);observed['quarantined_integrity']+=1
        elif row.get('status') in ('completed','unresolved_classification','processed') and not row.get('error_code'):
            observed['processed']+=1
        else:
            raise Incompatible('Unexpected failure cannot use exploratory progression')
    if any(prior['work_counts'].get(k,0)!=observed[k] for k in ('processed','deferred','quarantined_integrity','source_failed')):
        raise Incompatible('Prefix work counts do not reconcile')
    if actual_hints!=allowed_hints:raise Incompatible('Hint approval differs from observed prefix')
    for defect in prior.get('critical_defects',[]):
        if defect.get('code')!='reviewed_integrity_quarantine' or set(defect.get('page_ids',[]))!={r['page_id'] for r in quarantined}:
            raise Incompatible('Unreviewed critical defect')
    if quarantined:
        from .quarantine import active_review
        review=active_review(store,run_id)
        if review is None or review.review_id!=permit.get('reviewed_quarantine_id'):
            raise Incompatible('Missing matching refusal approval')
        for row in quarantined:
            saved=store.db.execute('SELECT record_json FROM candidate_work WHERE run_id=? AND page_id=?',(run_id,row['page_id'])).fetchone()
            if not saved:raise Incompatible('Missing quarantine checkpoint')
            review.restore_candidate(store,row['page_id'],json.loads(saved[0]))
    return {'permission_id':digest(permit),'from_report_hash':digest(prior),
        'experimental_only':True,'data_complete_override':False,'pilot_accepted_override':False}
