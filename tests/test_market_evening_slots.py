"""Offline synthetic checks for the two dated evening slots; no real APIs."""
import copy
import datetime as dt
import io
import json
from pathlib import Path
import shutil
import zipfile
import pytest
from collector_v1 import kis_manual as manual
from collector_v1.core import Failure, Store, encoded, sha
from collector_v1.evening_slots import policy, runner
from collector_v1.experiment import runtime, engine
from collector_v1.experiment.storage import events
from collector_v1.time_slots.compare import build_observations, build_indices, compare_pair
from test_full_market_experiment import universe, transport


@pytest.fixture
def synthetic(monkeypatch,tmp_path):
    monkeypatch.setattr('collector_v1.experiment.storage.time.sleep',lambda _:None)
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[0]+dt.timedelta(seconds=1))
    monkeypatch.setattr(manual.ManualBudget,'remaining_seconds',lambda self:10000)
    for key in ('KIS_APP_KEY','KIS_APP_SECRET'):monkeypatch.setenv(key,'SYNTHETIC_'+key)
    monkeypatch.setenv('GITHUB_RUN_ID','12345');monkeypatch.setenv('GITHUB_SHA','SYNTHETIC_COMMIT')
    monkeypatch.setenv('GITHUB_STEP_SUMMARY',str(tmp_path/'summary.md'))
    monkeypatch.setattr(runtime,'api',lambda *a,**k:{'jobs':[]})
    rows=universe()
    for row in rows:
        if row['symbol'] not in manual.TARGETS:row.update(eligible=False,exclusion_reasons=['halted'])
    return tmp_path,rows


def fresh_transport(rows,**kwargs):
    calls,original=transport(rows,**kwargs)
    class Response:
        status=200
        def __init__(self,raw):self.raw=raw
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return self.raw
    def opener(req,timeout):return Response(original(req,timeout).raw.replace(b'20261026',b'20261028'))
    return calls,opener


def receipt(rows):
    return dict(status='PASS',asof=policy.ASOF,population_sha256=sha(encoded(rows)),master_rows=len(rows),eligible=len(policy.eligible(rows)))


def test_exact_date_times_two_generations_and_midnight_target_retained():
    assert policy.ASOF=='2026-10-28'
    assert [x.isoformat() for x in policy.STARTS]==['2026-10-28T20:30:00+09:00','2026-10-28T23:30:00+09:00']
    assert len(policy.configuration('SYNTHETIC')['rounds'])==2
    assert policy.control(2)['expires_at']=='2026-10-29T02:20:00+09:00'
    for n in (0,3,4,True):
        with pytest.raises(Failure):policy.generation(n)
    assert policy.control(2)['asof']=='2026-10-28'


@pytest.mark.parametrize('field,value', [('asof','2026-10-27'),('max_total_http_per_generation',6000),('interval_seconds',0.5),('approval','UNAPPROVED')])
def test_exact_approved_configuration_cannot_be_broadened(field,value):
    c=policy.configuration('SYNTHETIC');c[field]=value
    with pytest.raises(Failure):policy.validate_configuration(c)


def test_waits_are_finite_and_credential_free_before_master():
    clock=[policy.STARTS[0]-dt.timedelta(hours=13)];sleeps=[]
    for target in policy.WAIT_TARGETS:
        def sleep(s):sleeps.append(s);clock[0]+=dt.timedelta(seconds=s)
        runner.wait_until(target,clock=lambda:clock[0],sleeper=sleep)
        assert clock[0]==target
    assert max(sleeps)<=30
    with pytest.raises(Failure,match='budget'):runner.wait_until(policy.STARTS[0],clock=lambda:policy.STARTS[0]-dt.timedelta(hours=5))
    with pytest.raises(Failure,match='expired'):runner.wait_until(policy.STARTS[0],clock=lambda:policy.STARTS[-1]+dt.timedelta(minutes=170))


