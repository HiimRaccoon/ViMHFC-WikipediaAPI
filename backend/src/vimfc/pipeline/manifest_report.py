"""Offline diagnostics for logical dependency inputs and stage identities."""
from collections import Counter
from vimfc.domain import digest


def dependency_audits(records,store,run):
    rules=run['config']['classification'];counts=Counter();reasons=Counter();entries=[];fingerprints=[]
    candidates={c['page_id']:c for c in store.candidates(run['pipeline_run_id'])}
    for record in records:
        cls=record.get('classification',{});manifest=cls.get('dependency_manifest') or record.get('dependency_manifest')
        if not manifest:
            code=record.get('error_code') or cls.get('error_code') or 'not_materialized'
            counts['input_failure' if rules.get('dependency_policy') else 'legacy_without_manifest']+=1
            reasons[code]+=1
            entries.append({'page_id':record['page_id'],'manifest_id':None,'input_error':code})
            continue
        frontier=manifest['frontier'];counts['complete' if manifest['complete'] else 'incomplete']+=1
        error=record.get('error_code')
        if error:counts['input_failure']+=1;reasons[error]+=1
        for reason in sorted({f['reason'] for f in frontier}):counts[reason]+=1
        reasons.update(f['reason'] for f in frontier)
        entries.append({'page_id':record['page_id'],'manifest_id':manifest['manifest_id'],'complete':manifest['complete'],
            'nodes':len(manifest['nodes']),'frontier':frontier,'outcome':cls.get('outcome','not_classified'),'reason':cls.get('reason'),
            'input_error':error,'primary_complete':manifest.get('primary',{}).get('complete'),
            'primary_status':cls.get('primary_status'),'subtype_complete':manifest.get('subtype',{}).get('complete')})
        class_deps={'snapshot':run['snapshot'],'article':manifest['article'],'rules':rules,'manifest_id':manifest['manifest_id']}
        candidate=candidates.get(record['page_id'])
        process_deps={'snapshot':run['snapshot'],'config':run['config_hash'],'candidate':candidate,
            'input':{'manifest_id':manifest['manifest_id'],'article_hash':digest(manifest['article'])}}
        ch,ph=digest(class_deps),digest(process_deps)
        fingerprints.append({'page_id':record['page_id'],'manifest_id':manifest['manifest_id'],'classification_dependency_hash':ch,
            'process_dependency_hash':ph,'input_validated':not bool(error),'classification_checkpoint':bool(store.result(run['pipeline_run_id'],record['page_id'],'classify',ch)),
            'process_checkpoint':bool(store.result(run['pipeline_run_id'],record['page_id'],'process',ph))})
    audit={'policy':rules.get('dependency_policy'),'records':len(records),'counts':dict(counts),'reason_counts':dict(reasons),'articles':entries}
    fingerprint={'manifest_bound_classifications':sum(f['classification_checkpoint'] and f['input_validated'] for f in fingerprints),
        'manifest_bound_processes':sum(f['process_checkpoint'] and f['input_validated'] for f in fingerprints),'articles':fingerprints}
    return audit,fingerprint


def subtype_audit(records):
    entries=[]
    for record in records:
        c=record.get('classification',{})
        if not c:continue
        entries.append({'page_id':record['page_id'],'primary_status':c.get('primary_status',{'completed':'classified'}.get(c.get('outcome'),c.get('outcome'))),
            'primary_entity_type':c.get('primary_entity_type'),'primary_decision':c.get('primary_decision'),
            'subtypes':c.get('subtypes',[]),'subtype_status':c.get('subtype_status','legacy_unspecified'),
            'subtype_search_status':c.get('subtype_search_status','legacy_unspecified'),'subtype_provenance':c.get('subtype_provenance',[]),
            'manifest_id':c.get('manifest_id'),'eligible':record.get('quality',{}).get('eligible',False)})
    classified=[e for e in entries if e['primary_status']=='classified']
    return {'primary_classified':len(classified),'primary_only_classified':sum(not e['subtypes'] for e in classified),
        'supported_subtype_articles':sum(bool(e['subtypes']) for e in classified),'subtype_coverage_denominator':len(classified),
        'subtype_status_counts':dict(Counter(e['subtype_status'] for e in entries)),
        'subtype_search_status_counts':dict(Counter(e['subtype_search_status'] for e in entries)),
        'primary_status_counts':dict(Counter(e['primary_status'] for e in entries)),'articles':entries}
