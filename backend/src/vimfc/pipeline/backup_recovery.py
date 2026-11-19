"""Offline file transfer and fresh restore for synthetic generations."""
from copy import deepcopy
from pathlib import Path, PurePosixPath
import shutil

from . import generation_publication as publication
from . import local_snapshot as snapshot
from .trusted_control import require


def _noop(_point):
    pass


def _targets(current, previous):
    current = deepcopy(publication.pointer(current))
    parent = current['parent_generation']
    if parent is None:
        require(previous is None, 'Unexpected previous expectation')
        return (current,)
    previous = deepcopy(publication.pointer(previous))
    require(parent == previous['identity']['generation_id']
            and parent != current['identity']['generation_id']
            and previous['parent_generation'] not in (parent, current['identity']['generation_id'])
            and all(current['identity'][k] == previous['identity'][k]
                    for k in ('workspace_id', 'run_id', 'bundle_id')), 'Generation ancestry differs')
    return current, previous


def _verify(root, target):
    manifest = publication.verify_bytes(root, target)
    files = set(manifest['artifacts']) | {'manifest.json'}
    require({'pipeline.sqlite', 'workspace.json'} <= files, 'Required generation files missing')
    dirs = {p.as_posix() for name in files for p in PurePosixPath(name).parents if p.parts}
    for entry in root.rglob('*'):
        name = entry.relative_to(root).as_posix()
        require((entry.is_dir() and name in dirs) or (entry.is_file() and name in files),
                'Unexpected generation entry')
    return manifest


def _pair(root, targets):
    require(publication.current(root) == targets[0], 'Backup pointer differs from trusted current')
    manifests = [_verify(root / target['identity']['generation_id'], target) for target in targets]
    require(publication.current(root) == targets[0], 'Backup pointer changed during verification')
    return manifests


def _copy(source, output, manifest, fault):
    output.mkdir()
    for name in sorted(set(manifest['artifacts']) | {'manifest.json'}):
        origin = snapshot.safe_path(source, name)
        target = snapshot.safe_path(output, name)
        fault('during_copy')
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream, origin.open('rb') as original:
            shutil.copyfileobj(original, stream)


def transfer_backup(source, output, *, expected_current, expected_previous=None,
                    synthetic=False, fault=_noop):
    """Copy a verified publication into a fresh fixture, committing its pointer last."""
    require(synthetic is True, 'Explicit synthetic backup required')
    targets = _targets(expected_current, expected_previous)
    source = Path(source)
    output = snapshot.separate_destination(output, (source,))
    manifests = _pair(source, targets)
    output.mkdir(parents=True)
    for target, manifest in zip(targets, manifests):
        generation = target['identity']['generation_id']
        _copy(source / generation, output / generation, manifest, fault)
    fault('before_backup_verification')
    for target in targets:
        _verify(output / target['identity']['generation_id'], target)
    _pair(source, targets)
    publication.atomic_json(output / 'latest.json', targets[0], fault)
    return {'current': targets[0], 'previous': targets[1] if len(targets) == 2 else None}


def _check_authority(authority, targets, compatible):
    view = authority.inspect()
    require(view['status'] == 'ACKNOWLEDGED' and view['eligible'],
            'Recovery requires acknowledged authority')
    state = view['state']
    intent = state['publication']
    require(state['owner']['state'] != 'PUBLISHING'
            and (intent is None or intent['state'] == 'RECONCILED_COMMITTED'),
            'Unresolved publication intent requires operator review')
    current = targets[0]
    require(state['runtime'] == compatible['runtime']
            and state['binding'] == {k: current['identity'][k] for k in ('workspace_id', 'run_id', 'bundle_id')},
            'Authority binding or runtime differs')
    if state['current'] is not None:
        require((state['current'], state['previous']) == (current, targets[1] if len(targets) == 2 else None),
                'Authority generation differs from trusted expectations')
    return view


def restore_backup(source, output, *, expected_current, compatible, bundle, authority,
                   expected_previous=None, synthetic=False, fault=_noop):
    """Verify the copy before any SQLite access. Never adopt or reactivate it."""
    require(synthetic is True, 'Explicit synthetic restore required')
    targets = _targets(expected_current, expected_previous)
    compatible = deepcopy(compatible)
    source = Path(source)
    control_paths = (authority.path, authority.witness.path)
    for path in (*control_paths, Path(bundle)):
        snapshot.require_local(path)
        require(not path.resolve().is_relative_to(source.resolve()),
                'Local validation input overlaps backup source')
    output = snapshot.separate_destination(output, (source, bundle, *control_paths))
    manifest = _pair(source, targets)[0]
    require(manifest['compatibility'] == compatible and set(compatible) == {'runtime', 'schema_hash', 'user_version'}
            and bool(compatible['runtime']) and type(compatible['runtime']) is str
            and type(compatible['user_version']) is int and compatible['user_version'] == 1,
            'Trusted runtime/schema metadata differs')
    target = targets[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    _copy(source / target['identity']['generation_id'], output, manifest, fault)
    fault('before_restore_verification')
    _verify(output, target)
    _pair(source, targets)
    before = _check_authority(authority, targets, compatible)
    receipt = snapshot.verify_generation(output, trusted=target, bundle=bundle, compatible=compatible)
    _verify(output, target)
    require(publication.current(source) == target, 'Backup pointer changed during restore')
    require(_check_authority(authority, targets, compatible) == before, 'Authority changed during restore')
    return {**receipt, 'current': target, 'previous': targets[1] if len(targets) == 2 else None,
            'selection_epoch_bound': False}

