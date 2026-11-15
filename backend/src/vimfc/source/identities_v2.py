"""Opt-in historical identities with explicit title/page-ID cache namespaces.

Move events retain their original archived representation. Only separately
proven logpage semantics may produce operational title-reconstruction events.
Legacy identity resolution records and selector behavior are never changed.
"""
from dataclasses import replace
from copy import deepcopy
import json
import re

from vimfc.domain import SourceUnavailable, canonical, digest
from .historical_move_proof import build_move_proof
from .identities import HistoricalIdentities, normalize_title, resolve_historical, title_at_snapshot

VERSION = 'historical-identities-2'


def resolution_key(title):
    return canonical([VERSION, 'title', normalize_title(title)])


def move_index_key(horizon):
    return '__move_index_v2__:' + horizon


class HistoricalIdentitiesV2(HistoricalIdentities):
    def __init__(self, source, snapshot, *, modern_logpage_verified=False, coverage_verifier=None,
                 hidden_coverage_verifier=None, should_acquire=None):
        super().__init__(source, snapshot)
        self.modern_logpage_verified = modern_logpage_verified is True
        self.move_proofs = {}
        self.coverage_verifier = coverage_verifier
        self.hidden_coverage_verifier = hidden_coverage_verifier
        self.should_acquire = should_acquire

    def _hidden_coverage(self, horizon, raw, public):
        if self.hidden_coverage_verifier is None:
            raise SourceUnavailable('Restricted move coverage has no independent authority',
                                    code='historical_hidden_move_coverage_unverified')
        try:
            receipt = deepcopy(self.hidden_coverage_verifier(self.snapshot, horizon, deepcopy(raw), deepcopy(public)))
            if (not isinstance(receipt, dict) or receipt.get('version') != 'historical-hidden-move-coverage-1'
                    or receipt.get('snapshot') != self.snapshot or receipt.get('horizon') != horizon
                    or receipt.get('raw_events_hash') != digest(raw)
                    or receipt.get('public_coverage_manifest_id') != public['manifest_id']
                    or receipt.get('complete') is not True or receipt.get('authoritative') is not True
                    or receipt.get('manifest_id') != digest({k: v for k, v in receipt.items() if k != 'manifest_id'})):
                raise ValueError('Restricted move attestation is not bound to this interval/index')
            evidence = receipt.get('evidence')
            if not isinstance(evidence, list) or not evidence:
                raise ValueError('Restricted move coverage authority evidence missing')
            for item in evidence:
                if (not isinstance(item, dict) or not isinstance(item.get('kind'), str) or not item['kind']
                        or not isinstance(item.get('reference'), str) or not item['reference']
                        or not isinstance(item.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
                    raise ValueError('Restricted move authority evidence is incomplete')
            return receipt
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise SourceUnavailable('Restricted move coverage authority is unverified',
                                    code='historical_hidden_move_coverage_unverified') from error

    def _acquire_index(self, horizon):
        if self.hidden_coverage_verifier is None:
            raise SourceUnavailable('Restricted move coverage has no independent authority',
                                    code='historical_hidden_move_coverage_unverified')
        params = {'action': 'query', 'list': 'logevents', 'letype': 'move',
                  'lestart': horizon, 'leend': self.snapshot, 'ledir': 'older',
                  'lelimit': 'max', 'leprop': 'ids|title|timestamp|details'}
        pages = iter(self.source.client.continued('viwiki', params))
        raw = []
        while True:
            if self.should_acquire is None or self.should_acquire() is not True:
                raise SourceUnavailable('Move-log acquisition is not permitted', code='historical_move_coverage_unverified')
            try:
                page = next(pages)
            except StopIteration:
                break
            events = page.get('query', {}).get('logevents')
            if not isinstance(events, list):
                raise SourceUnavailable('Move response event list is incomplete', code='historical_move_proof_incomplete')
            raw.extend(deepcopy(events))
            if page.get('continue') is None:
                break
        try:
            if any(not isinstance(event, dict) or not isinstance(event.get('timestamp'), str)
                   or 'logid' in event and type(event['logid']) is not int for event in raw):
                raise ValueError('Move index cannot be ordered safely')
            raw = sorted({digest(event): event for event in raw}.values(),
                         key=lambda event: (event['timestamp'], event.get('logid', 0)))
        except (ValueError, TypeError, KeyError) as error:
            raise SourceUnavailable('Historical move proof is incomplete', code='historical_move_proof_incomplete') from error
        coverage = self._coverage(horizon, raw)
        key = move_index_key(horizon)
        existing = self.source.store.db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                                                (self.snapshot, key)).fetchone()
        if existing and json.loads(existing[0]) != raw:
            raise SourceUnavailable('Reacquired move index conflicts with saved V2 index', code='invalid_historical_cache')
        self.source.store.db.execute('INSERT OR IGNORE INTO identity_resolutions VALUES (?,?,?)',
                                      (self.snapshot, key, canonical(raw)))
        return raw, coverage

    def _coverage(self, horizon, raw):
        """Validate a trusted verifier's receipt for returned public log pages.

        The injected verifier must read and hash actual response artifacts, bind
        every query interval and event payload, and verify pagination. Neither a
        cached event list nor a caller's complete=True alone proves coverage.
        Pagination cannot establish absence of events omitted by the public API.
        """
        if self.coverage_verifier is None:
            raise SourceUnavailable('Move interval coverage has no bound receipt', code='historical_move_coverage_unverified')
        try:
            receipt = deepcopy(self.coverage_verifier(self.snapshot, horizon, deepcopy(raw)))
            if (not isinstance(receipt, dict) or receipt.get('version') != 'historical-move-coverage-1'
                    or receipt.get('snapshot') != self.snapshot or receipt.get('horizon') != horizon
                    or receipt.get('raw_events_hash') != digest(raw) or receipt.get('complete') is not True
                    or receipt.get('interval') != {'start': horizon, 'end': self.snapshot, 'direction': 'older'}
                    or receipt.get('manifest_id') != digest({k: v for k, v in receipt.items() if k != 'manifest_id'})):
                raise ValueError('Move coverage receipt scope/hash differs')
            pages = receipt.get('pages')
            if not isinstance(pages, list) or not pages:
                raise ValueError('Move coverage response pages missing')
            expected, seen = {}, set()
            for index, page in enumerate(pages):
                if not isinstance(page, dict) or page.get('request_continue') != expected:
                    raise ValueError('Move coverage continuation chain differs')
                for field in ('response_sha256', 'events_hash'):
                    if not isinstance(page.get(field), str) or not re.fullmatch('[0-9a-f]{64}', page[field]):
                        raise ValueError('Move coverage content hash missing')
                if 'response_continue' not in page:
                    raise ValueError('Move coverage terminal continuation not witnessed')
                continuation = page['response_continue']
                if index == len(pages) - 1:
                    if continuation is not None:
                        raise ValueError('Move coverage pagination is unfinished')
                elif not isinstance(continuation, dict) or not continuation:
                    raise ValueError('Move coverage chain terminates early')
                elif canonical(continuation) in seen:
                    raise ValueError('Move coverage continuation repeats')
                else:
                    seen.add(canonical(continuation))
                expected = continuation
            return receipt
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise SourceUnavailable('Move interval coverage receipt is unverified', code='historical_move_coverage_unverified') from error

    def _move_index(self, horizon):
        if self.hidden_coverage_verifier is None:
            raise SourceUnavailable('Restricted move coverage has no independent authority',
                                    code='historical_hidden_move_coverage_unverified')
        if self.coverage_verifier is None:
            raise SourceUnavailable('Move interval coverage has no bound receipt', code='historical_move_coverage_unverified')
        try:
            raw, coverage = None, None
            for key in (move_index_key(horizon), '__move_index__:' + horizon):
                row = self.source.store.db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                                                   (self.snapshot, key)).fetchone()
                if not row:
                    continue
                candidate = json.loads(row[0])
                try:
                    coverage = self._coverage(horizon, candidate)
                except SourceUnavailable as error:
                    if error.code != 'historical_move_coverage_unverified':
                        raise
                    continue
                raw = candidate
                break
            if raw is None:
                if self.should_acquire is None or self.should_acquire() is not True:
                    raise SourceUnavailable('Move interval coverage has no bound receipt', code='historical_move_coverage_unverified')
                raw, coverage = self._acquire_index(horizon)
            self.moves[horizon] = deepcopy(raw)
            proof = build_move_proof(raw, self.snapshot, horizon,
                                     modern_logpage_verified=self.modern_logpage_verified)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise SourceUnavailable('Historical move proof is incomplete', code='historical_move_proof_incomplete') from error
        proof.update(coverage='verified_public_api_response_sequence_only', coverage_receipt=coverage,
                     hidden_omission_absence='not_established_by_pagination')
        proof['manifest_id'] = digest({k: v for k, v in proof.items() if k != 'manifest_id'})
        self.move_proofs[horizon] = proof
        if proof['status'] != 'VERIFIED':
            raise SourceUnavailable('Historical move proof is incomplete', code='historical_move_proof_incomplete',
                                    details={'move_proof_manifest_id': proof['manifest_id'], 'blockers': proof['blockers']})
        hidden = self._hidden_coverage(horizon, raw, coverage)
        proof.update(hidden_omission_absence='independently_attested', hidden_coverage_receipt=hidden)
        proof['manifest_id'] = digest({k: v for k, v in proof.items() if k != 'manifest_id'})
        return proof['operational_events']

    def raw_page(self, page_id):
        ref = self.source.select_page_revision('viwiki', page_id, self.snapshot)
        title = title_at_snapshot(ref.page_id, ref.title, self._move_index(ref.lookup_timestamp), self.snapshot)
        return replace(self.source.fetch_revisions('viwiki', [ref])[0], ref=replace(ref, title=title))

    def resolve(self, title):
        if self.hidden_coverage_verifier is None:
            raise SourceUnavailable('Restricted move coverage has no independent authority',
                                    code='historical_hidden_move_coverage_unverified')
        title = normalize_title(title)
        db = self.source.store.db
        key = resolution_key(title)
        row = db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                         (self.snapshot, key)).fetchone()
        if row:
            result = json.loads(row[0])
            if result.get('snapshot') != self.snapshot or result.get('identity_policy') != VERSION:
                raise SourceUnavailable('Typed historical identity cache scope differs', code='invalid_historical_cache')
            if result.get('status') == 'resolved':
                if self.hidden_coverage_verifier is None:
                    raise SourceUnavailable('Restricted move coverage has no independent authority',
                                            code='historical_hidden_move_coverage_unverified')
                if self.coverage_verifier is None:
                    raise SourceUnavailable('Move interval coverage has no bound receipt', code='historical_move_coverage_unverified')
                initial = self.source.select_title_revision('viwiki', title, self.snapshot)
                moves = self._move_index(initial.lookup_timestamp)
                move_proof = self.move_proofs[initial.lookup_timestamp]
                saved_proof = result.get('proof', {})
                if (saved_proof.get('move_index_hash') != move_proof['raw_events_hash']
                        or saved_proof.get('operational_move_index_hash') != digest(moves)
                        or saved_proof.get('move_proof_manifest_id') != move_proof['manifest_id']):
                    raise SourceUnavailable('Cached historical move proof changed', code='invalid_historical_cache')
            return result
        try:
            initial_ref = self.source.select_title_revision('viwiki', title, self.snapshot)
            horizon = initial_ref.lookup_timestamp
        except SourceUnavailable as error:
            if error.retryable or error.code != 'history_unavailable':
                raise
            horizon = self.source.client.query('viwiki', {'action': 'query', 'curtimestamp': 1}).get('curtimestamp')
        moves = self._move_index(horizon)

        def lookup(name):
            hints = set()
            try:
                hints.add(self.source.select_title_revision('viwiki', name, self.snapshot).page_id)
            except SourceUnavailable as error:
                if error.retryable or error.code != 'history_unavailable':
                    raise
            for move in moves:
                if normalize_title(move['title']) == name:
                    hints.add(move['pageid'])
            owners = []
            for page_id in sorted(hints):
                raw = self.raw_page(page_id)
                if normalize_title(raw.ref.title) == name:
                    owners.append({'page_id': page_id, 'title': raw.ref.title,
                                   'revision_id': raw.ref.revision_id,
                                   'revision_timestamp': raw.ref.revision_timestamp,
                                   'raw_hash': raw.local_sha256, 'content': raw.content})
            return owners

        result = resolve_historical(title, lookup)
        proof = self.move_proofs[horizon]
        result.update(snapshot=self.snapshot, identity_policy=VERSION)
        result['proof'] = {'kind': 'historical_revision_and_move_log',
                           'selector_policy': 'explicit-selector-1',
                           'move_index_hash': proof['raw_events_hash'],
                           'operational_move_index_hash': digest(moves),
                           'move_proof_manifest_id': proof['manifest_id']}
        db.execute('INSERT OR IGNORE INTO identity_resolutions VALUES (?,?,?)',
                    (self.snapshot, key, canonical(result)))
        return result
