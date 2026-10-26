"""Offline-only trials. Generated masters are synthetic; no external HTTP."""
import io
import json
import os
from pathlib import Path
import zipfile
import pytest
from collector_v1 import trial
from collector_v1.core import Failure, Stop, Store, encoded, sha
from collector_v1.providers import task
from test_collector_v1 import master_bytes

DAY='2026-10-26'

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(trial,'ROOT',tmp_path/'state')
    monkeypatch.setattr(trial,'ADMIN',tmp_path/'admin')
    monkeypatch.setattr(trial,'local_date',lambda:DAY)
    for n in trial.SECRET_NAMES:monkeypatch.setenv(n,'synthetic-test-only-'+n)
    for n,v in dict(GITHUB_REPOSITORY=trial.REPO,GITHUB_REF=trial.BRANCH,GITHUB_EVENT_NAME='push',GITHUB_RUN_ID='100',GITHUB_RUN_ATTEMPT='1').items():monkeypatch.setenv(n,v)
    c=dict(schema=1,trial_id='master-20261026',request_id='request-1',stage='master',asof=DAY,previous_run=None,max_total_http=20)
    p=Path(trial.CONTROL);p.parent.mkdir(parents=True);p.write_bytes(encoded(c))
    trial.ADMIN.mkdir();(trial.ADMIN/'preflight.json').write_bytes(encoded(dict(status='PASS',control=c,source_commit='test-sha',previous_run='')))
    (trial.ADMIN/'snapshot-before.json').write_bytes(encoded({}))
    monkeypatch.setattr(trial,'git',lambda *args:'test-sha')
    monkeypatch.setattr('collector_v1.core.time.sleep',lambda _:None)
    return c


def test_control_is_master_only_and_same_observed_date(env):
    assert trial.control()==env
    for k,v in [('stage','market'),('asof','2026-10-23'),('max_total_http',21)]:
        bad={**env,k:v};Path(trial.CONTROL).write_bytes(encoded(bad))
        with pytest.raises(Failure):trial.control()


def test_latest_predecessor_required_and_no_unrecorded_reset(env):
    runs=[dict(id=99,event='push',head_branch='feat/market-data-collector-v1',status='completed')]
    with pytest.raises(Failure,match='latest_trial_receipt_required'):trial.predecessor(runs,'100',env)
    assert trial.predecessor(runs,'100',{**env,'previous_run':'99'})=='99'
    with pytest.raises(Failure):trial.predecessor(runs,'100',{**env,'previous_run':'98'})
    with pytest.raises(Failure):trial.predecessor(runs*100,'100',env)


def test_stage_budget_blocks_price_and_date_rollover(env):
    store=Store(trial.ROOT);b=trial.MasterBudget(store,DAY)
    with pytest.raises(Failure,match='stage_forbids_endpoint'):b.before('KIS','/uapi/domestic-stock/v1/quotations/inquire-price','market')
    assert b.used==0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(trial,'local_date',lambda:'2026-10-27')
        with pytest.raises(Stop):b.before('KIS_MASTER','official','market')
    assert b.used==0


def test_restoration_requires_receipt_inventory_and_ledger(env):
    s=Store(trial.ROOT)
    trial.restore_check(s,env,'')
    s.write('receipt.json',dict(github_run_id='99',asof=DAY,trial_id=env['trial_id'],stage='master',request_id='old',total_http=0,status='PASS'))
    trial.seal(s)
    assert trial.restore_check(s,env,'99')['total_http']==0
    s.write('requests.json',[{'status':'attempted'}])
    with pytest.raises(Failure,match='artifact_hash'):trial.restore_check(s,env,'99')


def test_failed_trial_cannot_advance(env):
    s=Store(trial.ROOT);s.write('requests.json',[])
    s.write('receipt.json',dict(github_run_id='99',asof=DAY,trial_id=env['trial_id'],stage='master',request_id='old',total_http=0,status='FAIL'))
    trial.seal(s)
    with pytest.raises(Failure,match='failed_previous_trial'):trial.restore_check(s,env,'99')


def synthetic_master(market):
    codes=[f'{n:06}' for n in range(100000,100500)] if market=='KOSPI' else [f'{n:06}' for n in range(400000,401500)]
    codes += [s for s,(m,name) in trial.TARGETS.items() if m==market]
    lines=[];member=None
    for code in codes:
        raw=master_bytes(market,code)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            member=z.namelist()[0];line=z.read(member)
        if code in trial.TARGETS:
            line=line.replace(b'SYNTHETIC TEST ONLY',trial.TARGETS[code][1].encode('cp949'))
        lines.append(line)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr(member,b'\n'.join(lines))
    return out.getvalue()


