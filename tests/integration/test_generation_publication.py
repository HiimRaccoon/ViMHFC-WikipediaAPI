import copy
from pathlib import Path

import pytest

from vimfc.domain import Incompatible, canonical
from vimfc.pipeline import generation_publication as p
from vimfc.pipeline import local_snapshot as s
from test_local_snapshot import workspace


def generation(tmp_path, workspace, name='g1'):
    bundle, work, identity = workspace
    source = tmp_path / ('source-' + name)
    result = s.snapshot(work, source, bundle=bundle, identity={**identity, 'generation_id': name},
                        runtime='fixture', stopped_writer=True, synthetic=True)
    target = {**result['trusted'], 'parent_generation': None}
    return source, result['compatible'], {'target': target, 'expected_pointer': None, 'authorized': True}


def publish(tmp_path, workspace, source, compatible, expectations, **kwargs):
    return p.publish(source, tmp_path / 'storage', bundle=workspace[0], compatible=compatible,
                     expectations=expectations, synthetic=True, **kwargs)


def all_bytes(root):
    return {f.relative_to(root).as_posix(): f.read_bytes() for f in root.rglob('*') if f.is_file()}


def test_publish_retention_and_no_destination_sqlite(tmp_path, workspace, monkeypatch):
    source, compatible, expectations = generation(tmp_path, workspace)
    before = all_bytes(source), all_bytes(workspace[0]), all_bytes(workspace[1])
    connect = s.sqlite3.connect
    def local_only(database, *args, **kwargs):
        assert 'storage' not in str(database)
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(s.sqlite3, 'connect', local_only)
    one = publish(tmp_path, workspace, source, compatible, expectations)
    root = tmp_path / 'storage'
    assert one['state'] == 'COMMITTED' and one['bytes_transferred'] > 0
    assert p.verify_bytes(root / 'g1', expectations['target'])
    old = all_bytes(root / 'g1')
    assert p.recover(root, expected_current=expectations['target'], synthetic=True)['retention'] == 'BOOTSTRAP_ONE_GENERATION'
    two_source, compatible, two = generation(tmp_path, workspace, 'g2')
    two['expected_pointer'] = expectations['target']
    two['target']['parent_generation'] = 'g1'
    publish(tmp_path, workspace, two_source, compatible, two)
    report = p.recover(root, expected_current=two['target'], expected_previous=expectations['target'], synthetic=True)
    assert report['retention'] == 'PASS' and report['status'] == 'VERIFIED_CURRENT'
    assert all_bytes(root / 'g1') == old
    assert (all_bytes(source), all_bytes(workspace[0]), all_bytes(workspace[1])) == before
    assert all(state in p.TRANSITIONS for state in ('CREATED', 'STAGED', 'VERIFIED'))


@pytest.mark.parametrize('boundary', ['before_staging', 'during_copy', 'during_hash_verification',
    'before_promotion', 'after_promotion', 'during_pointer_temp', 'before_pointer_replace',
    'after_pointer_replace', 'during_receipt'])
def test_interrupted_publication_never_chooses_pending(tmp_path, workspace, boundary):
    source, compatible, one = generation(tmp_path, workspace)
    publish(tmp_path, workspace, source, compatible, one)
    root = tmp_path / 'storage'
    old = all_bytes(root / 'g1')
    source, compatible, two = generation(tmp_path, workspace, 'g2')
    two['expected_pointer'] = one['target']; two['target']['parent_generation'] = 'g1'
    copies = 0
    def fail(point):
        nonlocal copies
        if point == 'during_copy':
            copies += 1
            if copies == 1: return
        # Receipt failure after replacement exercises ambiguous success, not just startup.
        if point == boundary and (point != 'during_receipt' or p.current(root) == two['target']):
            raise OSError('injected ' + point)
    with pytest.raises(OSError, match='injected'):
        publish(tmp_path, workspace, source, compatible, two, fault=fail)
    post_replace = boundary in ('after_pointer_replace', 'during_receipt')
    assert p.current(root) == (two['target'] if post_replace else one['target'])
    before = all_bytes(root)
    report = p.recover(root, expected_current=one['target'], synthetic=True)
    assert report['status'] == ('UNKNOWN_REQUIRES_OPERATOR_REVIEW' if post_replace else 'VERIFIED_CURRENT')
    if post_replace:
        assert p.recover(root, expected_current=two['target'], expected_previous=one['target'], synthetic=True)['status'] == 'VERIFIED_CURRENT'
    assert all_bytes(root) == before
    assert all_bytes(root / 'g1') == old
    if boundary == 'during_copy':
        assert any(next(root.glob('.staging-*')).iterdir())


