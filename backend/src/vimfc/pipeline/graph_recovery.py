"""Isolated, bounded historical target recovery; original archives stay read-only.

The checkpoint charges physical requests before the wire and records pending
attempts before acquisition. Existing RevisionSource/HistoricalIdentities own
source selection, SHA-1 checks, move reconstruction and redirect resolution.
This adapter additionally revalidates their cached outputs before admitting edges.
"""
from collections import Counter
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from types import SimpleNamespace

import httpx

from vimfc.domain import Incompatible, SourceUnavailable, SourceRevisionRef, canonical, digest, at_or_before
from vimfc.source.identities import HistoricalIdentities, normalize_title, title_at_snapshot
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.source.revisions import RevisionSource
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from .relaxed_archive import CheckedArchive

VERSION = 'historical-target-resolution-1'
DEFAULT_LIMITS = {'max_requests': 1200, 'max_runtime_seconds': 1200,
                  'max_targets': 200, 'min_interval_seconds': 1}
REDIRECT = re.compile(r'^\s*#(?:redirect|đổi|đổi hướng)\s*\[\[([^\]]+)\]\]', re.I)


def _complete_moves(moves):
    """An incomplete global move index cannot prove unique title ownership."""
    try:
        if not isinstance(moves, list):
            raise ValueError('Move index is not a list')
        for event in moves:
            if (not isinstance(event, dict)
                    or any(key in event for key in ('actionhidden', 'titlehidden', 'suppressed'))
                    or not isinstance(event.get('title'), str) or not event['title'].strip()
                    or not isinstance(event.get('params'), dict)
                    or not isinstance(event['params'].get('target_title'), str)
                    or not event['params']['target_title'].strip()):
                raise ValueError('Move title/action details are missing or hidden')
            at_or_before(event['timestamp'], event['timestamp'])
            page, logpage = event.get('pageid'), event.get('logpage')
            visible = [value for value in (page, logpage) if value is not None]
            if (not visible or any(type(value) is not int or value <= 0 for value in visible)
                    or page is not None and logpage is not None and page != logpage):
                raise ValueError('Move page identity is missing or inconsistent')
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise SourceUnavailable('historical_move_proof_incomplete', code='historical_move_proof_incomplete') from error
    return moves


class _CheckedIdentities(HistoricalIdentities):
    def _move_index(self, horizon):
        try:
            return _complete_moves(super()._move_index(horizon))
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            # Native index construction can encounter suppressed event fields
            # before returning. Such source incompleteness is not hash corruption.
            raise SourceUnavailable('historical_move_proof_incomplete', code='historical_move_proof_incomplete') from error

    def resolve(self, title):
        if normalize_title(title).isdecimal():
            raise SourceUnavailable('unsupported_numeric_title', code='unsupported_numeric_title')
        return super().resolve(title)

    def raw_page(self, page_id):
        raw = super().raw_page(page_id)
        redirect = REDIRECT.match(raw.content)
        if redirect and normalize_title(redirect[1].partition('#')[0]).isdecimal():
            raise SourceUnavailable('unsupported_numeric_title', code='unsupported_numeric_title')
        return raw


class _CheckedMovesArchive(CheckedArchive):
    def _moves(self, horizon):
        return _complete_moves(super()._moves(horizon))


def _assert_owned(path, root):
    """Reject output aliases before any writable open, including Windows junctions."""
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root) or not path.resolve().is_relative_to(root.resolve()):
        raise Incompatible('Recovery artifact escapes owned output')
    current = path
    while current != root.parent:
        if current.is_symlink() or current.exists() and getattr(current.stat(follow_symlinks=False), 'st_file_attributes', 0) & 1024:
            raise Incompatible('Recovery artifact is a symlink/reparse point')
        if current == root:
            break
        current = current.parent


