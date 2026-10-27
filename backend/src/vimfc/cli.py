"""PowerShell-friendly commands. Source clients are loaded only for explicit work."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from .domain import VimfcError, InvalidConfig, Incompatible, InvalidCorpus, canonical, digest, now
from .config import load_config
from .storage.pipeline_store import PipelineStore


def parser():
    root=argparse.ArgumentParser(prog='vimfc',description='Historical corpus builder. No command automatically crosses pilot/production gates.')
    commands=root.add_subparsers(dest='group',required=True)
    pipeline=commands.add_parser('pipeline').add_subparsers(dest='action',required=True)
    for action in ('create','prepare-replay','pilot','run','extend','status','retry','report','freeze'):
        p=pipeline.add_parser(action)
        p.add_argument('--database',default='data/pipeline.sqlite')
        if action=='prepare-replay':
            p.add_argument('--source-report',required=True)
            p.add_argument('--config',required=True)
            p.add_argument('--output-dir',required=True)
        elif action=='create':
            p.add_argument('--snapshot',required=True)
            p.add_argument('--config',required=True)
            p.add_argument('--reuse-run')
        else:
            p.add_argument('--run',required=True)
        if action=='pilot':
            p.add_argument('--candidates',type=int,choices=(100,1000,10000),required=True)
            p.add_argument('--progress',action='store_true')
            p.add_argument('--max-wait-seconds',type=int,default=60,help='Total scheduler sleep budget (0..3600); HTTP timeouts/backoff are separate')
            p.add_argument('--retry-rounds',type=int,default=2,help='Additional due-only candidate passes (0..5)')
            p.add_argument('--deferred-only',action='store_true',help='Retry only deferred candidates; validate/reuse completed checkpoints without API recovery')
        if action=='run':
            p.add_argument('--production',action='store_true',required=True)
            p.add_argument('--pilot-report')
        if action=='extend': p.add_argument('--additional',type=int,default=10000)
        if action=='report':
            p.add_argument('--pilot-size',type=int,choices=(100,1000,10000))
            p.add_argument('--assessment')
            p.add_argument('--output')
        if action=='freeze': p.add_argument('--output',default='data/corpora')
    corpus=commands.add_parser('corpus').add_subparsers(dest='action',required=True)
    p=corpus.add_parser('verify')
    p.add_argument('--corpus',required=True)
    return root


def _print(value):
    print(canonical(value))


def _check_config_source(store,run_id):
    row=store.db.execute("SELECT record_json FROM run_events WHERE run_id=? AND stage='configuration_source' ORDER BY event_id DESC LIMIT 1",(run_id,)).fetchone()
    if row:
        path=json.loads(row[0])['path']
        try:
            config=load_config(path)
        except (OSError,ValueError,KeyError) as error:
            raise Incompatible('Original configuration is unavailable. Preserve this run and create a new run with --reuse-run.') from error
        store.require_config(run_id,config)


def main(argv=None):
    try:
        args=parser().parse_args(argv)
        if args.group=='corpus':
            from .storage.corpus_store import verify_package
            _print(verify_package(args.corpus))
            return 0
        if args.action=='prepare-replay':
            from .pipeline.replay import prepare_replay
            _print(prepare_replay(args.database,args.source_report,args.config,args.output_dir))
            return 0
        with PipelineStore(args.database) as store:
            if args.action=='create':
                cfg=load_config(args.config)
                run=store.create(args.snapshot,cfg,args.reuse_run)
                store.event(run,'configuration_source',{'path':str(Path(args.config).resolve())})
                _print({'run_id':run})
                return 0
            run=store.run(args.run)
            if args.action=='status':
                rows=store.db.execute('SELECT stage,status,count(*) AS count FROM stage_results WHERE run_id=? GROUP BY stage,status',(args.run,)).fetchall()
                progress=store.db.execute("SELECT record_json FROM run_events WHERE run_id=? AND stage='progress' ORDER BY event_id DESC LIMIT 1",(args.run,)).fetchone()
                queue=store.db.execute('SELECT status,count(*) AS count FROM candidate_work WHERE run_id=? GROUP BY status',(args.run,)).fetchall()
                cooldowns=[dict(r) for r in store.db.execute('SELECT site,retry_at FROM api_cooldowns')]
                _print({'run_id':args.run,'snapshot':run['snapshot'],'config_hash':run['config_hash'],'status':run['status'],'candidate_count':len(store.candidates(args.run)),
                    'candidate_work_counts':{r['status']:r['count'] for r in queue},'host_cooldowns':cooldowns,
                    'host_circuits':[{k:r[k] for k in ('site','state','consecutive_failures','next_retry_at','last_code','last_observed_at')} for r in store.db.execute('SELECT * FROM host_circuits')],
                    'stage_counts':[dict(r) for r in rows],'dependency_manifest_count':store.db.execute('SELECT count(*) FROM article_manifests WHERE run_id=?',(args.run,)).fetchone()[0],
                    'production_authorized':bool(run['production_report']),'progress':json.loads(progress[0]) if progress else {'speed_per_minute':None,'eta_seconds':None}})
                return 0
            if args.action!='report':
                _check_config_source(store,args.run)
            from .pipeline.runner import Builder,PilotControls
            if args.action=='retry':
                # Requeue only eligible transient failures, without running acquisition.
                updated=store.db.execute("UPDATE stage_results SET status='pending',last_error=NULL,next_retry_at=NULL,lease_until=NULL WHERE run_id=? AND status IN ('failed','pending','deferred') AND next_retry_at IS NOT NULL AND julianday(next_retry_at)<=julianday(?) AND (lease_until IS NULL OR julianday(lease_until)<=julianday(?))",(args.run,now(),now())).rowcount
                due=store.db.execute("SELECT count(*) FROM candidate_work WHERE run_id=? AND status='deferred' AND julianday(next_retry_at)<=julianday(?)",(args.run,now())).fetchone()[0]
                _print({'requeued':updated,'due_deferred_candidates':due,'message':'The same bounded pilot automatically retries due stages. This command does not start collection or bypass Retry-After/negative-hint TTL.'})
                return 0
            if args.action=='report':
                from .pipeline.report import corpus_report,template_audit,classification_audit,pair_generation_audit
                assessment=json.loads(Path(args.assessment).read_text(encoding='utf-8')) if args.assessment else None
                if args.pilot_size:
                    report=PilotControls(store,args.run).report(args.pilot_size)
                    if not report:
                        raise InvalidConfig('Requested pilot report does not exist')
                else:
                    # Include the latest pending result too; never instantiate a source provider.
                    rows=store.db.execute("SELECT identity,result_json FROM stage_results WHERE run_id=? AND stage='process' AND result_json IS NOT NULL ORDER BY rowid",(args.run,))
                    saved={r[0]:json.loads(r[1]) for r in rows}
                    quarantined=[json.loads(r[0]) for r in store.db.execute("SELECT record_json FROM candidate_work WHERE run_id=? AND status='quarantined_integrity'",(args.run,))]
                    # A preserved historical stage is not permission to revive
                    # evidence refused by a reviewed operational quarantine.
                    blocked={r['page_id'] for r in quarantined}
                    saved.update({str(r['page_id']):r for r in quarantined})
                    records=list(saved.values())
                    articles=[r for r in records if r.get('quality',{}).get('eligible')]
                    pairs=[p for r in store.db.execute('SELECT record_json FROM pair_candidates WHERE run_id=?',(args.run,))
                        if (p:=json.loads(r[0]))['endpoint_a'] not in blocked and p['endpoint_b'] not in blocked]
                    report={'run_id':args.run,'corpus':corpus_report(articles,pairs,run['config']['quotas'],assessment),'template_audit':template_audit(records,len(store.candidates(args.run))),
                        'classification_audit':classification_audit(records,len(store.candidates(args.run)),run['config']['classification']),
                        'pair_audit':pair_generation_audit(records,pairs,run['config']['ranking'])}
                    from .pipeline.manifest_report import dependency_audits
                    report['dependency_manifest_audit'],report['fingerprint_audit']=dependency_audits(records,store,run)
                    from .pipeline.manifest_report import subtype_audit
                    report['subtype_audit']=subtype_audit(records)
                    report['work_audit']={'articles':[json.loads(r[0]) for r in store.db.execute('SELECT record_json FROM candidate_work WHERE run_id=? ORDER BY page_id',(args.run,))]}
                    report['data_complete']=False
                    report['quarantined_count']=len(quarantined)
                    if quarantined:
                        report['quarantine_audit']={'quarantined_count':len(quarantined),'articles':quarantined,
                            'integrity_complete':False,'report_scope':'persisted_operational_receipts'}
                    report['pilot_accepted']=False
                    report['report_scope']='checkpoint_only_not_pilot_acceptance'
                if args.output:
                    path=Path(args.output)
                    path.parent.mkdir(parents=True,exist_ok=True)
                    Builder._atomic(path,report)
                    _print({'run_id':args.run,'output':str(path.resolve())})
                else:
                    _print(report)
                return 0
            if args.action=='freeze':
                if not run['production_report']:
                    raise InvalidConfig('Freeze of research corpus requires explicit production authorization after pilot review')
                from .pipeline.freeze import freeze
                records={int(r[0]):json.loads(r[1]) for r in store.db.execute("SELECT identity,result_json FROM stage_results WHERE run_id=? AND stage='process' AND status='completed'",(args.run,))}
                steps=[json.loads(r[0]) for r in store.db.execute('SELECT record_json FROM selection_steps WHERE run_id=? ORDER BY step',(args.run,))]
                ids=[i for step in steps for i in step['added_page_ids']]
                pairs=[json.loads(r[0]) for r in store.db.execute('SELECT record_json FROM pair_candidates WHERE run_id=? ORDER BY pair_kind,endpoint_a,endpoint_b',(args.run,))]
                path=freeze(run,[records[i] for i in ids],pairs,args.output)
                store.db.execute("UPDATE pipeline_runs SET status='frozen',updated_at=? WHERE pipeline_run_id=?",(now(),args.run))
                _print({'package':str(path)})
                return 0
            controls=PilotControls(store,args.run)
            reviewed=None
            if args.action=='pilot':
                controls.pilot(args.candidates)
            elif args.action=='run':
                if not args.pilot_report:
                    raise InvalidConfig('Missing --pilot-report; user must review final pilots and explicitly invoke production')
                reviewed=json.loads(Path(args.pilot_report).read_text(encoding='utf-8'))
                controls.production(reviewed)
            elif args.action=='extend' and not run['production_report']:
                raise InvalidConfig('Extend cannot initiate production or escape pilot bounds')
            # Contact config comes from the immutable run; never invent contact information.
            if not run['config'].get('contact'):
                raise InvalidConfig('Configure a real API contact in configs/pipeline.json and create a new run before source acquisition')
            from .source.mediawiki import MediaWikiClient
            from .storage.raw_cache import RawCache
            from .pipeline.provider import HistoricalProvider
            with MediaWikiClient(run['config']['contact'],user_agent=run['config'].get('user_agent') or 'ViMFC/0.1') as client:
                provider=HistoricalProvider(store,args.run,client,RawCache(Path(args.database).parent/'raw'))
                progress=(lambda record:print(canonical(record),file=sys.stderr,flush=True)) if getattr(args,'progress',False) else None
                builder=Builder(store,args.run,provider,progress=progress,
                    max_wait_seconds=getattr(args,'max_wait_seconds',0),retry_rounds=getattr(args,'retry_rounds',0),
                    deferred_only=getattr(args,'deferred_only',False))
                if args.action=='pilot':
                    report=builder.pilot(args.candidates)
                    result={'run_id':args.run,'size':args.candidates,'completed':report['completed'],
                        'data_complete':report['data_complete'],'pilot_accepted':report['pilot_accepted'],
                        'work_counts':report['work_counts'],'paused':report.get('paused',False),
                        'message':'STOP. Exit 5 means incomplete data; exit 0 is not pilot acceptance. Inspect reports before requesting another cohort.'}
                    if report.get('paused'):result.update(host_health=report['host_health'],health_report_path=report['health_report_path'])
                    _print(result)
                    return 0 if report['data_complete'] else 5
                elif args.action=='run':
                    _print(builder.production(reviewed))
                elif args.action=='extend':
                    _print({'candidate_count':len(builder.extend(args.additional))})
        return 0
    except VimfcError as error:
        print(str(error),file=sys.stderr)
        return error.exit_code
    except (ValueError,KeyError,TypeError) as error:
        print(f'Invalid arguments/configuration: {error}',file=sys.stderr)
        return 2
    except (OSError,sqlite3.Error) as error:
        print(f'Operational failure: {error}',file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted; completed stages are preserved. Resume the same bounded command.',file=sys.stderr)
        return 1
