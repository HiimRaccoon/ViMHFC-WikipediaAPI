"""Official metadata acquisition only: no pipeline run creation or article fetch."""
import json
from pathlib import Path
import shutil
import time
import uuid
from email.utils import parsedate_to_datetime
import math
import httpx
from vimfc.domain import Incompatible,canonical,digest,now
from vimfc.source.dump_download import BASE,Downloader,exclusive,hashes,publish_new
from vimfc.source.dump_download import select_artifacts
from vimfc.source.dump_inventory import DumpInventory,SNAPSHOT
from .report import ResourceSampler


def publish_inventory(root,inventory,verification):
    from .discovery_policy import verify_inventory
    verify_inventory(inventory)
    data=canonical(inventory).encode('utf-8')
    import hashlib
    manifest={'version':'dump-inventory-1','inventory_id':inventory['inventory_id'],
        'discovery_version':'seed-only-2','snapshot':inventory['snapshot'],
        'config_hash':inventory.get('source',{}).get('config_hash'),
        'semantic_rules':inventory.get('semantic_rules'),
        'inventory_sha256':hashlib.sha256(data).hexdigest(),'inventory_bytes':len(data),
        'candidate_count':len(inventory['seeds']),'ordered_page_ids_hash':digest([r['page_id'] for r in inventory['seeds']]),
        'verification':verification,'metadata_is_historical_evidence':False}
    publish_new(Path(root)/'inventory.json',data)
    # Manifest is the final seal; an orphan inventory without it is not published.
    publish_new(Path(root)/'manifest.json',canonical(manifest).encode('utf-8'))
    return manifest


