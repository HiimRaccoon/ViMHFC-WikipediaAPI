from vimfc.pipeline.report import ProgressWindow


def test_rolling_progress_drops_old_speed_and_does_not_invent_early_eta():
    progress=ProgressWindow(100,window_seconds=60)
    assert progress.observe(0,0)['eta_seconds'] is None
    progress.observe(60,60)
    progress.observe(61,61)
    result=progress.observe(70,80)
    assert result['speed_per_minute']==120
    assert result['eta_seconds']==10
