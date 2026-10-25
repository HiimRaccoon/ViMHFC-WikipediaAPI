"""Prepare an immutable replay bundle offline, from an actual archived run."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from vimfc.config import load_config
from vimfc.domain import InvalidConfig,Incompatible,canonical,digest


def prepare_replay(database,source_report,current_config,output_dir):
    database=Path(database).resolve()
    report=json.loads(Path(source_report).read_text(encoding='utf-8-sig'))
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT snapshot,config_json,config_hash FROM pipeline_runs WHERE pipeline_run_id=?',(report['run_id'],)).fetchone()
    if not row:
        raise InvalidConfig('Source report run is not in this database')
    snapshot,config_json,config_hash=row
    archived=json.loads(config_json)
    if report.get('size')!=100 or report.get('snapshot')!=snapshot or report.get('config_hash')!=config_hash or digest(archived)!=config_hash:
        raise Incompatible('Source Pilot 100 report/run fingerprint mismatch')
    current=load_config(current_config)
    prepared=deepcopy(archived)
    for key in ('min_tokens','min_sentences','factual_rules'):
        if current['extraction'][key]!=archived['extraction'][key]:
            raise Incompatible('Replay repair must preserve eligibility/factual rules')
    if current['classification']['roots']!=archived['classification']['roots'] or current['classification']['precedence']!=archived['classification']['precedence']:
        raise Incompatible('Replay repair must preserve classification taxonomy/precedence')
    prepared.update({key:current[key] for key in ('extraction','classification','version')})
    output=Path(output_dir).resolve()
    if output.exists():
        raise InvalidConfig('Replay bundle already exists; choose a new output directory')
    output.mkdir(parents=True)
    for key in ('extraction','classification','ranking'):
        (output/(key+'_rules.json')).write_text(canonical(prepared.pop(key))+'\n',encoding='utf-8')
        prepared[key+'_file']=key+'_rules.json'
    config_path=output/'pipeline.json'
    config_path.write_text(canonical(prepared)+'\n',encoding='utf-8')
    return {'source_run':report['run_id'],'source_report':str(Path(source_report).resolve()),'snapshot':snapshot,
        'database':str(database),'config':str(config_path),'config_hash':digest(load_config(config_path)),'candidates':100}
