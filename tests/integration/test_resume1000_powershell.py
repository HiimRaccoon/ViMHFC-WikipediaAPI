"""Run the real wrapper against a fake CLI; never run a source pilot."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from vimfc.domain import canonical
from vimfc.storage.pipeline_store import PipelineStore


@pytest.mark.parametrize('deferred_only', [False, True])
def test_resume_wrapper_logs_both_streams_preserves_exit5_and_never_runs_retry(tmp_path, config, deferred_only):
    shell = shutil.which('powershell') or shutil.which('pwsh')
    if not shell:
        pytest.skip('PowerShell unavailable')
    # This wrapper resumes the historical lead-3 run, not the new default lead-4.
    from copy import deepcopy
    config=deepcopy(config)
    config['version']='pipeline-5'
    config['extraction']=json.loads(Path('configs/legacy/extraction-lead-3.json').read_text(encoding='utf-8'))
    saved=deepcopy(config)
    for key in ('classification','extraction','ranking'):
        (tmp_path/(key+'.json')).write_text(canonical(saved.pop(key)),encoding='utf-8')
        saved[key+'_file']=key+'.json'
    config_path=tmp_path/'pipeline.json'
    config_path.write_text(canonical(saved),encoding='utf-8')
    db = tmp_path/'db'
    with PipelineStore(db) as store:
        run = store.create('2026-10-25T03:00:00Z', config)
        stamp = store.run(run)
        store.db.executemany('INSERT INTO candidates VALUES (?,?,?,?)',
            [(run, i, i, canonical({'page_id': i})) for i in range(1, 1001)])
        store.db.execute('INSERT INTO pilot_reports VALUES (?,?,?)',
            (run, 100, canonical({'completed': True, 'source_failure_count': 0})))
        if deferred_only:
            store.db.executemany('INSERT INTO candidate_work VALUES (?,?,?,?,?,?,?)',
                [(run,i,'deferred',canonical({'page_id':i,'status':'deferred'}),'2000-01-01T00:00:00Z',1,'2000-01-01T00:00:00Z') for i in range(1,1001)])
    fake = tmp_path/'python.cmd'
    fake.write_text('@echo off\nif "%1"=="-u" goto pilot\n'
                    f'"{sys.executable}" %*\nexit /b %errorlevel%\n:pilot\n'
                    'echo %*\necho fixture-stdout\necho fixture-stderr 1>&2\nexit /b 5\n', encoding='ascii')
    root = tmp_path/'reports'/run
    context = tmp_path/'context.json'
    context.write_text(json.dumps({'python': str(fake), 'database': str(db), 'run_id': run,
        'snapshot': stamp['snapshot'], 'config_hash': stamp['config_hash'],
        'config': str(config_path), 'report_root': str(root)}), encoding='utf-8')
    script = Path('scripts/Resume-Pilot1000.ps1').resolve()
    result = subprocess.run([shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                             '-ContextFile', str(context), '-MaxWaitSeconds', '0', '-RetryRounds', '0'] + (['-DeferredOnly'] if deferred_only else []),
                            capture_output=True, text=True)
    assert result.returncode == 5, result.stdout + result.stderr
    logs = list((root/'1000').glob('*.log'))
    assert len(logs) == 1
    data = logs[0].read_bytes()
    output = data.decode('utf-16') if data.startswith(b'\xff\xfe') else data.decode('utf-8-sig')
    assert 'fixture-stdout' in output and 'fixture-stderr' in output
    assert '--candidates 1000' in output and '--retry-rounds 0' in output
    assert 'pipeline retry' not in output
    assert ('--deferred-only' in output) == deferred_only
    assert logs[0].with_suffix('.exit-code.txt').read_text(encoding='utf-8-sig').strip() == '5'


def test_resume_wrapper_rejects_unknown_context_without_starting_native_work(tmp_path):
    shell = shutil.which('powershell') or shutil.which('pwsh')
    if not shell:
        pytest.skip('PowerShell unavailable')
    context = tmp_path/'context.json'
    context.write_text('{"run_id":"wrong"}', encoding='utf-8')
    result = subprocess.run([shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                             str(Path('scripts/Resume-Pilot1000.ps1').resolve()), '-ContextFile', str(context)],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert not list(tmp_path.rglob('*.log'))


def test_checkout_launcher_exposes_new_scheduler_options_even_with_stale_installed_package():
    result = subprocess.run([sys.executable, '-B', str(Path('scripts/vimfc-local.py').resolve()),
                             'pipeline', 'pilot', '--help'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '--max-wait-seconds' in result.stdout and '--retry-rounds' in result.stdout
