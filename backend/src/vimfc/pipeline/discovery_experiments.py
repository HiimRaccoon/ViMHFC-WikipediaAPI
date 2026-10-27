"""Isolated, versioned discovery experiments; preparation never contacts sources."""
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib,json,shutil,sqlite3,sys
from pathlib import Path
from vimfc.domain import canonical,digest,Incompatible,now
from vimfc.config import load_config
from vimfc.storage.pipeline_store import PipelineStore
from .discovery_policy import VERSION,verify_cohort
from .pairs import build_pairs

def offline_metrics(plan,records,ranking):
    ids={c['page_id'] for c in plan['candidates']}
    scope_unknown={c['page_id'] for c in plan['candidates'] if c.get('discovery_namespace_status')=='unverified'}
    known={p:r for p,r in records.items() if p in ids and p not in scope_unknown}
    eligible={p:r for p,r in known.items() if r['quality']['eligible']};eids=set(eligible)
    pairs=build_pairs(list(eligible.values()),ranking).pairs
    edges={(p,l['target']['page_id']) for p,r in eligible.items() for l in r['extraction']['links']
        if l.get('scope')=='retained_lead_prose' and l.get('target',{}).get('status')=='resolved' and l['target']['page_id'] in eids and l['target']['page_id']!=p}
    adjacent=defaultdict(set)
    for a,b in edges:adjacent[a].add(b);adjacent[b].add(a)
    remaining=set(eids);sizes=[]
    while remaining:
        todo=[min(remaining)];remaining.remove(todo[0]);size=0
        while todo:
            p=todo.pop();size+=1
            for n in adjacent.get(p,set())&remaining:remaining.remove(n);todo.append(n)
        sizes.append(size)
    blocks=Counter(tuple(p['provenance'][0]['block']) for p in pairs if p['kind']=='comparison')
    total=sum(blocks.values());targets=Counter(p['endpoint_b'] for p in pairs if p['kind']=='bridge');bt=sum(targets.values())
    groups=Counter(r['classification']['primary_entity_type'] for r in eligible.values());reporting=Counter()
    for group,count in groups.items():reporting[plan['policy']['reporting_groups'][group] or 'UNMAPPED: '+group]+=count
    unknown=len(ids)-len(known)
    return {'variant':plan['variant'],'manifest_id':plan['manifest_id'],'candidate_budget':plan['candidate_budget'],
        'selected_candidates':len(ids),'cohort_shortfall':plan['shortfall'],'slot_counts':plan['slot_counts'],'seed_fallback':plan['seed_fallback'],
        'known_processed_inputs':len(known),'unknown_source_inputs':unknown,'eligible_observed':len(eids),
        'namespace_unverified_candidates':len(scope_unknown),
        'eligible_lower_bound':len(eids)/len(ids) if ids else None,'eligible_yield_complete':None if unknown or plan['shortfall'] else len(eids)/len(ids),
        'primary_groups':dict(groups),'reporting_groups':dict(reporting),'sampling_strata':dict(Counter(s for c in plan['candidates'] for s in c['expected_strata'])),
        'lead_graph':{'vertices':len(eids),'directed_nonself_edges':len(edges),'nonisolated_vertices':len(adjacent),
            'nonisolated_fraction':len(adjacent)/len(eids) if eids else None,'weak_components':len(sizes),'largest_component':max(sizes,default=0)},
        'pairs':{'bridge':bt,'comparison':total,'endpoint_coverage':len({p[k] for p in pairs for k in ('endpoint_a','endpoint_b')}),
            'comparison_blocks':[{'block':list(k),'count':v} for k,v in sorted(blocks.items())],
            'comparison_block_hhi':sum((v/total)**2 for v in blocks.values()) if total else None,
            'bridge_target_hhi':sum((v/bt)**2 for v in targets.values()) if bt else None},
        'physical_api_requests':0,'live_api_cost':None,'human_valid_two_hop_yield':None,
        'scope':'Offline known-input subset. Unknown candidates are not ineligible; no new source acquisition, pilot or population yield claim.'}

