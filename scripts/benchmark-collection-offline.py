"""Synthetic selection/graph benchmarks; never creates a historical run or acquires data."""
import argparse
from dataclasses import asdict
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))
import psutil
from vimfc.domain import canonical, digest
from vimfc.pipeline.hyperlink_rank import graph_metrics
from vimfc.pipeline.collection_bundle import read_bundle
from vimfc.pipeline.relaxed_preservation import file_hash


def articles(size, shape):
    rows = []
    for p in range(1, size + 1):
        group = (p - 1) // 12 * 12 + 1
        targets = [q for q in (p + 1, p + 2) if q <= min(group + 11, size)]
        if shape == 'hub':
            targets = [1] if p != 1 else list(range(2, size + 1))
        rows.append({'page_id': p, 'title': f'Synthetic {p}', 'revision_id': p + size,
            'raw_hash': digest(['synthetic', p]), 'snapshot': '2026-10-25T03:00:00Z',
            'source_validation': {'manifest_id': digest(['proof-fixture', p])},
            'quality': {'eligible': True, 'score': 60 + p % 11, 'quality_version': 'quality-relaxed-1'},
            'classification': {'outcome': 'unclassified', 'primary_entity_type': None, 'subtypes': []},
            'extraction': {'full_lead': (f'Synthetic article {p} contains fixture text only. ' * 40),
                'links': [{'scope': 'retained_lead_prose', 'edge_status': 'CANDIDATE_EDGE',
                    'edge_policy': 'annotation-edge-1', 'target_title': f'Synthetic {q}',
                    'target': {'status': 'resolved', 'page_id': q, 'revision_id': q + size,
                        'snapshot': '2026-10-25T03:00:00Z', 'raw_hash': digest(['synthetic', q]),
                        'proof': {'kind': 'synthetic_fixture'}}} for q in targets]}})
    # Match distinct strings/objects after checkpoint JSON materialization.
    return json.loads(canonical(rows))


