"""Offline connectivity check on the existing 1,000-source archive; select 100."""
from dataclasses import asdict
import json
from pathlib import Path
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))
from vimfc.domain import canonical, digest
from vimfc.pipeline.annotation_builder import AnnotationCollectionBuilder
from vimfc.pipeline.relaxed_archive import CheckedArchive
from vimfc.pipeline.relaxed_preservation import snapshot_files
from vimfc.pipeline.wikigraph import archived_graph, select_structural


def main():
    def denied(*args, **kwargs):
        raise RuntimeError('WikiGraph pilot is offline')
    socket.socket.connect = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    root = Path(__file__).resolve().parents[1]
    old = root / '.tools/annotation-collection-v2-pilot1000-20261116/checkpoint.json'
    saved = json.loads(old.read_text(encoding='utf-8'))
    assert saved['sha256'] == digest(saved['state'])
    context = saved['state']['binding']['context']
    registry = root / 'data/integrity/blockers-80524a0f.sqlite'
    output = root / '.tools/wikigraph-pilot100-20261116-final'
    intake = root / '.tools/wikigraph-pilot100-20261116/checkpoint.json'
    roots = [Path(context['database']).parent, registry.parent, old.parent, intake.parent]
    before = snapshot_files(roots)
    started = time.monotonic()
    # Reuse checked intake only; rebuild graph and selection with the current code.
    if not output.exists() and intake.exists():
        checked = json.loads(intake.read_text(encoding='utf-8'))
        assert checked['sha256'] == digest(checked['state'])
        state = checked['state']
        state.pop('result', None)
        output.mkdir()
        (output / 'checkpoint.json').write_text(canonical({'state': state, 'sha256': digest(state)}), encoding='utf-8')
    result = AnnotationCollectionBuilder(context, output, registry_path=registry, target=100).run()
    with CheckedArchive(context, registry) as archive:
        replay = archived_graph(archive, list(reversed(result['articles'])))
        assert replay == result['structural_graph']
        selection = select_structural(list(reversed(result['articles'])), archive, archive.config['quotas'], 100, replay)
        assert asdict(selection['selection']) == result['selection']
        assert selection['manifest'] == result['manifest']
    after = snapshot_files(roots)
    assert before == after, 'Source preservation differs'
    report = {'manifest': result['manifest'], 'metrics': result['metrics'], 'blockers': result['blockers'],
              'unavailable_sources': result['structural_graph']['unavailable_sources'],
              'selected_count': len(result['selection']['page_ids']), 'pilot_accepted': False,
              'graph_and_selection_replay_identical': True, 'preserved_files': len(before),
              'elapsed_seconds': round(time.monotonic() - started, 2)}
    (output / 'verification.json').write_text(canonical(report), encoding='utf-8')
    print(canonical(report))


if __name__ == '__main__':
    main()
