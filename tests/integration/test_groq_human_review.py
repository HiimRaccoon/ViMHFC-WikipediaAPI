import copy,csv,json
from pathlib import Path
import pytest
from vimfc.domain import digest


def sample():
 rows=[];results=[]
 specifications=[(19569090,'unclassified',None,'Science / Technology','no_supported_rule'),
  (19836772,'unresolved',None,'Landmark / Facility','primary_ambiguity'),
  (1,'completed','Organization','Landmark / Facility',None),(2,'completed','Organization','Location',None),
  (3,'completed','Event','Award / Competition',None),(4,'completed','Award / Competition','Event',None),
  (5,'completed','Award / Competition','Event',None),(6,'unclassified',None,'Science / Technology','no_supported_rule')]
 for i in range(10,40):
  outcome=('completed','unclassified','unresolved')[i%3]
  specifications.append((i,outcome,'Person' if outcome=='completed' else None,
   'Person' if outcome=='completed' else 'Event',None if outcome=='completed' else 'no_supported_rule' if outcome=='unclassified' else 'primary_dependency_missing'))
 for page,outcome,group,predicted,reason in specifications:
  inp={'title':'Bài '+str(page),'lead_text':'Một chủ thể được mô tả.','sentences':[{'id':0,'text':'Một chủ thể được mô tả.'}]}
  row={'input':inp,'input_fingerprint':digest(inp),'evaluation':{'page_id':page,'revision_id':page+100,'snapshot':'2026-10-25T03:00:00Z',
   'baseline_outcome':outcome,'baseline_primary':group,'classification_reason':reason,'sampling_stratum':'fixture'},'provenance':{'source':'synthetic-test-only'}}
  prediction={'predicted_group':predicted,'abstain':False,'evidence_sentence_ids':[0],'reason':'Câu 0 mô tả chủ thể.','ambiguity':None}
  results.append({'page_id':page,'input_fingerprint':row['input_fingerprint'],'model_id':'qwen/qwen3.8-27b','mode':'blind','status':'valid',
   'prediction':prediction,'response_content':json.dumps(prediction),'request_key':str(page),'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
  rows.append(row)
 return rows,results


def test_selection_is_deterministic_stratified_and_includes_all_diagnostics():
 from vimfc.experiments.groq_classification.human_review import select_sample
 rows,results=sample();s=select_sample(rows,results,seed=20261030)
 assert s==select_sample(list(reversed(rows)),list(reversed(results)),seed=20261030)
 assert len(s['members'])==11 and len({r['page_id'] for r in s['members']})==11
 ids={r['page_id'] for r in s['members']};assert {19569090,19836772,1,2,3,4,5,6}<=ids
 random=[r for r in s['members'] if r['partition']=='stratified_random']
 assert {r['stratum']['outcome'] for r in random}=={'completed','unclassified','unresolved'}
 assert all(0<r['conditional_inclusion_probability']<=1 for r in random)
 assert next(r for r in random if r['stratum']['outcome']=='unresolved')['stratum']['reason']=='primary_dependency_missing'


def bundle(tmp_path):
 from vimfc.experiments.groq_classification.human_review import create_bundle
 rows,results=sample();return create_bundle(rows,results,tmp_path/'review packet',seed=20261030,source={'kind':'synthetic-test-only'},precedence=['Person','Organization','Location','Event','Creative Work','Award / Competition','Landmark / Facility','Science / Technology'])


def test_blind_export_has_no_comparison_or_partition_leak(tmp_path):
 m=bundle(tmp_path);root=tmp_path/'review packet'
 records=[json.loads(line) for line in (root/'blind/labels.jsonl').read_text(encoding='utf-8').splitlines()]
 assert len(records)==11
 for r in records:
  assert not {'baseline_outcome','baseline_primary','predicted_group','partition','selection_reason','sampling_stratum','model_id','reason'}&set(r)
  assert r['human_primary']=='' and r['human_abstain']=='' and r['out_of_taxonomy']==''
 assert (root/'coordinator/comparison.csv').exists() and m['manifest_id']


def test_pending_labels_do_not_become_gold_and_metrics_are_partitioned(tmp_path):
 from vimfc.experiments.groq_classification.human_review import score_labels
 m=bundle(tmp_path);root=tmp_path/'review packet'
 score=score_labels(root,root/'blind/labels.csv',expected_manifest_id=m['manifest_id'])
 assert score['reviewed_count']==0 and score['status']=='AWAITING_HUMAN_LABELS'
 assert set(score['partitions'])=={'diagnostic_purposive','stratified_random'}
 assert all(v['metrics'] is None for v in score['partitions'].values())
 assert score['population_accuracy_claim'] is False


def test_metrics_count_model_abstention_as_error_without_inventing_a_gold_class():
 from vimfc.experiments.groq_classification.human_review import classification_metrics
 m=classification_metrics(['Event','Event','Person','Person'],['Event','Person','Person','__abstain__'],labels=['Event','Person'])
 assert m['accuracy']==.5 and m['coverage']==.75 and m['abstention_rate']==.25
 assert m['precision']==.75 and m['recall']==.5 and m['macro_f1']==pytest.approx(7/12)
 assert m['denominator']==4


def edited(root,m,updates):
 records=[json.loads(line) for line in (root/'blind/labels.jsonl').read_text(encoding='utf-8').splitlines()]
 for r,u in zip(records,updates):r.update(review_status='reviewed',reviewer='actual-test-fixture-reviewer',review_date='2026-10-30',**u)
 p=root.parent/'submitted.jsonl';p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8');return p,records


def test_human_uncertainty_and_out_of_taxonomy_are_distinct_and_not_classifier_truth(tmp_path):
 from vimfc.experiments.groq_classification.human_review import score_labels
 m=bundle(tmp_path);root=tmp_path/'review packet'
 path,records=edited(root,m,[{'human_primary':'','human_abstain':True,'out_of_taxonomy':False},
  {'human_primary':'','human_abstain':False,'out_of_taxonomy':True},
  {'human_primary':'Person','human_abstain':False,'out_of_taxonomy':False}])
 score=score_labels(root,path,expected_manifest_id=m['manifest_id'])
 assert score['reviewed_count']==3 and score['human_uncertain']==1 and score['human_out_of_taxonomy']==1
 assert sum(p['in_taxonomy_gold_count'] for p in score['partitions'].values())==1
 assert score['accepted_corpus_promotion'] is False


@pytest.mark.parametrize('field,value',[('human_primary','Unknown group'),('reviewer',''),('review_date','bad-date'),('human_abstain','maybe'),('out_of_taxonomy',True)])
def test_invalid_manual_review_is_rejected(tmp_path,field,value):
 from vimfc.experiments.groq_classification.human_review import score_labels
 m=bundle(tmp_path);root=tmp_path/'review packet'
 path,records=edited(root,m,[{'human_primary':'Person','human_abstain':False,'out_of_taxonomy':False}])
 records[0][field]=value;path.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
 with pytest.raises(ValueError):score_labels(root,path,expected_manifest_id=m['manifest_id'])


def test_input_drift_duplicate_ids_and_comparison_as_gold_are_rejected(tmp_path):
 from vimfc.experiments.groq_classification.human_review import score_labels
 m=bundle(tmp_path);root=tmp_path/'review packet'
 path,records=edited(root,m,[]);records[0]['lead_text']='changed'
 path.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
 with pytest.raises(ValueError):score_labels(root,path,expected_manifest_id=m['manifest_id'])
 original=(root/'blind/labels.jsonl').read_text(encoding='utf-8').splitlines()[0]
 path.write_text(original+'\n'+original+'\n',encoding='utf-8')
 with pytest.raises(ValueError):score_labels(root,path,expected_manifest_id=m['manifest_id'])
 with pytest.raises(ValueError):score_labels(root,root/'coordinator/comparison.jsonl',expected_manifest_id=m['manifest_id'])


def test_tampered_immutable_bundle_and_wrong_manifest_are_rejected(tmp_path):
 from vimfc.experiments.groq_classification.human_review import score_labels
 m=bundle(tmp_path);root=tmp_path/'review packet'
 with pytest.raises(ValueError):score_labels(root,root/'blind/labels.csv',expected_manifest_id='wrong')
 (root/'coordinator/comparison.jsonl').write_text('{}\n',encoding='utf-8')
 with pytest.raises(ValueError):score_labels(root,root/'blind/labels.csv',expected_manifest_id=m['manifest_id'])


@pytest.mark.parametrize('name',['data/raw/accidental','api/accidental','data/experiments/historical/new'])
def test_score_output_cannot_write_source_or_api_cache(tmp_path,monkeypatch,name):
 from vimfc.experiments.groq_classification import human_review as h
 m=bundle(tmp_path);root=tmp_path/'review packet';monkeypatch.setattr(h,'ROOT',tmp_path)
 output=tmp_path/name
 with pytest.raises(ValueError):h.import_and_report(root,root/'blind/labels.csv',output,expected_manifest_id=m['manifest_id'])
 assert not output.exists()


def test_csv_and_jsonl_manual_import_match_without_changing_bundle(tmp_path):
 from vimfc.experiments.groq_classification.human_review import score_labels,create_bundle
 rows,results=sample();rows[0]['input']['title']='= Bài có dấu';rows[0]['input_fingerprint']=digest(rows[0]['input']);results[0]['input_fingerprint']=rows[0]['input_fingerprint']
 root=tmp_path/'unicode packet';m=create_bundle(rows,results,root)
 jsonpath,records=edited(root,m,[{'human_primary':'Person','human_abstain':False,'out_of_taxonomy':False}])
 csvpath=tmp_path/'submitted.csv'
 with csvpath.open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader()
  for r in records:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,list) else v for k,v in r.items()})
 a=score_labels(root,jsonpath,expected_manifest_id=m['manifest_id']);b=score_labels(root,csvpath,expected_manifest_id=m['manifest_id'])
 assert a['partitions']==b['partitions'] and a['reviewed_count']==b['reviewed_count']==1


def test_boolean_sentence_id_is_not_equivalent_to_integer_zero(tmp_path):
 from vimfc.experiments.groq_classification.human_review import score_labels
 m=bundle(tmp_path);root=tmp_path/'review packet';path,records=edited(root,m,[])
 records[0]['sentences'][0]['id']=False
 path.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
 with pytest.raises(ValueError,match='edited'):score_labels(root,path,expected_manifest_id=m['manifest_id'])
