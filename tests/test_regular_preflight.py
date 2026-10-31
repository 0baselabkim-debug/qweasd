"""Read-only Nov2 checks. Synthetic metadata, no market/auth/remote mutation."""
import copy
import datetime as dt
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock
import pytest
from collector_v1.core import Failure
from collector_v1.regular import policy
from kis_shared.lease import initial
from scripts import preflight_regular_20261102 as audit


def snapshot():
    return dict(repository=dict(default_branch='main'),main_commit='f36ab970428564e7569ae37d61f2cfa3f644e119',
        workflow=dict(state='active',path=policy.WORKFLOW),lease=initial(),
        ledger=dict(schema=1,year='2026',slots={'2026-10-30/2030':dict(status='FAILED')}))


def control():
    return dict(mode='READ_ONLY_NO_PROVIDER_HTTP',target='2026-11-02',request_id='offline-preflight-fixture',
        main_commit='f36ab970428564e7569ae37d61f2cfa3f644e119',
        not_before='2026-10-31T17:00:00Z',expires_at='2026-10-31T18:00:00Z')


def test_nov2_utc_kst_and_existing_failure_do_not_skip_or_claim():
    s=snapshot();old=copy.deepcopy(s);r=audit.assess('.',s,control(),dict(KIS_APP_KEY=True,KIS_APP_SECRET=True))
    assert r['status']=='PASS_READ_ONLY_CONFIGURATION' and r['target_slot_status']=='UNCLAIMED'
    assert r['scheduled_KST']=='2026-11-02T20:30:00+09:00' and r['scheduled_UTC']=='2026-11-02T11:30:00+00:00'
    assert r['expires_KST']=='2026-11-02T23:20:00+09:00' and r['generation_id']=='kis-regular-20261102-2030'
    assert r['provider_HTTP']==0 and not r['slot_claimed'] and s==old
    assert r['KRX_official_target_day_confirmation']=='NOT_VERIFIED_BY_THIS_AUDIT'


@pytest.mark.parametrize('status',['CLAIMED','PREPARING','COLLECTING','INTERNAL_PASS','COMPLETE_PROVISIONAL','FAILED','BLOCKED'])
def test_existing_target_slot_is_never_reset(status):
    s=snapshot();s['ledger']['slots']['2026-11-02/2030']=dict(status=status);before=copy.deepcopy(s)
    r=audit.assess('.',s,control(),dict(KIS_APP_KEY=True,KIS_APP_SECRET=True))
    assert r['status']=='BLOCKED_READ_ONLY_CONFIGURATION' and r['target_slot_status']==status and s==before


@pytest.mark.parametrize('fault',['secret','workflow','main','branch','owner'])
def test_configuration_faults_are_blocked_without_mutation(fault):
    s=snapshot();presence=dict(KIS_APP_KEY=True,KIS_APP_SECRET=True)
    if fault=='secret':presence['KIS_APP_SECRET']=False
    if fault=='workflow':s['workflow']['state']='disabled_manually'
    if fault=='main':s['main_commit']='a'*40
    if fault=='branch':s['repository']['default_branch']='other'
    if fault=='owner':s['lease']['owner']=dict(repository='0baselabkim-debug/qweasd',run_id='99',commit='a'*40,id='a'*32,acquired_at='2026-11-02T11:30:00Z',expires_at='2026-11-02T14:30:00Z')
    before=copy.deepcopy(s);r=audit.assess('.',s,control(),presence)
    assert r['status']=='BLOCKED_READ_ONLY_CONFIGURATION' and r['errors'] and s==before


def test_audit_never_receives_actual_provider_credentials_or_reruns():
    env=dict(GITHUB_REPOSITORY=audit.REPO,GITHUB_REF='refs/heads/feat/decision-evidence-v2',GITHUB_RUN_ATTEMPT='1')
    clock=policy.timestamp('2026-10-31T17:30:00Z');audit.check_control(control(),env,clock)
    for change in [dict(KIS_APP_KEY='SYNTHETIC_NOT_A_SECRET'),dict(GITHUB_RUN_ATTEMPT='2'),dict(GITHUB_REF='refs/heads/main')]:
        with pytest.raises(ValueError):audit.check_control(control(),dict(env,**change),clock)
    with pytest.raises(ValueError,match='window'):audit.check_control(control(),env,policy.timestamp('2026-10-31T18:00:00Z'))


def test_metadata_transport_is_github_get_only(monkeypatch):
    run=Mock(return_value=subprocess.CompletedProcess([],0,b'{"state":"active"}',b''));monkeypatch.setattr(audit.subprocess,'run',run)
    assert audit.github_get('repos/'+audit.REPO+'/actions/workflows/kis-market-2030.yml')['state']=='active'
    args=run.call_args.args[0];assert args[-2:]==['--method','GET'] and '--input' not in args
    for path in ['https://openapi.koreainvestment.com:9443/oauth2/tokenP','repos/'+audit.REPO+'/actions/secrets','repos/'+audit.REPO+'/actions/workflows/1/dispatches']:
        with pytest.raises(ValueError):audit.github_get(path)
    assert run.call_count==1


def test_finite_audit_workflow_has_only_boolean_secret_expressions():
    text=Path('.github/workflows/regular-preflight-20261102.yml').read_text()
    assert 'schedule:' not in text and 'workflow_dispatch:' not in text and 'contents: write' not in text
    assert 'kis-validation-auth' not in text and "paths: ['.github/readiness/regular-20261102.json']" in text
    for name in ['KIS_APP_KEY','KIS_APP_SECRET']:
        assert '${{ secrets.'+name+" != '' }}" in text
        assert '${{ secrets.'+name+' }}' not in text
    assert 'retention-days: 90' in text


def test_exact_nov2_runtime_window_does_not_roll_forward_after_midnight():
    c=policy.freeze(dict(event='schedule',created_at='2026-11-02T11:30:00Z',head_sha='a'*40,id=99),policy.CRON)
    policy.window(c,policy.timestamp('2026-11-02T14:19:59Z'))
    for late in ['2026-11-02T14:20:00Z','2026-11-02T15:01:00Z','2026-11-03T00:00:00Z']:
        with pytest.raises(Failure):policy.window(c,policy.timestamp(late))
        assert c['asof']=='2026-11-02'
