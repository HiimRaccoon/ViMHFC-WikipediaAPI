"""Offline-only Human Review Preparation/import; never calls Groq or resumes work."""
import argparse,json,sys
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from vimfc.experiments.groq_classification.human_review import prepare_from_exports,import_and_report

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='action',required=True)
 p=subs.add_parser('prepare')
 for name in ('dataset','results','review-export','dataset-manifest','output'):p.add_argument('--'+name,required=True)
 p.add_argument('--seed',type=int,default=20261030)
 p=subs.add_parser('import')
 for name in ('bundle','labels','manifest-id','output'):p.add_argument('--'+name,required=True)
 args=parser.parse_args(argv)
 if args.action=='prepare':
  m=prepare_from_exports(args.dataset,args.results,args.review_export,args.output,seed=args.seed,expected_dataset_manifest=args.dataset_manifest)
  print(json.dumps({'status':'REVIEW_READY','records':len(m['selection']['members']),'manifest_id':m['manifest_id'],'output':args.output}))
 else:
  r=import_and_report(args.bundle,args.labels,args.output,expected_manifest_id=args.manifest_id)
  print(json.dumps({'status':r['status'],'reviewed':r['reviewed_count'],'selected':r['selected_count'],'output':args.output,'population_accuracy_claim':False}))
 return 0

if __name__=='__main__':
 def offline(*args,**kwargs):raise RuntimeError('Human review commands are offline-only')
 try:
  with patch('socket.socket.connect',offline),patch('socket.socket.connect_ex',offline),patch('socket.getaddrinfo',offline):
   raise SystemExit(main())
 except (Exception,KeyboardInterrupt) as e:
  print(json.dumps({'status':'REVIEW_STOPPED','error_type':type(e).__name__}),file=sys.stderr);raise SystemExit(3)
