"""Offline analysis only; no PipelineStore/Builder/source client is instantiated."""
import argparse
import json
from pathlib import Path
import socket
import sys
import sqlite3

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from vimfc.config import load_config
from vimfc.pipeline.retrospective import retrospective
from vimfc.pipeline.extract import extract_lead
from vimfc.pipeline.quality import assess_quality


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',required=True);p.add_argument('--run',required=True)
    p.add_argument('--config',required=True);p.add_argument('--raw-cache',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--controls',help='Optional independently labelled JSON signal controls')
    args=p.parse_args();output=Path(args.output).resolve()
    if output.exists():p.error('Output exists; historical reports must not be overwritten')
    if output.is_relative_to(Path(args.database).resolve().parent) or output.is_relative_to(Path(args.raw_cache).resolve()):
        p.error('Output cannot be inside the input database/raw-cache directory')
    def deny(*a,**kw):raise RuntimeError('Network forbidden during retrospective')
    socket.socket.connect=deny
    config=load_config(args.config)
    result=retrospective(args.database,args.run,config,args.raw_cache)
    if args.controls:
        with sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True) as db:
            old=json.loads(db.execute('SELECT config_json FROM pipeline_runs WHERE pipeline_run_id=?',(args.run,)).fetchone()[0])
        controls=json.loads(Path(args.controls).read_text(encoding='utf-8'))
        evidence=[];counts={name:{k:0 for k in ('tp','fp','tn','fn')} for name in ('before','after')}
        for case in controls['cases']:
            row={**case}
            for label,rules in (('before',old['extraction']),('after',config['extraction'])):
                sig=assess_quality(extract_lead(case['text'],rules),{'outcome':'completed'},[],rules)['signals']
                hit=bool(sig);key=('tp' if hit else 'fn') if case['expected_signal'] else ('fp' if hit else 'tn')
                counts[label][key]+=1;row[label]={'detected':hit,'signals':sig}
            evidence.append(row)
        result['signal_controls']={'scope':controls['scope'],'counts':counts,'cases':evidence}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2)
    print(json.dumps({k:result[k] for k in ('scope','cohort_count','old_extraction_replays','before','after','not_processed')},ensure_ascii=False))


if __name__=='__main__':main()