def cache_baseline(root):
    files=[]
    for path in sorted(Path(root).rglob('*')):
        if not path.is_file():continue
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
        files.append([path.relative_to(root).as_posix(),path.stat().st_size,h.hexdigest()])
    return {'baseline_id':digest(files),'files':len(files),'bytes':sum(r[1] for r in files)}

def prepare_experiment(context,config,inventory,plan,output_dir,*,source_review=None,private_cache=False):
    verify_cohort(plan,inventory)
    if plan['shortfall'] or plan['candidate_budget']!=1000 or len(plan['candidates'])!=1000:
        raise Incompatible('An exact frozen1000-candidate plan is required')
    if inventory['snapshot']!=context['snapshot'] or inventory['semantic_rules']!={k:digest(config[k]) for k in ('classification','extraction','ranking')}:
        raise Incompatible('Discovery cannot change snapshot or semantic rules')
    source_db=Path(context['database']).resolve();output=Path(output_dir).resolve();raw_root=Path(context['raw_cache']).resolve()
    if output.exists() or output==source_db.parent or output.is_relative_to(raw_root) or output.is_relative_to(source_db.parent):
        raise Incompatible('Use a NEW isolated output directory outside historical database/raw directories')
    cfg=deepcopy(config);cfg['version']='pipeline-7'
    cfg['discovery']={'version':VERSION,'variant':plan['variant'],'inventory_id':inventory['inventory_id'],'manifest_id':plan['manifest_id'],'cohort_hash':plan['cohort_hash'],'namespace_policy':'historical-title-namespace-1'}
    approval=None
    with sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True) as source:
        source.row_factory=sqlite3.Row
        run=source.execute('SELECT * FROM pipeline_runs WHERE pipeline_run_id=?',(context['run_id'],)).fetchone()
        if not run or run['snapshot']!=context['snapshot'] or run['config_hash']!=context['config_hash'] or json.loads(run['config_json'])!=config:
            raise Incompatible('Source context/config changed')
        if source_review:
            from .quarantine import ReviewedIntegrityQuarantine
            gate=ReviewedIntegrityQuarantine.from_file(source_review,dict(run));approval=deepcopy(gate.record)
            approval['revisions'][0]['evidence_path']=str((Path(source_review).resolve().parent/approval['revisions'][0]['evidence_path']).resolve())
        output.mkdir(parents=True,exist_ok=False);database=output/'pipeline.sqlite'
        with sqlite3.connect(database) as target:source.backup(target)
    encoded=deepcopy(cfg)
    for key in ('classification','extraction','ranking'):
        (output/(key+'_rules.json')).write_text(canonical(encoded.pop(key))+'\n',encoding='utf-8')
        encoded[key+'_file']=key+'_rules.json'
    config_path=output/'pipeline.json';config_path.write_text(canonical(encoded)+'\n',encoding='utf-8')
    (output/'inventory.json').write_text(canonical(inventory)+'\n',encoding='utf-8')
    (output/'cohort-manifest.json').write_text(canonical(plan)+'\n',encoding='utf-8')
    with PipelineStore(database) as store:
        run_id=store.create(context['snapshot'],cfg,reuse_run=context['run_id'])
        if store.candidates(run_id):raise Incompatible('Incompatible discovery unexpectedly inherited candidates')
        store.db.executemany('INSERT INTO candidates VALUES (?,?,?,?)',[(run_id,c['page_id'],i,canonical(c)) for i,c in enumerate(plan['candidates'])])
        store.event(run_id,'configuration_source',{'path':str(config_path)})
        store.event(run_id,'discovery_cohort',{'manifest_id':plan['manifest_id'],'inventory_id':inventory['inventory_id'],'version':VERSION})
        # Carry future negative-hint TTLs, not semantic derived results. This
        # prevents a new discovery run from bypassing source etiquette.
        cols=[r[1] for r in store.db.execute('PRAGMA table_info(stage_results)')];tail=','.join('s.'+name for name in cols[1:])
        store.db.execute(f"INSERT INTO stage_results({','.join(cols)}) SELECT ?,{tail} FROM stage_results s JOIN candidates c ON c.run_id=? AND c.canonical_page_id=CAST(s.identity AS INTEGER) WHERE s.run_id=? AND s.stage='item_hint_refresh' AND s.status='deferred' AND s.next_retry_at>? AND json_extract(s.error_json,'$.code')='missing_item_hint'",(run_id,run_id,context['run_id'],now()))
        store.db.execute("INSERT INTO candidate_work SELECT ?,w.page_id,w.status,w.record_json,w.next_retry_at,w.attempt_count,w.updated_at FROM candidate_work w JOIN candidates c ON c.run_id=? AND c.canonical_page_id=w.page_id WHERE w.run_id=? AND w.status='deferred' AND w.next_retry_at>? AND json_extract(w.record_json,'$.error_code')='missing_item_hint'",(run_id,run_id,context['run_id'],now()))
    baseline={'mode':'shared','baseline_id':None,'cost_comparison':'uncontrolled cache warm-up across arms'}
    if private_cache:
        baseline={**cache_baseline(raw_root),'mode':'private-copy'}
        private_root=output/'raw-cache';shutil.copytree(raw_root,private_root)
        if cache_baseline(private_root)['baseline_id']!=baseline['baseline_id']:
            raise Incompatible('Raw cache changed while creating private baseline; do not run this preparation')
        raw_root=private_root
    result={'run_id':run_id,'database':str(database),'config':str(config_path),'config_hash':digest(cfg),'raw_cache':str(raw_root),
        'snapshot':context['snapshot'],'reports':str(output/'reports'),'cohort_hash':plan['cohort_hash'],'cohort_count':1000,
        'source_run':context['run_id'],'source_database':str(source_db),'discovery_manifest':str(output/'cohort-manifest.json'),
        'discovery_inventory':str(output/'inventory.json'),'variant':plan['variant'],'python':sys.executable,'cache_baseline':baseline}
    if approval:
        approval.update(run_id=run_id,config_hash=result['config_hash']);approval['review']['reviewed_by']=''
        approval['review']['reason']='DRAFT: requires explicit human review for this new run/config; not active permission'
        path=output/'quarantine-review-DRAFT.json';path.write_text(canonical(approval)+'\n',encoding='utf-8')
        result['quarantine_review_draft']=str(path)
    context_path=output/'run-context.json';context_path.write_text(canonical(result)+'\n',encoding='utf-8')
    return {**result,'context':str(context_path)}

def validate_experiment(context,store):
    inventory=json.loads(Path(context['discovery_inventory']).read_text(encoding='utf-8'))
    plan=json.loads(Path(context['discovery_manifest']).read_text(encoding='utf-8'));verify_cohort(plan,inventory)
    run=store.run(context['run_id']);config=load_config(context['config'])
    if (run['config_hash']!=context['config_hash'] or digest(config)!=context['config_hash'] or run['snapshot']!=context['snapshot']
        or store.candidates(context['run_id'])!=plan['candidates'] or digest(plan['candidates'])!=context['cohort_hash']
        or run['config']['discovery']['manifest_id']!=plan['manifest_id']
        or run['config']['discovery'].get('inventory_id')!=inventory['inventory_id']
        or run['config']['discovery'].get('variant')!=plan['variant'] or context['variant']!=plan['variant']
        or run['config']['discovery'].get('namespace_policy')!='historical-title-namespace-1'
        or inventory['semantic_rules']!={k:digest(config[k]) for k in ('classification','extraction','ranking')}):
        raise Incompatible('Experiment checkpoint/config/cohort identity mismatch')
    return plan
