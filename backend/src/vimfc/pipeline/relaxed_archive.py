"""Read-only admission adapter. Missing classification never substitutes for source proof."""
from collections import Counter
from dataclasses import asdict
import json
import re
import sqlite3
from types import SimpleNamespace
from pathlib import Path

from vimfc.domain import Incompatible, SourceUnavailable, digest, SourceRevisionRef
from vimfc.source.dependencies import DependencySource
from vimfc.source.identities import normalize_title, title_at_snapshot
from .discovery_archive import Archive
from .dependencies import verify_manifest, classify_manifest
from .extract import extract_lead
from .quality import assess_quality
from .integrity_registry import IntegrityRegistry, FORMAT, UNKNOWN, REFUSAL


class ReadOnlyRegistry(IntegrityRegistry):
    def __init__(self, path, archive, artifact_resolver=None):
        self.artifact_resolver = artifact_resolver
        self.path = Path(path).resolve()
        self.store = SimpleNamespace(db=archive.db)
        self.run_id = archive.context['run_id']
        self.run = archive.run
        self.header = {'format': FORMAT, 'run_id': self.run_id,
                       'config_hash': self.run['config_hash'], 'snapshot': self.run['snapshot']}
        self.registry_id = digest(self.header)
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('BEGIN')
        self.observations = self.verify()

    def _read_artifact(self, ref):
        if self.artifact_resolver:
            ref = {**ref, 'path': str(self.artifact_resolver(ref['path']))}
        return super()._read_artifact(ref)

    def _member(self, artifact, review_id):
        if not self.artifact_resolver:
            return super()._member(artifact, review_id)
        from .quarantine import ReviewedIntegrityQuarantine
        self._read_artifact(artifact)
        gate = ReviewedIntegrityQuarantine.from_file(artifact['path'], self.run, resolve_path=self.artifact_resolver)
        if review_id not in gate.review_ids:
            raise Incompatible('Refusal component absent from reviewed approval')
        return gate.members[review_id] if hasattr(gate, 'members') else gate

    def check(self, ref, review=None):
        for observation in self.observations.values():
            source = observation.get('source_ref')
            if not source or source['site'] != ref.site or source['revision_id'] != ref.revision_id:
                continue
            if any(source[k] != asdict(ref)[k] for k in source if k != 'lookup_timestamp'):
                raise Incompatible('Registry exact-ref conflict')
            if observation['state'] in (UNKNOWN, REFUSAL):
                raise Incompatible('Known integrity dependency remains refused: ' + observation['state'])


