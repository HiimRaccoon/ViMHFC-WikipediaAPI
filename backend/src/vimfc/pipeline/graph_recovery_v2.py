"""Typed-selector historical recovery, isolated from the version-1 checkpoint.

Unproven move-log coverage or semantics never authorizes an identity. The
previous version and its saved results remain untouched.
"""
from dataclasses import asdict
import json
from pathlib import Path

from vimfc.domain import Incompatible, SourceUnavailable, SourceRevisionRef
from vimfc.source.explicit_revisions import selector_key, selector_ref_record, selector_value
from vimfc.source.identities import normalize_title, title_at_snapshot
from vimfc.source.identities_v2 import HistoricalIdentitiesV2, resolution_key
from .graph_recovery import GraphRecovery
from .move_coverage_receipts import verify_move_receipts
from .selector_receipts import verify_selector_receipt
from .relaxed_archive import CheckedArchive

VERSION = 'historical-target-resolution-2'


class _CheckedArchiveV2(CheckedArchive):
    def _moves(self, horizon):
        return self.owner.identities._move_index(horizon)

    def _identity_raw(self, page, title, revision_id=None, timestamp=None, raw_hash=None):
        ref = self.owner._selected_ref('page_id', str(page))
        if not ref:
            raise SourceUnavailable('Cached selected target revision missing', code='graph_proof_missing')
        self.owner._check_ref(ref, str(page))
        if ref['page_id'] != page:
            raise Incompatible('Selected page identity differs')
        if self.registry:
            self.registry.check(SourceRevisionRef(**ref))
        moves = self._moves(ref['lookup_timestamp'])
        if (title_at_snapshot(page, ref['title'], moves, self.context['snapshot']) != normalize_title(title)
                or revision_id is not None and ref['revision_id'] != revision_id
                or timestamp is not None and ref['revision_timestamp'] != timestamp):
            raise Incompatible('Cached graph target revision/title differs')
        row = self.db.execute('SELECT record_json FROM source_revisions WHERE site=? AND revision_id=?',
                              ('viwiki', ref['revision_id'])).fetchone()
        if not row:
            raise SourceUnavailable('Cached graph raw not registered', code='graph_proof_missing')
        cache_ref = json.loads(row[0])['cache_ref']
        if Path(cache_ref['path']).parts[:2] != ('viwiki', str(ref['revision_id'])):
            raise Incompatible('Graph raw is not revision addressed')
        value = self.cache.get(cache_ref)
        self.checked_raw(cache_ref, page_id=page, revision_timestamp=ref['revision_timestamp'],
                         raw_hash=raw_hash or value['local_sha256'], model='wikitext')
        return value, ref, moves


class GraphRecoveryV2(GraphRecovery):
    version = VERSION
    numeric_titles_supported = True

    def __init__(self, context, output, **kwargs):
        super().__init__(context, output, **kwargs)
        self.source.require_selector_receipts = True
        self.source.selector_receipt_verifier = lambda context, saved: verify_selector_receipt(
            self.output, self.state['receipts'], context, saved)
        self.identities = HistoricalIdentitiesV2(
            self.source, self.context['snapshot'],
            should_acquire=lambda: self.online,
            coverage_verifier=lambda snapshot, horizon, raw: verify_move_receipts(
                self.output, self.state['receipts'], snapshot, horizon, raw))
        checked = _CheckedArchiveV2.__new__(_CheckedArchiveV2)
        checked.owner = self
        checked.context, checked.db, checked.cache = self.context, self.store.db, self.cache
        checked.registry, checked.move_indexes = self.archive.registry, {}
        self.checked = checked

    def _resolution_key(self, title):
        return resolution_key(title)

    def _title_selector_key(self, title):
        return selector_key('title', title)

    def _resolution_record(self, title):
        result = super()._resolution_record(title)
        if result is not None or title.isdecimal():
            return result
        row = self.store.db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                                    (self.context['snapshot'], title)).fetchone()
        return json.loads(row[0]) if row else None

    def _selected_ref(self, kind, identity):
        key = selector_key(kind, identity)
        row = self.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                                    ('viwiki', key, self.context['snapshot'])).fetchone()
        if row:
            envelope = json.loads(row[0])
            if envelope.get('selector') != {'kind': kind, 'value': selector_value(kind, identity)}:
                raise Incompatible('Typed selector receipt differs from requested key')
            selector_ref_record(envelope)
            selected = (self.source.select_title_revision('viwiki', identity, self.context['snapshot'])
                        if kind == 'title' else
                        self.source.select_page_revision('viwiki', identity, self.context['snapshot']))
            ref = asdict(selected)
            self._check_ref(ref, selector_value(kind, identity))
            if kind == 'page_id' and ref['page_id'] != int(identity):
                raise Incompatible('Typed page selector differs from page identity')
            return ref
        if kind == 'title' and str(identity).isdecimal():
            return None
        legacy = super()._selected_ref(kind, identity)
        if legacy and kind == 'page_id' and legacy.get('page_id') != int(identity):
            return None
        return legacy

    def _move_hash_matches(self, moves, horizon, expected):
        proof = self.identities.move_proofs.get(horizon)
        return bool(proof and proof['raw_events_hash'] == expected)

    def _move_hash(self, moves, horizon):
        proof = self.identities.move_proofs.get(horizon)
        if not proof:
            raise SourceUnavailable('Move proof not available', code='graph_proof_missing')
        return proof['raw_events_hash']

    def _witness(self, ref, raw, moves):
        value = super()._witness(ref, raw, moves)
        proof = self.identities.move_proofs.get(ref['lookup_timestamp'])
        if not proof:
            raise SourceUnavailable('Move proof not available', code='graph_proof_missing')
        value['move_index_hash'] = proof['raw_events_hash']
        value['move_proof_manifest_id'] = proof['manifest_id']
        return value
