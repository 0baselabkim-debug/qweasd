"""Offline tests for this finite user-approved launcher; no provider HTTP."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('manual_start',ROOT/'manual_collection/launch.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


@pytest.fixture
def control(tmp_path,monkeypatch):
    p=tmp_path/'control.json';value=dict(asof=m.DAY,approval='USER_START_NOW_CANCEL_AUTOMATIC_SCHEDULE',max_total_http=5600,runtime_commit=m.SOURCE,
        not_before='2026-10-30T12:00:00+00:00',expires_at='2026-10-30T14:50:00+00:00')
    p.write_text(json.dumps(value));monkeypatch.setattr(m,'CONTROL',p)
    monkeypatch.setenv('GITHUB_RUN_ID','123');monkeypatch.setenv('GITHUB_SHA','a'*40)
    return value


def test_manual_context_does_not_forge_schedule_event_or_creation_time(control):
    from collector_v1.regular import policy
    c=m.context({'created_at':'2026-10-30T12:01:00Z'})
    assert c['asof']=='2026-10-30' and c['trigger']=='USER_REQUEST_START_NOW'
    assert c['run_created_at']=='2026-10-30T12:01:00Z'
    assert c['scheduled_at']==control['not_before'] and c['original_planned_slot']=='2030'
    assert (policy.timestamp(c['expires_at'])-policy.timestamp(c['scheduled_at'])).total_seconds()==170*60
    policy.window(c,policy.timestamp('2026-10-30T12:02:00Z'))
    with pytest.raises(Exception):policy.window(c,policy.timestamp('2026-10-31T00:01:00+09:00'))


def test_actor_blocks_other_branch_before_any_GitHub_or_KIS(control,monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY',m.REPO);monkeypatch.setenv('GITHUB_REF','refs/heads/main')
    monkeypatch.setenv('GITHUB_EVENT_NAME','push');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    with pytest.raises(ValueError,match='actor_scope'):m.actor()


def test_wrong_date_is_not_replaced_by_current_date(control,monkeypatch):
    control['not_before']='2026-10-31T12:00:00+00:00';m.CONTROL.write_text(json.dumps(control))
    with pytest.raises(ValueError,match='target_date_changed'):m.context({'created_at':'2026-10-31T12:00:00Z'})


def test_combined_HTTP_limit_blocks_before_request(control,monkeypatch):
    from types import SimpleNamespace
    from collector_v1.regular import policy
    monkeypatch.setattr(m,'actor',lambda:control);monkeypatch.setattr(policy,'window',lambda c:None)
    monkeypatch.setattr(policy,'access_proof',lambda *a:None)
    g=m.Guard({'expires_at':'2026-10-30T15:00:00Z'},None,None,{})
    g.apis=[SimpleNamespace(budget=SimpleNamespace(used=4)),SimpleNamespace(budget=SimpleNamespace(used=5596))]
    with pytest.raises(Exception,match='combined_physical'):g()


def test_source_runtime_and_operating_data_are_not_changed():
    import subprocess
    assert subprocess.run(['git','diff','--exit-code',m.SOURCE,'--','collector_v1','kis_shared','snapshot','.github/config','.github/workflows/kis-market-2030.yml'],cwd=m.RUNTIME).returncode==0
    text=(ROOT/'.github/workflows/kis-manual-now-20261030.yml').read_text()
    assert 'schedule:' not in text and 'cron:' not in text and 'group: kis-validation-auth' in text
    assert 'ref: '+m.SOURCE in text and 'branches: [fix/manual-start-20261030]' in text