class CheckedArchive(Archive):
    def __init__(self, context, registry_path=None, *, artifact_resolver=None):
        super().__init__(context)
        self.registry = None
        self.resolution_cache = {}
        self.candidate_by_id = {c['page_id']: c for c in self.cohort}
        self.move_indexes = {}
        try:
            anchored = self.db.execute("SELECT 1 FROM run_events WHERE run_id=? AND stage='integrity_registry_anchor' LIMIT 1",
                                       (context['run_id'],)).fetchone()
            if anchored and registry_path is None: raise Incompatible('Anchored source requires the existing read-only registry')
            if registry_path is not None: self.registry = ReadOnlyRegistry(registry_path, self, artifact_resolver)
        except BaseException:
            self.db.close()
            raise

    def __exit__(self, *args):
        if self.registry: self.registry.close()
        super().__exit__(*args)

    def _stage(self, page, stage, dependency_hash=None):
        rows = list(self.db.execute('SELECT * FROM stage_results WHERE run_id=? AND identity=? AND stage=? AND status=\'completed\'',
                                   (self.context['run_id'], str(page), stage)))
        if dependency_hash is not None: rows = [r for r in rows if r['dependency_hash'] == dependency_hash]
        if len(rows) != 1: raise Incompatible('Missing or ambiguous completed ' + stage + ' checkpoint')
        row = rows[0]; value = json.loads(row['result_json'])
        if digest(value) != row['result_hash']: raise Incompatible('Checkpoint checksum mismatch: ' + stage)
        return row, value

    def article_source(self, page):
        _, acquired = self._stage(page, 'acquire', digest({'snapshot': self.context['snapshot'], 'page_id': page}))
        ref = acquired['ref']
        if (ref['site'] != 'viwiki' or ref['page_id'] != page or ref['snapshot'] != self.context['snapshot']
                or acquired['content_model'] != 'wikitext'):
            raise Incompatible('Historical Wikipedia intake identity/model/snapshot differs')
        if self.registry: self.registry.check(SourceRevisionRef(**ref))
        raw = self.checked_raw(acquired['cache_ref'], page_id=page, revision_timestamp=ref['revision_timestamp'],
                               raw_hash=acquired['local_sha256'], model='wikitext')
        if (raw['content'] != acquired['content'] or raw['source_sha1'] != acquired['source_sha1']
                or str(ref['revision_id']) not in Path(acquired['cache_ref']['path']).parts):
            raise Incompatible('Acquired source differs from revision-addressed raw')
        article = {'site': 'viwiki', 'page_id': page, 'title': ref['title'], 'revision_id': ref['revision_id'],
                   'revision_timestamp': ref['revision_timestamp'], 'content_hash': acquired['local_sha256']}
        if self.config.get('discovery'):
            selected = self.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                                       ('viwiki', str(page), self.context['snapshot'])).fetchone()
            if not selected: raise Incompatible('Selected historical revision proof missing')
            selected = json.loads(selected[0])
            if any(selected[k] != ref[k] for k in ('page_id', 'revision_id', 'revision_timestamp', 'snapshot')):
                raise Incompatible('Selected revision and acquired source disagree')
            moves = self._moves(selected.get('lookup_timestamp'))
            if title_at_snapshot(page, selected['title'], moves, self.context['snapshot']) != ref['title']:
                raise Incompatible('Historical title/move proof differs')
        scope = {'namespace': 0, 'article': article, 'basis': 'checked_historical_canonical_title_no_separator'}
        if ':' in ref['title']:
            deps = {'article': article, 'snapshot': self.context['snapshot'], 'policy': 'historical-title-namespace-1'}
            _, scope = self._stage(page, 'verify_namespace', digest(deps))
            if scope.get('namespace') != 0 or scope.get('article') != article:
                raise Incompatible('Historical namespace not verified')
        source = {'article': article, 'snapshot': self.context['snapshot'], 'cache_ref': acquired['cache_ref'],
                  'namespace_scope': scope, 'source_sha1': raw['source_sha1'], 'policy': 'relaxed-source-1'}
        source['manifest_id'] = digest(source)
        return acquired, source

    def processed(self, page):
        row, record = self._stage(page, 'process')
        cls = record['classification']; manifest = cls['dependency_manifest']
        acquired, source_manifest = self.article_source(page)
        expected_source = {'page_id': page, 'title': acquired['ref']['title'],
                           'revision_id': acquired['ref']['revision_id'], 'revision_timestamp': acquired['ref']['revision_timestamp'],
                           'raw_hash': acquired['local_sha256'], 'cache_ref': acquired['cache_ref'], 'content_model': 'wikitext'}
        if any(record.get(k) != v for k, v in expected_source.items()):
            raise Incompatible('Saved process identity differs from checked historical Wikipedia raw')
        if record.get('article_kinds', []) != self.article_kinds(acquired['content'], acquired['ref']['title']):
            raise Incompatible('Saved article kinds differ from checked historical raw')
        verify_manifest(manifest, self.config['classification'], source_manifest['article'])
        if record['snapshot'] != self.context['snapshot'] or manifest['snapshot'] != self.context['snapshot']:
            raise Incompatible('Process snapshot differs')
        binding = self.db.execute('SELECT m.record_json FROM article_manifests b JOIN dependency_manifests m USING(manifest_id) WHERE b.run_id=? AND b.page_id=?',
                                  (self.context['run_id'], page)).fetchone()
        if not binding or json.loads(binding[0]) != manifest: raise Incompatible('Archived dependency binding differs')
        if self.config.get('discovery') and record.get('discovery_source_scope') != source_manifest['namespace_scope']:
            raise Incompatible('Process historical namespace scope differs')
        deps = {'snapshot': self.context['snapshot'], 'config': self.context['config_hash'],
                'candidate': self.candidate_by_id[page],
                'input': {'manifest_id': manifest['manifest_id'], 'article_hash': digest(manifest['article'])}}
        if self.config.get('discovery'): deps['input']['discovery_scope_id'] = digest(record['discovery_source_scope'])
        if row['dependency_hash'] != digest(deps): raise Incompatible('Process input fingerprint differs')
        source = SimpleNamespace(store=SimpleNamespace(db=self.db), cache=self.cache,
                                 reviewed_quarantine=None, integrity_registry=self.registry)
        items = DependencySource(source, self.context['snapshot'], self.config['classification']).materialize(manifest, allow_fetch=False)
        replay = asdict(classify_manifest(manifest, items, self.config['classification']))
        if any(cls.get(k) != v for k, v in replay.items()): raise Incompatible('Saved qid-5 classification does not replay')
        # Validate the saved projections as well as the separately checked raw.
        if asdict(classify_manifest(manifest, cls['items'], self.config['classification'])) != replay:
            raise Incompatible('Saved classification evidence differs')
        ext = asdict(extract_lead(acquired['content'], self.config['extraction']))
        saved_ext = {**record['extraction'], 'links': [{k: v for k, v in l.items() if k != 'target'} for l in record['extraction']['links']]}
        if ext != saved_ext: raise Incompatible('Historical lead-4 extraction does not replay')
        if assess_quality(ext, cls, record.get('article_kinds', []), self.config['extraction']) != record['quality']:
            raise Incompatible('Original quality-2.1 does not replay')
        record['source_validation'] = source_manifest
        record['original_graph_links'] = record['extraction']['links']
        record['extraction'] = ext
        record['classification'].pop('items', None)
        return record

    def source_only(self, page):
        if self.db.execute('SELECT 1 FROM article_manifests WHERE run_id=? AND page_id=?',
                           (self.context['run_id'], page)).fetchone():
            raise Incompatible('Published classification dependency cannot be bypassed by source-only intake')
        acquired, proof = self.article_source(page)
        text = acquired['content']; title = proof['article']['title']
        kinds = self.article_kinds(text, title)
        a = proof['article']
        return {'page_id': page, 'title': title, 'revision_id': a['revision_id'], 'revision_timestamp': a['revision_timestamp'],
                'raw_hash': a['content_hash'], 'snapshot': self.context['snapshot'], 'source_validation': proof,
                'article_kinds': kinds, 'extraction': asdict(extract_lead(text, self.config['extraction'])),
                'classification': {'outcome': 'unresolved', 'primary_entity_type': None, 'subtypes': [],
                                   'reason': 'missing_item_hint', 'source_only_intake': True},
                'quality': {'eligible': False, 'reasons': ['unsupported_classification']}}

    @staticmethod
    def article_kinds(text, title):
        kinds = []
        if re.match(r'^\s*#(?:redirect|đổi)', text, re.I): kinds.append('redirect')
        if re.search(r'__DISAMBIG__|\{\{\s*(?:disambig|disambiguation|định hướng|trang định hướng)(?:\s*[|}])', text, re.I): kinds.append('disambiguation')
        if title.casefold().startswith(('danh sách ', 'list of ')): kinds.append('list')
        return kinds

    def _moves(self, horizon):
        if not horizon: raise Incompatible('Missing historical move horizon')
        if horizon not in self.move_indexes:
            row = self.db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                                  (self.context['snapshot'], '__move_index__:' + horizon)).fetchone()
            if not row: raise Incompatible('Pinned historical move index missing')
            self.move_indexes[horizon] = json.loads(row[0])
        return self.move_indexes[horizon]

    def _identity_raw(self, page, title, revision_id=None, timestamp=None, raw_hash=None):
        row = self.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                              ('viwiki', str(page), self.context['snapshot'])).fetchone()
        if not row: raise SourceUnavailable('Cached selected target revision missing', code='graph_proof_missing')
        ref = json.loads(row[0]); moves = self._moves(ref.get('lookup_timestamp'))
        if ref.get('site') != 'viwiki' or ref.get('page_id') != page or ref.get('snapshot') != self.context['snapshot']:
            raise Incompatible('Graph selected-ref scope differs from lookup')
        if self.registry: self.registry.check(SourceRevisionRef(**ref))
        if (title_at_snapshot(page, ref['title'], moves, self.context['snapshot']) != normalize_title(title)
                or revision_id is not None and ref['revision_id'] != revision_id
                or timestamp is not None and ref['revision_timestamp'] != timestamp):
            raise Incompatible('Cached graph target revision/title differs')
        row = self.db.execute('SELECT record_json FROM source_revisions WHERE site=? AND revision_id=?', ('viwiki', ref['revision_id'])).fetchone()
        if not row: raise SourceUnavailable('Cached graph raw not registered', code='graph_proof_missing')
        cache_ref = json.loads(row[0])['cache_ref']; value = self.cache.get(cache_ref)
        if Path(cache_ref['path']).parts[:2] != ('viwiki', str(ref['revision_id'])):
            raise Incompatible('Graph raw is not addressed by the selected revision')
        self.checked_raw(cache_ref, page_id=page, revision_timestamp=ref['revision_timestamp'],
                         raw_hash=raw_hash or value['local_sha256'], model='wikitext')
        return value, ref, moves

    def resolve(self, title):
        title = normalize_title(title)
        if title in self.resolution_cache: return self.resolution_cache[title]
        row = self.db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                              (self.context['snapshot'], title)).fetchone()
        if not row: return {'status': 'unresolved', 'reason': 'no_archived_identity_resolution'}
        result = json.loads(row[0])
        if result.get('status') != 'resolved': return {k: v for k, v in result.items() if k != 'content'}
        return self._checked_resolution(title,result)

    def _checked_resolution(self,title,result,*,proof_kind='historical_revision_and_move_log'):
        if result.get('snapshot') != self.context['snapshot'] or result.get('proof', {}).get('kind') != proof_kind:
            raise Incompatible('Graph historical identity proof differs')
        current = title
        for step in result.get('chain', []):
            if normalize_title(step['original_title']) != current: raise Incompatible('Redirect chain title differs')
            raw, _, _ = self._identity_raw(step['page_id'], current, step['revision_id'])
            match = re.match(r'^\s*#(?:redirect|đổi|đổi hướng)\s*\[\[([^\]]+)\]\]', raw['content'], re.I)
            if not match or match[1].partition('#')[0] != step['target']: raise Incompatible('Redirect target lacks historical raw proof')
            current = normalize_title(step['target'])
        if current != normalize_title(result['title']): raise Incompatible('Final graph target does not close redirect chain')
        raw, _, moves = self._identity_raw(result['page_id'], result['title'], result['revision_id'],
                                          result['revision_timestamp'], result['raw_hash'])
        # resolve() pins the initial lookup horizon; raw_page() may independently
        # reuse an earlier page-ID selection horizon. Both indexes must remain
        # checked, but their hashes need not equal (historical source contract).
        if not hasattr(self, 'move_proof_hashes'):
            self.move_proof_hashes = {digest(json.loads(r[0])) for r in self.db.execute(
                "SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title LIKE '__move_index__:%'",
                (self.context['snapshot'],))}
        if result['proof']['move_index_hash'] not in self.move_proof_hashes or raw['content'] != result.get('content'):
            raise Incompatible('Graph target move/content proof differs')
        if re.match(r'^\s*#(?:redirect|đổi|đổi hướng)\s*\[\[', raw['content'], re.I):
            raise Incompatible('Resolved graph target is still a redirect')
        if ':' in result['title']:
            return {'status': 'unresolved', 'reason': 'graph_target_namespace_needs_exact_proof'}
        result = {k: v for k, v in result.items() if k != 'content'}
        self.resolution_cache[title] = result
        return result
