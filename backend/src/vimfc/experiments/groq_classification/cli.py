"""Local CPU-only experiment CLI; contains no accepted-corpus write interface."""
import argparse,csv,hashlib,json,os,re,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from vimfc.domain import digest
from .classifier import GroqAssistedClassifier,validate_free_confirmation,request_key
from .dataset import prepare_dataset,load_dataset
from .evaluation import evaluate_records,export_review,render_report
from .prompt import build_prompt
from .provider import GroqClient,ProviderError,load_credentials,load_free_tier_confirmation,select_model
from .store import ExperimentStore,Pause

ROOT=Path(__file__).resolve().parents[5]
DEFAULT_CONFIG=ROOT/'configs/groq_classification.json'

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write_new(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
def validate_settings(cfg):
 allowed={'version','model_id','mode','max_output_tokens','timeout_seconds','max_requests','max_total_tokens','rpm','rpd','tpm','tpd','max_attempts','max_wait_seconds','max_runtime_seconds','free_tier_confirmed','model_limit_caps','limit_provenance'}
 if set(cfg)!=allowed or cfg['version']!='groq-classification-1' or cfg['mode'] not in ('assisted','blind'):raise ValueError('Unsupported Groq experiment config')
 if type(cfg['free_tier_confirmed']) is not bool:raise ValueError('Free-tier confirmation must be boolean')
 for key in ('max_output_tokens','timeout_seconds','max_requests','max_total_tokens','rpm','rpd','tpm','tpd','max_attempts','max_wait_seconds','max_runtime_seconds'):
  if type(cfg[key]) is not int or cfg[key]<=0:raise ValueError('Invalid finite budget: '+key)
 if cfg['max_attempts']>3 or cfg['max_wait_seconds']>60 or cfg['max_output_tokens']>1024:raise ValueError('Conservative inference limits exceeded')
 if cfg['model_id'] is not None and (not isinstance(cfg['model_id'],str) or not re.fullmatch(r'[A-Za-z0-9._/-]{1,128}',cfg['model_id']) or cfg['model_id'].startswith('gsk_')):raise ValueError('Invalid configurable model ID')
 for model,caps in cfg['model_limit_caps'].items():
  if set(caps)!={'rpm','rpd','tpm','tpd'} or any(type(v) is not int or v<=0 for v in caps.values()):raise ValueError('Invalid model-specific limits')
 return cfg
def parser():
 p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='action',required=True)
 prep=subs.add_parser('prepare');prep.add_argument('--context',required=True);prep.add_argument('--output',required=True)
 prep.add_argument('--size',type=int,default=300);prep.add_argument('--seed',type=int,default=20261029)
 for action in ('preflight','run'):
  sub=subs.add_parser(action);sub.add_argument('--dataset',required=True);sub.add_argument('--output',required=True)
  sub.add_argument('--config',default=str(DEFAULT_CONFIG));sub.add_argument('--env-file',default=str(ROOT/'api/.env'))
  sub.add_argument('--model');sub.add_argument('--ledger',default=str(ROOT/'api/groq-experiment-ledger.sqlite'))
  if action=='run':
   sub.add_argument('--limit',type=int,default=20);sub.add_argument('--mode',choices=('assisted','blind'))
   sub.add_argument('--resume',action='store_true');sub.add_argument('--smoke',action='store_true')
 ev=subs.add_parser('evaluate');ev.add_argument('--dataset',required=True);ev.add_argument('--results-root',required=True);ev.add_argument('--output',required=True)
 ev.add_argument('--ledger',default=str(ROOT/'api/groq-experiment-ledger.sqlite'));ev.add_argument('--human-labels')
 report=subs.add_parser('report');report.add_argument('--metrics',required=True);report.add_argument('--output',required=True)
 return p