def test_prestart_expired_wrong_date_and_budget_have_zero_http(synthetic,monkeypatch):
    root,rows=synthetic
    def forbidden(*a,**k):pytest.fail('invalid control cannot make HTTP')
    for when in [policy.STARTS[0]-dt.timedelta(seconds=1),policy.STARTS[0]+dt.timedelta(minutes=170)]:
        monkeypatch.setattr(policy,'utcnow',lambda:when)
        with pytest.raises(Failure,match='window'):manual.run(root/'closed',policy.control(1),rows,'SYNTHETIC',opener=forbidden,policy=policy)
    c=policy.control(1);c['asof']='2026-10-27'
    with pytest.raises(Failure,match='scope'):manual.run(root/'wrong-date',c,rows,'SYNTHETIC',opener=forbidden,policy=policy)
    c=policy.control(1);c['max_total_http']=5601
    with pytest.raises(Failure,match='scope'):manual.run(root/'wrong-budget',c,rows,'SYNTHETIC',opener=forbidden,policy=policy)


def test_fresh_independent_http_same_target_date_and_offline_verification(synthetic,monkeypatch):
    root,rows=synthetic;generations=[];all_calls=[]
    for n in (1,2):
        monkeypatch.setattr(policy,'utcnow',lambda n=n:policy.STARTS[n-1]+dt.timedelta(seconds=1))
        calls,opener=fresh_transport(rows);folder=root/policy.generation(n)
        r=manual.run(folder/'collection',policy.control(n),rows,'SYNTHETIC',opener=opener,policy=policy)
        r.update(round=n,generation_id=policy.generation(n))
        assert r['status']=='PASS' and r['physical_http']==len(calls)==9 and r['db_json_match']
        assert all('20261027' not in u and '20261029' not in u for u in calls)
        Store(folder).write('universe.json',rows);Store(folder).write('master-receipt.json',receipt(rows))
        runner.verify_generation(folder,r)
        views=build_observations(folder/'collection',r,rows,asof=policy.ASOF)
        indices=build_indices(folder/'collection',r,asof=policy.ASOF)
        assert len(views)==3 and set(indices)=={'KOSPI','KOSDAQ'}
        generations.append(dict(report=r,observations=views,indices=indices));all_calls+=calls
    pair=compare_pair(*generations,asof=policy.ASOF)
    assert pair['complete_population_comparable'] and pair['classification']=='A_COMPLETE_IDENTICAL'
    assert pair['comparable_symbols']==3 and len(all_calls)==18
    assert generations[0]['report']['generation_id']!=generations[1]['report']['generation_id']
    assert not compare_pair(*generations)['complete_population_comparable'] # old default stays Oct27


def test_cross_generation_state_reuse_forbidden_before_new_http(synthetic,monkeypatch):
    root,rows=synthetic;calls,opener=fresh_transport(rows)
    manual.run(root/'one',policy.control(1),rows,'SYNTHETIC',opener=opener,policy=policy)
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[1]+dt.timedelta(seconds=1))
    def forbidden(*a,**k):pytest.fail('must not use another generation cache')
    with pytest.raises(Failure,match='binding'):manual.run(root/'one',policy.control(2),rows,'SYNTHETIC',opener=forbidden,policy=policy)


def test_fixed_population_uses_today_not_previous_day(synthetic):
    _,rows=synthetic;r=receipt(rows)
    assert len(policy.verify_population(rows,r))==3
    r['asof']='2026-10-27'
    with pytest.raises(Failure,match='population'):policy.verify_population(rows,r)
    with pytest.raises(Failure,match='duplicate'):policy.eligible(rows+[rows[0]])


def test_one_shot_authorization_rejects_second_arm_and_rerun(monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY',policy.REPO);monkeypatch.setenv('GITHUB_REF',policy.BRANCH)
    monkeypatch.setenv('GITHUB_EVENT_NAME','push');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
    with pytest.raises(Failure,match='context'):runner.context()
    monkeypatch.setattr(runner,'context',lambda:policy.configuration('SYNTHETIC'))
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[0]-dt.timedelta(hours=1))
    monkeypatch.setenv('GITHUB_RUN_ID','12345')
    def api(path,*a,**k):
        if path.endswith('/12345'):return {'path':'.github/workflows/'+policy.WORKFLOW,'workflow_id':1}
        return {'workflow_runs':[{'id':12345},{'id':12344}]}
    monkeypatch.setattr(runtime,'api',api)
    with pytest.raises(Failure,match='already_armed'):runner.authorize()


