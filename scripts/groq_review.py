"""Offline review UI, revisioned human labels and partitioned quality evaluation."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))

from vimfc.experiments.groq_classification.human_review import _bytes, _new, _read
from vimfc.experiments.groq_classification.review_evaluation import evaluate_review
from vimfc.experiments.groq_classification.review_offline import offline_review_network
from vimfc.experiments.groq_classification.review_packet import load_review_packet
from vimfc.experiments.groq_classification.review_prepare import prepare_review
from vimfc.experiments.groq_classification.review_store import ReviewStore, safe_review_path


def render_report(result):
    lines = ['# Groq Human Classification Quality Evaluation', '',
             f"Status: **{result['status']}**; reviewed **{result['reviewed_count']}/{result['selected_count']}**.", '',
             result['metric_policy'], '', 'No corpus promotion. No population accuracy claim.', '']
    for name, scope in result['partitions'].items():
        lines += ['## ' + name, '', f"Reviewed {scope['reviewed_count']}; pending {scope['pending_count']}; audited {scope['audited_count']}.", '']
        if scope['rq1'] is None:
            lines += ['RQ1 / RQ2 / RQ3: **UNASSESSED**, no actual human labels.', '']
            continue
        lines += ['### RQ1 — membership behavior', '',
                  '| Approach | Decided IN/OUT N | Membership precision | Membership recall | False inclusion among OUT |',
                  '|---|---:|---:|---:|---:|']
        for view, m in scope['rq1'].items():
            lines.append(f"| {view} | {m['decided_denominator']} | {m['membership_precision']} | {m['membership_recall']} | {m['false_inclusion_rate']} |")
        lines += ['', 'Abstention is not an explicit OUT prediction. Full four-way behavior and denominators are in metrics.json.', '',
                  '### RQ2 — human IN primary groups', '']
        if scope['rq2']:
            lines += ['| Approach | Human IN N | Accuracy | Precision | Recall | Macro-F1 (8 groups) | Coverage | Abstention |',
                      '|---|---:|---:|---:|---:|---:|---:|---:|']
            for view, m in scope['rq2'].items():
                lines.append(f"| {view} | {m['denominator']} | {m['accuracy']:.4f} | {m['precision']:.4f} | {m['recall']:.4f} | {m['macro_f1']:.4f} | {m['coverage']:.4f} | {m['abstention_rate']:.4f} |")
        else:
            lines += ['UNASSESSED: no reviewed human IN labels.']
        rq3 = scope['rq3']
        coverage = rq3['correct_additional_coverage']
        lines += ['', '### RQ3 — suggestions on original baseline gaps', '',
                  f"Reviewed suggestions {rq3['reviewed_suggestions']}; pending {rq3['pending_suggestions']}; semantic correct {rq3['semantic_correct']}; wrong {rq3['semantic_wrong']}; undecidable {rq3['undecidable']}.",
                  f"Historically verified suggestions {rq3['historically_verified_suggestions']}; semantic-correct without sufficient historical verification {rq3['semantic_correct_without_sufficient_historical_verification']}.",
                  f"Correct additional coverage: {coverage['numerator']}/{coverage['reviewed_article_denominator']} reviewed articles; {coverage['numerator']}/{coverage['reviewed_baseline_gap_denominator']} reviewed baseline gaps.", '',
                  'Per-reason breakdown, Science probes, support and confusion matrices are in metrics.json.', '',
                  'Small-sample groups (<5 human IN): ' + ', '.join(scope['small_sample_groups']), '', scope['warning'], '']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    prepare = subs.add_parser('prepare')
    for name in ('dataset', 'results', 'previous-bundle', 'output'):
        prepare.add_argument('--' + name, required=True)
    prepare.add_argument('--seed', type=int, default=20261031)
    for action in ('serve', 'export', 'import', 'evaluate'):
        command = subs.add_parser(action)
        command.add_argument('--profile')
        for name in ('bundle', 'workspace', 'manifest-id'):
            command.add_argument('--' + name)
        if action == 'serve':
            command.add_argument('--port', type=int, default=8765)
        if action == 'export':
            command.add_argument('--output', required=True)
            command.add_argument('--format', choices=('csv', 'jsonl'), default='jsonl')
        if action == 'import':
            command.add_argument('--labels', required=True)
        if action == 'evaluate':
            command.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    with offline_review_network():
        if args.action == 'prepare':
            manifest = prepare_review(args.dataset, args.results, args.previous_bundle, args.output, seed=args.seed)
            print(json.dumps({'status': 'REVIEW_READY', 'manifest_id': manifest['manifest_id'],
                              'records': len(manifest['selection']['members']), 'new_api_requests': 0}))
            return 0
        profile = _read(args.profile) if args.profile else {}
        if not isinstance(profile, dict) or set(profile) - {'bundle', 'workspace', 'manifest_id'}:
            raise ValueError('Unexpected review profile fields')
        values = {name: getattr(args, name) or profile.get(name) for name in ('bundle', 'workspace', 'manifest_id')}
        if not all(values.values()):
            raise ValueError('Provide bundle, workspace and manifest-id, directly or in a review profile')
        packet = load_review_packet(values['bundle'], values['manifest_id'])
        store = ReviewStore(packet, values['workspace'])
        if args.action == 'serve':
            if not 1024 <= args.port <= 65535:
                raise ValueError('Choose a localhost port from 1024 to 65535')
            import uvicorn
            from vimfc.experiments.groq_classification.review_server import create_review_app
            print(f'Human Review UI: http://127.0.0.1:{args.port} (offline, no Groq requests)', flush=True)
            uvicorn.run(create_review_app(packet, values['workspace']), host='127.0.0.1', port=args.port, log_level='warning')
        elif args.action == 'export':
            output = safe_review_path(args.output, packet)
            _new(output, store.export(args.format))
            print(json.dumps({'status': 'EXPORTED', 'reviewed': store.progress()['reviewed'], 'output': str(output)}))
        elif args.action == 'import':
            path = Path(args.labels)
            count = store.import_labels(path.read_bytes(), path.suffix.lower().lstrip('.'))
            print(json.dumps({'status': 'IMPORTED', 'imported': count, 'promotion': False}))
        else:
            output = safe_review_path(args.output, packet)
            if output.exists():
                raise FileExistsError('Use a new evaluation output directory')
            result = evaluate_review(packet, *store.snapshot())
            _new(output / 'metrics.json', _bytes(result))
            _new(output / 'report.md', render_report(result).encode('utf-8'))
            print(json.dumps({'status': result['status'], 'reviewed': result['reviewed_count'], 'output': str(output)}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'status': 'REVIEW_STOPPED', 'error_type': type(error).__name__, 'error': str(error)}), file=sys.stderr)
        raise SystemExit(3)