class _Overlay(RawCache):
    def __init__(self, root, original, output):
        super().__init__(root)
        self.original = original
        self.output = output

    def put(self, site, revision_id, value):
        _assert_owned(self.root / site / str(revision_id) / (digest(value) + '.json.gz'), self.output)
        return super().put(site, revision_id, value)

    def get(self, ref):
        path = (self.root / ref['path']).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError('Invalid raw cache path')
        _assert_owned(self.root / ref['path'], self.output)
        # Existing corrupt overlay objects cannot be replaced by another copy.
        return super().get(ref) if path.exists() else self.original.get(ref)

    def find(self, site, revision_id):
        _assert_owned(self.root / site / str(revision_id), self.output)
        found = self.original.find(site, revision_id) + super().find(site, revision_id)
        return list({canonical(ref): (ref, raw) for ref, raw in found}.values())


class _MeteredTransport(httpx.BaseTransport):
    def __init__(self, owner, delegate):
        self.owner, self.delegate = owner, delegate

    def handle_request(self, request):
        run = self.owner
        run._reserve_request(request)
        try:
            response = self.delegate.handle_request(request)
            response.read()
        except httpx.TransportError as error:
            run._stop('transport_error', {'exception': type(error).__name__})
            raise
        run._receipt(request, response)
        return response

    def close(self):
        self.delegate.close()


