"""Read-only retrospective verification of Pilot 100 round 2. Never a pilot."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib,json,socket,sqlite3,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'/'src'))
import mwparserfromhell as mw
from vimfc.config import load_config
from vimfc.domain import digest,at_or_before
from vimfc.pipeline.classify import classify
from vimfc.pipeline.extract import extract_lead
from vimfc.pipeline.report import pair_generation_audit
from vimfc.source.revisions import RevisionSource
from vimfc.source.wikidata import WikidataSource
from vimfc.storage.raw_cache import RawCache


class NoNetwork:
    def query(self,*args,**kwargs):
        raise AssertionError('Audit requires uncached metadata; no source request is permitted')


def denied(*args,**kwargs):
    raise AssertionError('Network is forbidden in offline retrospective analysis')


def audit(database,report_path,config_path):
    socket.socket.connect=denied
    report=json.loads(Path(report_path).read_text(encoding='utf-8'))
    database=Path(database).resolve()
    db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    run=report['run_id']
    archived=db.execute('SELECT * FROM pipeline_runs WHERE pipeline_run_id=?',(run,)).fetchone()
    assert archived['snapshot']==report['snapshot'] and archived['config_hash']==report['config_hash']==digest(json.loads(archived['config_json']))
    cfg=load_config(config_path)
    rawcache=RawCache(database.parent/'raw')
    source=RevisionSource(NoNetwork(),SimpleNamespace(db=db),rawcache)
    wd=WikidataSource(source,report['snapshot'],cfg['classification'])
    classifications=[];extractions=[];current_records=[]
    cutoff_nodes=Counter();blocked=Counter();start_props=Counter()
    for candidate in report['cohort_page_ids']:
        old=json.loads(db.execute('SELECT record_json FROM classification_results WHERE run_id=? AND page_id=?',(run,candidate)).fetchone()[0])
        process=json.loads(db.execute("SELECT result_json FROM stage_results WHERE run_id=? AND identity=? AND stage='process' AND result_json IS NOT NULL ORDER BY rowid DESC LIMIT 1",(run,str(candidate))).fetchone()[0])
        current_records.append(process)
        acquired=json.loads(db.execute("SELECT result_json FROM stage_results WHERE run_id=? AND identity=? AND stage='acquire' AND status='completed'",(run,str(candidate))).fetchone()[0])
        raw=rawcache.get(acquired['cache_ref'])
        assert raw['local_sha256']==acquired['local_sha256']==hashlib.sha256(raw['content'].encode('utf-8')).hexdigest()
        assert at_or_before(raw['timestamp'],report['snapshot'])
        old_ext=json.loads(db.execute('SELECT record_json FROM lead_results WHERE run_id=? AND page_id=?',(run,candidate)).fetchone()[0])
        new_ext=asdict(extract_lead(raw['content'],cfg['extraction']))
        comparable={**new_ext,'extraction_version':old_ext['extraction_version']}
        unchanged=comparable==old_ext
        labels_only={**comparable,'links':[{**link,'link_text':old_link['link_text']} for link,old_link in zip(comparable['links'],old_ext['links'])]}
        quarantined=old_ext['outcome']==new_ext['outcome']=='unresolved_extraction' and {d['template'] for d in old_ext['diagnostics']}<={d['template'] for d in new_ext['diagnostics']}
        assert unchanged or labels_only==old_ext or candidate==3148970 or quarantined,('Unreviewed extraction change',candidate)
        if candidate==3148970:
            assert ': Free Fire' in new_ext['full_lead']
        # Independent coverage and provenance invariants on all completed leads.
        lead_code=mw.parse(raw['content']).get_sections(include_lead=True)[0]
        authored_targets={str(link.title).strip() for link in lead_code.filter_wikilinks()}
        if new_ext['outcome']=='completed':
            assert new_ext['full_lead']=='\n\n'.join(new_ext['paragraphs'])
            for paragraph_id,text in enumerate(new_ext['paragraphs']):
                cursor=0
                for sent in (s for s in new_ext['sentences'] if s['paragraph_id']==paragraph_id):
                    assert text[sent['start']:sent['end']]==sent['text'] and not text[cursor:sent['start']].strip()
                    cursor=sent['end']
                assert not text[cursor:].strip()
            for link in new_ext['links']:
                sent=new_ext['sentences'][link['sent_id']]
                assert link['scope']=='retained_lead_prose' and link['original_target'] in authored_targets
                assert sent['start']<=link['start']<link['end']<=sent['end'],(candidate,link,sent)
                assert new_ext['paragraphs'][link['paragraph_id']][link['start']:link['end']]==link['link_text'],(candidate,link)
        extractions.append({'page_id':candidate,'title':process['title'],'revision_id':process['revision_id'],'raw_hash':raw['local_sha256'],
            'outcome':new_ext['outcome'],'unchanged_except_version':unchanged,'sentence_count':len(new_ext['sentences']),'lead_link_count':len(new_ext['links']),
            'diagnostics':new_ext['diagnostics'],'completed_prose_sentence_link_invariants_passed':new_ext['outcome']=='completed'})
        qid=wd.cached_qid(process['title'])
        assert qid,('Expected archived association',candidate)
        items=wd.closure(qid)
        new_cls=asdict(classify(process['title'],qid,items,cfg['classification'],report['snapshot']))
        for diagnostic in old.get('diagnostics',[]):
            cutoff_nodes[diagnostic['qid']]+=1
            for _,child in diagnostic.get('required_children',[]): blocked[child]+=1
            if diagnostic.get('properties'): start_props[diagnostic['properties'][0]]+=1
        classifications.append({'page_id':candidate,'title':process['title'],'qid':qid,'old_outcome':old['outcome'],'old_reason':old.get('reason'),
            'old_configured_roots':sorted(set(old['items'])&set(cfg['classification']['roots'])),'old_cutoffs':old.get('diagnostics',[]),
            'new_classification':new_cls,'closure':wd.last_traversal.copy(),'dependency_count':len(items),
            'dependency_refs':[{'qid':q,**{k:v.get(k) for k in ('revision_id','revision_timestamp','raw_hash')}} for q,v in sorted(items.items())]})
    pairs=[json.loads(row[0]) for row in db.execute('SELECT record_json FROM pair_candidates WHERE run_id=?',(run,))]
    result={'mode':'offline_retrospective_not_a_pilot','source_run':run,'snapshot':report['snapshot'],'source_config_hash':report['config_hash'],
        'cohort_hash':report['cohort_hash'],'new_classification_version':cfg['classification']['version'],'extraction_version':cfg['extraction']['version'],
        'network_requests':0,'new_actual_runs':0,'classification_summary':{
            'original_truncated':sum(a['old_reason']=='truncated_hierarchy' for a in classifications),
            'original_truncated_with_root':sum(a['old_reason']=='truncated_hierarchy' and bool(a['old_configured_roots']) for a in classifications),
            'cutoff_diagnostics':sum(cutoff_nodes.values()),'distinct_cutoff_nodes':len(cutoff_nodes),'distinct_blocked_nodes':len(blocked),
            'starting_properties':dict(start_props),'original_truncated_after':dict(Counter(a['new_classification']['outcome'] for a in classifications if a['old_reason']=='truncated_hierarchy')),
            'all_after':dict(Counter(a['new_classification']['outcome'] for a in classifications)),
            'missing_frontier':dict(Counter(q for a in classifications for q in a['closure']['uncached_frontier']))},
        'classification_articles':classifications,'extraction_summary':dict(Counter(a['outcome'] for a in extractions)),
        'extraction_articles':extractions,'pair_audit_of_original_16_eligible':pair_generation_audit(current_records,pairs,cfg['ranking']),
        'source_cache_metrics':dict(wd.metrics),'limitations':['No new Pilot 100 or source API execution; retrospective classification does not establish new live eligibility, runtime or pair yield.',
            'Missing frontier nodes remain terminal unresolved and ineligible; no unsupported class inference.']}
    db.close()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('--source-report',required=True)
    parser.add_argument('--config',default='configs/pipeline.json')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=audit(args.database,args.source_report,args.config)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('mode','network_requests','classification_summary','extraction_summary','pair_audit_of_original_16_eligible')}))
