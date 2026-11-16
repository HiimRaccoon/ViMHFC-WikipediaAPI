"""Bounded historical intake and offline V2 selection in an isolated workspace."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import uuid

from vimfc.domain import Incompatible, SourceUnavailable, canonical, digest, at_or_before, now
from vimfc.source.dump_download import exclusive
from vimfc.source.identities import title_at_snapshot, normalize_title
from vimfc.source.mediawiki import MediaWikiClient
from vimfc.storage.pipeline_store import PipelineStore
from vimfc.storage.raw_cache import RawCache
from .annotation_edges import select_articles, rankable_links, edge_report
from .collection_bundle import inside, read_bundle
from .discovery_policy import SNAPSHOT
from .extract import extract_lead
from .hyperlink_rank import graph_metrics
from .provider import HistoricalProvider
from .quality import assess_quality
from .relaxed_archive import CheckedArchive
from .relaxed_pilot import relaxed_rules
from .seed_only import plan_seed_cohort, seed_policy


class CollectionBudget(Incompatible):
    code = 'collection_budget'
    exit_code = 5

    def __init__(self, message, scope='invocation'):
        super().__init__(message)
        self.scope = scope


def failure_kind(error):
    """Operational disposition only; source validation and edge policy stay intact."""
    message = str(error)
    code = getattr(error, 'code', '') or ''
    sqlite_code = getattr(error, 'sqlite_errorcode', 0) or 0
    cause = getattr(error, '__cause__', None)
    if isinstance(cause, SourceUnavailable) and cause is not error and failure_kind(cause) == 'integrity_fatal':
        return 'integrity_fatal'
    if ((sqlite_code & 255) in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}
            or any(part in code for part in ('integrity', 'hash_mismatch', 'invalid_historical_cache', 'snapshot_mismatch', 'cutoff'))
            or re.search(r'mismatch|checksum|corrupt|future revision|beyond snapshot|ambiguous historical move chain|conflicting raw|disk image is malformed', message, re.I)):
        return 'integrity_fatal'
    if isinstance(error, CollectionBudget):
        return 'budget_exhausted'
    if isinstance(error, KeyboardInterrupt) or isinstance(error, sqlite3.Error) and getattr(error, 'sqlite_errorcode', None) == sqlite3.SQLITE_INTERRUPT:
        return 'interrupted'
    if isinstance(error, SourceUnavailable):
        if code in {'history_unavailable', 'historical_content_unavailable'}:
            return 'missing'
        if code in {'article_namespace_excluded', 'article_model_unsupported'} or message == 'Candidate is not historical wikitext':
            return 'unsupported'
        if error.retryable:
            return 'retryable'
        return 'needs_review'
    if isinstance(error, sqlite3.OperationalError) or isinstance(error, OSError):
        return 'operational_error'
    return 'integrity_fatal'


def _publish_json(path, value):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
            prefix=path.stem + '-', suffix='.pending', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def _progress(store, through):
    states = dict(store.db.execute('SELECT status,count(*) FROM collection_items GROUP BY status'))
    eligible = store.db.execute("SELECT coalesce(sum(json_extract(record_json,'$.quality.eligible')),0) FROM collection_items WHERE status='processed'").fetchone()[0]
    blockers = [json.loads(r[0]) for r in store.db.execute("SELECT record_json FROM collection_items WHERE status='needs_review' ORDER BY page_id")]
    unseen = through - sum(states.values())
    to_acquire = unseen + states.get('retryable', 0)
    to_process = states.get('acquired', 0)
    return {'checkpoint_states': states, 'not_seen': unseen, 'remaining_to_acquire': to_acquire,
            'remaining_to_process': to_process, 'eligible_supply': int(eligible),
            'quality_rejected': states.get('processed', 0) - int(eligible),
            'missing': states.get('missing', 0), 'unsupported': states.get('unsupported', 0),
            'needs_review': states.get('needs_review', 0), 'retryable': states.get('retryable', 0),
            'remaining_in_prefix': to_acquire + to_process + states.get('needs_review', 0),
            'blockers': blockers, 'progress_available': True}


class CollectionArchive(CheckedArchive):
    """New checked articles can supply candidate targets without live title lookups."""
    def resolve(self, title):
        result = super().resolve(title)
        if result.get('reason') != 'no_archived_identity_resolution':
            return result
        matches = self.collection_targets.get(normalize_title(title), [])
        if len(matches) != 1:
            return result if not matches else {'status': 'unresolved', 'reason': 'ambiguous_checked_collection_title'}
        article = matches[0]
        return {'status': 'resolved', 'page_id': article['page_id'], 'title': article['title'],
                'revision_id': article['revision_id'], 'revision_timestamp': article['revision_timestamp'],
                'raw_hash': article['raw_hash'], 'snapshot': article['snapshot'], 'chain': [],
                'proof': {'kind': 'checked_collection_canonical_title',
                          'source_manifest_id': article['source_validation']['manifest_id']}}


def prepare_collection(bundle, output, *, request_limit=100000, target=20000):
    """Only creates a private workspace; acquires no article and starts no pilot."""
    manifest, source, resolver = read_bundle(bundle)
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    if (output.exists() or output.is_relative_to(bundle) or bundle.is_relative_to(output)
            or type(request_limit) is not int or request_limit <= 0
            or type(target) is not int or not 1 <= target <= 20000 or source['snapshot'] != SNAPSHOT):
        raise Incompatible('New workspace, frozen snapshot and positive bounds required')
    inventory = json.loads((bundle / 'inventory.json').read_text(encoding='utf-8'))
    if inventory['inventory_id'] != manifest['inventory_id']:
        raise Incompatible('Bundle inventory binding differs')
    with CheckedArchive(source, bundle / 'registry.sqlite', artifact_resolver=resolver) as archive:
        if inventory['semantic_rules'] != {k: digest(archive.config[k]) for k in ('classification', 'extraction', 'ranking')}:
            raise Incompatible('Inventory semantic rules differ')
        plan = plan_seed_cohort(inventory, seed_policy(archive.config['quotas']), len(inventory['seeds']))
        config = deepcopy(archive.config)
        config['discovery'] = {'version': 'seed-only-2', 'inventory_id': inventory['inventory_id'],
                               'manifest_id': plan['manifest_id'], 'cohort_hash': plan['cohort_hash'],
                               'namespace_policy': 'historical-title-namespace-1', 'variant': 'seed-only'}
        output.mkdir(parents=True)
        with sqlite3.connect(output / 'pipeline.sqlite') as destination:
            archive.db.backup(destination)
        shutil.copytree(source['raw_cache'], output / 'raw-cache')
    with PipelineStore(output / 'pipeline.sqlite') as store:
        run = store.create(SNAPSHOT, config, reuse_run=source['run_id'])
        if store.candidates(run):
            raise Incompatible('New collection unexpectedly inherited a cohort')
        with store.transaction():
            store.db.executemany('INSERT INTO candidates VALUES (?,?,?,?)',
                [(run, c['page_id'], i, canonical(c)) for i, c in enumerate(plan['candidates'])])
        store.db.executescript('''
            CREATE TABLE collection_items(page_id INTEGER PRIMARY KEY,status TEXT NOT NULL,record_json TEXT NOT NULL,record_hash TEXT NOT NULL);
            CREATE TABLE collection_control(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE collection_invocations(id INTEGER PRIMARY KEY,record_json TEXT NOT NULL);
        ''')
        context = {'database': 'pipeline.sqlite', 'raw_cache': 'raw-cache', 'run_id': run,
                   'snapshot': SNAPSHOT, 'config_hash': digest(config), 'cohort_hash': plan['cohort_hash']}
        header = {'format': 'incremental-annotation-1', 'context': context, 'bundle_id': manifest['bundle_id'],
                  'inventory_id': inventory['inventory_id'], 'candidate_count': len(plan['candidates']),
                  'request_limit': request_limit, 'target': target,
                  'quality': 'quality-relaxed-1', 'edge': 'annotation-edge-1', 'rank': 'rank-annotation-1'}
        header['binding_id'] = digest(header)
        store.db.execute('INSERT INTO collection_control VALUES (?,?)', ('binding', canonical(header)))
        store.db.execute('INSERT INTO collection_control VALUES (?,?)', ('attempts', '0'))
        (output / 'workspace.json').write_text(canonical(header), encoding='utf-8')
    return header


def _items(store):
    items = []
    for row in store.db.execute('SELECT * FROM collection_items ORDER BY page_id'):
        record = json.loads(row['record_json'])
        if row['status'] not in {'acquired', 'missing', 'unsupported', 'needs_review', 'retryable', 'processed'} or row['record_hash'] != digest(record) or record['page_id'] != row['page_id']:
            raise Incompatible('Collection checkpoint checksum/identity differs')
        items.append((row['status'], record))
    return items


def _save(store, status, record):
    store.db.execute('INSERT INTO collection_items VALUES (?,?,?,?) ON CONFLICT(page_id) DO UPDATE SET status=excluded.status,record_json=excluded.record_json,record_hash=excluded.record_hash',
                     (record['page_id'], status, canonical(record), digest(record)))


def _checked_acquisition(provider, candidate):
    raw = provider._acquire_raw(candidate)
    ref = raw.ref
    if (ref.site != 'viwiki' or type(ref.page_id) is not int or ref.page_id != candidate['page_id'] or ref.snapshot != SNAPSHOT
            or not isinstance(ref.title, str) or not ref.title.strip()
            or type(ref.revision_id) is not int or ref.revision_id <= 0
            or not at_or_before(ref.revision_timestamp, SNAPSHOT)):
        raise Incompatible('Historical acquisition identity/model/cutoff differs')
    selected = provider.store.db.execute('SELECT record_json FROM snapshot_revision_refs WHERE site=? AND identity=? AND snapshot=?',
                                          ('viwiki', str(ref.page_id), SNAPSHOT)).fetchone()
    if not selected:
        raise Incompatible('Selected historical revision proof missing')
    pin = json.loads(selected[0])
    moves = provider.identities._move_index(pin.get('lookup_timestamp'))
    if (type(pin['page_id']) is not int or type(pin['revision_id']) is not int
            or any(pin[k] != asdict(ref)[k] for k in ('page_id', 'revision_id', 'revision_timestamp', 'snapshot'))
            or title_at_snapshot(ref.page_id, pin['title'], moves, SNAPSHOT) != ref.title):
        raise Incompatible('Historical selected revision/title differs')
    value = provider.source.cache.get(raw.cache_ref)
    registered = provider.store.db.execute('SELECT record_json FROM source_revisions WHERE site=? AND revision_id=?',
                                           ('viwiki', ref.revision_id)).fetchone()
    if not registered or json.loads(registered[0]).get('cache_ref') != raw.cache_ref:
        raise Incompatible('Acquired raw and registered source cache reference mismatch')
    encoded = raw.content.encode('utf-8')
    if (value['content'] != raw.content or type(value['page_id']) is not int or value['page_id'] != ref.page_id or value['timestamp'] != ref.revision_timestamp
            or value['content_model'] != raw.content_model
            or Path(raw.cache_ref['path']).parts[:2] != ('viwiki', str(ref.revision_id))
            or value['local_sha256'] != raw.local_sha256 or value['source_sha1'] != raw.source_sha1
            or hashlib.sha256(encoded).hexdigest() != raw.local_sha256 or hashlib.sha1(encoded).hexdigest() != raw.source_sha1):
        raise Incompatible('Checked raw identity/hash differs')
    if provider.source.integrity_registry:
        provider.source.integrity_registry.check(ref)
    provider._discovery_namespace(raw)
    if raw.content_model != 'wikitext':
        raise SourceUnavailable('Candidate is not historical wikitext', code='article_model_unsupported')
    return {'page_id': ref.page_id, 'revision_id': ref.revision_id, 'raw_hash': raw.local_sha256}


def _metadata(store, source_run, record):
    rows = list(store.db.execute("SELECT result_json,result_hash FROM stage_results WHERE run_id=? AND identity=? AND stage='process' AND status='completed'",
                                 (source_run, str(record['page_id']))))
    if len(rows) > 1:
        raise Incompatible('Ambiguous archived classification metadata')
    if rows:
        old = json.loads(rows[0][0])
        if digest(old) != rows[0][1]:
            raise Incompatible('Archived metadata checkpoint checksum differs')
        if all(old.get(k) == record[k] for k in ('page_id', 'revision_id', 'raw_hash', 'snapshot')):
            classification = deepcopy(old['classification'])
            classification.pop('items', None)
            return classification
    return {'outcome': 'unclassified', 'primary_entity_type': None, 'subtypes': [],
            'reason': 'classification_optional_collection'}


def collect_batch(bundle, workspace, *, mode, through, batch_size=250, max_requests=1000,
                  allow_network=False, client_factory=None):
    """One invocation only. Caller chooses each next prefix; no scale progression."""
    workspace = Path(workspace).resolve()
    if not (workspace / 'workspace.json').is_file():
        raise Incompatible('Prepared workspace required')
    if (mode not in ('acquire', 'process') or type(through) is not int or through <= 0
            or type(batch_size) is not int or not 1 <= batch_size <= 1000
            or type(max_requests) is not int or max_requests < 0):
        raise Incompatible('Invalid collection bounds/mode')
    if mode == 'acquire' and not allow_network:
        raise Incompatible('Acquisition requires explicit network authorization')
    started = time.monotonic()
    with exclusive(workspace / '.writer.lock'):
        manifest, source_context, resolver = read_bundle(bundle)
        header = json.loads((workspace / 'workspace.json').read_text(encoding='utf-8'))
        if (header.get('binding_id') != digest({k: v for k, v in header.items() if k != 'binding_id'})
                or header['bundle_id'] != manifest['bundle_id'] or through > header['candidate_count']):
            raise Incompatible('Collection input binding/prefix differs')
        context = {**header['context'], 'database': str(inside(workspace, header['context']['database'])),
                   'raw_cache': str(inside(workspace, header['context']['raw_cache']))}
        with PipelineStore(context['database']) as store, CheckedArchive(source_context, Path(bundle) / 'registry.sqlite', artifact_resolver=resolver) as source:
            if canonical(header) != store.db.execute("SELECT value FROM collection_control WHERE key='binding'").fetchone()[0]:
                raise Incompatible('Collection checkpoint binding differs')
            store.require_config(context['run_id'], store.run(context['run_id'])['config'])
            candidates = store.candidates(context['run_id'])
            if digest(candidates) != context['cohort_hash'] or store.run(context['run_id'])['config_hash'] != context['config_hash']:
                raise Incompatible('Frozen collection config/cohort differs')
            items = _items(store)
            fatal = store.db.execute("SELECT value FROM collection_control WHERE key='fatal'").fetchone()
            if fatal:
                raise Incompatible('Previous fatal collection stop requires review: ' + fatal[0])
            recorded = {r['page_id'] for status, r in items}
            done = {r['page_id'] for status, r in items if status != 'retryable'}
            if not recorded <= {c['page_id'] for c in candidates[:through]}:
                raise Incompatible('Cannot reduce the processed inventory prefix')
            processed = [r for status, r in items if status == 'processed']
            eligible = sum(r['quality']['eligible'] for r in processed)
            attempted = 0

            def guard(request):
                nonlocal attempted
                if request.url.host != 'vi.wikipedia.org' or request.url.params.get('maxlag') != '5':
                    raise Incompatible('Only maxlag=5 Vietnamese Wikipedia acquisition is allowed')
                charged = int(store.db.execute("SELECT value FROM collection_control WHERE key='attempts'").fetchone()[0])
                if charged >= header['request_limit']:
                    raise CollectionBudget('Physical collection request budget exhausted', 'collection')
                if attempted >= max_requests:
                    raise CollectionBudget('Physical invocation request budget exhausted')
                last = store.db.execute("SELECT value FROM collection_control WHERE key='last_request'").fetchone()
                if last:
                    delay = max(0, float(last[0]) + 1 - time.time())
                    if delay > 5:
                        raise Incompatible('Acquisition clock moved backwards; inspect before resume')
                    time.sleep(delay)
                with store.transaction():
                    store.db.execute("UPDATE collection_control SET value=? WHERE key='attempts'", (str(charged + 1),))
                    store.db.execute("INSERT INTO collection_control VALUES ('last_request',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(time.time()),))
                attempted += 1

            failure = None; caught = None; count = 0; selection = None
            active_page = None; phase = mode; receipt_errors = []
            invocation = str(uuid.uuid4()); started_at = now()
            try:
                if mode == 'acquire' and eligible < header['target']:
                    cfg = store.run(context['run_id'])['config']
                    factory = client_factory or (lambda: MediaWikiClient(cfg['contact'], user_agent=cfg.get('user_agent') or 'ViMFC/0.1', attempts=1))
                    with factory() as client:
                        client.client.event_hooks['request'].append(guard)
                        provider = HistoricalProvider(store, context['run_id'], client, RawCache(context['raw_cache']), classification_required=False)
                        # Carry the original read-only refusals; never bind or write that registry.
                        provider.source.integrity_registry = source.registry
                        for candidate in candidates[:through]:
                            if candidate['page_id'] in done:
                                continue
                            active_page = candidate['page_id']
                            try:
                                _save(store, 'acquired', _checked_acquisition(provider, candidate))
                            except SourceUnavailable as error:
                                kind = failure_kind(error)
                                if kind not in {'missing', 'unsupported', 'needs_review', 'retryable'}:
                                    raise
                                _save(store, kind, {'page_id': candidate['page_id'], 'reason': error.code,
                                    'message': str(error), 'retry_at': error.retry_at, 'disposition': kind})
                                if kind == 'retryable':
                                    raise
                            count += 1
                            if count >= batch_size:
                                break
                elif mode == 'process':
                    acquired = {r['page_id'] for status, r in items if status == 'acquired'}
                    with CollectionArchive(context) as archive:
                        archive.registry = source.registry
                        try:
                            for record in processed:
                                active_page = record['page_id']
                                _, proof = archive.article_source(record['page_id'])
                                if proof != record['source_validation']:
                                    raise Incompatible('Processed source provenance changed before selection')
                            for candidate in candidates[:through]:
                                page = candidate['page_id']
                                if page not in acquired or eligible >= header['target']:
                                    continue
                                active_page = page
                                raw, proof = archive.article_source(page)
                                ref = proof['article']
                                record = {'page_id': page, 'title': ref['title'], 'revision_id': ref['revision_id'],
                                          'revision_timestamp': ref['revision_timestamp'], 'raw_hash': ref['content_hash'],
                                          'snapshot': SNAPSHOT, 'source_validation': proof,
                                          'extraction': asdict(extract_lead(raw['content'], archive.config['extraction'])),
                                          'article_kinds': archive.article_kinds(raw['content'], ref['title'])}
                                record['classification'] = _metadata(store, source_context['run_id'], record)
                                record['quality'] = assess_quality(record['extraction'], record['classification'], record['article_kinds'], relaxed_rules(archive.config))
                                _save(store, 'processed', record)
                                eligible += record['quality']['eligible']; count += 1
                                if count >= batch_size:
                                    break
                            records = [r for status, r in _items(store) if status == 'processed']
                            active_page = None; phase = 'selection'
                            archive.collection_targets = {}
                            for record in records:
                                if 'redirect' not in record['article_kinds']:
                                    archive.collection_targets.setdefault(normalize_title(record['title']), []).append(record)
                            selection = select_articles(records, archive, archive.config['quotas'], target=header['target'])
                            selection['selection'] = asdict(selection['selection'])
                            selected = set(selection['selection']['page_ids'])
                            selection['graph'] = graph_metrics(rankable_links(a for a in selection['articles'] if a['page_id'] in selected))
                            selection['edges'] = edge_report(selection['articles'])
                            selection['pilot_accepted'] = False
                            phase = 'publication'
                            _publish_json(workspace / 'selection.json', selection)
                        finally:
                            archive.registry = None
            except BaseException as error:
                caught = error
                failure = {'type': type(error).__name__, 'message': str(error), 'code': getattr(error, 'code', None),
                           'disposition': failure_kind(error), 'page_id': active_page, 'phase': phase,
                           'retry_at': getattr(error, 'retry_at', None)}
                if failure['disposition'] == 'integrity_fatal':
                    try:
                        store.db.execute("INSERT OR REPLACE INTO collection_control VALUES ('fatal',?)", (canonical(failure),))
                    except sqlite3.Error as receipt_error:
                        receipt_errors.append(str(receipt_error))
            finally:
                try:
                    progress = _progress(store, through)
                    charged = int(store.db.execute("SELECT value FROM collection_control WHERE key='attempts'").fetchone()[0])
                except (sqlite3.Error, ValueError) as receipt_error:
                    progress = {key: None for key in ('remaining_to_acquire', 'remaining_to_process', 'eligible_supply',
                                'missing', 'unsupported', 'needs_review', 'retryable', 'remaining_in_prefix')}
                    progress.update(progress_available=False, blockers=[]); charged = None
                    receipt_errors.append(str(receipt_error))
                    if caught is None:
                        caught = receipt_error
                kind = failure_kind(caught) if caught else ('needs_review' if progress.get('blockers') else 'completed')
                if caught is not None and failure is None:
                    failure = {'type': type(caught).__name__, 'message': str(caught), 'phase': 'receipt', 'disposition': kind}
                exit_code = {'completed': 0, 'missing': 2, 'unsupported': 2, 'needs_review': 2, 'budget_exhausted': 5, 'retryable': 75,
                             'operational_error': 75, 'interrupted': 130, 'integrity_fatal': 3}[kind]
                summary = {'invocation_id': invocation, 'started_at': started_at, 'finished_at': now(),
                           'mode': mode, 'through': through, 'processed_this_invocation': count,
                           'physical_requests': attempted, 'cumulative_requests': charged,
                           'elapsed_seconds': round(time.monotonic() - started, 3), **progress,
                           'status': kind, 'expected_stop': kind in {'completed', 'budget_exhausted', 'retryable', 'interrupted'},
                           'exit_code': exit_code, 'stop_reason': failure,
                           'budget_scope': getattr(caught, 'scope', None),
                           'blocker': failure if kind in {'integrity_fatal', 'operational_error'} else None,
                           'selection_published': selection is not None and caught is None,
                           'receipt_errors': receipt_errors, 'pilot_accepted': False}
                if summary['blocker']:
                    summary['blockers'].append(summary['blocker'])
                try:
                    store.db.execute('INSERT INTO collection_invocations(record_json) VALUES (?)', (canonical(summary),))
                except sqlite3.Error as receipt_error:
                    receipt_errors.append(str(receipt_error))
                    if caught is None:
                        caught = receipt_error
                        reason = {'type': type(caught).__name__, 'message': str(caught), 'phase': 'receipt'}
                        summary['blockers'].append(reason)
                        summary.update(status=failure_kind(caught), expected_stop=False, exit_code=75, blocker=reason, stop_reason=reason)
                try:
                    _publish_json(workspace / 'last-invocation.json', summary)
                except OSError as receipt_error:
                    receipt_errors.append(str(receipt_error))
                    if caught is None:
                        caught = receipt_error
                        reason = {'type': type(caught).__name__, 'message': str(caught), 'phase': 'receipt'}
                        summary['blockers'].append(reason)
                        summary.update(status='operational_error', expected_stop=False, exit_code=75, blocker=reason, stop_reason=reason)
                if caught is not None:
                    caught.collection_receipt = summary
            if caught is not None:
                raise caught
            return {'summary': summary, 'selection': selection}
