import hashlib
import importlib.util
from pathlib import Path
import signal

import pytest
from vimfc.domain import Incompatible


def command():
    spec=importlib.util.spec_from_file_location('fast_command',Path('scripts/fast20k.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_missing_or_modified_model_is_optional_and_logged(tmp_path):
    module=command();model=tmp_path/'model.gguf';model.write_bytes(b'not a model')
    with module.model_service(dict(model_sha256='0'*64),tmp_path/'absent',model,tmp_path) as (client,status):
        assert client is None and status['status']=='UNAVAILABLE'
        assert 'SHA-256 differs' in status['error']


def test_signal_sets_safe_boundary_stop_without_raising(monkeypatch):
    handlers={};module=command()
    monkeypatch.setattr(signal,'signal',lambda number,handler:handlers.update({number:handler}))
    stop=module.install_signals()
    assert not stop()
    handlers[signal.SIGTERM](signal.SIGTERM,None)
    assert stop()


def test_slurm_has_one_cpu_worker_and_no_automatic_submission():
    script=Path('configs/slurm/fast20k.sbatch').read_text()
    for setting in ('--cpus-per-task=4','--mem=16G','--time=3-00:00:00','--gres=none','--no-requeue','--signal=B:TERM@600'):
        assert setting in script
    assert script.count('scripts/fast20k.py run')==1
    assert 'FAST20K_EGRESS_APPROVED' in script and 'FAST20K_COMMIT' in script
    assert '\nsbatch ' not in script and '\nsrun ' not in script