def _model_setting(env_file):
 if os.environ.get('GROQ_MODEL'):return os.environ['GROQ_MODEL']
 try:lines=Path(env_file).read_text(encoding='utf-8-sig').splitlines()
 except FileNotFoundError:return None
 values=[]
 for line in lines:
  match=re.match(r'^\s*(?:export\s+)?GROQ_MODEL\s*=\s*(.*?)\s*$',line)
  if match:values.append(match[1].strip().strip('"\''))
 if len(values)>1:raise ValueError('Duplicate GROQ_MODEL setting')
 return (values[0] or None) if values else None
def _safe_path(path,manifest=None):
 path=Path(path).resolve()
 forbidden=[ROOT/'data/inventories',ROOT/'data/replays',ROOT/'data/integrity']
 if manifest:
  ctx=manifest['source']['context'];forbidden.extend([Path(ctx['raw_cache']),Path(ctx['database']).parent])
 if any(path==p.resolve() or path.is_relative_to(p.resolve()) for p in forbidden):raise ValueError('Experiment output overlaps preserved source')
 for base in (ROOT/'data/experiments',ROOT/'reports/verification'):
  if path.is_relative_to(base):
   parts=path.relative_to(base).parts
   if not parts or not parts[0].startswith('groq-'):raise ValueError('Output must use a separate Groq experiment namespace')
 return path
def _settings(args):
 cfg=validate_settings(read(args.config));cfg['model_id']=args.model or _model_setting(args.env_file) or cfg['model_id']
 cfg['free_tier_confirmed']=load_free_tier_confirmation(Path(args.env_file))['confirmed']
 if getattr(args,'mode',None):cfg['mode']=args.mode
 return validate_settings(cfg)
def _metadata(client,store,invocation,settings):
 if store.pending():raise Pause('uncertain_request_requires_review')
 started=time.time()
 while True:
  try:attempt=store.reserve('models:'+invocation,0,'model-discovery','models',invocation);break
  except Pause as e:
   wait=e.retry_at-time.time() if e.retry_at is not None else None
   if wait is None or wait<=0 or wait>settings['max_wait_seconds'] or time.time()+wait-started>=settings['max_runtime_seconds']:raise
   time.sleep(wait+0.001)
 try:r=client.list_models()
 except ProviderError as e:
  store.finish(attempt,{'code':e.code,'http_status':e.status,'headers':e.headers},error=True,uncertain=e.status is None)
  if e.retry_after_seconds:store.cooldown(e.retry_after_seconds,e.code)
  raise Pause(e.code) from None
 store.finish(attempt,r,tokens=0);store.headers(r['headers']);return r