@pytest.mark.parametrize('fault', ['corrupt', 'partial', 'extra'])
def test_bad_transfer_preserves_prior(tmp_path, workspace, fault):
    source, compatible, one = generation(tmp_path, workspace)
    publish(tmp_path, workspace, source, compatible, one)
    source, compatible, two = generation(tmp_path, workspace, 'g2')
    two['expected_pointer'] = one['target']; two['target']['parent_generation'] = 'g1'
    before = all_bytes(source)
    root = tmp_path / 'storage'
    def damage(point):
        if point != 'during_hash_verification': return
        stage = next(root.glob('.staging-*'))
        if fault == 'corrupt': (stage / 'workspace.json').write_bytes(b'corrupt')
        if fault == 'partial': (stage / 'workspace.json').unlink()
        if fault == 'extra': (stage / 'extra').write_bytes(b'unexpected')
    with pytest.raises(Incompatible):
        publish(tmp_path, workspace, source, compatible, two, fault=damage)
    assert p.current(root) == one['target'] and not (root / 'g2').exists()
    assert all_bytes(source) == before
    assert list(root.glob('.staging-*'))


@pytest.mark.parametrize('fault', ['corrupt', 'missing', 'pointer', 'manifest'])
def test_recovery_rejects_corrupt_current_without_fallback(tmp_path, workspace, fault):
    source, compatible, expectations = generation(tmp_path, workspace)
    publish(tmp_path, workspace, source, compatible, expectations)
    root = tmp_path / 'storage'
    if fault == 'corrupt': (root / 'g1/pipeline.sqlite').write_bytes(b'bad')
    if fault == 'missing': (root / 'g1/workspace.json').unlink()
    if fault == 'manifest': (root / 'g1/manifest.json').write_bytes(b'bad')
    if fault == 'pointer': (root / 'latest.json').write_text(canonical({**expectations['target'], 'manifest_sha256': '0'*64}))
    before = all_bytes(root)
    assert p.recover(root, expected_current=expectations['target'], synthetic=True)['status'] == 'UNKNOWN_REQUIRES_OPERATOR_REVIEW'
    assert all_bytes(root) == before


@pytest.mark.parametrize('fault', ['unauthorized', 'missing', 'parent', 'hash', 'identity', 'path'])
def test_untrusted_expectations_rejected_without_output(tmp_path, workspace, fault):
    source, compatible, expectations = generation(tmp_path, workspace)
    expectations = copy.deepcopy(expectations)
    if fault == 'unauthorized': expectations['authorized'] = False
    if fault == 'missing': del expectations['expected_pointer']
    if fault == 'parent': expectations['target']['parent_generation'] = 'unknown'
    if fault == 'hash': expectations['target']['manifest_sha256'] = '0'*64
    if fault == 'identity': expectations['target']['identity']['run_id'] = 'wrong'
    if fault == 'path': expectations['target']['identity']['generation_id'] = '../escape'
    with pytest.raises(Incompatible): publish(tmp_path, workspace, source, compatible, expectations)
    assert not (tmp_path / 'storage').exists()