def run(size, shape, baseline=None):
    if baseline:
        spec = importlib.util.spec_from_file_location('vimfc.pipeline._benchmark_baseline', baseline)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        select = module.select_connected
    else:
        from vimfc.pipeline.connectivity_rank import select_connected as select
    process = psutil.Process(); peak = [process.memory_info().rss]; stop = threading.Event()
    def sample():
        while not stop.wait(.01):
            peak[0] = max(peak[0], process.memory_info().rss)
    thread = threading.Thread(target=sample, daemon=True); thread.start()
    started = time.perf_counter(); rows = articles(size, shape); build = time.perf_counter() - started
    results = {'size': size, 'shape': shape, 'fixture_seconds': build}
    for name, target in [('whole_pool', 20000), ('half_pool', size // 2)]:
        started = time.perf_counter(); selection = select(rows, [], {'version': 'rank-annotation-1'}, {}, target)
        elapsed = time.perf_counter() - started
        assert len(selection.page_ids) == len(set(selection.page_ids)) == min(size, target)
        results[name] = {'seconds': elapsed, 'selected': len(selection.page_ids),
                         'selection_digest': digest(asdict(selection))}
    started = time.perf_counter(); graph = graph_metrics(rows)
    assert graph['vertices'] == size and all(len(set(path)) == 3 for path in graph['two_hop_examples'])
    results['graph'] = {'seconds': time.perf_counter() - started,
                       **{k: graph[k] for k in ('vertices', 'directed_nonself_edges', 'potential_two_hop_paths')}}
    stop.set(); thread.join(); peak[0] = max(peak[0], process.memory_info().rss)
    results['peak_rss_bytes_sampled'] = peak[0]
    return results


def main():
    os.environ['VIMFC_OFFLINE'] = '1'
    def denied(*args, **kwargs):
        raise RuntimeError('Synthetic benchmark is offline')
    socket.socket.connect = denied
    socket.create_connection = denied
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', type=int, nargs='+', default=[1000, 5000, 10000])
    parser.add_argument('--shape', choices=['blocks', 'hub'], default='blocks')
    parser.add_argument('--baseline')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--output', required=True)
    parser.add_argument('--child', action='store_true')
    parser.add_argument('--bundle-hash', action='store_true')
    parser.add_argument('--database-bytes', type=int, default=2476113920)
    args = parser.parse_args()
    if args.bundle_hash:
        output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
        fixture = output / 'hash-fixture'; fixture.mkdir(exist_ok=True)
        # Hash-only bytes: this is deliberately not a historical SQLite run.
        with (fixture / 'source.sqlite').open('wb') as stream:
            stream.write(b'SYNTHETIC HASH BENCHMARK ONLY\n'); stream.truncate(args.database_bytes)
        (fixture / 'objects').mkdir(exist_ok=True)
        (fixture / 'artifact-map.json').write_text('{}', encoding='utf-8')
        files = {'source.sqlite': {'bytes': args.database_bytes, 'sha256': file_hash(fixture / 'source.sqlite')},
                 'artifact-map.json': {'bytes': 2, 'sha256': file_hash(fixture / 'artifact-map.json')}}
        rows = []; current = 0
        for size in args.sizes:
            for p in range(current, size):
                body = (f'synthetic source {p}: ' + 'fixture only. ' * 600).encode()
                relative = f'objects/{p}.bin'; (fixture / relative).write_bytes(body)
                files[relative] = {'bytes': len(body), 'sha256': file_hash(fixture / relative)}
            current = size
            manifest = {'format': 'annotation-source-bundle-1', 'context': {'database': 'source.sqlite', 'raw_cache': 'objects'},
                        'inventory_id': 'synthetic-only', 'files': files}
            manifest['bundle_id'] = digest(manifest)
            (fixture / 'bundle.json').write_text(canonical(manifest), encoding='utf-8')
            times = []
            for _ in range(3):
                started = time.perf_counter(); read_bundle(fixture); times.append(time.perf_counter() - started)
            row = {'articles': size, 'database_bytes': args.database_bytes,
                   'verified_files': len(files), 'verified_bytes_per_call': sum(v['bytes'] for v in files.values()),
                   'verification_seconds': times, 'calls_for_acquire_and_process_batches_250': 2 * math.ceil(size / 250),
                   'scope': 'synthetic byte verification only; filesystem cache affects repeat timings'}
            row['campaign_hash_seconds_at_median'] = sorted(times)[1] * row['calls_for_acquire_and_process_batches_250']
            rows.append(row); (output / 'summary.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
            print(json.dumps(row), flush=True)
        return
    if args.child:
        value = run(args.sizes[0], args.shape, args.baseline)
        Path(args.output).write_text(json.dumps(value, indent=2), encoding='utf-8')
        return
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    summary = []
    for size in args.sizes:
        destination = output / f'{args.shape}-{size}.json'
        command = [sys.executable, '-B', __file__, '--child', '--sizes', str(size), '--shape', args.shape, '--output', str(destination)]
        if args.baseline:
            command += ['--baseline', args.baseline]
        child = subprocess.Popen(command)
        peak = 0; started = time.monotonic()
        while child.poll() is None:
            try:
                peak = max(peak, psutil.Process(child.pid).memory_info().rss)
            except psutil.NoSuchProcess:
                pass
            if time.monotonic() - started > args.timeout:
                child.kill(); child.wait()
                break
            time.sleep(.05)
        if child.returncode == 0:
            summary.append(json.loads(destination.read_text(encoding='utf-8')))
        elif time.monotonic() - started >= args.timeout:
            summary.append({'size': size, 'shape': args.shape, 'timeout_seconds': args.timeout,
                            'peak_rss_bytes_sampled_before_timeout': peak})
        else:
            raise RuntimeError(f'Synthetic benchmark child failed: {child.returncode}')
        (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary[-1]), flush=True)


if __name__ == '__main__':
    main()