class GraphRecovery:
    version = VERSION
    numeric_titles_supported = False

    def __init__(self, context, output, *, registry_path=None, limits=None, transport=None, sleep=time.sleep):
        self.context = deepcopy(context)
        requested_output = Path(output).absolute()
        if requested_output.is_symlink() or requested_output.exists() and getattr(requested_output.stat(follow_symlinks=False), 'st_file_attributes', 0) & 1024:
            raise Incompatible('Recovery output cannot be a symlink/reparse point')
        self.output = Path(output).resolve()
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        if set(self.limits) != set(DEFAULT_LIMITS):
            raise Incompatible('Unknown graph-recovery acquisition limit')
        for key in ('max_requests', 'max_runtime_seconds', 'max_targets'):
            value = self.limits[key]
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or value <= 0 or value > DEFAULT_LIMITS[key]):
                raise Incompatible('Graph-recovery limit exceeds authorized envelope: ' + key)
            if key != 'max_runtime_seconds' and type(value) is not int:
                raise Incompatible('Recovery request/target caps must be integers')
        if (not isinstance(self.limits['min_interval_seconds'], (int, float))
                or not math.isfinite(self.limits['min_interval_seconds'])
                or self.limits['min_interval_seconds'] < (0 if transport is not None else 1)):
            raise Incompatible('Live graph recovery requires at least one second between starts')
        self.sleep, self.online = sleep, False
        self.active_title = None
        self.failures = []
        self.archive = self.store = self.client = self.lock = None
        original_raw = Path(context['raw_cache']).resolve()
        protected = [original_raw, Path(context['database']).resolve().parent]
        if registry_path:
            protected.append(Path(registry_path).resolve().parent)
        if any(self.output == root or self.output.is_relative_to(root) or root.is_relative_to(self.output)
               for root in protected):
            raise Incompatible('Recovery output overlaps an original source/registry root')
        self.output.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = self.output / 'checkpoint.json'
        self.database = self.output / 'source.sqlite'
        if self.database == Path(context['database']).resolve():
            raise Incompatible('Recovery database must differ from original')
        try:
            for path in (self.checkpoint_path, self.database, self.output / 'raw'):
                _assert_owned(path, self.output)
            self._lock()
            self.archive = CheckedArchive(context, registry_path)
            scope = {'version': self.version, 'context': self.context, 'limits': self.limits,
                     'registry_path': str(Path(registry_path).resolve()) if registry_path else None}
            if self.checkpoint_path.exists():
                envelope = json.loads(self.checkpoint_path.read_text(encoding='utf-8'))
                self.state = envelope['checkpoint']
                if digest(self.state) != envelope['sha256'] or self.state['scope'] != scope:
                    raise Incompatible('Recovery checkpoint checksum/source/limits differ')
                if not self.database.is_file():
                    raise Incompatible('Recovery checkpoint database is missing')
            else:
                if self.database.exists():
                    raise Incompatible('Unbound recovery database cannot be reused')
                temp = self.database.with_suffix('.pending')
                _assert_owned(temp, self.output)
                if temp.exists():
                    raise Incompatible('Interrupted initial backup requires a new output directory')
                with closing(sqlite3.connect(temp)) as clone:
                    self.archive.db.backup(clone)
                temp.replace(self.database)
                self.state = {'scope': scope, 'started_at': None, 'deadline': None,
                              'physical_attempts': 0, 'live_targets': [], 'last_start': None,
                              'online_stopped': False, 'stop_reason': None, 'stop_details': {},
                              'results': {}, 'attempts': [], 'receipts': []}
                self._save()
            self.store = PipelineStore(self.database)
            self.cache = _Overlay(self.output / 'raw', self.archive.cache, self.output)
            self.checked = _CheckedMovesArchive.__new__(_CheckedMovesArchive)
            self.checked.context = self.context
            self.checked.db, self.checked.cache = self.store.db, self.cache
            self.checked.registry = self.archive.registry
            self.checked.move_indexes = {}
            self.checked.resolution_cache = {}
            self.contact = self.archive.config.get('contact')
            self.client = MediaWikiClient(
                self.contact or 'offline-fixture@example.invalid',
                user_agent=self.archive.config.get('user_agent') or 'ViMFC/0.1',
                transport=_MeteredTransport(self, transport or httpx.HTTPTransport(retries=0)),
                sleep=sleep, attempts=1)
            self.mock_transport = transport is not None
            self.source = RevisionSource(self.client, self.store, self.cache, integrity_guard=self._guard)
            self.source.integrity_registry = self.archive.registry
            self.identities = _CheckedIdentities(self.source, self.context['snapshot'])
        except BaseException:
            self.close()
            raise

    def _lock(self):
        _assert_owned(self.output / '.recovery.lock', self.output)
        self.lock = (self.output / '.recovery.lock').open('a+b')
        if os.name == 'nt':
            import msvcrt
            self.lock.seek(0)
            if not self.lock.read(1):
                self.lock.write(b'0')
                self.lock.flush()
            self.lock.seek(0)
            try:
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise Incompatible('Another graph-recovery invocation owns this output') from error
        else:
            import fcntl
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise Incompatible('Another graph-recovery invocation owns this output') from error

    def _save(self):
        temp = self.checkpoint_path.with_suffix('.pending')
        _assert_owned(temp, self.output)
        _assert_owned(self.checkpoint_path, self.output)
        body = canonical({'checkpoint': self.state, 'sha256': digest(self.state)})
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers/virus scanners can briefly deny atomic replacement.
        # Retain the fsynced pending file and old checkpoint while retrying only
        # this local publication; acquisition and budget reservations never rerun.
        for attempt in range(8):
            _assert_owned(temp, self.output)
            _assert_owned(self.checkpoint_path, self.output)
            try:
                temp.replace(self.checkpoint_path)
                break
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 7:
                    raise
                self.sleep(min(0.05 * (2 ** attempt), 0.5))

    def _stop(self, reason, details=None):
        if not self.state['online_stopped']:
            self.state.update(online_stopped=True, stop_reason=reason, stop_details=details or {})
            self._save()

    def _reserve_request(self, request):
        if not self.online:
            raise SourceUnavailable('Recovery cache-only lookup', code='recovery_offline')
        if request.url.host != 'vi.wikipedia.org':
            self._stop('unexpected_host')
            raise SourceUnavailable('Only Wikipedia historical queries are authorized', code='recovery_stop')
        if self.state['online_stopped']:
            raise SourceUnavailable('Recovery online acquisition has stopped', code='recovery_stop')
        now = time.time()
        if self.state['deadline'] is None:
            self.state['started_at'] = now
            self.state['deadline'] = now + self.limits['max_runtime_seconds']
        earliest = max(now, (self.state['last_start'] or 0) + self.limits['min_interval_seconds'])
        if (self.state['physical_attempts'] >= self.limits['max_requests']
                or earliest >= self.state['deadline']):
            self._stop('request_budget' if self.state['physical_attempts'] >= self.limits['max_requests'] else 'runtime_budget')
            raise SourceUnavailable('Recovery acquisition budget exhausted', code='recovery_stop')
        if earliest > now:
            self.sleep(earliest - now)
        started = time.time()
        if started >= self.state['deadline']:
            self._stop('runtime_budget')
            raise SourceUnavailable('Recovery runtime budget exhausted', code='recovery_stop')
        self.state['physical_attempts'] += 1
        self.state['last_start'] = started
        self.state['attempts'].append({'number': self.state['physical_attempts'],
                                      'title': self.active_title, 'started_at': started,
                                      'params': dict(request.url.params), 'state': 'PENDING'})
        # A crash between this fsync and wire dispatch retains a conservative charge.
        self._save()

    def _receipt(self, request, response):
        body = response.content
        sha = hashlib.sha256(body).hexdigest()
        path = self.output / 'responses' / (sha + '.json')
        _assert_owned(path, self.output)
        path.parent.mkdir(exist_ok=True)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise Incompatible('Recovery response artifact was corrupted')
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
        receipt = {'path': path.relative_to(self.output).as_posix(), 'sha256': sha,
                   'request': dict(request.url.params), 'status': response.status_code,
                   'retry_after': response.headers.get('Retry-After')}
        self.state['receipts'].append(receipt)
        self.state['attempts'][-1].update(state='RESPONSE', response_sha256=sha)
        self._save()
        try:
            payload = response.json()
            api_error = payload.get('error', {}).get('code')
        except (ValueError, AttributeError):
            api_error = 'invalid_response'
        if response.status_code >= 400 or api_error:
            self._stop('http_' + str(response.status_code) if response.status_code >= 400 else api_error,
                       {'retry_after': receipt['retry_after'], 'response_sha256': sha})

    def _guard(self, error):
        # HistoricalIdentities may catch optional source failures. Retain each
        # refusal independently so no surviving hint can silently authorize it.
        if error.code not in ('recovery_offline', 'recovery_stop', 'history_unavailable', 'graph_proof_missing'):
            integrity = (error.code in ('source_hash_mismatch', 'invalid_historical_cache')
                         or error.code == 'source_unavailable' and any(
                             word in str(error).lower() for word in ('mismatch', 'future revision', 'invalid historical')))
            self.failures.append({'code': error.code, 'message': str(error),
                                  'details': error.details, 'integrity': integrity})

    def _namespace(self, result, ref, raw):
        exact = []
        for receipt in self.state['receipts']:
            query = receipt['request']
            if str(ref['revision_id']) not in query.get('revids', '').split('|'):
                continue
            path = (self.output / receipt['path']).resolve()
            if not path.is_relative_to(self.output) or hashlib.sha256(path.read_bytes()).hexdigest() != receipt['sha256']:
                raise Incompatible('Namespace response artifact checksum/path differs')
            payload = json.loads(path.read_bytes())
            for page in payload.get('query', {}).get('pages', []):
                for revision in page.get('revisions', []):
                    if revision.get('revid') == ref['revision_id']:
                        slot = revision.get('slots', {}).get('main', {})
                        if (page.get('pageid') != ref['page_id'] or revision.get('timestamp') != ref['revision_timestamp']
                                or slot.get('content') != raw['content'] or revision.get('sha1') != raw['source_sha1']):
                            raise Incompatible('Namespace exact-revision response differs from checked raw')
                        if normalize_title(page.get('title', '')) != normalize_title(result['title']):
                            # ns describes the returned current page, so a move
                            # across namespaces invalidates this as scope proof.
                            continue
                        exact.append({'namespace': page.get('ns'), 'response': receipt,
                                      'basis': 'exact_revision_namespace_response'})
        if exact:
            if any(scope['namespace'] != 0 for scope in exact):
                raise SourceUnavailable('Historical target is outside article namespace', code='graph_namespace_nonzero')
            return exact[0]
        if ':' in result['title']:
            raise SourceUnavailable('Historical target namespace needs exact proof', code='graph_proof_missing')
        return {'namespace': 0, 'basis': 'checked_historical_canonical_title_no_separator'}

    def _ownership(self, title, expected, moves, horizon):
        hints = set()
        ref = self._selected_ref('title', title)
        if not ref:
            # A move hint alone cannot rule out a second current owner.
            raise SourceUnavailable('Historical title-owner lookup proof missing', code='graph_proof_missing')
        self._check_ref(ref, title)
        if not at_or_before(ref['lookup_timestamp'], horizon):
            # Redirect lookups can occur after the initial title query. Their
            # newer ownership horizon must include all intervening move hints.
            horizon = ref['lookup_timestamp']
            moves = self.checked._moves(horizon)
        hints.add(ref['page_id'])
        if self.archive.registry:
            self.archive.registry.check(SourceRevisionRef(**ref))
        for move in moves:
            if normalize_title(move['title']) == title:
                page = move.get('pageid', move.get('logpage'))
                if page:
                    hints.add(page)
        owners, witnesses = [], []
        for page in sorted(hints):
            ref = self._selected_ref('page_id', str(page))
            if not ref:
                raise SourceUnavailable('Historical owner hint lacks a selected revision', code='graph_proof_missing')
            self._check_ref(ref, str(page))
            owner_title = title_at_snapshot(page, ref['title'], self.checked._moves(ref.get('lookup_timestamp')),
                                           self.context['snapshot'])
            raw, ref, owner_moves = self.checked._identity_raw(page, owner_title)
            witnesses.append(self._witness(ref, raw, owner_moves))
            if normalize_title(owner_title) == title:
                owners.append(page)
        if len(owners) > 1:
            raise SourceUnavailable('Multiple verified historical owners', code='historical_ambiguous', details={'owners': owners})
        if not owners:
            raise SourceUnavailable('Historical owner is missing', code='graph_proof_missing')
        if owners[0] != expected:
            raise Incompatible('Resolved historical owner differs from verified unique owner')
        return {'title': title, 'owner': expected, 'hint_witnesses': witnesses,
                'lookup_horizon': horizon, 'move_index_hash': self._move_hash(moves, horizon)}

    def _check_ref(self, ref, identity):
        if (ref.get('site') != 'viwiki' or ref.get('identity') != identity
                or ref.get('snapshot') != self.context['snapshot']
                or not at_or_before(ref['revision_timestamp'], self.context['snapshot'])
                or not ref.get('lookup_timestamp')
                or not at_or_before(self.context['snapshot'], ref['lookup_timestamp'])):
            raise Incompatible('Historical selected-ref identity/snapshot/horizon differs')

    def _selected_ref(self, kind, identity):
        row = self.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                                    ('viwiki', str(identity), self.context['snapshot'])).fetchone()
        return json.loads(row[0]) if row else None

    def _move_hash_matches(self, moves, horizon, expected):
        return digest(moves) == expected

    def _move_hash(self, moves, horizon):
        return digest(moves)

    def _resolution_record(self, title):
        row = self.store.db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                                    (self.context['snapshot'], self._resolution_key(title))).fetchone()
        return json.loads(row[0]) if row else None

    def _resolution_key(self, title):
        return title

    def _title_selector_key(self, title):
        return title

    def _validate(self, title):
        if title.isdecimal() and not self.numeric_titles_supported:
            raise SourceUnavailable('unsupported_numeric_title', code='unsupported_numeric_title')
        result = self._resolution_record(title)
        if result is None:
            raise SourceUnavailable('No checked historical identity resolution', code='graph_proof_missing')
        if result.get('status') != 'resolved':
            return result, None
        if normalize_title(result.get('title', '')).isdecimal() and not self.numeric_titles_supported:
            raise SourceUnavailable('unsupported_numeric_title', code='unsupported_numeric_title')
        if (result.get('snapshot') != self.context['snapshot']
                or result.get('proof', {}).get('kind') != 'historical_revision_and_move_log'):
            raise Incompatible('Historical target resolution proof/snapshot differs')
        initial_ref = self._selected_ref('title', title)
        if not initial_ref:
            raise SourceUnavailable('Initial historical lookup horizon is missing', code='graph_proof_missing')
        self._check_ref(initial_ref, title)
        if self.archive.registry:
            self.archive.registry.check(SourceRevisionRef(**initial_ref))
        initial_moves = self.checked._moves(initial_ref.get('lookup_timestamp'))
        if not self._move_hash_matches(initial_moves, initial_ref['lookup_timestamp'], result['proof'].get('move_index_hash')):
            raise Incompatible('Initial historical lookup move proof differs')
        current, seen, witnesses, ownership = title, set(), [], []
        for step in result.get('chain', []):
            if normalize_title(step.get('target', '')).isdecimal() and not self.numeric_titles_supported:
                raise SourceUnavailable('unsupported_numeric_title', code='unsupported_numeric_title')
            if current in seen or normalize_title(step['original_title']) != current:
                raise Incompatible('Historical redirect chain title/cycle differs')
            seen.add(current)
            ownership.append(self._ownership(current, step['page_id'], initial_moves, initial_ref['lookup_timestamp']))
            raw, ref, moves = self.checked._identity_raw(step['page_id'], current, step['revision_id'])
            match = REDIRECT.match(raw['content'])
            if not match or match[1].partition('#')[0] != step['target']:
                raise Incompatible('Historical redirect target is not proven by raw')
            witnesses.append(self._witness(ref, raw, moves))
            current = normalize_title(step['target'])
        if current in seen or current != normalize_title(result['title']):
            raise Incompatible('Historical redirect closure differs')
        ownership.append(self._ownership(current, result['page_id'], initial_moves, initial_ref['lookup_timestamp']))
        raw, ref, moves = self.checked._identity_raw(result['page_id'], result['title'], result['revision_id'],
                                                   result['revision_timestamp'], result['raw_hash'])
        if raw['content'] != result.get('content') or REDIRECT.match(raw['content']):
            raise Incompatible('Historical target content/redirect closure differs')
        witnesses.append(self._witness(ref, raw, moves))
        namespace = self._namespace(result, ref, raw)
        target = {k: v for k, v in result.items() if k != 'content'}
        proof = {'version': self.version, 'snapshot': self.context['snapshot'], 'lookup_title': title,
                 'initial_ref': initial_ref, 'initial_move_index_hash': self._move_hash(initial_moves, initial_ref['lookup_timestamp']),
                 'witnesses': witnesses, 'ownership': ownership, 'namespace': namespace}
        target['namespace_scope'] = namespace
        target['recovery_proof'] = {**proof, 'manifest_id': digest(proof)}
        return result, target

    def _witness(self, ref, raw, moves):
        row = self.store.db.execute('SELECT record_json FROM source_revisions WHERE site=? AND revision_id=?',
                                    ('viwiki', ref['revision_id'])).fetchone()
        return {'ref': ref, 'cache_ref': json.loads(row[0])['cache_ref'],
                'raw_hash': raw['local_sha256'], 'source_sha1': raw['source_sha1'],
                'move_index_hash': digest(moves)}

    def _result(self, title, cohort, *, prior=None, reason=None, blocked=False):
        try:
            result, target = self._validate(title)
            if blocked:
                target = None
            if target:
                state = 'RESOLVED_VALID' if target['page_id'] in cohort else 'RESOLVED_OUTSIDE_COHORT'
                reason = None
            else:
                reason = reason or result.get('reason', 'unresolved_historical_identity')
                state = 'SOURCE_INTEGRITY_BLOCKED' if blocked else 'UNRESOLVED'
        except SourceUnavailable as error:
            target = None
            reason = reason or error.code
            state = 'SOURCE_INTEGRITY_BLOCKED' if blocked else 'AMBIGUOUS' if error.code == 'historical_ambiguous' else 'UNRESOLVED'
        except (Incompatible, ValueError, TypeError, KeyError, OSError) as error:
            target, state, reason = None, 'SOURCE_INTEGRITY_BLOCKED', str(error)
        output = {'version': self.version, 'title': title, 'snapshot': self.context['snapshot'],
                  'state': state, 'target': target, 'reason': reason}
        if prior is not None:
            output['prior_resolution'] = prior
        return output

    def resolve(self, title, cohort_ids, *, allow_live=False):
        title, cohort = normalize_title(title), set(cohort_ids)
        self.online = False
        if title.isdecimal() and not self.numeric_titles_supported:
            return self._publish(title, {'version': VERSION, 'title': title, 'snapshot': self.context['snapshot'],
                                         'state': 'UNRESOLVED', 'target': None, 'reason': 'unsupported_numeric_title'},
                                 bool(self.state['results'].get(title, {}).get('live_attempted')))
        saved = self.state['results'].get(title)
        if saved:
            if saved['result'].get('target'):
                # Replay is strictly cache-only and rechecks raw, registry and
                # all witnesses. Success bytes must match their original proof.
                result = self._result(title, cohort, prior=saved['result'].get('prior_resolution'))
                if result.get('target') and digest(result['target']) != saved['target_hash']:
                    result.update(state='SOURCE_INTEGRITY_BLOCKED', target=None, reason='replayed_target_proof_differs')
                return result
        attempted = bool(saved and saved.get('live_attempted'))
        self.failures = []
        before = self._result(title, cohort)
        # Missing identity publication can be rebuilt from already checked
        # selected refs, raw and move indexes without dispatching any HTTP.
        # Roll back negative cache writes made by an interrupted offline lookup.
        if before['target'] is None and before['state'] == 'UNRESOLVED':
            self.store.db.execute('SAVEPOINT recovery_offline')
            try:
                negative = self.store.db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                                                  (self.context['snapshot'], self._resolution_key(title))).fetchone()
                negative = json.loads(negative[0]) if negative else None
                if negative is not None and negative.get('status') != 'resolved':
                    self.store.db.execute('DELETE FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                                          (self.context['snapshot'], self._resolution_key(title)))
                cached = self.identities.resolve(title)
                if cached.get('status') == 'resolved':
                    self.store.db.execute('RELEASE recovery_offline')
                    before = self._result(title, cohort, prior=negative)
                else:
                    self.store.db.execute('ROLLBACK TO recovery_offline')
                    self.store.db.execute('RELEASE recovery_offline')
            except (SourceUnavailable, Incompatible, ValueError, TypeError, KeyError, OSError) as error:
                self.store.db.execute('ROLLBACK TO recovery_offline')
                self.store.db.execute('RELEASE recovery_offline')
                if isinstance(error, Incompatible) or any(f['integrity'] for f in self.failures):
                    before.update(state='SOURCE_INTEGRITY_BLOCKED', target=None, reason=str(error))
                elif isinstance(error, SourceUnavailable) and error.code in (
                        'historical_move_proof_incomplete', 'unsupported_numeric_title',
                        'historical_move_coverage_unverified', 'historical_hidden_move_coverage_unverified',
                        'historical_selector_receipt_unverified'):
                    before.update(state='UNRESOLVED', target=None, reason=error.code)
            if any(f['integrity'] for f in self.failures):
                before.update(state='SOURCE_INTEGRITY_BLOCKED', target=None,
                              reason=next(f['code'] for f in self.failures if f['integrity']))
        if before['target'] or before['state'] == 'SOURCE_INTEGRITY_BLOCKED':
            return self._publish(title, before, attempted)
        if before.get('reason') in ('historical_move_proof_incomplete', 'unsupported_numeric_title',
                                    'historical_hidden_move_coverage_unverified'):
            return self._publish(title, before, attempted)
        row = self.store.db.execute('SELECT record_json FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                                    (self.context['snapshot'], self._resolution_key(title))).fetchone()
        prior = json.loads(row[0]) if row else None
        if not allow_live or self.state['online_stopped'] or attempted:
            return self._publish(title, before, attempted)
        if not self.contact and not self.mock_transport:
            before['reason'] = 'configured_source_contact_missing'
            return self._publish(title, before, False)
        if title in self.state['live_targets']:
            before['reason'] = 'interrupted_live_target_attempt'
            return self._publish(title, before, True)
        if len(self.state['live_targets']) >= self.limits['max_targets']:
            self._stop('target_budget')
            before['reason'] = 'target_budget'
            return self._publish(title, before, False)
        self.state['live_targets'].append(title)
        self.state['results'][title] = {'result': {**before, 'reason': 'pending_live_target'},
                                        'live_attempted': True, 'target_hash': None}
        self._save()
        # Only the isolated clone loses negative observations, and their full
        # earlier outcome is retained in the resolution checkpoint.
        if prior is not None and prior.get('status') != 'resolved':
            self.store.db.execute('DELETE FROM identity_resolutions WHERE snapshot=? AND lookup_title=?',
                                  (self.context['snapshot'], self._resolution_key(title)))
        # Cached select failures may predate this bounded recovery; preserve them
        # before allowing one new query for this explicitly scheduled target.
        failures = [dict(r) for r in self.store.db.execute(
            'SELECT * FROM source_failures WHERE site=? AND identity=? AND snapshot=?',
            ('viwiki', self._title_selector_key(title), self.context['snapshot']))]
        self.state['results'][title]['prior_source_failures'] = failures
        self._save()
        self.store.db.execute('DELETE FROM source_failures WHERE site=? AND identity=? AND snapshot=?',
                              ('viwiki', self._title_selector_key(title), self.context['snapshot']))
        self.online, self.active_title = True, title
        self.failures = []
        reason, blocked = None, False
        try:
            self.identities.resolve(title)
        except SourceUnavailable as error:
            reason = error.code
        except (Incompatible, ValueError, TypeError, KeyError, OSError) as error:
            reason, blocked = str(error), True
        finally:
            self.online, self.active_title = False, None
        if self.failures:
            blocked = blocked or any(f['integrity'] for f in self.failures)
            reason = self.failures[0]['code']
        output = self._result(title, cohort, prior=prior, reason=reason, blocked=blocked)
        if self.failures:
            output['source_failures'] = deepcopy(self.failures)
        return self._publish(title, output, True)

    def _publish(self, title, result, attempted):
        self.state['results'][title] = {**self.state['results'].get(title, {}),
                                        'result': result, 'live_attempted': attempted,
                                        'target_hash': digest(result['target']) if result['target'] else None}
        self._save()
        return deepcopy(result)

    def summary(self):
        return {'version': self.version, 'physical_attempts': self.state['physical_attempts'],
                'live_target_attempts': len(self.state['live_targets']), 'limits': dict(self.limits),
                'started_at': self.state['started_at'], 'deadline': self.state['deadline'],
                'online_stopped': self.state['online_stopped'], 'stop_reason': self.state['stop_reason'],
                'stop_details': deepcopy(self.state['stop_details']),
                'resolution_states': dict(Counter(v['result']['state'] for v in self.state['results'].values())),
                'pending_request_reservations': sum(a['state'] == 'PENDING' for a in self.state['attempts']),
                'checkpoint': str(self.checkpoint_path), 'database': str(self.database),
                'raw_overlay': str(self.output / 'raw')}

    def __enter__(self):
        return self

    def close(self):
        if self.client:
            self.client.__exit__(None, None, None)
            self.client = None
        if self.store:
            self.store.__exit__(None, None, None)
            self.store = None
        if self.archive:
            self.archive.__exit__(None, None, None)
            self.archive = None
        if self.lock:
            self.lock.close()
            self.lock = None

    def __exit__(self, *args):
        self.close()