def test_master_end_to_end_four_physical_calls_and_no_stage2(env,monkeypatch):
    replies=[encoded({'access_token':'synthetic-ephemeral-token'}),encoded({'rt_cd':'0','output':[{'bass_dt':'20261026','opnd_yn':'Y'}]}),synthetic_master('KOSPI'),synthetic_master('KOSDAQ')]
    calls=[]
    class Response:
        status=200
        def __init__(self,raw):self.raw=raw
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return self.raw
    def open_(req,timeout):
        calls.append(req.full_url)
        assert json.loads((trial.ROOT/'requests.json').read_bytes())[-1]['status']=='attempted'
        return Response(replies.pop(0))
    from collector_v1.providers import Providers
    real=Providers
    monkeypatch.setattr(trial,'Providers',lambda s,b:real(s,b,open_))
    monkeypatch.setattr('collector_v1.providers.now',lambda:'2026-10-26T01:00:00+00:00')
    assert trial.collect_master()==0
    r=json.loads((trial.ROOT/'receipt.json').read_bytes())
    assert r['status']=='PASS' and r['total_http']==4 and not r['stage2_executed']
    assert r['rows']==2003 and set(r['targets'])==set(trial.TARGETS)
    assert len(calls)==4 and all('inquire-' not in x and 'investor-trade' not in x for x in calls)
    assert trial.finalize()==0
    # New explicit control restores successes without another market HTTP.
    c={**env,'request_id':'request-2','previous_run':'100'};Path(trial.CONTROL).write_bytes(encoded(c))
    (trial.ADMIN/'preflight.json').write_bytes(encoded(dict(control=c,source_commit='test-sha',previous_run='100')))
    monkeypatch.setenv('GITHUB_RUN_ID','101')
    assert trial.collect_master()==0 and len(calls)==4
    assert json.loads((trial.ROOT/'receipt.json').read_bytes())['physical_http_this_run']==0


def test_missing_secret_stops_without_http(env,monkeypatch):
    monkeypatch.delenv('KIS_APP_KEY')
    monkeypatch.setattr(trial,'Providers',lambda *a:pytest.fail('must stop before transport'))
    assert trial.collect_master()==1
    r=json.loads((trial.ROOT/'receipt.json').read_bytes())
    assert r['status']=='BLOCKED' and r['total_http']==0 and not r['secret_presence']['KIS_APP_KEY']


def test_interruption_finalizer_preserves_reserved_calls(env):
    s=Store(trial.ROOT);s.write('requests.json',[{'status':'attempted'}])
    assert trial.finalize()==0
    r=s.read('receipt.json')
    assert r['total_http']==1 and r['status']=='BLOCKED' and r['reason']=='trial_did_not_complete'
    assert s.read('state-manifest.json')['files']['requests.json']==sha((s.root/'requests.json').read_bytes())


def test_trial_workflow_is_control_push_only():
    root=Path(__file__).resolve().parents[1]
    s=(root/'.github/workflows/collector-v1-trial.yml').read_text()
    assert 'branches: [feat/market-data-collector-v1]' in s
    assert 'paths: [.github/trials/collector-v1.json]' in s
    assert 'schedule:' not in s and 'workflow_dispatch:' not in s
    assert 'persist-credentials: false' in s and 'group: kis-validation-auth' in s
    assert 'contents: read' in s and 'actions: read' in s
    assert 'trial master' in s and 'trial market' not in s


def test_preflight_rejects_wrong_branch_rerun_and_code_change(env,monkeypatch):
    monkeypatch.setenv('GITHUB_REF','refs/heads/main')
    with pytest.raises(Failure,match='wrong_repository'):trial.preflight()
    monkeypatch.setenv('GITHUB_REF',trial.BRANCH);monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
    with pytest.raises(Failure,match='rerun_requires'):trial.preflight()
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    event=Path('event.json');event.write_bytes(encoded(dict(before='parent',after='head',created=False,forced=False,deleted=False)))
    monkeypatch.setenv('GITHUB_EVENT_PATH',str(event))
    def git(*a):
        if a==('rev-parse','HEAD^'):return 'parent'
        if a==('rev-parse','HEAD'):return 'head'
        return trial.CONTROL+'\ncollector_v1/trial.py'
    monkeypatch.setattr(trial,'git',git)
    with pytest.raises(Failure,match='control_only_commit'):trial.preflight()


def test_auth_failure_counts_once_and_never_fetches_master(env,monkeypatch):
    import urllib.error
    calls=[]
    def denied(req,timeout):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url,401,'unauthorized',{},None)
    from collector_v1.providers import Providers
    monkeypatch.setattr(trial,'Providers',lambda s,b:Providers(s,b,denied))
    assert trial.collect_master()==1
    r=json.loads((trial.ROOT/'receipt.json').read_bytes())
    assert len(calls)==1 and r['total_http']==1 and r['status']=='BLOCKED'
    assert not (trial.ROOT/'masters.json').exists()
