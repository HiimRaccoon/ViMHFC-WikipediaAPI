"""Execute only a new offline relaxed Pilot1000; never acquires source or uses LLMs."""
import argparse
import json
from pathlib import Path
import socket
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))

from vimfc.domain import canonical
from vimfc.pipeline.relaxed_pilot import SNAPSHOT, evaluate_archive, safe_output, write_report, seal_audit
from vimfc.pipeline.relaxed_preservation import snapshot_files, compare_files


def denied(*args, **kwargs):
    raise RuntimeError('Relaxed Pilot is offline: all source/LLM networking is forbidden')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--integrity-registry', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    context = json.loads(Path(args.context).read_text(encoding='utf-8'))
    if context['snapshot'] != SNAPSHOT or context['run_id'] != '80524a0f-87fe-4c01-af9f-dfa4b3951899':
        raise ValueError('This invocation is restricted to the exact authorized historical source run')
    output = safe_output(args.output, [Path(context['database']).parent, root / 'data/inventories',
                                      root / 'data/integrity', root / 'api'])
    socket.socket.connect = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    log = lambda row: print(canonical(row), flush=True)
    protected = [root / 'data', root / 'reports', root / 'configs', root / 'backend/src']
    protected += list((root / 'api').glob('*.sqlite*'))
    before = snapshot_files(protected, exclude=[output], progress=log)
    started = datetime.now(timezone.utc).isoformat()
    failure = None
    try:
        result = evaluate_archive(context, registry_path=args.integrity_registry, progress=log)
        manifest = write_report(result, output)
    except (Exception, KeyboardInterrupt) as error:
        output.mkdir(parents=True, exist_ok=True)
        failure = {'error_type': type(error).__name__, 'reason': str(error)}
        result = {'status': 'BLOCKED', 'metrics': None, 'baseline_metrics': None,
                  'api_requests': {'wikimedia': 0, 'other_llm': 0}}
        manifest = {'manifest_id': None}
    after = snapshot_files(protected, exclude=[output], progress=log)
    preservation = compare_files(before, after)
    (output / 'preservation-before.json').write_text(canonical(before) + '\n', encoding='utf-8')
    (output / 'preservation.json').write_text(canonical(preservation) + '\n', encoding='utf-8')
    receipt = {'started_at': started, 'ended_at': datetime.now(timezone.utc).isoformat(), 'source_context': str(Path(args.context).resolve()),
               'status': result['status'], 'metrics': result['metrics'], 'baseline_metrics': result['baseline_metrics'],
               'manifest_id': manifest['manifest_id'], 'preservation': preservation,
               'api_requests': result['api_requests'], 'network_denied': True, 'pilot_accepted': False,
               'failure': failure}
    (output / 'execution-receipt.json').write_text(canonical(receipt) + '\n', encoding='utf-8')
    seal_audit(output)
    log(receipt)
    return 3 if not preservation['passed'] or result['status'] == 'BLOCKED' else 5 if result['status'] == 'PARTIAL' else 0


if __name__ == '__main__':
    sys.exit(main())