def acquire(config,output,*,target=100000,client_factory=None,progress=None):
    if not 60000<=target<=100000:raise Incompatible('Inventory target60000..100000')
    output=Path(output).resolve()
    approved_root=Path('data/inventories').resolve()
    if not output.is_relative_to(approved_root) or not output.name.startswith('dump-'):
        raise Incompatible('New data/inventories/dump-* directory required')
    output.mkdir(parents=True,exist_ok=True)
    attempt=output/'attempts'/str(uuid.uuid4());attempt.mkdir(parents=True)
    result={'version':'dump-inventory-1','started_at':now(),'snapshot':SNAPSHOT,'target':target,
        'completed':False,'data_complete':False,'pilot_accepted':False,'pilot_invocations':0}
    mark=time.perf_counter();metadata=output/'official';metadata.mkdir(exist_ok=True)
    with ResourceSampler(output,1) as sampler:
        try:
            with exclusive(output/'acquisition.lock'):
                if shutil.disk_usage(output).free<8*1024**3:raise Incompatible('Need8GiB free metadata/import headroom')
                user_agent=config.get('user_agent')
                if not user_agent or not config.get('contact'):raise Incompatible('Configured contact/User-Agent required')
                with (client_factory() if client_factory else httpx.Client(headers={'User-Agent':user_agent,'Accept-Encoding':'identity'},timeout=60,follow_redirects=False)) as client:
                    # Pin verified manifest bytes once; restart never changes date/source.
                    meta_state=metadata/'requests.json'
                    meta_host=json.loads(meta_state.read_text()) if meta_state.exists() else {'attempts':0,'retry_at':0,'receipts':[]}
                    meta_count=meta_host['attempts']
                    if (output/'source.json').exists():
                        pinned=json.loads((output/'source.json').read_text(encoding='utf-8'))
                        for name,expected in pinned['official_manifests'].items():
                            if not (metadata/name).exists() or hashes(metadata/name)!=expected:
                                raise Incompatible('Pinned official manifest changed/missing; no refetch')
                    for name in ('dumpstatus.json','viwiki-20261001-sha1sums.txt'):
                        path=metadata/name
                        if path.exists():continue
                        if meta_count>=4:raise Incompatible('Official manifest request cap exhausted')
                        if meta_host.get('retry_at',0)>time.time():raise Incompatible('Official dump host cooldown; no request')
                        meta_count+=1
                        from vimfc.source.dump_download import atomic_json
                        meta_host['attempts']=meta_count;meta_host['retry_at']=time.time()+5;atomic_json(meta_state,meta_host)
                        body=bytearray()
                        with client.stream('GET',BASE+'/viwiki/20261001/'+name) as response:
                            meta_receipt={'url':str(response.url),'status':response.status_code,'observed_at':now(),
                                'headers':{k:v for k,v in response.headers.items() if k in ('retry-after','date','server','content-length')}}
                            meta_host['receipts'].append(meta_receipt)
                            if response.status_code!=200:
                                delay=5;after=response.headers.get('retry-after')
                                if after:
                                    try:seconds=float(after)
                                    except ValueError:seconds=parsedate_to_datetime(after).timestamp()-time.time()
                                    if not math.isfinite(seconds):raise Incompatible('Invalid manifest Retry-After')
                                    delay=max(delay,seconds)
                                meta_host['retry_at']=time.time()+delay;atomic_json(meta_state,meta_host)
                                raise Incompatible(f'Official manifest HTTP{response.status_code}; stop')
                            for chunk in response.iter_bytes(65536):
                                body.extend(chunk)
                                if len(body)>2*1024**2:raise Incompatible('Oversized official manifest')
                        meta_host['retry_at']=0;atomic_json(meta_state,meta_host)
                        publish_new(path,bytes(body))
                        time.sleep(1)
                    status=json.loads((metadata/'dumpstatus.json').read_bytes())
                    specs=select_artifacts(status,(metadata/'viwiki-20261001-sha1sums.txt').read_text(encoding='utf-8'),'20261001')
                    source={'version':'dump-inventory-1','date':'20261001','wiki':'viwiki','artifacts':specs,
                        'official_manifests':{p.name:hashes(p) for p in (metadata/'dumpstatus.json',metadata/'viwiki-20261001-sha1sums.txt')},
                        'target':target,'config':{k:config[k] for k in ('quotas','streams','classification','extraction','ranking')}}
                    publish_new(output/'source.json',canonical(source).encode('utf-8'))
                    downloader=Downloader(output/'downloads',client)
                    for spec in specs:
                        if progress:progress({'event':'download_start','table':spec['table'],'bytes':spec['size']})
                        downloader.fetch(spec)
                        if progress:progress({'event':'download_verified','table':spec['table']})
                # No network client remains open during parsing/validation/publication.
                with DumpInventory(output/'metadata.sqlite',config) as store:
                    for spec in specs:
                        if progress:progress({'event':'import_start','table':spec['table']})
                        record=store.import_file(spec['table'],output/'downloads'/spec['name'],spec)
                        if progress:progress({'event':'import_complete','table':spec['table'],'rows':record['rows']})
                    if store.db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise Incompatible('Metadata SQLite integrity failure')
                    summary=store.summary();inventory=store.inventory(target=target)
                    if progress:progress({'event':'inventory_selected','unique':len(inventory['seeds'])})
                    # Repeat pure selection before freeze; do not call source for replay.
                    replay=store.inventory(target=target)
                    if replay['inventory_id']!=inventory['inventory_id']:raise Incompatible('Nondeterministic dump inventory')
                    del replay
                    namespace_conflicts=store.db.execute('SELECT count(*) FROM links l JOIN targets t ON t.id=l.target JOIN pages p ON p.id=l.page WHERE (l.kind=\'subcat\' AND p.ns!=14) OR (l.kind=\'page\' AND p.ns=14)').fetchone()[0]
                    unresolved_targets=store.db.execute('SELECT count(*) FROM links l LEFT JOIN targets t ON t.id=l.target WHERE t.id IS NULL').fetchone()[0]
                    unresolved_pages=store.db.execute('SELECT count(*) FROM links l LEFT JOIN pages p ON p.id=l.page WHERE p.id IS NULL').fetchone()[0]
                    verification={'sqlite_integrity':'ok','repeated_selection_id_match':True,
                        'unresolved_category_targets':unresolved_targets,'unresolved_category_pages':unresolved_pages,
                        'category_type_namespace_conflicts':namespace_conflicts,'source_manifest_id':digest(source)}
                    if unresolved_targets or namespace_conflicts:raise Incompatible('Unresolved/inconsistent category metadata; no freeze')
                    manifest=publish_inventory(output,inventory,verification)
                    result.update(completed=True,inventory_frozen=True,candidates=len(inventory['seeds']),
                        inventory_id=inventory['inventory_id'],manifest=manifest,source=inventory['source'],metadata_summary=summary,
                        historical_evidence_verified=False,exit_code=0)
        except (Exception,KeyboardInterrupt) as error:
            result.update(stopped=True,error={'type':type(error).__name__,'message':str(error)},exit_code=3)
        result['ended_at']=now();result['runtime_seconds']=time.perf_counter()-mark
        publish_new(attempt/'outcome.json',canonical(result).encode('utf-8'))
    resources=sampler.report();resources.update(includes_primary_export=True,excludes_final_telemetry_flush=True)
    publish_new(attempt/'resources.json',canonical(resources).encode('utf-8'))
    result['resources']=resources;result['attempt_dir']=str(attempt)
    publish_new(attempt/'receipt.json',canonical(result).encode('utf-8'))
    return result
