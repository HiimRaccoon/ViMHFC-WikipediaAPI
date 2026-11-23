"""Explicit FAST-20K entrypoint. No scheduler submission or automatic retry."""
import argparse
from contextlib import contextmanager
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend/src'))
from vimfc.domain import Incompatible,canonical
from vimfc.pipeline.fast20k import load,run,validate_profile
from vimfc.pipeline.fast20k_corpus import publish
from vimfc.pipeline.incremental_collection import _publish_json
from vimfc.pipeline.qwen_fallback import QwenFallback
from vimfc.source.dump_download import exclusive


def install_signals():
    state={'stop':False}
    def stop(signum,frame):state['stop']=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    return lambda:state['stop']


def environment(parent,*,require_ext4=True):
    if sys.version_info[:2]!=(3,12):raise Incompatible('FAST-20K runtime must be Python 3.12')
    if sqlite3.sqlite_version_info<(3,35):raise Incompatible('SQLite 3.35+ required')
    packages={}
    for line in (ROOT/'backend/requirements.lock').read_text().splitlines():
        if not line or line.startswith('#'):continue
        name,wanted=line.split('==');packages[name]=version(name)
        if packages[name]!=wanted:raise Incompatible('Locked dependency differs: '+name)
    parent=Path(parent).resolve()
    fs=subprocess.check_output(['findmnt','-n','-o','FSTYPE','-T',str(parent)],text=True).strip() if sys.platform=='linux' else platform.system()
    if require_ext4 and fs!='ext4':raise Incompatible('Active workspace requires verified node-local ext4')
    usage=shutil.disk_usage(parent)
    if usage.free<40*1024**3:raise Incompatible('At least 40 GiB free required for active workspace')
    return dict(python=sys.version,sqlite=sqlite3.sqlite_version,platform=platform.platform(),
                filesystem=fs,free_bytes=usage.free,packages=packages)


@contextmanager
def model_service(config,runtime,model,logs):
    """An optional CPU child, bound to loopback, always stopped by its owning invocation."""
    process=None;client=None;stream=None
    try:
        model=Path(model)
        with model.open('rb') as f:checksum=hashlib.file_digest(f,'sha256').hexdigest()
        if checksum!=config['model_sha256']:raise Incompatible('Qwen artifact SHA-256 differs')
        if config['endpoint']!='http://127.0.0.1:18865':raise Incompatible('Unexpected Qwen listen endpoint')
        stream=(Path(logs)/'qwen-server.log').open('ab')
        process=subprocess.Popen([str(runtime),'-m',str(model),'--host','127.0.0.1','--port','18865',
            '--alias',config['base_model'],
            '--ctx-size','4096','--threads','3','--threads-batch','3','--parallel','1',
            '--n-gpu-layers','0','--reasoning','off','--cache-ram','0','--no-webui','--no-warmup'],
            stdout=stream,stderr=subprocess.STDOUT,env={**os.environ,'CUDA_VISIBLE_DEVICES':''})
        import httpx
        with httpx.Client(trust_env=False,timeout=2) as probe:
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('Qwen server exited: '+str(process.returncode))
                try:
                    if probe.get(config['endpoint']+'/health').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(1)
            else:raise TimeoutError('Qwen initialization exceeded 120 seconds')
        client=QwenFallback(config)
        status=dict(status='READY',model_id=config['model_id'],model_revision=config['model_revision'],
                    model_sha256=checksum,runtime=config['runtime'],cpu_only=True)
    except Exception as error:
        status=dict(status='UNAVAILABLE',error_type=type(error).__name__,error=str(error))
    try:yield client,status
    finally:
        if client:client.close()
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        if stream:stream.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['run','cache-smoke','qwen-smoke','preflight'])
    p.add_argument('--profile',default=str(ROOT/'configs/fast20k.json'))
    p.add_argument('--bundle');p.add_argument('--workspace');p.add_argument('--durable');p.add_argument('--output')
    p.add_argument('--runtime');p.add_argument('--model');p.add_argument('--logs',required=True)
    p.add_argument('--allow-network',action='store_true')
    args=p.parse_args(argv);profile=load(args.profile);validate_profile(profile)
    logs=Path(args.logs).resolve();logs.mkdir(parents=True,exist_ok=True)
    stop=install_signals()
    if args.action=='preflight':
        result=environment(Path(args.workspace).parent)
        _publish_json(logs/'environment.json',result);print(canonical(result));return 0
    if args.action=='qwen-smoke':
        with model_service(profile['qwen'],args.runtime,args.model,logs) as (qwen,result):
            if qwen:
                suggestion,raw=qwen.classify(dict(title='Marie Curie',extraction=dict(
                    full_lead='Marie Curie là nhà vật lý và hóa học, sinh năm 1867, đoạt giải Nobel Vật lý năm 1903.')))
                result.update(suggestion=suggestion,raw=raw)
                if suggestion['suggested_primary_entity_type']!='Person':result['status']='UNAVAILABLE'
            _publish_json(logs/'qwen-smoke.json',result);print(canonical(result));return 0 if result['status']=='READY' else 3
    for key in ('bundle','workspace','durable','output'):
        if getattr(args,key) is None:p.error('--'+key+' required')
    _publish_json(logs/'environment.json',environment(Path(args.workspace).parent))
    if args.action=='run':
        if args.allow_network and os.environ.get('FAST20K_EGRESS_APPROVED')!='1':
            raise Incompatible('Compute egress approval/viability not established')
    else:
        if args.allow_network:raise Incompatible('Cached smoke never permits source HTTP')
        os.environ['VIMFC_OFFLINE']='1'
    limited={'stop':False}
    def checkpointed(receipt):
        if args.action=='cache-smoke':limited['stop']=True
    def execute(qwen):
        return run(args.bundle,args.workspace,args.durable,profile,allow_network=args.allow_network,
            qwen=qwen,stop_requested=lambda:stop() or limited['stop'],on_checkpoint=checkpointed)
    if args.action=='run' and args.runtime and args.model:
        with model_service(profile['qwen'],args.runtime,args.model,logs) as (qwen,status):
            _publish_json(logs/'qwen-status.json',status);result=execute(qwen)
    else:result=execute(None)
    if result['stop_reason']=='TARGET_REACHED':
        with exclusive(Path(args.durable)/'.campaign.lock'):
            output=Path(args.output)
            if output.exists():
                manifest=load(output/'manifest.json')
                if manifest['binding_id']!=result['binding_id']:raise Incompatible('Existing publication differs')
                for name,info in manifest['files'].items():
                    with (output/name).open('rb') as f:
                        if hashlib.file_digest(f,'sha256').hexdigest()!=info['sha256']:raise Incompatible('Published artifact differs')
            else:manifest=publish(args.workspace,output)
            result.update(corpus_complete=True,corpus_version=manifest['corpus_version'])
            _publish_json(Path(args.durable)/'progress.json',result)
            _publish_json(Path(args.workspace)/'progress.json',result)
    _publish_json(logs/'result.json',result);print(canonical(result))
    return dict(TARGET_REACHED=0,CHECKPOINT_STOP=130,REQUEST_BUDGET_EXHAUSTED=5,
                CANDIDATE_EXHAUSTED=2,SOURCE_COOLDOWN=75,INTEGRITY_FAILURE=3,OPERATIONAL_ERROR=75)[result['stop_reason']]


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        print(canonical(dict(status='FAILED',error_type=type(error).__name__,error=str(error),corpus_complete=False)),file=sys.stderr)
        sys.exit(3)