def test_rejected_master_date_and_non_market_endpoint(synthetic,monkeypatch):
    root,_=synthetic;budget=runner.MasterBudget(Store(root/'master-budget'))
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[0]-dt.timedelta(days=1))
    with pytest.raises(Exception,match='date'):budget.check()
    with pytest.raises(Failure,match='endpoint'):budget.before('KRX','https://UNAPPROVED','forbidden')


def test_two_round_complete_lifecycle_and_comparison(synthetic,monkeypatch):
    root,rows=synthetic;master=root/'source-master';master.mkdir()
    sources={'market-20261028-master':master};published={};physical=[]
    monkeypatch.setattr(runner,'context',lambda:policy.configuration('SYNTHETIC'))
    monkeypatch.setattr(runner,'wait_until',lambda *a,**k:None)
    monkeypatch.setattr(runner,'verify_master',lambda _: (rows,receipt(rows)))
    def restore(name,target,kind):shutil.copytree(sources[name],target);return dict(name=name,artifact_id='SYNTHETIC',zip_sha256='SYNTHETIC')
    monkeypatch.setattr(runner,'restore',restore)
    def publish(name,value,**kwargs):published[name]=value;return 'SYNTHETIC_SHA'
    monkeypatch.setattr(runner,'publish_file',publish)
    original=manual.ManualAPI.__init__
    def init(self,store,budget,metrics,opener=None):
        calls,open_=fresh_transport(rows);physical.append(calls);original(self,store,budget,metrics,open_)
    monkeypatch.setattr(manual.ManualAPI,'__init__',init)
    for n in (1,2):
        job=root/('job'+str(n));monkeypatch.setattr(runner,'ROOT',job)
        monkeypatch.setattr(policy,'utcnow',lambda n=n:policy.STARTS[n-1]+dt.timedelta(seconds=1))
        assert runner.collect(n)==0
        folder=job/'generations'/policy.generation(n);assert runtime.verify(folder)['kind']==policy.generation(n)
        sources[policy.artifact(n)]=folder
    assert sum(len(c) for c in physical)==18
    job=root/'comparison-job';monkeypatch.setattr(runner,'ROOT',job)
    assert runner.compare_all()==0
    final=Store(job/'comparison').read('final-report.json')
    assert final['status']=='COMPLETE_OBSERVATION_COMPARISON' and final['asof']=='2026-10-28'
    assert final['pair']['identical_ratio']==1 and not final['additional_generations_scheduled']
    assert final['provider_finality']=='UNVERIFIED' and final['official_price_status']=='PENDING_KRX'
    assert runtime.verify(job/'comparison')['kind']=='comparison' and 'final-report.json' in published


def test_failed_restore_report_never_filled_from_other_generation(synthetic,monkeypatch):
    root,_=synthetic;monkeypatch.setattr(runner,'ROOT',root/'comparison-failure')
    monkeypatch.setattr(runner,'context',lambda:policy.configuration('SYNTHETIC'))
    monkeypatch.setattr(runner,'publish_file',lambda *a,**k:'SYNTHETIC')
    def fail(*a,**k):raise Failure('artifact_missing')
    monkeypatch.setattr(runner,'restore',fail)
    assert runner.compare_all()==1
    r=Store(root/'comparison-failure'/'comparison').read('final-report.json')
    assert r['status']=='INCOMPLETE_COMPARISON' and r['pair']['comparable_symbols']==0


def test_workflow_has_two_collectors_four_finite_waits_and_no_recurring_schedule():
    import re
    text=Path('.github/workflows/market-20261028-evening.yml').read_text()
    jobs=set(re.findall(r'^  ([a-z0-9]+):$',text.split('jobs:\n',1)[1],re.M))
    assert jobs=={'authorize','wait1','wait2','wait3','wait4','master','round1','round2','comparison'}
    assert 'schedule:' not in text and 'workflow_dispatch:' not in text
    assert text.count('runner collect --round ')==2 and text.count('group: kis-validation-auth')==3
    assert 'needs: [authorize, master, round1]' in text
    assert 'needs: [authorize, master, round1, round2]' in text
    assert 'needs: [authorize, wait4]' in text
    assert 'KRX_API_KEY' not in text and 'actions: write' not in text
    assert policy.CONTROL in text and policy.artifact(1) in text and policy.artifact(2) in text


