"""Run one bounded historical-target completion invocation on the frozen Pilot1000."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))

from vimfc.domain import Incompatible, canonical, digest
from vimfc.pipeline.graph_pilot import evaluate_graph_archive, write_graph_report
from vimfc.pipeline.relaxed_pilot import SNAPSHOT
from vimfc.pipeline.relaxed_preservation import snapshot_files, compare_files

RUN_ID = '80524a0f-87fe-4c01-af9f-dfa4b3951899'


def denied(*args, **kwargs):
    raise RuntimeError('Graph Pilot cache-only invocation forbids networking')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(canonical(value) + '\n')


def handoff(result, preservation, report_dir, failure=None):
    if result is None:
        return '# Technical Review Handoff\n\nStatus: BLOCKED.\n\n' + canonical(failure) + '\n'
    graph = result['graph']['both_endpoints_relaxed_eligible']
    old_graph = (result['comparisons'].get('prior_release') or {}).get('graph', {}).get('both_endpoints_relaxed_eligible', {})
    lines = ['# Technical Review Handoff', '', f"Pilot execution status: {result['status']}.", '',
             'Historical target identities were completed with checked cache first and bounded Wikimedia historical acquisition when authorized.',
             'The exact frozen cohort, snapshot, quality-relaxed-1 article decisions and archived classification labels remain unchanged.', '',
             '## Baseline comparison', '', (report_dir / 'comparison.md').read_text(encoding='utf-8'),
             '| Graph detail | Prior relaxed | Recovered |', '|---|---:|---:|',
             f"| Largest weak component | {old_graph.get('largest_weak_component', 'NOT_AVAILABLE')} | {graph['largest_weak_component']} |",
             f"| Unresolved eligible link occurrences | {old_graph.get('unresolved_link_occurrences', 'NOT_AVAILABLE')} | {graph['unresolved_link_occurrences']} |",
             f"| Isolated eligible vertices | {old_graph.get('isolated_vertices', 'NOT_AVAILABLE')} | {graph['isolated_vertices']} |", '',
             'Target identity coverage: ' + canonical(result['target_coverage']), '',
             'Directed edge additions/losses: ' + canonical({k: v for k, v in result['graph_change'].items()
                                                            if k not in ('new_edge_witnesses', 'previous_edge_witnesses')}), '',
             '## Provenance and source blockers', '',
             'edge-witnesses.json binds source revision/raw/manifest, retained Lead occurrence and the target decision hash. ',
             'target-decisions.json records verified inside/outside-cohort identities, unresolved, ambiguous and source-integrity-blocked outcomes.',
             'The recovery directory retains the acquisition/cache/checkpoint receipts; original sources and registry are read-only.', '',
             'Target states: ' + canonical(result['target_states']), '',
             'Target occurrence states: ' + canonical(result['target_occurrence_states']), '',
             'Cumulative recovery/budget/429 or other stop details: ' + canonical(result['recovery']), '',
             'Source blockers: ' + canonical(result['source_blockers']), '',
             '## Connectivity and ranking', '',
             'Weak component sizes: ' + canonical(graph['weak_component_sizes'][:20]), '',
             'Top connected clusters with membership: ' + canonical(result['graph']['connected_clusters'][:10]), '',
             'Verified distinct-vertex A→B→C examples: ' + canonical(graph['two_hop_examples'][:20]), '',
             'Top articles: ' + canonical(result['ranking'][:20]), '',
             'Reduced-capacity ablations (target=' + str(result['ablations']['diagnostic_target']) + '):', '',
             '| Variant | Selected | Edges | Non-isolated | Paths | Largest component |', '|---|---:|---:|---:|---:|---:|']
    for name, row in result['ablations']['reduced_capacity'].items():
        m = row['graph']
        lines.append(f"| {name} | {len(row['selection']['page_ids'])} | {m['directed_nonself_edges']} | {m['nonisolated_vertices']} | {m['potential_two_hop_paths']} | {m['largest_weak_component']} |")
    lines += ['', 'The target-20000 diagnostic retains all eligible supply; ranking gains require the separately labeled reduced-capacity comparisons.',
              'Existing primary provenance provides a weak tie-break proxy, not a verified semantic specificity score. ',
              'Normal pair generation supplies bridge/comparison proposals; this task adds no shared-entity detector.',
              'Selection is deterministic greedy, not a global optimum. Hub-heavy 20k scaling still needs profiling.',
              'Paths indicate annotation potential; human multi-hop claim yield remains unmeasured.', '',
              '## Verification and preservation', '',
              'Focused/full tests: NOT_RECORDED_IN_THIS_INVOCATION. Consult the final test log before claiming a passing release.',
              'Preservation: ' + canonical(preservation), '',
              'No LLM API requests, new labels, cohort expansion, production run or Git commit were performed.', '',
              '## Recommended next steps', '',
              'Review unresolved and ambiguous historical target receipts, sample real evidence chains with annotators, and profile selection before scaling.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', required=True)
    parser.add_argument('--prior-relaxed', required=True)
    parser.add_argument('--integrity-registry', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--allow-live', action='store_true', help='Authorize the single cumulative bounded online invocation')
    parser.add_argument('--max-requests', type=int, default=1200)
    parser.add_argument('--max-runtime-seconds', type=int, default=1200)
    parser.add_argument('--max-targets', type=int, default=200)
    parser.add_argument('--min-interval-seconds', type=float, default=1)
    parser.add_argument('--diagnostic-target', type=int, default=100)
    parser.add_argument('--recovery-version', choices=['historical-target-resolution-1', 'historical-target-resolution-2'],
                        default='historical-target-resolution-1')
    args = parser.parse_args()
    context = read(args.context)
    if context['snapshot'] != SNAPSHOT or context['run_id'] != RUN_ID:
        raise Incompatible('Graph Pilot CLI is restricted to the exact authorized historical source run')
    if (not 0 <= args.max_requests <= 1200 or not 0 < args.max_runtime_seconds <= 1200
            or not 0 <= args.max_targets <= 200 or args.min_interval_seconds < 1):
        raise Incompatible('Graph recovery limits exceed the authorized finite envelope')
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    protected_overlap = [Path(context['database']).resolve().parent, Path(context['raw_cache']).resolve(),
                         Path(args.prior_relaxed).resolve(), Path(args.integrity_registry).resolve().parent,
                         root / 'data/inventories', root / 'api', root / 'configs', root / 'backend', root / 'scripts']
    for other in protected_overlap:
        if output == other or output.is_relative_to(other) or other.is_relative_to(output):
            raise Incompatible('Owned graph output overlaps protected inputs')
    if args.resume:
        if not (output / 'operational-checkpoint.json').is_file():
            raise Incompatible('Resume requires a previously owned Graph Pilot output')
    elif output.exists():
        raise FileExistsError('New Graph Pilot output must not exist')
    if not args.allow_live:
        socket.socket.connect = denied
        socket.create_connection = denied
        socket.getaddrinfo = denied
    log = lambda row: print(canonical(row), flush=True)
    protected = [root / 'data', root / 'reports', root / 'configs', root / 'backend/src',
                 root / 'scripts/graph-pilot.py', context['database'], context['raw_cache'],
                 args.integrity_registry, Path(args.integrity_registry).with_name(Path(args.integrity_registry).stem + '-artifacts'),
                 args.prior_relaxed]
    protected += list((root / 'api').glob('*.sqlite*'))
    # Pin input bytes before creating any output beneath a protected root.
    before = snapshot_files(protected, exclude=[output], progress=log)
    identity = {'source_run_id': context['run_id'], 'snapshot': context['snapshot'],
                'source_config_hash': context['config_hash'], 'cohort_hash': context['cohort_hash'],
                'prior_relaxed': str(Path(args.prior_relaxed).resolve()), 'preservation_hash': digest(before)}
    checkpoint = output / 'operational-checkpoint.json'
    if args.resume:
        saved = read(checkpoint)
        if saved['identity'] != identity or read(output / 'preservation-before.json') != before:
            raise Incompatible('Protected source bytes or invocation identity changed before resume')
        if args.allow_live and saved['online_invocation_started']:
            raise Incompatible('A second automatic online invocation is forbidden; resume cache-only')
    else:
        output.mkdir(parents=True)
        write(output / 'preservation-before.json', before)
        write(checkpoint, {'identity': identity, 'online_invocation_started': False})
    if args.allow_live:
        # Reserve the online invocation before entering the recovery engine.
        saved = {'identity': identity, 'online_invocation_started': True}
        temp = output / 'operational-checkpoint.next'
        write(temp, saved); temp.replace(checkpoint)
    reports = output / 'reports'; reports.mkdir(exist_ok=True)
    index = max((int(p.name) for p in reports.iterdir() if p.is_dir() and p.name.isdigit()), default=0) + 1
    report_dir = reports / f'{index:06d}'
    started = datetime.now(timezone.utc).isoformat()
    result = None; manifest = None; failure = None
    try:
        result = evaluate_graph_archive(context, output, registry_path=args.integrity_registry,
            prior_relaxed=args.prior_relaxed, allow_live=args.allow_live,
            recovery_version=args.recovery_version,
            diagnostic_target=args.diagnostic_target,
            limits={'max_requests': args.max_requests, 'max_runtime_seconds': args.max_runtime_seconds,
                    'max_targets': args.max_targets, 'min_interval_seconds': args.min_interval_seconds}, progress=log)
        manifest = write_graph_report(result, report_dir)
    except (Exception, KeyboardInterrupt) as error:
        failure = {'error_type': type(error).__name__, 'reason': str(error)}
        report_dir.mkdir(parents=True, exist_ok=True)
    after = snapshot_files(protected, exclude=[output], progress=log)
    preservation = compare_files(before, after)
    write(report_dir / 'preservation.json', preservation)
    receipt = {'started_at': started, 'ended_at': datetime.now(timezone.utc).isoformat(),
               'status': result['status'] if result else 'BLOCKED', 'failure': failure,
               'source_context': str(Path(args.context).resolve()), 'prior_relaxed': identity['prior_relaxed'],
               'result_manifest_id': manifest['manifest_id'] if manifest else None,
               'preservation': preservation, 'allow_live': args.allow_live,
               'api_requests': result['api_requests'] if result else {'other_llm': 0}}
    write(report_dir / 'execution-receipt.json', receipt)
    (report_dir / 'Technical Review Handoff.md').write_text(handoff(result, preservation, report_dir, failure), encoding='utf-8')
    # Seal the complete owned cache/checkpoints and every report generation.
    files = snapshot_files([output], progress=log)
    audit = {'format': 'vimfc-graph-pilot-audit-1',
             'files': {Path(path).relative_to(output).as_posix(): info['sha256'] for path, info in files.items()}}
    audit['manifest_id'] = digest(audit)
    write(report_dir / 'audit-manifest.json', audit)
    log(receipt)
    return 3 if failure or not preservation['passed'] else 5 if result['status'] == 'PARTIAL' else 0


if __name__ == '__main__':
    sys.exit(main())
