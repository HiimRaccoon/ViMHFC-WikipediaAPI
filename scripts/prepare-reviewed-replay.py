"""Offline isolated database/config/cohort preparation; no source API client."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from vimfc.config import load_config
from vimfc.pipeline.reviewed_pilot import prepare_reviewed_replay


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source-database','source-run','raw-cache','config','output-dir','reports'):p.add_argument('--'+name,required=True)
    a=p.parse_args()
    print(json.dumps(prepare_reviewed_replay(a.source_database,a.source_run,load_config(a.config),a.raw_cache,a.output_dir,a.reports)))


if __name__=='__main__':main()
