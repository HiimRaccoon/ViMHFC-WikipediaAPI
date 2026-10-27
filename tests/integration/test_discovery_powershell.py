import json,shutil,subprocess
from pathlib import Path
import pytest

@pytest.mark.parametrize('size',[100,1000])
def test_discovery_wrapper_keeps_both_streams_native_exit_and_stage_size(tmp_path,size):
    shell=shutil.which('powershell') or shutil.which('pwsh')
    if not shell:pytest.skip('PowerShell unavailable')
    fake=tmp_path/'fake.cmd';fake.write_text('@echo off\necho %*\necho fixture-out\necho fixture-err 1>&2\nexit /b 5\n',encoding='ascii')
    context=tmp_path/'context.json';context.write_text(json.dumps({'python':str(fake),'reports':str(tmp_path/'reports'),'run_id':'fixture','variant':'hybrid'}),encoding='utf-8')
    wrapper=Path('scripts/Invoke-DiscoveryPilot.ps1').resolve()
    result=subprocess.run([shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper),'-ContextFile',str(context),'-Size',str(size),'-MaxRequests','120','-RetryRounds','0','-MaxWaitSeconds','0'],capture_output=True,text=True)
    assert result.returncode==5,result.stdout+result.stderr
    logs=list((tmp_path/'reports/fixture'/str(size)).glob('*.log'));assert len(logs)==1
    data=logs[0].read_bytes();log=data.decode('utf-16') if data.startswith(b'\xff\xfe') else data.decode('utf-8-sig')
    assert 'fixture-out' in log and 'fixture-err' in log
    assert f'--size {size}' in log and '--max-requests 120' in log and '--retry-rounds 0' in log
    assert logs[0].with_suffix('.exit-code.txt').read_text(encoding='utf-8-sig').strip()=='5'
