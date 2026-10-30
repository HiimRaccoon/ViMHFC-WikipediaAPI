"""Offline, provenance-bound review sampling and explicit human-label scoring.

No API/credential/ledger write interface. Purposive diagnostics and conditional
stratified random draws are distinct; neither supports population accuracy.
"""
import copy,csv,hashlib,io,json,os
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from vimfc.domain import GROUPS,digest
from .prompt import DEFINITIONS,validate_input,validate_prediction
from .evaluation import _csv_cell

POLICY='human-review-diagnostic8-random3-1'
REQUIRED=(19569090,19836772)
IMMUTABLE=('blind/reference.jsonl','blind/INSTRUCTIONS.md','coordinator/comparison.jsonl','coordinator/comparison.csv')
HUMAN=('human_primary','human_abstain','out_of_taxonomy','review_status','reviewer','review_date','notes')
ROOT=Path(__file__).resolve().parents[5]

def _unique(pairs):
 result={}
 for k,v in pairs:
  if k in result:raise ValueError('Duplicate JSON field')
  result[k]=v
 return result

def _json(text):return json.loads(text,object_pairs_hook=_unique,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))
def _read(path):return _json(Path(path).read_text(encoding='utf-8-sig'))
def _lines(path):return [_json(line) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
def _bytes(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8')
def _sha(path):
 with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def _rank(seed,domain,value):return digest({'seed':seed,'domain':domain,'value':value})
def _new(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
def _jsonl(records):return ''.join(json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n' for r in records).encode('utf-8')
def _csv(records):
 stream=io.StringIO(newline='');w=csv.DictWriter(stream,fieldnames=list(records[0]));w.writeheader()
 w.writerows({k:_csv_cell(v) for k,v in r.items()} for r in records)
 return stream.getvalue().encode('utf-8-sig')

def _records(rows,results):
 byid={}
 for row in rows:
  validate_input(row['input']);e=row['evaluation'];page=e['page_id']
  if type(page) is not int or page in byid or digest(row['input'])!=row['input_fingerprint']:raise ValueError('Source identity/fingerprint differs')
  if e['baseline_outcome'] not in ('completed','unclassified','unresolved'):raise ValueError('Unknown baseline outcome')
  if (e['baseline_outcome']=='completed')!=(e['baseline_primary'] in GROUPS):raise ValueError('Baseline group/outcome differs')
  byid[page]=row
 available={}
 for r in results:
  page=r['page_id']
  if page not in byid or page in available or r['input_fingerprint']!=byid[page]['input_fingerprint']:raise ValueError('Result identity differs')
  if r['mode']!='blind' or r['model_id']!='qwen/qwen3.8-27b':raise ValueError('Wrong model/mode')
  if r['status']!='valid':continue
  checked=validate_prediction(r['response_content'],byid[page])
  if checked['status']!='valid' or checked['prediction']!=r['prediction']:raise ValueError('Response validation differs')
  available[page]=r
 return byid,available

def select_sample(rows,results,seed=20261030):
 byid,available=_records(rows,results)
 if not set(REQUIRED)<=set(available):raise ValueError('Required reviewed-response cases missing')
 members=[];selected=set()
 def add(page,partition,reason,stratum=None,probability=None):
  if page in selected:return
  selected.add(page);members.append({'page_id':page,'partition':partition,'selection_reason':reason,
   'stratum':stratum,'conditional_inclusion_probability':probability})
 for page in REQUIRED:add(page,'diagnostic_purposive','required_priority_case')
 disagreements=[page for page,r in available.items() if byid[page]['evaluation']['baseline_outcome']=='completed'
  and not r['prediction']['abstain'] and r['prediction']['predicted_group']!=byid[page]['evaluation']['baseline_primary']]
 for page in sorted(disagreements,key=lambda p:_rank(seed,'disagreement',p))[:5]:add(page,'diagnostic_purposive','verified_reference_disagreement')
 science=[p for p,r in available.items() if p not in selected and r['prediction']['predicted_group']=='Science / Technology']
 if not science:raise ValueError('Additional Science review candidate missing')
 add(min(science,key=lambda p:_rank(seed,'science_probe',p)),'diagnostic_purposive','additional_science_technology_probe')
 frames=[]
 for outcome in ('completed','unclassified','unresolved'):
  strata=defaultdict(list)
  for page,r in available.items():
   e=byid[page]['evaluation']
   if page in selected or e['baseline_outcome']!=outcome:continue
   # Station F covers ambiguity. Allocate the random unresolved slot to the
   # missing-dependency reason a priori; do not search seeds for desired cases.
   if outcome=='unresolved' and e['classification_reason']!='primary_dependency_missing':continue
   group=e['baseline_primary'] if outcome=='completed' else r['prediction']['predicted_group'] or '__abstain__'
   strata[(e['classification_reason'],group)].append(page)
  if not strata:raise ValueError('Required random reason/outcome frame is empty')
  keys=sorted(strata,key=lambda k:_rank(seed,'stratum:'+outcome,list(k)));key=keys[0]
  page=min(strata[key],key=lambda p:_rank(seed,'within-stratum:'+outcome,p))
  stratum={'outcome':outcome,'reason':key[0],'group':key[1]}
  add(page,'stratified_random','seeded_two_stage_stratum_then_article',stratum,1/(len(strata)*len(strata[key])))
  frames.append({'outcome':outcome,'restriction':'primary_dependency_missing' if outcome=='unresolved' else 'all remaining valid responses',
   'strata':[{'reason':k[0],'group':k[1],'page_ids':sorted(v),'count':len(v)} for k,v in sorted(strata.items(),key=lambda item:str(item[0]))],
   'selected_stratum':stratum,'stratum_count':len(strata),'selected_stratum_count':len(strata[key])})
 members.sort(key=lambda r:_rank(seed,'blind-order',r['page_id']))
 for i,m in enumerate(members,1):m['review_id']=f'HR{i:02d}';m['input_fingerprint']=byid[m['page_id']]['input_fingerprint']
 return {'policy':POLICY,'seed':seed,'available_count':len(available),'available_page_ids':sorted(available),
  'members':members,'random_frames':frames,'all_reference_disagreements':sorted(disagreements),
  'scope_warning':'Purposive diagnostic + conditional random reason/group strata; no population accuracy for179/300/corpus.'}

def create_bundle(rows,results,output,*,seed=20261030,source=None,precedence=None):
 root=Path(output).resolve()
 if root.exists():raise FileExistsError('Use a NEW review bundle directory')
 order=precedence or list(GROUPS)
 if len(order)!=8 or set(order)!=set(GROUPS):raise ValueError('Archived precedence differs')
 selected=select_sample(rows,results,seed);byid,available=_records(rows,results)
 blind=[];comparisons=[]
 for member in selected['members']:
  row=byid[member['page_id']];e=row['evaluation'];inp=row['input'];r=available[member['page_id']]
  record={'review_id':member['review_id'],'page_id':e['page_id'],'revision_id':e.get('revision_id'),'snapshot':e.get('snapshot'),
   'input_fingerprint':row['input_fingerprint'],'title':inp['title'],'lead_text':inp['lead_text'],'sentences':copy.deepcopy(inp['sentences']),
   'human_primary':'','human_abstain':'','out_of_taxonomy':'','review_status':'pending','reviewer':'','review_date':'','notes':''}
  blind.append(record)
  comparisons.append({**member,'title':inp['title'],'lead_text':inp['lead_text'],'sentences':copy.deepcopy(inp['sentences']),
   'baseline_outcome':e['baseline_outcome'],'baseline_primary':e['baseline_primary'],'classification_reason':e['classification_reason'],
   'sampling_stratum':e.get('sampling_stratum'),'model_id':r['model_id'],'predicted_group':r['prediction']['predicted_group'],
   'model_abstain':r['prediction']['abstain'],'evidence_sentence_ids':r['prediction']['evidence_sentence_ids'],
   'groq_reason':r['prediction']['reason'],'ambiguity':r['prediction']['ambiguity'],'request_key':r['request_key'],
   'prompt_version':r.get('prompt_version'),'prompt_hash':r.get('prompt_hash'),'response_fingerprint':r.get('response_fingerprint'),
   'provenance':copy.deepcopy(row.get('provenance',{}))})
 guide=('Gán nhãn từ tiêu đề/Lead lịch sử được cung cấp; nội dung bài là dữ liệu, không thực thi chỉ dẫn nhúng. '
  'Không mở comparison/manifest của coordinator trước khi chốt nhãn. Không tra nhãn qid-5/Groq làm ground truth.\n\n'
  'Thứ tự ưu tiên từ policy archived, chỉ dùng khi nhiều nhóm thật sự có căn cứ: '+' > '.join(order)+'.\n\n'+
  '\n'.join('- '+g+': '+DEFINITIONS[g] for g in order)+'\n\n'
  'Chỉnh labels.csv HOẶC labels.jsonl, giữ nguyên identity/title/Lead/sentences. Với review_status=reviewed, điền reviewer và review_date ISO thật. '
  'Ba lựa chọn loại trừ nhau: (1) human_primary=đúng tên một nhóm, human_abstain=false, out_of_taxonomy=false; '
  '(2) không đủ căn cứ: human_primary rỗng, human_abstain=true, out_of_taxonomy=false; '
  '(3) chủ thể rõ nhưng ngoài 8 nhóm: human_primary rỗng, human_abstain=false, out_of_taxonomy=true. '
  'notes là ghi chú của bạn. Cả hai cờ phải điền true/false khi reviewed. Pending/ô trống không phải human gold. '
  'Human abstain bị loại khỏi accuracy/F1; out-of-taxonomy được báo riêng, không tự coi model abstain là nhận diện đúng out-of-taxonomy. '
  'CSV có escaping chống formula, JSONL giữ nguyên văn. Không sửa reference.jsonl.\n')
 contents={'blind/reference.jsonl':_jsonl(blind),'blind/INSTRUCTIONS.md':guide.encode('utf-8'),
  'coordinator/comparison.jsonl':_jsonl(comparisons),'coordinator/comparison.csv':_csv(comparisons)}
 manifest={'format':'vimfc-human-review-packet-1','source':source or {},'selection':selected,'precedence':list(order),
  'editable_files':['blind/labels.csv','blind/labels.jsonl'],
  'files':{name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)} for name,data in contents.items()}}
 manifest['manifest_id']=digest(manifest)
 for name,data in contents.items():_new(root/name,data)
 _new(root/'blind/labels.csv',_csv(blind));_new(root/'blind/labels.jsonl',_jsonl(blind))
 _new(root/'coordinator/manifest.json',_bytes(manifest))
 _new(root/'README.md',('Give annotator only blind/. Keep coordinator/ separate until labels are locked. '
  'The editable labels files are not immutable references. Import with the manifest_id pinned in the handoff. '
  'No API calls or corpus promotion.\n').encode())
 return manifest

def load_bundle(root,expected_manifest_id):
 root=Path(root);m=_read(root/'coordinator/manifest.json');identity=m.pop('manifest_id')
 if identity!=expected_manifest_id or digest(m)!=identity or m.get('format')!='vimfc-human-review-packet-1':raise ValueError('Manifest identity differs')
 if set(m['files'])!=set(IMMUTABLE):raise ValueError('Unexpected immutable file paths')
 for name,record in m['files'].items():
  p=root/name
  if p.stat().st_size!=record['bytes'] or _sha(p)!=record['sha256']:raise ValueError('Review bundle tampered')
 reference=_lines(root/'blind/reference.jsonl');comparison=_lines(root/'coordinator/comparison.jsonl')
 refs={r['review_id']:r for r in reference};comp={r['review_id']:r for r in comparison}
 if len(refs)!=len(reference) or set(refs)!=set(comp) or len(refs)!=len(m['selection']['members']):raise ValueError('Bundle membership differs')
 for r in reference:
  if digest({'title':r['title'],'lead_text':r['lead_text'],'sentences':r['sentences']})!=r['input_fingerprint']:raise ValueError('Blind input fingerprint differs')
 m['manifest_id']=identity
 return m,refs,comp

def _bool(value):
 if type(value) is bool:return value
 if isinstance(value,str) and value.lower().strip() in ('true','false'):return value.lower().strip()=='true'
 raise ValueError('Reviewed flags require explicit true/false')

def _submitted(path):
 path=Path(path)
 if path.suffix.lower()=='.csv':
  with path.open(encoding='utf-8-sig',newline='') as f:
   reader=csv.DictReader(f)
   if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames):raise ValueError('Duplicate CSV headers')
   return list(reader)
 return _lines(path)

def classification_metrics(truth,prediction,*,labels):
 if len(truth)!=len(prediction) or not truth:return None
 if any(t not in GROUPS for t in truth):raise ValueError('Closed taxonomy gold differs')
 if not labels or any(g not in GROUPS for g in labels):raise ValueError('Metric label set differs')
 matrix=defaultdict(Counter);per={}
 for t,p in zip(truth,prediction):matrix[t][p or '__unresolved__']+=1
 for g in labels:
  tp=sum(t==g and p==g for t,p in zip(truth,prediction));fp=sum(t!=g and p==g for t,p in zip(truth,prediction));fn=sum(t==g and p!=g for t,p in zip(truth,prediction))
  precision=tp/(tp+fp) if tp+fp else 0.;recall=tp/(tp+fn) if tp+fn else 0.
  per[g]={'support':truth.count(g),'tp':tp,'fp':fp,'fn':fn,'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0.}
 return {'denominator':len(truth),'accuracy':sum(t==p for t,p in zip(truth,prediction))/len(truth),
  'precision':mean(v['precision'] for v in per.values()),'recall':mean(v['recall'] for v in per.values()),'macro_f1':mean(v['f1'] for v in per.values()),
  'coverage':sum(p in GROUPS for p in prediction)/len(truth),'abstention_rate':prediction.count('__abstain__')/len(truth),
  'unresolved_rate':sum(p is None for p in prediction)/len(truth),'macro_labels':labels,'per_group':per,'confusion_matrix':dict(matrix)}

def _views(c):
 baseline=c['baseline_primary'] if c['baseline_outcome']=='completed' else None
 model='__abstain__' if c['model_abstain'] else c['predicted_group']
 return {'qid5_baseline':baseline,'groq_only':model,'wikidata_first_suggestions':baseline or model}

def score_labels(root,labels_path,*,expected_manifest_id):
 manifest,refs,comparisons=load_bundle(root,expected_manifest_id);submitted=_submitted(labels_path);seen=set();reviewed=[]
 for r in submitted:
  if set(r)!=set(next(iter(refs.values()))):raise ValueError('Human input has unexpected/missing fields; never import comparison as gold')
  rid=r['review_id']
  if rid not in refs or rid in seen:raise ValueError('Unknown or duplicate review ID')
  seen.add(rid);expected=refs[rid]
  for field,value in expected.items():
   if field in HUMAN:continue
   actual=r[field]
   if field in ('page_id','revision_id'):
    if isinstance(actual,str) and actual.isdecimal():actual=int(actual)
   elif field=='sentences' and isinstance(actual,str):actual=_json(actual)
   # Python equates False with0 and True with1; source sentence/page IDs are
   # typed identities. Canonical hashes also reject that silent type drift.
   if digest(actual)!=digest(value) and digest(actual)!=digest(_csv_cell(value)):raise ValueError('Historical input identity/content was edited')
  status=r['review_status']
  if status not in ('pending','','reviewed'):raise ValueError('Unknown human review status')
  if status!='reviewed':continue
  if not isinstance(r['reviewer'],str) or not r['reviewer'].strip():raise ValueError('Actual reviewer required')
  try:datetime.fromisoformat(r['review_date'].replace('Z','+00:00'))
  except (ValueError,TypeError,AttributeError):raise ValueError('Valid actual review date required') from None
  abstain=_bool(r['human_abstain']);outside=_bool(r['out_of_taxonomy']);group=r['human_primary'] or None
  if group is not None and group not in GROUPS:raise ValueError('Unknown human group')
  if sum((group is not None,abstain,outside))!=1:raise ValueError('Choose exactly one human group/uncertain/out-of-taxonomy')
  reviewed.append({**r,'page_id':expected['page_id'],'human_primary':group,'human_abstain':abstain,'out_of_taxonomy':outside,
   'partition':comparisons[rid]['partition']})
 partitions={}
 for partition in ('diagnostic_purposive','stratified_random'):
  members=[c for c in comparisons.values() if c['partition']==partition];gold=[r for r in reviewed if r['partition']==partition]
  closed=[r for r in gold if r['human_primary'] in GROUPS];outside=[r for r in gold if r['out_of_taxonomy']]
  labels=sorted({r['human_primary'] for r in closed}|{g for r in closed for g in _views(comparisons[r['review_id']]).values() if g in GROUPS})
  metrics=None
  if closed:metrics={view:classification_metrics([r['human_primary'] for r in closed],[_views(comparisons[r['review_id']])[view] for r in closed],labels=labels) for view in ('qid5_baseline','groq_only','wikidata_first_suggestions')}
  oot={}
  for view in ('qid5_baseline','groq_only','wikidata_first_suggestions'):
   values=[_views(comparisons[r['review_id']])[view] for r in outside]
   oot[view]={'denominator':len(values),'assigned_in_taxonomy_count':sum(v in GROUPS for v in values),
    'model_declined_count':values.count('__abstain__'),'unresolved_count':values.count(None),
    'note':'Declining is not an explicit prediction or proof of recognizing out-of-taxonomy.'}
  partitions[partition]={'selected_count':len(members),'reviewed_count':len(gold),'pending_count':len(members)-len(gold),
   'human_uncertain':sum(r['human_abstain'] for r in gold),'in_taxonomy_gold_count':len(closed),'out_of_taxonomy_count':len(outside),
   'metrics':metrics,'out_of_taxonomy_behavior':oot,'scope':'Selected reviewed cases only; no generalization to179/300/corpus.'}
 return {'format':'vimfc-human-review-scores-1','manifest_id':manifest['manifest_id'],
  'status':'HUMAN_LABELS_IMPORTED' if reviewed else 'AWAITING_HUMAN_LABELS','selected_count':len(refs),'reviewed_count':len(reviewed),
  'human_uncertain':sum(r['human_abstain'] for r in reviewed),'human_out_of_taxonomy':sum(r['out_of_taxonomy'] for r in reviewed),
  'partitions':partitions,'reviewed_labels':reviewed,'submitted_labels_sha256':_sha(labels_path),
  'population_accuracy_claim':False,'accepted_corpus_promotion':False,
  'metric_policy':'Human uncertainty excluded; out-of-taxonomy separate. Model abstention/unresolved counts as wrong on in-taxonomy human gold. Macro labels: common union of human gold and three-view assigned labels within the reviewed partition, not all unobserved groups; zero division=0. No pooled headline score or inverse-probability population estimate.'}

def _safe_output(path,forbidden):
 path=Path(path).resolve()
 for p in [ROOT/'api',ROOT/'data/inventories',ROOT/'data/replays',ROOT/'data/integrity',*map(Path,forbidden)]:
  p=p.resolve()
  if path==p or path.is_relative_to(p):raise ValueError('Review output overlaps immutable source')
 if path.is_relative_to(ROOT/'data') and not path.is_relative_to(ROOT/'data/experiments'):raise ValueError('Review output cannot write source data')
 for base in (ROOT/'data/experiments',ROOT/'reports/verification'):
  if path.is_relative_to(base):
   parts=path.relative_to(base).parts
   if not parts or not parts[0].startswith('groq-'):raise ValueError('Use a separate Groq review namespace')
 if path.exists():raise FileExistsError('Use a new output directory')
 return path

def prepare_from_exports(dataset,results_path,review_export,output,*,seed,expected_dataset_manifest):
 from .dataset import load_dataset
 dataset=Path(dataset);results_path=Path(results_path);review_export=Path(review_export)
 output=_safe_output(output,[dataset,results_path.parent,review_export.parent])
 m=_read(dataset/'manifest.json')
 if m['manifest_id']!=expected_dataset_manifest:raise ValueError('Unexpected source dataset')
 rows=load_dataset(dataset);results=_read(results_path);exports=_lines(review_export);byid={r['evaluation']['page_id']:r for r in rows}
 if len(exports)!=len(rows) or len({r['page_id'] for r in exports})!=len(rows):raise ValueError('Existing review export identity differs')
 for e in exports:
  row=byid[e['page_id']]
  if any(e[k]!=row['input'][k] for k in ('title','lead_text','sentences')) or e['input_fingerprint']!=row['input_fingerprint']:raise ValueError('Existing human-review source drift')
 byid,available=_records(rows,results)
 if len(available)!=179:raise ValueError('Expected exact179 captured valid responses; no API fallback')
 source={'dataset_manifest_id':m['manifest_id'],'dataset_manifest_sha256':_sha(dataset/'manifest.json'),'inputs_sha256':_sha(dataset/'inputs.jsonl'),
  'results_sha256':_sha(results_path),'review_export_sha256':_sha(review_export),'captured_responses':len(available),
  'immutable_source_paths':[str(dataset.resolve()),str(results_path.parent.resolve()),str(review_export.parent.resolve())],
  'snapshot':'2026-10-25T03:00:00Z','pipeline_rules':'qid-5 / lead-4 / quality-2.1 / rank-1'}
 return create_bundle(rows,results,output,seed=seed,source=source,precedence=m['config']['classification']['precedence'])

def import_and_report(bundle,labels,output,*,expected_manifest_id):
 manifest,_,_=load_bundle(bundle,expected_manifest_id)
 output=_safe_output(output,[bundle,*manifest['source'].get('immutable_source_paths',[])]);score=score_labels(bundle,labels,expected_manifest_id=expected_manifest_id)
 _new(output/'metrics.json',_bytes(score));_new(output/'reviewed-labels.jsonl',_jsonl(score['reviewed_labels']))
 lines=['# Human review results','',f"Status: {score['status']}; reviewed {score['reviewed_count']}/{score['selected_count']}.",'',score['metric_policy'],'']
 for name,partition in score['partitions'].items():
  lines.extend(['## '+name,'',f"Reviewed {partition['reviewed_count']}, in-taxonomy gold {partition['in_taxonomy_gold_count']}, human uncertain {partition['human_uncertain']}, out-of-taxonomy {partition['out_of_taxonomy_count']}.",''])
  if partition['metrics'] is None:lines.append('Accuracy/precision/recall/Macro-F1 unassessed: no reviewed in-taxonomy human gold.')
  else:
   lines.extend(['| Approach | N | Accuracy | Macro precision | Macro recall | Macro-F1 | Coverage | Model abstention |','|---|---:|---:|---:|---:|---:|---:|---:|'])
   for view,v in partition['metrics'].items():lines.append(f"|{view}|{v['denominator']}|{v['accuracy']:.4f}|{v['precision']:.4f}|{v['recall']:.4f}|{v['macro_f1']:.4f}|{v['coverage']:.4f}|{v['abstention_rate']:.4f}|")
  lines.append('')
 lines.append('No automatic corpus promotion. Diagnostic and conditional random samples are not accuracy estimates for179/300or the corpus.')
 _new(output/'report.md',('\n'.join(lines)+'\n').encode('utf-8'));return score
