"""Retrospective analysis only: read archived DB/raw, write a separate audit.

No run creation, no stage writes, no source clients. Run from the repository root.
"""
import argparse
from collections import Counter,defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'/'src'))
from vimfc.config import load_config
from vimfc.domain import canonical,digest,at_or_before
from vimfc.pipeline.extract import extract_lead
from vimfc.pipeline.classify import classify
from vimfc.pipeline.inline_templates import nonprose_kind
from vimfc.source.identities import normalize_title
from vimfc.storage.raw_cache import RawCache


def denied(*args,**kwargs):
    raise RuntimeError('Network is forbidden in retrospective analysis')


def audit(database,source_report,config_path):
    socket.socket.connect=denied
    report=json.loads(Path(source_report).read_text(encoding='utf-8'))
    run=report['run_id']
    database=Path(database).resolve()
    db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    snapshot,archived_json,config_hash=db.execute('SELECT snapshot,config_json,config_hash FROM pipeline_runs WHERE pipeline_run_id=?',(run,)).fetchone()
    assert snapshot==report['snapshot'] and config_hash==report['config_hash'] and digest(json.loads(archived_json))==config_hash
    cfg=load_config(config_path)
    cache=RawCache(database.parent/'raw')
    items={}
    title_qids=defaultdict(set)
    refs=db.execute("SELECT s.identity,s.record_json,r.record_json FROM snapshot_revision_refs s JOIN source_revisions r ON r.site=s.site AND r.revision_id=json_extract(s.record_json,'$.revision_id') WHERE s.site='wikidata' AND s.snapshot=? AND s.identity GLOB 'Q[0-9]*'",(snapshot,)).fetchall()
    for qid,ref_json,raw_json in refs:
        ref=json.loads(ref_json)
        raw=cache.get(json.loads(raw_json)['cache_ref'])
        assert raw['timestamp']==ref['revision_timestamp'] and at_or_before(raw['timestamp'],snapshot)
        assert hashlib.sha256(raw['content'].encode()).hexdigest()==raw['local_sha256']
        entity=json.loads(raw['content'])
        assert entity['id']==qid
        items[qid]={'entity':entity,'revision_id':ref['revision_id'],'revision_timestamp':ref['revision_timestamp'],'raw_hash':raw['local_sha256']}
        title=(entity.get('sitelinks') or {}).get('viwiki',{}).get('title')
        if title: title_qids[normalize_title(title)].add(qid)
    extraction_rows=[]
    classification_rows=[]
    templates=defaultdict(lambda:{'articles':set(),'diagnostics':0,'forms':[]})
    outcomes=Counter()
    all_extractions=[]
    for candidate_json, in db.execute('SELECT record_json FROM candidates WHERE run_id=? ORDER BY discovery_order LIMIT 100',(run,)):
        candidate=json.loads(candidate_json)
        page_id=candidate['page_id']
        acquired=json.loads(db.execute("SELECT result_json FROM stage_results WHERE run_id=? AND identity=? AND stage='acquire' AND status='completed'",(run,str(page_id))).fetchone()[0])
        raw=cache.get(acquired['cache_ref'])
        assert raw['local_sha256']==acquired['local_sha256']==hashlib.sha256(raw['content'].encode()).hexdigest()
        old_ext=json.loads(db.execute('SELECT record_json FROM lead_results WHERE run_id=? AND page_id=?',(run,page_id)).fetchone()[0])
        new_ext=asdict(extract_lead(raw['content'],cfg['extraction']))
        all_extractions.append({'page_id':page_id,'before':old_ext['outcome'],'after':new_ext['outcome']})
        outcomes[new_ext['outcome']]+=1
        if old_ext['outcome']=='unresolved_extraction':
            extraction_rows.append({'page_id':page_id,'title':acquired['ref']['title'],'revision_id':acquired['ref']['revision_id'],'raw_hash':raw['local_sha256'],
                'before_diagnostics':old_ext['diagnostics'],'after_outcome':new_ext['outcome'],'after_diagnostics':new_ext['diagnostics'],'after_template_usage':new_ext['template_usage']})
            for diagnostic in old_ext['diagnostics']:
                name=diagnostic['template']
                entry=templates[name]
                entry['articles'].add(page_id)
                entry['diagnostics']+=1
                entry['forms'].append({'page_id':page_id,'arguments':diagnostic['arguments']})
        old_cls=json.loads(db.execute('SELECT record_json FROM classification_results WHERE run_id=? AND page_id=?',(run,page_id)).fetchone()[0])
        associations=title_qids.get(normalize_title(acquired['ref']['title']),set())
        cls=asdict(classify(acquired['ref']['title'],next(iter(associations)),items,cfg['classification'],snapshot)) if len(associations)==1 else {'outcome':'pending','reason':'historical_association_not_in_cache'}
        classification_rows.append({'page_id':page_id,'title':acquired['ref']['title'],'before_outcome':old_cls['outcome'],'before_reason':old_cls.get('reason'),
            'before_dependency_count':len(old_cls.get('items',{})),'before_roots_present':sorted(set(old_cls.get('items',{}))&set(cfg['classification']['roots'])),
            'after':cls,'scope':'All compatible snapshot raw objects available offline; missing required dependencies remain pending.'})
    db.close()
    taxonomy=[]
    for name,value in sorted(templates.items()):
        kind=nonprose_kind(name,cfg['extraction'])
        category=kind or ('sentence_boundary' if name=='link_boundary' else 'argument_only_formatter' if name in cfg['extraction']['formatters'] else 'unresolved_requires_template_body_or_formatter')
        taxonomy.append({'template':name,'category':category,'affected_articles':sorted(value['articles']),'diagnostics':value['diagnostics'],'forms':value['forms']})
    return {'source_run':run,'source_snapshot':snapshot,'source_config_hash':config_hash,'new_rule_versions':{k:cfg[k]['version'] for k in ('classification','extraction')},
        'mode':'offline_retrospective_not_a_pilot','network_requests':0,'new_pipeline_runs':0,'cached_qids':len(items),
        'extraction_summary':{'cohort':len(all_extractions),'original_unresolved':len(extraction_rows),'original_diagnostics':sum(v['diagnostics'] for v in templates.values()),'distinct_names':len(templates),'offline_outcomes':dict(outcomes)},
        'template_taxonomy':taxonomy,'unresolved_articles':extraction_rows,'all_extractions':all_extractions,
        'classification_summary':{'before_counts':dict(Counter(a['before_reason'] or a['before_outcome'] for a in classification_rows)),
            'after_counts':dict(Counter(a['after'].get('reason') or a['after']['outcome'] for a in classification_rows)),
            'original_truncated_after':dict(Counter(a['after'].get('reason') or a['after']['outcome'] for a in classification_rows if a['before_reason']=='truncated_hierarchy')),
            'original_maxlag_after':dict(Counter(a['after'].get('reason') or a['after']['outcome'] for a in classification_rows if 'maxlag' in (a['before_reason'] or '')))},
        'classification_articles':classification_rows,'original_physical_requests':report['performance']['physical_requests'],
        'limitations':['No live Pilot 100 has been rerun. Cached retrospective outcomes are not measured source performance or final eligibility.',
            'The old maxlag records lack failed-QID/request context; it cannot be recovered reliably from aggregate metrics.',
            'Generic unsupported topic boxes and generated factual forms remain unresolved; no factual output is silently discarded.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('--source-report',required=True)
    parser.add_argument('--config',default='configs/pipeline.json')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=audit(args.database,args.source_report,args.config)
    path=Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(canonical({key:result[key] for key in ('mode','source_run','network_requests','extraction_summary','classification_summary')}))