def test_stale_concurrent_and_existing_generation(tmp_path, workspace):
    source, compatible, one = generation(tmp_path, workspace)
    def concurrent(point):
        if point == 'before_staging':
            with pytest.raises(Incompatible, match='locked'):
                publish(tmp_path, workspace, source, compatible, one)
    publish(tmp_path, workspace, source, compatible, one, fault=concurrent)
    source, compatible, two = generation(tmp_path, workspace, 'g2')
    with pytest.raises(Incompatible, match='stale'):
        publish(tmp_path, workspace, source, compatible, two)
    two['expected_pointer'] = one['target']; two['target']['parent_generation'] = 'g1'
    (tmp_path / 'storage/g2').mkdir()
    before = all_bytes(tmp_path / 'storage')
    with pytest.raises(Incompatible): publish(tmp_path, workspace, source, compatible, two)
    assert all_bytes(tmp_path / 'storage') == before


@pytest.mark.parametrize('tree', ['source', 'bundle', 'explicit'])
def test_storage_overlap_refused(tmp_path, workspace, tree):
    source, compatible, expectations = generation(tmp_path, workspace)
    explicit = tmp_path / 'protected'; explicit.mkdir()
    root = {'source': source, 'bundle': workspace[0], 'explicit': explicit}[tree]
    before = all_bytes(root)
    with pytest.raises(Incompatible, match='overlaps'):
        p.publish(source, root / 'nested', bundle=workspace[0], compatible=compatible,
                  expectations=expectations, synthetic=True, protected_inputs=(explicit,))
    assert all_bytes(root) == before and not (root / 'nested').exists()


def test_cross_device_rename_failure_is_not_replaced_by_copy(tmp_path, workspace, monkeypatch):
    import errno
    source, compatible, expectations = generation(tmp_path, workspace)
    def cross_device(*args): raise OSError(errno.EXDEV, 'synthetic cross-device rename')
    monkeypatch.setattr(Path, 'rename', cross_device)
    with pytest.raises(OSError): publish(tmp_path, workspace, source, compatible, expectations)
    root = tmp_path / 'storage'
    assert p.current(root) is None and not (root / 'g1').exists()
    assert list(root.glob('.staging-*'))


def test_state_receipts_and_repeat_immutable_generation(tmp_path, workspace):
    source, compatible, expectations = generation(tmp_path, workspace)
    root = tmp_path / 'storage'
    observed = []
    def inspect(point):
        if point in ('during_copy', 'during_hash_verification', 'before_promotion'):
            observed.append(s.read_json(next(root.glob('attempt-*.json')))['state'])
    result = publish(tmp_path, workspace, source, compatible, expectations, fault=inspect)
    assert observed[0] == 'CREATED' and 'STAGED' in observed and observed[-1] == 'VERIFIED'
    assert s.read_json(root / result['receipt'])['state'] == 'COMMITTED'
    before = all_bytes(root)
    with pytest.raises(Incompatible): publish(tmp_path, workspace, source, compatible, expectations)
    assert all_bytes(root) == before


def test_no_trusted_pointer_and_noncanonical_pointer_require_review(tmp_path, workspace):
    source, compatible, expectations = generation(tmp_path, workspace)
    publish(tmp_path, workspace, source, compatible, expectations)
    root = tmp_path / 'storage'
    assert p.recover(root, expected_current=None, synthetic=True)['status'] == 'UNKNOWN_REQUIRES_OPERATOR_REVIEW'
    with (root / 'latest.json').open('ab') as stream: stream.write(b'\n')
    before = all_bytes(root)
    assert p.recover(root, expected_current=expectations['target'], synthetic=True)['status'] == 'UNKNOWN_REQUIRES_OPERATOR_REVIEW'
    assert all_bytes(root) == before


def test_unexpected_pointer_change_before_replace_is_not_overwritten(tmp_path, workspace):
    source, compatible, expectations = generation(tmp_path, workspace)
    unexpected = {**expectations['target'], 'manifest_sha256': '0' * 64}
    def change(point):
        if point == 'before_pointer_replace':
            (tmp_path / 'storage/latest.json').write_text(canonical(unexpected), encoding='utf-8')
    with pytest.raises(Incompatible, match='Pointer changed'):
        publish(tmp_path, workspace, source, compatible, expectations, fault=change)
    assert p.current(tmp_path / 'storage') == unexpected

