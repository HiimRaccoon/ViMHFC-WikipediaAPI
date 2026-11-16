"""Opt-in, read-only source intake with an isolated resumable selection output."""
from dataclasses import asdict
import json
import os
import tempfile
import time
from pathlib import Path

from vimfc.domain import Incompatible, SourceUnavailable, canonical, digest
from .annotation_edges import select_articles
from .extract import extract_lead
from .quality import assess_quality
from .relaxed_archive import CheckedArchive
from .relaxed_pilot import SNAPSHOT, relaxed_rules
from .relaxed_preservation import snapshot_files
from .wikigraph import VERSION, RANK_VERSION, source_links, archived_graph, structural_metrics


class AnnotationCollectionBuilder:
    """No source requests, classification dependency acquisition or corpus freeze."""

    def __init__(self, context, output, *, registry_path=None, target=20000):
        self.context, self.output = context, Path(output).resolve()
        self.registry_path, self.target = registry_path, target
        if context['snapshot'] != SNAPSHOT or type(target) is not int or target <= 0:
            raise Incompatible('Annotation collection snapshot/target differs')
        protected = [Path(context['database']).resolve().parent, Path(context['raw_cache']).resolve()]
        if registry_path:
            protected.append(Path(registry_path).resolve().parent)
        if any(self.output == p or self.output.is_relative_to(p) or p.is_relative_to(self.output) for p in protected):
            raise Incompatible('Annotation output overlaps protected inputs')

    def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / '.writer.lock').open('a+b') as lock:
            try:
                if os.name == 'nt':
                    import msvcrt
                    lock.seek(0)
                    if not lock.read(1):
                        lock.write(b'0'); lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise Incompatible('Annotation output writer lock unavailable') from error
            return self._run_locked()

    def _run_locked(self):
        with CheckedArchive(self.context, self.registry_path) as archive:
            roots = [Path(self.context['database']), Path(self.context['raw_cache'])]
            roots += [p for p in [Path(str(self.context['database']) + '-wal')] if p.exists()]
            if self.registry_path:
                registry = Path(self.registry_path)
                roots += [registry, registry.with_name(registry.stem + '-artifacts')]
                roots += [p for p in [Path(str(registry) + '-wal')] if p.exists()]
            binding = {'format': 'annotation-builder-wikigraph-1', 'context': self.context,
                       'config': archive.config, 'cohort': archive.cohort, 'target': self.target,
                       'quality': 'quality-relaxed-1', 'edge': VERSION,
                       'rank': RANK_VERSION, 'manifest': 'annotation-collection-wikigraph-1',
                       'source_fingerprint': digest(snapshot_files(roots))}
            self.output.mkdir(parents=True, exist_ok=True)
            path = self.output / 'checkpoint.json'
            if path.exists():
                saved = json.loads(path.read_text(encoding='utf-8'))
                state = saved['state']
                if saved['sha256'] != digest(state) or state['binding'] != binding:
                    raise Incompatible('Annotation checkpoint/input fingerprint differs')
                if 'result' in state:
                    result = state['result']; manifest = result['manifest']
                    if (manifest['manifest_id'] != digest({k: v for k, v in manifest.items() if k != 'manifest_id'})
                            or manifest['version'] != binding['manifest']
                            or manifest['selected_page_ids'] != result['selection']['page_ids']):
                        raise Incompatible('Annotation manifest/selection differs')
                    return state['result']
            else:
                if any(p.name != '.writer.lock' for p in self.output.iterdir()):
                    raise Incompatible('Annotation output contains unrelated files')
                state = {'binding': binding, 'records': {}, 'blockers': {}}

            def save():
                # Unique pending files preserve failed writes and the legacy temp.
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                        dir=self.output, prefix='checkpoint-', suffix='.pending', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(canonical({'state': state, 'sha256': digest(state)}))
                    stream.flush()
                    os.fsync(stream.fileno())
                for attempt in range(8):
                    try:
                        temporary.replace(path)
                        break
                    except PermissionError as error:
                        if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 7:
                            raise
                        time.sleep(min(0.05 * 2 ** attempt, 0.5))

            save()
            for candidate in archive.cohort:
                page = candidate['page_id']; key = str(page)
                if key in state['records'] or key in state['blockers']:
                    continue
                try:
                    acquired, proof = archive.article_source(page)
                    ref = proof['article']
                    extraction = asdict(extract_lead(acquired['content'], archive.config['extraction']))
                    kinds = archive.article_kinds(acquired['content'], ref['title'])
                    classification = {'outcome': 'unclassified', 'primary_entity_type': None,
                                      'subtypes': [], 'reason': 'classification_optional_collection'}
                    record = {'page_id': page, 'title': ref['title'], 'revision_id': ref['revision_id'],
                              'revision_timestamp': ref['revision_timestamp'], 'raw_hash': ref['content_hash'],
                              'snapshot': self.context['snapshot'], 'source_validation': proof,
                              'article_kinds': kinds, 'extraction': extraction, 'classification': classification,
                              'structural_source': source_links(acquired['content']),
                              'quality': assess_quality(extraction, classification, kinds, relaxed_rules(archive.config))}
                    state['records'][key] = record
                except (Incompatible, SourceUnavailable, ValueError, KeyError, TypeError, OSError) as error:
                    state['blockers'][key] = {'page_id': page, 'reason': str(error), 'type': type(error).__name__}
                save()

            records = sorted(state['records'].values(), key=lambda a: a['page_id'])
            graph = archived_graph(archive, records)
            result = select_articles(records, archive, archive.config['quotas'], target=self.target,
                                     structural_graph=graph)
            result['selection'] = asdict(result['selection'])
            selected = set(result['selection']['page_ids'])
            eligible = [a for a in result['articles'] if a['quality']['eligible']]
            result['metrics'] = {'eligible_articles': len(eligible),
                'snapshot_graph': structural_metrics(graph, graph['vertices']),
                'feasible_graph': structural_metrics(graph, {a['page_id'] for a in eligible}),
                'selected_graph': structural_metrics(graph, selected),
                'unresolved_reasons': graph['unresolved_reasons'],
                'verified_structural_occurrences': sum(o['state'] == 'VERIFIED_STRUCTURAL_EDGE'
                                                     for o in graph['occurrences']),
                'structural_edges_are_factual_evidence': False}
            result['blockers'] = list(state['blockers'].values())
            result['pilot_accepted'] = False
            state['result'] = result
            save()
            return result
