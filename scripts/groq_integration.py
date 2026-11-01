"""Separate read-only source shadow and bounded experimental Groq integration."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))
from vimfc.experiments.groq_classification.integration_shadow import prepare_shadow, load_shadow, replay_shadow
from vimfc.experiments.groq_classification.integration_report import write_shadow_report
from vimfc.experiments.groq_classification.review_offline import offline_review_network


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    p = subs.add_parser('prepare')
    for key in ('context', 'legacy-ledger', 'output'): p.add_argument('--' + key, required=True)
    for action in ('replay', 'run'):
        p = subs.add_parser(action)
        for key in ('dataset', 'output'): p.add_argument('--' + key, required=True)
        p.add_argument('--manifest-id')
        if action == 'run':
            p.add_argument('--config', required=True)
            p.add_argument('--env-file', default='api/.env')
            p.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        with offline_review_network():
            m = prepare_shadow(args.context, args.legacy_ledger, args.output)
        print(json.dumps({'status': 'SHADOW_PREPARED', 'manifest_id': m['manifest_id'], 'processed': m['size']}))
        return 0
    data = load_shadow(args.dataset, args.manifest_id)
    if args.action == 'replay':
        with offline_review_network():
            r = replay_shadow(data)
            write_shadow_report(r, args.output)
    else:
        from vimfc.experiments.groq_classification.integration_runner import run_integration
        r = run_integration(data, args.output, args.config, args.env_file, resume=args.resume)
    print(json.dumps({k: r[k] for k in ('counts', 'verified_eligible', 'provisional_eligible_candidates', 'pending_live_count')}))
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        # Never log raw provider messages, credentials or traceback causes.
        print(json.dumps({'status': 'INTEGRATION_STOPPED', 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(3)
