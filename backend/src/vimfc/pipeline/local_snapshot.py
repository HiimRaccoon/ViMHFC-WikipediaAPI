"""Phase 2A synthetic-only local snapshots; no acquisition or publication integration."""
from contextlib import closing, ExitStack
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import tempfile

from vimfc.domain import Incompatible, canonical, digest, at_or_before
from vimfc.storage.raw_cache import RawCache
from .collection_bundle import read_bundle
from .relaxed_archive import CheckedArchive
from .relaxed_preservation import file_hash


def filesystem(path):
    if os.name == 'nt':
        import ctypes
        return 'local' if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 3 else 'network'
    result = subprocess.run(['findmnt', '-n', '-o', 'FSTYPE', '--target', str(path)],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip()


def require_local(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise Incompatible('Symlink/junction forbidden')
    existing = path
    while not existing.exists():
        existing = existing.parent
    if filesystem(existing) not in ('local', 'ext4'):
        raise Incompatible('Local filesystem required; network/unknown filesystem refused')


def read_json(path):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise Incompatible('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs)


def safe_path(root, name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))
            or PurePosixPath(name).as_posix() != name):
        raise Incompatible('Unsafe artifact path')
    path = Path(root) / name
    require_local(path)
    return path


def inventory(root):
    result = {}
    for path in sorted(Path(root).rglob('*')):
        require_local(path)
        if path.is_file() and path.relative_to(root).as_posix() != 'manifest.json':
            result[path.relative_to(root).as_posix()] = {
                'bytes': path.stat().st_size, 'sha256': file_hash(path)}
    return result


def no_sidecars(path):
    if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise Incompatible('SQLite sidecars require a separately reviewed capture boundary')


def readonly(path):
    require_local(path)
    no_sidecars(path)
    # immutable avoids even SHM creation; permitted only at caller's stopped boundary.
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    return db


def compatibility(db, runtime):
    return {'runtime': runtime, 'user_version': db.execute('PRAGMA user_version').fetchone()[0],
            'schema_hash': digest([tuple(r) for r in db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")])}


def verify_workspace(root, bundle):
    """Read-only actual incremental schema; no fabricated source/provenance fields."""
    root = Path(root)
    require_local(root); require_local(bundle)
    header = read_json(root / 'workspace.json')
    if header.get('format') != 'incremental-annotation-1' or header.get('binding_id') != digest(
            {k: v for k, v in header.items() if k != 'binding_id'}):
        raise Incompatible('Workspace header binding differs')
    if header['context']['database'] != 'pipeline.sqlite' or header['context']['raw_cache'] != 'raw-cache':
        raise Incompatible('Unsupported workspace layout')
    # Bundle verification is file-only. SQLite in the bundle is never opened here.
    inventory(bundle)
    manifest, source_context, resolver = read_bundle(bundle)
    if manifest['bundle_id'] != header['bundle_id'] or manifest['inventory_id'] != header['inventory_id']:
        raise Incompatible('Bundle binding differs')
    context = {**header['context'], 'database': str(root / 'pipeline.sqlite'), 'raw_cache': str(root / 'raw-cache')}
    with closing(readonly(root / 'pipeline.sqlite')) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise Incompatible('SQLite integrity failed')
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise Incompatible('Unsupported schema')
        control = dict(db.execute('SELECT key,value FROM collection_control'))
        if read_json(root / 'workspace.json') != json.loads(control['binding']) or 'fatal' in control:
            raise Incompatible('Checkpoint binding/fatal state differs')
        attempts = int(control['attempts'])
        if not 0 <= attempts <= header['request_limit']:
            raise Incompatible('Request counter outside budget')
        # Preserve and validate every cache reference reachable from JSON DB fields.
        cache = RawCache(root / 'raw-cache')
        registered = {}
        for row in db.execute('SELECT site,revision_id,record_json FROM source_revisions'):
            ref = json.loads(row[2])['cache_ref']
            safe_path(root / 'raw-cache', ref['path'])
            if Path(ref['path']).parts[:2] != (row[0], str(row[1])):
                raise Incompatible('Registered raw path/revision differs')
            raw = cache.get(ref)
            encoded = raw['content'].encode('utf-8')
            if (type(raw['page_id']) is not int or not isinstance(raw['content_model'], str)
                    or not at_or_before(raw['timestamp'], context['snapshot'])
                    or hashlib.sha256(encoded).hexdigest() != raw['local_sha256']
                    or hashlib.sha1(encoded).hexdigest() != raw['source_sha1']):
                raise Incompatible('Registered raw provenance differs')
            registered[(row[0], row[1])] = raw
        for row in db.execute('SELECT record_json FROM snapshot_revision_refs'):
            ref = json.loads(row[0])
            raw = registered.get((ref['site'], ref['revision_id']))
            if raw and (raw['page_id'] != ref['page_id'] or raw['timestamp'] != ref['revision_timestamp']
                        or ref['snapshot'] != context['snapshot']):
                raise Incompatible('Selected source/raw provenance differs')
        def walk(value):
            if isinstance(value, dict):
                ref = value.get('cache_ref')
                if ref is not None:
                    safe_path(root / 'raw-cache', ref['path'])
                    raw = cache.get(ref)
                    if 'content' in raw:
                        encoded = raw['content'].encode('utf-8')
                        if (hashlib.sha256(encoded).hexdigest() != raw['local_sha256']
                                or hashlib.sha1(encoded).hexdigest() != raw['source_sha1']):
                            raise Incompatible('Raw content provenance hash differs')
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [r[1] for r in db.execute('PRAGMA table_info(' + quoted + ')') if r[1].endswith('_json')]
            for column in columns:
                for row in db.execute('SELECT "' + column + '" FROM ' + quoted):
                    if row[0] is not None: walk(json.loads(row[0]))
        stages = list(db.execute("SELECT result_json,result_hash FROM stage_results WHERE status='completed'"))
        if any(digest(json.loads(r[0])) != r[1] for r in stages):
            raise Incompatible('Stage checkpoint hash differs')
        rows = list(db.execute('SELECT * FROM collection_items ORDER BY page_id'))
        state = [(r['page_id'], r['status'], r['record_hash']) for r in rows]
    # CheckedArchive validates real selected revisions, move proofs, cutoff and namespace.
    with ExitStack() as stack:
        # Audit old adapters against closed local copies; never let them create
        # SHM or change journal files in the immutable source bundle.
        audit = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='snapshot-audit-', dir=root.parent)))
        for original, name in ((Path(source_context['database']), 'source.sqlite'), (Path(bundle) / 'registry.sqlite', 'registry.sqlite')):
            require_local(original)
            wal = Path(str(original) + '-wal')
            if (wal.exists() and wal.stat().st_size) or Path(str(original) + '-journal').exists():
                raise Incompatible('Bundle has pending WAL/journal; stopped capture required')
            with closing(sqlite3.connect(original.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as src:
                with closing(sqlite3.connect(audit / name)) as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                        raise Incompatible('Audit backup mode differs')
        source_archive = stack.enter_context(CheckedArchive({**source_context, 'database':str(audit / 'source.sqlite')}, audit / 'registry.sqlite', artifact_resolver=resolver))
        archive = stack.enter_context(CheckedArchive(context))
        archive.registry = source_archive.registry
        # The source archive owns the registry connection.
        stack.callback(setattr, archive, 'registry', None)
        candidates = {c['page_id'] for c in archive.cohort}
        for row in rows:
            record = json.loads(row['record_json'])
            if (row['status'] not in {'acquired','processed','missing','unsupported','needs_review','retryable'}
                    or row['page_id'] not in candidates or record['page_id'] != row['page_id']
                    or digest(record) != row['record_hash']):
                raise Incompatible('Collection checkpoint identity/hash differs')
            if row['status'] in ('acquired', 'processed'):
                raw, proof = archive.article_source(row['page_id'])
                article = proof['article']
                if record['revision_id'] != article['revision_id'] or record['raw_hash'] != article['content_hash']:
                    raise Incompatible('Collection source identity differs')
                if row['status'] == 'processed' and record['source_validation'] != proof:
                    raise Incompatible('Processed provenance differs')
        policy_hashes = {key:digest(archive.config[key]) for key in ('classification','extraction','ranking')}
    return {'binding': header, 'policy_hashes':policy_hashes, 'checkpoint_hash': digest(state), 'attempts': attempts,
            'records': len(rows), 'selection': 'stale' if (root / 'selection.json').exists() else 'absent'}


def verify_generation(root, *, trusted, bundle, compatible):
    root = Path(root); require_local(root)
    require_local(root / 'manifest.json')
    raw = (root / 'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != trusted['manifest_sha256']:
        raise Incompatible('Trusted manifest hash differs')
    manifest = read_json(root / 'manifest.json')
    if raw != canonical(manifest).encode('utf-8'):
        raise Incompatible('Noncanonical manifest')
    if set(manifest) != {'format','identity','compatibility','checkpoint','artifacts'} or manifest['format'] != 'generation-manifest-v1':
        raise Incompatible('Unsupported/malformed generation manifest')
    if manifest['identity'] != trusted['identity'] or manifest['compatibility'] != compatible:
        raise Incompatible('Trusted identity/runtime/schema differs')
    if (set(compatible) != {'runtime','user_version','schema_hash'} or not isinstance(compatible['runtime'], str)
            or not compatible['runtime'] or type(compatible['user_version']) is not int or compatible['user_version'] != 1):
        raise Incompatible('Unsupported runtime/schema metadata')
    identity = manifest['identity']
    if set(identity) != {'workspace_id','run_id','attempt_id','epoch_id','generation_id','bundle_id'} or any(
            not isinstance(v, str) or not v for v in identity.values()):
        raise Incompatible('Malformed generation identity')
    for name, entry in manifest['artifacts'].items():
        safe_path(root, name)
        if (not isinstance(entry, dict) or set(entry) != {'bytes','sha256'} or type(entry['bytes']) is not int or entry['bytes'] < 0
                or not isinstance(entry['sha256'], str) or re.fullmatch('[0-9a-f]{64}', entry['sha256']) is None):
            raise Incompatible('Malformed artifact metadata')
    if inventory(root) != manifest['artifacts']:
        raise Incompatible('Generation file set/hash differs')
    no_sidecars(root / 'pipeline.sqlite')
    with closing(readonly(root / 'pipeline.sqlite')) as db:
        if compatibility(db, compatible['runtime']) != compatible:
            raise Incompatible('Actual SQLite schema differs')
    checkpoint = verify_workspace(root, bundle)
    if checkpoint != manifest['checkpoint'] or checkpoint['binding']['context']['run_id'] != identity['run_id'] or checkpoint['binding']['bundle_id'] != identity['bundle_id']:
        raise Incompatible('Generation checkpoint binding differs')
    return {'verified': True, 'generation_id': identity['generation_id'], **checkpoint}


def snapshot(source, output, *, bundle, identity, runtime, stopped_writer=False, synthetic=False):
    if not stopped_writer or not synthetic:
        raise Incompatible('Explicit synthetic stopped-writer boundary required')
    require_local(source)
    source, output = Path(source).resolve(), Path(output).absolute()
    for path in (source, output, bundle): require_local(path)
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise Incompatible('Fresh separate output required')
    before = inventory(source)
    no_sidecars(source / 'pipeline.sqlite')
    output.mkdir(parents=True)
    for entry in source.iterdir():
        if entry.name in ('pipeline.sqlite', '.writer.lock'): continue
        if entry.is_dir(): shutil.copytree(entry, output / entry.name)
        else: shutil.copy2(entry, output / entry.name)
    with closing(readonly(source / 'pipeline.sqlite')) as db, closing(sqlite3.connect(output / 'pipeline.sqlite')) as backup:
        db.backup(backup)
        if backup.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower() != 'delete':
            raise Incompatible('Backup journal mode differs')
    no_sidecars(output / 'pipeline.sqlite')
    checkpoint = verify_workspace(output, bundle)
    with closing(readonly(output / 'pipeline.sqlite')) as db:
        compat = compatibility(db, runtime)
    if inventory(source) != before:
        raise Incompatible('Source mutated across stopped boundary')
    manifest = {'format':'generation-manifest-v1', 'identity':identity, 'compatibility':compat,
                'checkpoint':checkpoint, 'artifacts':inventory(output)}
    content = canonical(manifest).encode('utf-8')
    trusted = {'identity':identity, 'manifest_sha256':hashlib.sha256(content).hexdigest()}
    # Verify before a completed manifest exists; interruption leaves an untrusted partial directory.
    pending = output / 'manifest.json'
    pending.write_bytes(content)
    try:
        receipt = verify_generation(output, trusted=trusted, bundle=bundle, compatible=compat)
    except BaseException:
        pending.unlink()
        raise
    return {'trusted':trusted, 'compatible':compat, 'receipt':receipt}


def restore(generation, output, *, trusted, bundle, compatible, synthetic=False):
    if not synthetic: raise Incompatible('Synthetic restore only')
    verify_generation(generation, trusted=trusted, bundle=bundle, compatible=compatible)
    output = Path(output); require_local(output)
    if output.exists(): raise Incompatible('Fresh restore directory required')
    shutil.copytree(generation, output)
    return verify_generation(output, trusted=trusted, bundle=bundle, compatible=compatible)

