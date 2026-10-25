import json
from pathlib import Path
import shutil
import subprocess
import pytest


def test_terminal_wrapper_tees_both_streams_and_preserves_native_exit_code(tmp_path):
    shell=shutil.which('powershell') or shutil.which('pwsh')
    if not shell:
        pytest.skip('Windows PowerShell not installed')
    fake=tmp_path/'fake-python.cmd'
    fake.write_text('@echo off\necho fixture-stdout\necho fixture-stderr 1>&2\nexit /b 7\n',encoding='ascii')
    context=tmp_path/'context.json'
    context.write_text(json.dumps({'python':str(fake),'run_id':'fixture-run','database':str(tmp_path/'db'),'report_dir':str(tmp_path/'reports'),'candidates':100}),encoding='utf-8')
    result=subprocess.run([shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(Path('scripts/Invoke-Pilot100.ps1').resolve()),'-ContextFile',str(context)],capture_output=True,text=True)
    assert result.returncode==7, result.stdout+result.stderr
    logs=list((tmp_path/'reports').glob('*.log'))
    assert len(logs)==1
    data=logs[0].read_bytes()
    output=data.decode('utf-16') if data.startswith(b'\xff\xfe') else data.decode('utf-8-sig')
    assert 'fixture-stdout' in output and 'fixture-stderr' in output
    assert logs[0].with_suffix('.exit-code.txt').read_text(encoding='utf-8-sig').strip()=='7'


def test_terminal_wrapper_refuses_non100_context(tmp_path):
    shell=shutil.which('powershell') or shutil.which('pwsh')
    if not shell: pytest.skip('Windows PowerShell not installed')
    context=tmp_path/'context.json'
    context.write_text(json.dumps({'candidates':1000}),encoding='utf-8')
    result=subprocess.run([shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(Path('scripts/Invoke-Pilot100.ps1').resolve()),'-ContextFile',str(context)],capture_output=True,text=True)
    assert result.returncode!=0
    assert '100 candidates' in result.stdout+result.stderr


def test_terminal_wrapper_launch_failure_never_uses_a_previous_success_exit_code(tmp_path):
    shell=shutil.which('powershell') or shutil.which('pwsh')
    if not shell: pytest.skip('Windows PowerShell not installed')
    context=tmp_path/'context.json'
    context.write_text(json.dumps({'python':str(tmp_path/'missing.exe'),'run_id':'fixture-run','database':str(tmp_path/'db'),'report_dir':str(tmp_path/'reports'),'candidates':100}),encoding='utf-8')
    wrapper=Path('scripts/Invoke-Pilot100.ps1').resolve()
    command=f"$global:LASTEXITCODE=0; & '{wrapper}' -ContextFile '{context}'; exit $LASTEXITCODE"
    result=subprocess.run([shell,'-NoProfile','-ExecutionPolicy','Bypass','-Command',command],capture_output=True,text=True)
    assert result.returncode!=0
    exit_files=list((tmp_path/'reports').glob('*.exit-code.txt'))
    assert len(exit_files)==1 and exit_files[0].read_text(encoding='utf-8-sig').strip()!='0'
