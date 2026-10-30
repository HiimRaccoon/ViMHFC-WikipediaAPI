"""Offline only: inspect a pinned captured response; never import/fetch source data."""
import argparse
import json
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend/src'))
from vimfc.domain import SourceRevisionRef
from vimfc.source.integrity_diagnostics import inspect_revision_response


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('response','response-sha256','qid','timestamp','snapshot','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--page-id',type=int,required=True)
    parser.add_argument('--revision-id',type=int,required=True)
    args=parser.parse_args()
    source=Path(args.response).resolve();output=Path(args.output).resolve()
    if output.exists() or output==source:raise ValueError('Output must be a NEW file distinct from the input')
    def denied(*a,**k):raise AssertionError('Integrity inspection is strictly offline')
    socket.socket.connect=denied
    ref=SourceRevisionRef('wikidata',args.qid,args.page_id,args.qid,args.revision_id,args.timestamp,args.snapshot)
    result=inspect_revision_response(source.read_bytes(),ref,expected_response_sha256=args.response_sha256)
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(output),'status':result['status'],'trusted_historical_evidence':False}))


if __name__=='__main__':main()
