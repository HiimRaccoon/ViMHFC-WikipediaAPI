"""Synthetic local publication only. Caller expectations are not a production authority."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import uuid

from vimfc.domain import Incompatible, canonical
from vimfc.source.dump_download import exclusive
from . import local_snapshot as s


TRANSITIONS = {'CREATED': 'STAGED', 'STAGED': 'VERIFIED', 'VERIFIED': 'COMMITTED'}
IDENTITY_KEYS = {'workspace_id', 'run_id', 'attempt_id', 'epoch_id', 'generation_id', 'bundle_id'}


def pointer(value):
    if (not isinstance(value, dict) or set(value) != {'identity', 'manifest_sha256', 'parent_generation'}
            or not isinstance(value['identity'], dict) or set(value['identity']) != IDENTITY_KEYS
            or any(not isinstance(v, str) or not v for v in value['identity'].values())
            or re.fullmatch('[a-f0-9]{64}', str(value['manifest_sha256'])) is None):
        raise Incompatible('Missing/malformed trusted pointer expectation')
    for name in (value['identity']['generation_id'], value['parent_generation']):
        if name is not None and (not isinstance(name, str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,100}', name) is None):
            raise Incompatible('Unsafe generation ID')
    return value


def read_canonical(path):
    s.require_local(path)
    value = s.read_json(path)
    if Path(path).read_bytes() != canonical(value).encode('utf-8'):
        raise Incompatible('Noncanonical control JSON')
    return value


def verify_bytes(root, expected):
    """File-only verification; never open destination SQLite."""
    pointer(expected)
    root = Path(root)
    s.require_local(root / 'manifest.json')
    if s.file_hash(root / 'manifest.json') != expected['manifest_sha256']:
        raise Incompatible('Manifest digest differs')
    manifest = read_canonical(root / 'manifest.json')
    if (manifest.get('format') != 'generation-manifest-v1'
            or manifest.get('identity') != expected['identity']):
        raise Incompatible('Generation identity differs')
    if s.inventory(root) != manifest['artifacts']:
        raise Incompatible('Transferred file set/size/hash differs')
    s.no_sidecars(root / 'pipeline.sqlite')
    return manifest


def current(root):
    path = Path(root) / 'latest.json'
    s.require_local(path)
    return pointer(read_canonical(path)) if path.exists() else None


def atomic_json(path, value, hook=lambda _: None, *, expected_pointer=None):
    s.require_local(path)
    pending = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    with pending.open('xb') as stream:
        hook('during_pointer_temp' if path.name == 'latest.json' else 'during_receipt')
        stream.write(canonical(value).encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    if path.name == 'latest.json':
        hook('before_pointer_replace')
        if current(path.parent) != expected_pointer:
            raise Incompatible('Pointer changed during publication')
    os.replace(pending, path)
    if path.name == 'latest.json': hook('after_pointer_replace')


def publish(source, root, *, bundle, compatible, expectations, synthetic=False,
            protected_inputs=(), fault=lambda _: None):
    if (not synthetic or not isinstance(expectations, dict)
            or set(expectations) != {'target', 'expected_pointer', 'authorized'}
            or expectations['authorized'] is not True):
        raise Incompatible('Explicit synthetic publication authorization/expectations required')
    target = pointer(expectations['target'])
    prior = expectations['expected_pointer']
    if prior is not None: pointer(prior)
    if target['parent_generation'] != (prior['identity']['generation_id'] if prior else None):
        raise Incompatible('Parent expectation differs')
    if prior and (target['identity']['generation_id'] == prior['identity']['generation_id'] or any(
            target['identity'][k] != prior['identity'][k] for k in ('workspace_id', 'run_id', 'bundle_id'))):
        raise Incompatible('Publication chain identity differs')
    root = Path(root)
    # Check the storage root itself, including overlap in either direction.
    s.require_local(root)
    for protected in (source, bundle, *protected_inputs):
        s.require_local(protected)
        a, b = root.resolve(), Path(protected).resolve()
        if a.is_relative_to(b) or b.is_relative_to(a):
            raise Incompatible('Publication storage overlaps protected input')
    root = root.resolve()
    final = s.separate_destination(root / target['identity']['generation_id'], ())
    s.verify_generation(source, trusted={'identity': target['identity'],
                        'manifest_sha256': target['manifest_sha256']}, bundle=bundle, compatible=compatible)
    source_before = (s.inventory(source), s.file_hash(Path(source) / 'manifest.json'))
    root.mkdir(parents=True, exist_ok=True)
    lock = root / '.publication.lock'
    s.require_local(lock)
    # Same-host cooperating publishers only; not distributed fencing.
    with exclusive(lock):
        if current(root) != prior: raise Incompatible('Unexpected prior pointer; stale publisher')
        if prior: verify_bytes(root / prior['identity']['generation_id'], prior)
        if final.exists(): raise Incompatible('Immutable generation already exists')
        attempt = uuid.uuid4().hex
        stage = s.separate_destination(root / ('.staging-' + attempt), (source, bundle, *protected_inputs))
        receipt = root / ('attempt-' + attempt + '.json')
        state = 'CREATED'
        def record():
            atomic_json(receipt, {'state': state, 'target': target, 'stage': stage.name}, fault)
        def advance(next_state):
            nonlocal state
            if TRANSITIONS.get(state) != next_state: raise Incompatible('Invalid publication transition')
            state = next_state
            record()
        fault('before_staging')
        stage.mkdir()
        record()
        for entry in sorted(Path(source).iterdir()):
            fault('during_copy')
            if entry.is_dir(): shutil.copytree(entry, stage / entry.name)
            else: shutil.copy2(entry, stage / entry.name)
        advance('STAGED')
        fault('during_hash_verification')
        manifest = verify_bytes(stage, target)
        if source_before != (s.inventory(source), s.file_hash(Path(source) / 'manifest.json')):
            raise Incompatible('Source generation changed during transfer')
        advance('VERIFIED')
        fault('before_promotion')
        if stage.stat().st_dev != root.stat().st_dev:
            raise Incompatible('Cross-filesystem promotion refused')
        if final.exists(): raise Incompatible('Immutable generation already exists')
        stage.rename(final)
        fault('after_promotion')
        verify_bytes(final, target)
        if current(root) != prior: raise Incompatible('Pointer changed before publication')
        atomic_json(root / 'latest.json', target, fault, expected_pointer=prior)
        advance('COMMITTED')
        return {'state': state, 'pointer': target, 'receipt': receipt.name,
                'bytes_transferred': sum(v['bytes'] for v in manifest['artifacts'].values())
                    + (final / 'manifest.json').stat().st_size}


def recover(root, *, expected_current, expected_previous=None, synthetic=False):
    """Read-only inspection. Never promote, fall back, unlock or delete."""
    if not synthetic: raise Incompatible('Synthetic recovery only')
    root = Path(root)
    try:
        s.require_local(root)
        if expected_current is None:
            raise Incompatible('No independently trusted current generation')
        pointer(expected_current)
        if current(root) != expected_current: raise Incompatible('Current pointer expectation differs')
        verify_bytes(root / expected_current['identity']['generation_id'], expected_current)
        parent = expected_current['parent_generation']
        if parent is not None:
            if expected_previous is None or pointer(expected_previous)['identity']['generation_id'] != parent:
                raise Incompatible('Trusted previous generation required for retention')
            if any(expected_previous['identity'][k] != expected_current['identity'][k]
                   for k in ('workspace_id', 'run_id', 'bundle_id')):
                raise Incompatible('Previous generation chain differs')
            verify_bytes(root / parent, expected_previous)
        elif expected_previous is not None:
            raise Incompatible('Unexpected previous generation')
        return {'status': 'VERIFIED_CURRENT', 'retention': 'PASS' if parent else 'BOOTSTRAP_ONE_GENERATION',
                'current': expected_current, 'previous': expected_previous,
                'preserved_entries': sorted(p.name for p in root.iterdir()),
                'publication_receipt_authority': 'UNVERIFIED'}
    except (Incompatible, OSError, ValueError, KeyError, TypeError) as error:
        return {'status': 'UNKNOWN_REQUIRES_OPERATOR_REVIEW', 'reason': str(error)}