def test_control_only_push_guard_accepts_exact_commit_and_rejects_code_push(synthetic,monkeypatch):
    root,_=synthetic;c=policy.configuration('SYNTHETIC');monkeypatch.setattr(runner,'configuration',lambda:c)
    event=root/'event.json';event.write_bytes(encoded(dict(before='a'*40,after='b'*40,forced=False,created=False,deleted=False)))
    for key,value in [('GITHUB_REPOSITORY',policy.REPO),('GITHUB_REF',policy.BRANCH),('GITHUB_EVENT_NAME','push'),('GITHUB_RUN_ATTEMPT','1'),('GITHUB_EVENT_PATH',str(event))]:monkeypatch.setenv(key,value)
    changed=[policy.CONTROL]
    def output(command,**kwargs):
        if command[1:]==['rev-parse','HEAD^']:return 'a'*40+'\n'
        if command[1:]==['rev-parse','HEAD']:return 'b'*40+'\n'
        if command[1:3]==['diff','--name-only']:return '\n'.join(changed)+'\n'
        pytest.fail('unexpected Git read')
    monkeypatch.setattr(runner.subprocess,'check_output',output)
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:None)
    assert runner.context()==c
    changed.append('collector_v1/kis_manual.py')
    with pytest.raises(Failure,match='only_experiment_control'):runner.context()


def test_fresh_master_and_raw_restoration_are_oct28_not_oct27(synthetic,monkeypatch):
    from test_collector_v1 import master_bytes
    from collector_v1.providers import Providers,LAYOUTS
    root,_=synthetic
    monkeypatch.setattr(runner,'ROOT',root/'experiment')
    monkeypatch.setattr(runner,'context',lambda:policy.configuration('SYNTHETIC'))
    monkeypatch.setattr(runner,'publish_file',lambda *a,**k:'SYNTHETIC_SHA')
    monkeypatch.setattr(runner,'wait_until',lambda *a,**k:None)
    monkeypatch.setattr('collector_v1.providers.now',lambda:'2026-10-28T08:30:00+00:00')
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[0]-dt.timedelta(minutes=30))
    def master_raw(market):
        n=600 if market=='KOSPI' else 1400
        codes=[f'{i:06}' for i in range(100000 if market=='KOSPI' else 400000,(100000 if market=='KOSPI' else 400000)+n)]
        codes += [code for code,(board,name) in manual.TARGETS.items() if board==market]
        lines=[]
        for code in codes:
            with zipfile.ZipFile(io.BytesIO(master_bytes(market,code,'N'))) as z:
                member=z.namelist()[0];line=z.read(member)
            if code in manual.TARGETS:line=line.replace(b'SYNTHETIC TEST ONLY',manual.TARGETS[code][1].encode('cp949'))
            lines.append(line)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr(member,b'\n'.join(lines))
        return out.getvalue()
    raw={m:master_raw(m) for m in ('KOSPI','KOSDAQ')}
    calls=[]
    class Response:
        status=200
        def __init__(self,raw):self.raw=raw
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return self.raw
    def open_(req,timeout):
        calls.append(req.full_url)
        if req.full_url.endswith('tokenP'):return Response(encoded({'access_token':'SYNTHETIC_ACCESS_ONLY'}))
        if 'chk-holiday' in req.full_url:return Response(encoded({'rt_cd':'0','output':[{'bass_dt':'20261028','opnd_yn':'Y'}]}))
        return Response(next(raw[m] for m in raw if req.full_url==LAYOUTS[m]['url']))
    original=Providers.__init__
    monkeypatch.setattr(Providers,'__init__',lambda self,store,budget,opener=None:original(self,store,budget,open_))
    assert runner.master()==0 and len(calls)==4
    u,r=runner.verify_master(root/'experiment'/'master')
    assert len(u)==2003 and r['asof']=='2026-10-28' and r['eligible']==2003
    assert runtime.verify(root/'experiment'/'master')['asof']=='2026-10-28'
    # Re-entering the master command cannot silently clear its physical ledger.
    with pytest.raises(Failure,match='existing_state'):runner.master()