def _evaluate(args):
 rows=load_dataset(Path(args.dataset));manifest=read(Path(args.dataset)/'manifest.json');out=_safe_path(args.output,manifest)
 if out.exists():raise FileExistsError('Evaluation output exists; choose a NEW directory')
 root=Path(args.results_root);pin=read(root/'experiment-config.json') if (root/'experiment-config.json').exists() else None
 results=[];summary={'physical_attempts':0,'charged_tokens':0,'live_status':'LIVE_NOT_RUN'}
 if pin:
  if pin['scope']['dataset_manifest_id']!=manifest['manifest_id']:raise ValueError('Results/dataset mismatch')
  with ExperimentStore(_safe_path(args.ledger,manifest),pin['scope'],pin['settings']) as store:
   for row in rows:
    p=build_prompt(row,manifest['config']['classification']);key=request_key(row,pin['profile'],p,pin['settings']);result=store.cached(key)
    if result:results.append(result)
   summary=store.summary()
 labels=None
 if args.human_labels:
  path=Path(args.human_labels)
  if path.suffix.lower()=='.csv':
   with path.open(encoding='utf-8-sig',newline='') as f:labels=list(csv.DictReader(f))
  else:labels=[json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
 metrics=evaluate_records(rows,results,labels);metrics['ledger']=summary;metrics['dataset_manifest_id']=manifest['manifest_id']
 metrics['operations']['request_attempt_count']=summary['physical_attempts']
 metrics['live_result_count']=len(results);out.mkdir(parents=True)
 write_new(out/'metrics.json',metrics);paths=export_review(rows,results,out/'human-review')
 write_new(out/'export-paths.json',{k:str(v) for k,v in paths.items()});render_report(metrics,out/'report.md')
 print(json.dumps({'status':'EVALUATED_OFFLINE','records':len(rows),'live_results':len(results),'human_metrics':metrics['human_review']['status'],'output':str(out)}));return 0
def main(argv=None):
 args=parser().parse_args(argv)
 if args.action=='prepare':
  if not 1<=args.size<=300:raise ValueError('Experiment sample must be1..300')
  manifest=prepare_dataset(read(args.context),_safe_path(args.output),args.size,args.seed)
  print(json.dumps({'status':'PREPARED','manifest_id':manifest['manifest_id'],'size':manifest['size'],'output':args.output}));return 0
 if args.action=='report':
  if Path(args.output).exists():raise FileExistsError('Report output exists')
  render_report(read(args.metrics),_safe_path(args.output));print(json.dumps({'status':'REPORT_WRITTEN','output':args.output}));return 0
 if args.action=='evaluate':return _evaluate(args)
 out=_safe_path(args.output)
 if args.action=='run' and out.exists() and not args.resume:raise ValueError('Existing output requires --resume; prior state is never reset')
 if args.action=='run' and (not 1<=args.limit<=300 or args.smoke and args.limit>5):raise ValueError('Smoke≤5; experiment≤300')
 cfg=_settings(args)
 header_path=Path(args.dataset)/'manifest.json'
 if header_path.exists():
  header=read(header_path);_safe_path(out,header);_safe_path(args.ledger,header)
 owner_path=out/'.groq-output.json'
 owner={'format':'vimfc-groq-output-1','dataset_path':str(Path(args.dataset).resolve())}
 if out.exists():
  if not owner_path.is_file() or read(owner_path)!=owner:raise ValueError('Output is not an owned Groq experiment; do not adopt historical directories')
 else:
  out.mkdir(parents=True);write_new(owner_path,owner)
 if Path(args.ledger).suffix.lower()!='.sqlite':raise ValueError('Ledger must be a separate .sqlite file, never dotenv/config')
 prior_pin=read(out/'experiment-config.json') if (out/'experiment-config.json').exists() else None
 if prior_pin:
  if prior_pin['source_config_hash']!=digest(read(args.config)):raise ValueError('Source experiment config changed')
  if getattr(args,'mode',None) is None:cfg['mode']=prior_pin['settings']['mode']
  if args.model is None and cfg['model_id'] is None:cfg['model_id']=prior_pin['settings']['model_id']
 invocation=str(uuid.uuid4());started=time.time();receipt={'format':'vimfc-groq-invocation-1','invocation_id':invocation,'action':args.action,
  'started_at':datetime.now(timezone.utc).isoformat(),'model_id':None,'physical_attempts':0,'article_responses':0,'status':'STARTING'}
 store=None;client=None;initial=0;profile=None
 try:
  key=load_credentials(Path(args.env_file))
  if key is None:raise Pause('LIVE_NOT_RUN_MISSING_API_KEY')
  manifest=read(Path(args.dataset)/'manifest.json');_safe_path(out,manifest);_safe_path(args.ledger,manifest)
  if args.action=='run':validate_free_confirmation(cfg.get('free_tier_confirmed'))
  if args.action=='run' and args.limit>5 and not (out/'smoke.json').exists():raise Pause('SMOKE_NOT_PASSED')
  rows=load_dataset(Path(args.dataset))
  scope={'format':cfg['version'],'output':str(out),'dataset_manifest_id':manifest['manifest_id'],'settings_hash':digest(cfg)}
  pin_path=out/'experiment-config.json';prior=read(pin_path) if pin_path.exists() else None
  if prior and (prior['scope']!=scope or prior['settings']!=cfg):raise ValueError('Pinned experiment config changed; do not reset budgets')
  with ExperimentStore(args.ledger,scope,cfg) as store,store.lease(),GroqClient(key,timeout=cfg['timeout_seconds']) as client:
   initial=store.summary()['physical_attempts'];metadata=_metadata(client,store,invocation,cfg)
   profile=select_model(metadata['models'],prior['profile']['model_id'] if prior else cfg['model_id'])
   if prior:
    if any(profile[k]!=prior['profile'][k] for k in ('model_id','response_mode','request_kwargs')):raise Pause('MODEL_PROFILE_CHANGED')
    profile=prior['profile']
   receipt.update(model_id=profile['model_id'],model_profile=profile,availability=metadata)
   caps=cfg['model_limit_caps'].get(profile['model_id'])
   if not caps:raise Pause('MODEL_LIMITS_UNVERIFIED')
   effective={**cfg,**{k:min(cfg[k],caps[k]) for k in ('rpm','rpd','tpm','tpd')}};store.limits=effective
   if args.action=='preflight':
    receipt['status']='MODELS_VERIFIED_FREE_TIER_UNCONFIRMED'
    try:receipt['free_tier_confirmation']=validate_free_confirmation(cfg.get('free_tier_confirmed'));receipt['status']='PREFLIGHT_PASS'
    except Pause:pass
   else:
    pin={'scope':scope,'settings':cfg,'profile':profile,'effective_limits':effective,'dataset_manifest_id':manifest['manifest_id'],'source_config_hash':digest(read(args.config))}
    if prior is None:write_new(pin_path,pin)
    classifier=GroqAssistedClassifier(store,client,profile,manifest['config']['classification'],effective);classifier.invocation_id=invocation;classifier.started=started
    proof_path=out/'smoke.json'
    if args.limit>5:
     proof=read(proof_path)
     if proof['status']!='PASS' or proof['scope']!=scope or proof['model_id']!=profile['model_id'] or not proof['request_keys']:raise Pause('SMOKE_NOT_PASSED')
     for request in proof['request_keys']:
      cached=store.cached(request)
      if not cached or cached['status']!='valid' or cached['usage'] is None:raise Pause('SMOKE_NOT_PASSED')
    outputs=[]
    for row in rows[:args.limit]:
     result=classifier.classify(row,mode=cfg['mode']);outputs.append(result)
     receipt['article_responses']=len(outputs)
    if args.smoke:
     generated=[r for r in outputs if r['status']!='skipped_verified']
     if not generated or any(r['status']!='valid' or r['usage'] is None for r in generated):raise Pause('SMOKE_VALIDATION_FAILED')
     proof={'status':'PASS','scope':scope,'model_id':profile['model_id'],'request_keys':[r['request_key'] for r in generated]}
     if proof_path.exists():
      if read(proof_path)!=proof:raise ValueError('Existing smoke proof differs')
     else:write_new(proof_path,proof)
    receipt['status']='SMOKE_PASS' if args.smoke else 'EXPERIMENT_BOUNDED_COMPLETE'
   receipt['physical_attempts']=store.summary()['physical_attempts']-initial;receipt['ledger']=store.summary()
 except (Pause,ProviderError) as error:
  receipt['status']=error.code
  if getattr(error,'retry_at',None):receipt['retry_at_unix']=error.retry_at
 except (Exception,KeyboardInterrupt) as error:
  receipt['status']='EXPERIMENT_STOPPED';receipt['error_type']=type(error).__name__
  raise
 finally:
  if store is not None:
   # Context manager is already closed; reopen only our known experiment ledger
   # to retain failure costs without constructing a corpus store.
   with ExperimentStore(args.ledger,scope,cfg) as final:
    receipt['ledger']=final.summary();receipt['physical_attempts']=receipt['ledger']['physical_attempts']-initial
  receipt['ended_at']=datetime.now(timezone.utc).isoformat();receipt['runtime_seconds']=time.time()-started
  write_new(out/'invocations'/(invocation+'.json'),receipt)
 print(json.dumps({k:receipt[k] for k in ('status','model_id','physical_attempts','article_responses')}))
 return 0 if receipt['status'] in ('PREFLIGHT_PASS','SMOKE_PASS','EXPERIMENT_BOUNDED_COMPLETE') else 5
