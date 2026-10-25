"""Read-only archived graph replay. Never creates a run or calls a source API."""
import argparse,hashlib,json,sqlite3,sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'/'src'))
from vimfc.config import load_config
from vimfc.domain import RawRevision,SourceRevisionRef,SourceUnavailable,canonical,digest
from vimfc.pipeline.dependencies import article_identity,make_manifest,classify_manifest
from vimfc.source.dependencies import DependencySource
from vimfc.source.identities import normalize_title
from vimfc.storage.raw_cache import RawCache


def audit(database,raw_root,source_report):
    report=json.loads(Path(source_report).read_text(encoding='utf-8'))
    run,snapshot=report['run_id'],report['snapshot']
    path=Path(database).resolve();wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:raise ValueError('Stop: checkpoint source database before immutable read')
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    cache=RawCache(raw_root)
    archived=db.execute("SELECT config_json FROM pipeline_runs WHERE snapshot=? AND json_extract(config_json,'$.classification.version')='qid-4' ORDER BY created_at DESC LIMIT 1",(snapshot,)).fetchone()
    if not archived:raise ValueError('Archived qid-4 rules are required; current policy must not reinterpret a qid-4 audit')
    rules=json.loads(archived[0])['classification']
    dep=DependencySource(None,snapshot,rules);catalog={}
    for qid,refj,rawj in db.execute("SELECT s.identity,s.record_json,r.record_json FROM snapshot_revision_refs s JOIN source_revisions r ON r.site=s.site AND r.revision_id=json_extract(s.record_json,'$.revision_id') WHERE s.site='wikidata' AND s.snapshot=? AND s.identity GLOB 'Q[0-9]*'",(snapshot,)):
        ref=SourceRevisionRef(**json.loads(refj));obj=cache.get(json.loads(rawj)['cache_ref'])
        assert obj['page_id']==ref.page_id and obj['timestamp']==ref.revision_timestamp
        catalog[qid]=dep._item(qid,RawRevision(ref,obj['content'],obj['content_model'],obj['source_sha1'],obj['local_sha256']))
    legacy=json.loads(Path('reports/verification/pilot2-remediation-audit.json').read_text(encoding='utf-8'))
    retrospective={r['page_id']:r['new_classification']['outcome'] for r in legacy['classification_articles']}
    cohort=report['cohort_page_ids'];rows=[]
    assert len(cohort)==100 and db.execute('SELECT config_hash FROM pipeline_runs WHERE pipeline_run_id=?',(run,)).fetchone()[0]==report['config_hash']
    for page in cohort:
        old=json.loads(db.execute('SELECT record_json FROM classification_results WHERE run_id=? AND page_id=?',(run,page)).fetchone()[0])
        rawj=db.execute("SELECT result_json,result_hash FROM stage_results WHERE run_id=? AND identity=? AND stage='acquire' AND status='completed'",(run,str(page))).fetchone()
        value=json.loads(rawj[0]);assert digest(value)==rawj[1]
        raw=RawRevision(**{**value,'ref':SourceRevisionRef(**value['ref'])})
        matches=[q for q,x in old['items'].items() if normalize_title((x['entity'].get('sitelinks') or {}).get('viwiki',{}).get('title',''))==normalize_title(raw.ref.title)]
        assert len(matches)==1,(page,matches)
        m=make_manifest(article_identity(raw),matches[0],lambda q:catalog[q],rules,snapshot)
        scoped={n['qid']:catalog[n['qid']] for n in m['nodes']}
        replay=make_manifest(article_identity(raw),matches[0],lambda q:scoped[q],rules,snapshot)
        a=classify_manifest(m,scoped,rules);b=classify_manifest(m,catalog,rules)
        assert m==replay and a==b
        rows.append({'page_id':page,'title':raw.ref.title,'qid':matches[0],'old_outcome':old['outcome'],'old_reason':old.get('reason'),
            'manifest_id':m['manifest_id'],'complete':m['complete'],'nodes':len(m['nodes']),'frontier':m['frontier'],
            'outcome':a.outcome,'reason':a.reason,'qid3_retrospective_outcome':retrospective[page],'scoped_vs_warm_identical':True})
    truncated=[r for r in rows if r['old_reason']=='truncated_hierarchy']
    prior37=[r for r in truncated if r['qid3_retrospective_outcome']!='unresolved']
    prior6=[r for r in truncated if r['qid3_retrospective_outcome']=='unresolved']
    assert len(prior37)==37 and len(prior6)==6
    def counts(data):
        return {'cases':len(data),'closure_complete':sum(r['complete'] for r in data),'missing_manifest_artifacts':0,
            'policy_limited':sum(any(f['reason']=='dependency_policy_limit' for f in r['frontier']) for r in data),
            'historical_unavailable_with_evidence':sum(any(f['reason']=='history_unavailable' for f in r['frontier']) for r in data),
            'invalid_cache':0,'node_budget_exhausted':sum(any(f['reason']=='hierarchy_node_budget_exhausted' for f in r['frontier']) for r in data),
            'outcomes':dict(Counter(r['outcome'] for r in data))}
    missing=[{'qid':q,'snapshot_references':db.execute('SELECT count(*) FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',('wikidata',q,snapshot)).fetchone()[0],
        'source_failures':db.execute('SELECT count(*) FROM source_failures WHERE site=? AND identity=? AND snapshot=?',('wikidata',q,snapshot)).fetchone()[0]} for q in ('Q141438977','Q141445732')]
    db.close()
    return {'mode':'offline_archived_graph_replay_not_a_pilot','source_run':run,'snapshot':snapshot,'source_config_hash':report['config_hash'],
        'rules_version':rules['version'],'policy':rules['dependency_policy'],'selected_historical_items':len(catalog),'network_requests':0,'new_actual_runs':0,
        'original_43':counts(truncated),'previously_resolved_37':counts(prior37),'previously_unresolved_6':counts(prior6),
        'all_100':counts(rows),'old_qid3_retrospective':legacy['classification_summary']['all_after'],
        'original_six_missing':missing,'comparison_scope':'Same archived revisions; declared/scoped artifacts versus full warm catalog. Physical cold-cache restoration is covered by MockTransport tests, not by real API acquisition.',
        'articles':rows}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--raw',required=True)
    parser.add_argument('--source-report',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=audit(args.database,args.raw,args.source_report)
    Path(args.output).write_text(canonical(result)+'\n',encoding='utf-8')
    print(canonical({k:result[k] for k in ('mode','source_run','snapshot','original_43','all_100','original_six_missing')}))
