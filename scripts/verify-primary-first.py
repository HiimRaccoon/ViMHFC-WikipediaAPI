"""Offline archived retrospective and footprint probe; never starts a pipeline run."""
import argparse,hashlib,json,sqlite3,sys,time,tracemalloc
from collections import Counter
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'/'src'))
from vimfc.config import load_config
from vimfc.domain import RawRevision,SourceRevisionRef,SourceUnavailable,canonical,digest
from vimfc.pipeline.dependencies import article_identity
from vimfc.pipeline.primary import make_manifest,classify_manifest,project_item
from vimfc.pipeline.quality import assess_quality
from vimfc.pipeline.pairs import build_pairs
from vimfc.pipeline.report import ResourceSampler
from vimfc.source.dependencies import DependencySource
from vimfc.source.identities import normalize_title
from vimfc.storage.raw_cache import RawCache


def audit(database,raw_root,source_report):
    report=json.loads(Path(source_report).read_text(encoding='utf-8'));run,snapshot=report['run_id'],report['snapshot']
    path=Path(database).resolve();wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:raise ValueError('Stop: WAL must be checkpointed before immutable read')
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    old_cfg=json.loads(db.execute('SELECT config_json FROM pipeline_runs WHERE pipeline_run_id=?',(run,)).fetchone()[0])
    cfg=load_config('configs/pipeline.json');rules=cfg['classification'];cache=RawCache(raw_root)
    assert rules['version']=='qid-5' and digest(old_cfg)==report['config_hash']
    assert cfg['extraction']==old_cfg['extraction'] and cfg['ranking']==old_cfg['ranking']
    assert rules['roots']==old_cfg['classification']['roots'] and rules['precedence']==old_cfg['classification']['precedence']
    dep=DependencySource(None,snapshot,rules);catalog={};old_records=[];new_records=[];rows=[];unknown_links=[]
    old_cls_bytes=new_cls_bytes=old_process_bytes=new_process_bytes=0
    started=time.perf_counter();tracemalloc.start()
    with ResourceSampler('.tools/primary-first/memory-probe',.1) as sampler:
        for qid,refj,rawj in db.execute("SELECT s.identity,s.record_json,r.record_json FROM snapshot_revision_refs s JOIN source_revisions r ON r.site=s.site AND r.revision_id=json_extract(s.record_json,'$.revision_id') WHERE s.site='wikidata' AND s.snapshot=? AND s.identity GLOB 'Q[0-9]*'",(snapshot,)):
            ref=SourceRevisionRef(**json.loads(refj));obj=cache.get(json.loads(rawj)['cache_ref'])
            assert obj['page_id']==ref.page_id and obj['timestamp']==ref.revision_timestamp
            catalog[qid]=dep._item(qid,RawRevision(ref,obj['content'],obj['content_model'],obj['source_sha1'],obj['local_sha256']))
        cohort=[json.loads(r[0]) for r in db.execute('SELECT record_json FROM candidates WHERE run_id=? ORDER BY discovery_order,canonical_page_id',(run,))]
        assert len(cohort)==100 and digest(cohort)==report['cohort_hash']
        for candidate in cohort:
            page=candidate['page_id']
            recordj,h=db.execute("SELECT result_json,result_hash FROM stage_results WHERE run_id=? AND identity=? AND stage='process' AND status='completed' ORDER BY rowid DESC LIMIT 1",(run,str(page))).fetchone()
            old=json.loads(recordj);assert digest(old)==h
            old_cls=old['classification'];old_m=old_cls['dependency_manifest'];root=old_m['root_qid']
            old_cls_bytes+=len(canonical(old_cls).encode());old_process_bytes+=len(recordj.encode())
            m=make_manifest(old_m['article'],root,lambda q:catalog[q],rules,snapshot)
            items={n['qid']:catalog[n['qid']] for n in m['nodes']}
            result=asdict(classify_manifest(m,items,rules))
            assert classify_manifest(m,dict(reversed(list(catalog.items()))),rules)==classify_manifest(m,items,rules)
            cls={**result,'items':items,'manifest_id':m['manifest_id'],'dependency_manifest':m}
            new={k:v for k,v in old.items() if k!='classification'};new['classification']=cls
            # Extraction and factual/article-kind rules are unchanged; only the
            # classification gate can alter eligibility here.
            new['quality']=assess_quality(new['extraction'],cls,new.get('article_kinds',[]),cfg['extraction'])
            new['status']='unresolved_classification' if cls['outcome']=='unresolved' else 'completed'
            new['reason']=cls['reason']
            new['extraction']={**new['extraction'],'links':[dict(l) for l in new['extraction']['links']]}
            if new['quality']['eligible']:
                for link in new['extraction']['links']:
                    if link.get('target',{}).get('status')=='resolved':continue
                    resolution=db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',(snapshot,normalize_title(link['target_title']))).fetchone()
                    if resolution:
                        target=json.loads(resolution[0]);link['target']={k:v for k,v in target.items() if k!='content'}
                    else:unknown_links.append({'page_id':page,'target_title':link['target_title']})
            new_cls_bytes+=len(canonical(cls).encode());new_process_bytes+=len(canonical(new).encode())
            # Do not retain the old repeated full Wikidata entities in memory.
            compact_old={**old,'classification':{k:v for k,v in old_cls.items() if k not in ('items','dependency_manifest')}}
            old_records.append(compact_old);new_records.append(new)
            rows.append({'page_id':page,'title':old['title'],'old_outcome':old_cls['outcome'],'new_outcome':cls['outcome'],
                'primary_status':cls['primary_status'],'primary_entity_type':cls['primary_entity_type'],'primary_decision':cls['primary_decision'],
                'primary_candidates':cls['primary_candidates'],'primary_complete':m['primary']['complete'],'primary_frontier':m['primary']['frontier'],
                'subtypes':cls['subtypes'],'subtype_status':cls['subtype_status'],'subtype_search_status':cls['subtype_search_status'],
                'subtype_frontier':m['subtype']['frontier'],'manifest_id':m['manifest_id'],'dependency_count':len(m['nodes']),
                'old_eligible':old['quality']['eligible'],'new_eligible':new['quality']['eligible'],
                'old_quality_reasons':old['quality']['reasons'],'new_quality_reasons':new['quality']['reasons']})
        pairs=build_pairs(new_records,cfg['ranking'])
    _,heap_peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    def metrics(records):
        cls=[r['classification'] for r in records];outcomes=Counter(c['outcome'] for c in cls)
        return {'primary_classified':outcomes['completed'],'primary_unclassified':outcomes['unclassified'],
            'primary_unresolved_or_ambiguous':outcomes['unresolved'],'supported_subtype_articles':sum(c['outcome']=='completed' and bool(c['subtypes']) for c in cls),
            'classification_quality_rejections':sum('unsupported_classification' in r['quality']['reasons'] for r in records),
            'eligible_articles':sum(r['quality']['eligible'] for r in records)}
    old_metrics=metrics(old_records);new_metrics=metrics(new_records)
    old_pairs=report['pair_audit'].get('generated_counts',{})
    old_metrics.update(bridge_candidates=old_pairs.get('bridge',0),comparison_candidates=old_pairs.get('comparison',0))
    new_metrics.update(bridge_candidates=None if unknown_links else sum(p['kind']=='bridge' for p in pairs.pairs),
        comparison_candidates=sum(p['kind']=='comparison' for p in pairs.pairs))
    unresolved=[r for r in rows if r['old_outcome']=='unresolved'];assert len(unresolved)==43
    classification_only=[r for r in unresolved if r['new_outcome']=='completed' and r['primary_complete']]
    breakdown={'original_unresolved':43,'now_primary_classified':sum(r['primary_status']=='classified' for r in unresolved),
        'primary_complete_prior_blocker_nonprimary':len(classification_only),
        'proven_primary_optional_search_incomplete':sum(r['primary_status']=='classified' and r['subtype_search_status']!='complete' for r in unresolved),
        'still_primary_ambiguous':sum(r['primary_decision']=='primary_ambiguity' for r in unresolved),
        'still_primary_missing_evidence':sum(r['primary_decision']=='primary_dependency_missing' for r in unresolved),
        'missing_archived_primary_dependencies':sum(any(f.get('source_code')=='dependency_input_missing' for f in r['primary_frontier']) for r in unresolved),
        'missing_archived_optional_dependencies':sum(any(f.get('source_code')=='dependency_input_missing' for f in r['subtype_frontier']) for r in unresolved),
        'still_unresolved':sum(r['new_outcome']=='unresolved' for r in unresolved)}
    db.close()
    return {'mode':'offline_retrospective_not_a_pilot','source_run':run,'snapshot':snapshot,'cohort_hash':report['cohort_hash'],
        'classification_version':rules['version'],'policy':rules['dependency_policy'],'network_requests':0,'new_actual_runs':0,
        'old_policy':old_metrics,'primary_first':new_metrics,'original_43_breakdown':breakdown,
        'subtype_status_counts':dict(Counter(r['subtype_status'] for r in rows)),
        'bridge_completeness':{'status':'unavailable' if unknown_links else 'verified','missing_identity_resolution_occurrences':len(unknown_links),
            'known_bridge_candidates_lower_bound':sum(p['kind']=='bridge' for p in pairs.pairs),'missing_examples':unknown_links[:15]},
        'memory_measurement':{'scope':'Isolated read-only catalog + pure manifest/classification/quality/pair replay, not CLI/Builder or source acquisition',
            'elapsed_seconds_with_tracemalloc':time.perf_counter()-started,'python_heap_peak_bytes':heap_peak,'resource_samples':sampler.report(),
            'old_actual_pilot_peak_rss_bytes':report['performance']['resources']['peak_rss_bytes'],
            'old_classification_payload_bytes':old_cls_bytes,'projected_classification_payload_bytes':new_cls_bytes,
            'old_process_payload_bytes':old_process_bytes,'projected_process_payload_bytes':new_process_bytes,
            'catalog_projected_bytes':len(canonical(catalog).encode()),'catalog_items':len(catalog),
            'warning':'Different scopes: do not claim live pilot memory/speed or extrapolate production RAM. Payload figures are serialization measurements, not observed SQLite file growth.'},
        'articles':rows}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',required=True);p.add_argument('--raw',required=True)
    p.add_argument('--source-report',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    result=audit(a.database,a.raw,a.source_report);Path(a.output).write_text(canonical(result)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('mode','old_policy','primary_first','original_43_breakdown','bridge_completeness')},ensure_ascii=True))
