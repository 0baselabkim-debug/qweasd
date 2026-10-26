"""Offline experiment integration. All HTTP and all market rows are synthetic."""
import copy,datetime as dt,json,urllib.parse,urllib.error
from pathlib import Path
import pytest
from collector_v1.core import Budget,Failure,Stop,Store,encoded,sha
from collector_v1.experiment import policy,engine,runtime
from collector_v1.experiment.storage import Evidence,Metrics,ExperimentBudget,events
from collector_v1.experiment.compare import compare,value_hash
from collector_v1.experiment.analysis import aggregate
from collector_v1 import trial
from test_collector_market_trial import source_zip


@pytest.fixture
def env(monkeypatch,tmp_path):
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[0]+dt.timedelta(minutes=5))
    monkeypatch.setattr('collector_v1.experiment.storage.time.sleep',lambda _:None)
    for n in ('KIS_APP_KEY','KIS_APP_SECRET','KRX_API_KEY'):monkeypatch.setenv(n,'SYNTHETIC_'+n)
    monkeypatch.setenv('GITHUB_RUN_ID','999999')
    return tmp_path


def universe():
    rows=[dict(symbol=s,market=m,name=name,eligible=True,exclusion_reasons=[]) for s,(m,name) in trial.TARGETS.items()]
    rows += [dict(symbol=f'{n:06}',market='KOSPI' if n%2 else 'KOSDAQ',eligible=True,exclusion_reasons=[]) for n in range(100000,102346)]
    rows += [dict(symbol=f'{n:06}',market='KOSPI',eligible=False,exclusion_reasons=['preferred']) for n in range(700000,701601)]
    assert len(rows)==3950
    return rows


def transport(rows,fail_once=False,conflict=False,forbidden=False):
    calls=[];failed=False
    class Response:
        status=200
        def __init__(self,d):self.raw=encoded(d)
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return self.raw
    def open_(req,timeout):
        nonlocal failed
        calls.append(req.full_url)
        if forbidden:raise urllib.error.HTTPError(req.full_url,403,'forbidden',{},None)
        if fail_once and not failed:
            failed=True;raise urllib.error.HTTPError(req.full_url,503,'temporary',{},None)
        path=urllib.parse.urlparse(req.full_url).path;p=urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query,keep_blank_values=True)
        if path.endswith('tokenP'):return Response({'access_token':'SYNTHETIC_EPHEMERAL'})
        if path.endswith('inquire-daily-indexchartprice'):return Response({'rt_cd':'0','output2':[{'stck_bsop_date':'20261026','bstp_nmix_prpr':'1000'}]})
        if path.endswith('_bydd_trd'):
            market='KOSPI' if path.endswith('stk_bydd_trd') else 'KOSDAQ'
            return Response({'OutBlock_1':[dict(BAS_DD='20261026',ISU_CD=r['symbol'],TDD_OPNPRC='100',TDD_HGPRC='110',TDD_LWPRC='90',TDD_CLSPRC='106' if conflict and r['symbol']=='000720' else '105',ACC_TRDVOL='10',ACC_TRDVAL='1050',MKTCAP='100000') for r in rows if r['eligible'] and r['market']==market]})
        code=p['FID_INPUT_ISCD'][0]
        if path.endswith('investor-trade-by-stock-daily'):return Response({'rt_cd':'0','output2':[dict(stck_bsop_date='20261026',frgn_ntby_qty='3',frgn_ntby_tr_pbmn='2',orgn_ntby_qty='-1',orgn_shnu_vol='1',orgn_seln_vol='2',orgn_ntby_tr_pbmn='100',stck_clpr='105',acml_vol='10',acml_tr_pbmn='1050')]})
        assert path.endswith('inquire-daily-itemchartprice') and p['FID_ORG_ADJ_PRC']==['1']
        return Response({'rt_cd':'0','output1':{'stck_shrn_iscd':code},'output2':[dict(stck_bsop_date='20261026',stck_oprc='100',stck_hgpr='110',stck_lwpr='90',stck_clpr='105',acml_vol='10',acml_tr_pbmn='1050',mod_yn='N')]})
    return calls,open_


def test_four_exact_windows_and_budget_math():
    e=policy.budget_estimate()
    assert e['minimum_http']==4703 and e['four_round_baseline']==18812
    assert e['recovery_allowance']==897 and e['four_round_limit']==22400
    for i,start in enumerate(policy.STARTS,1):
        assert policy.window(i,start)[0]==start
        with pytest.raises(Failure):policy.window(i,start-dt.timedelta(seconds=1))
        with pytest.raises(Failure):policy.window(i,start+dt.timedelta(hours=3))
    with pytest.raises(Failure):policy.window(5)


def test_probe_gate_and_old_twenty_limit_are_independent(env):
    s=Store(env/'round');b=ExperimentBudget(s,1,Metrics(),'test')
    for _ in range(20):b.before('KIS','endpoint','test')
    with pytest.raises(Stop):b.before('KIS','endpoint','test')
    with pytest.raises(Failure):b.promote({'status':'PASS','round':2})
    b.promote({'status':'PASS','round':1});b.before('KIS','endpoint','test')
    assert b.used==21
    restored=ExperimentBudget(s,1,Metrics(),'recovery');assert restored.used==21
    with pytest.raises(Stop):restored.check()
    with pytest.raises(Failure):Budget(s,calls=5600)


def test_full_population_round_is_not_a_three_symbol_success(env):
    u=universe();calls,open_=transport(u)
    r=engine.run_round(env/'r1',1,u,{'status':'PASS','interval_seconds':1.05},'SYNTHETIC',opener=open_)
    assert r['status']=='COMPLETE' and r['received']==2349 and r['validated']==2349
    assert r['physical_http']==4703==len(calls) and r['db_json_match']
    assert r['universe_rows']==3950 and r['excluded']==1601 and len(r['rows'])==2349
    assert r['provider_finality']=='UNVERIFIED' and all(x['institution_net_amount'] is None for x in r['rows'])
    assert not any('inquire-price?' in url or 'master' in url for url in calls)
    assert all('FID_INPUT_DATE_2=20261026' in url for url in calls if 'itemchartprice' in url)


def test_probe_failure_does_not_expand_to_full_market(env):
    u=universe();calls,open_=transport(u,conflict=True)
    r=engine.run_round(env/'hold',1,u,{'status':'PASS','interval_seconds':1.05},'test',opener=open_)
    assert r['status']=='UNRESOLVED' and r['physical_http']<=20 and len(calls)<=20
    assert r['errors'][0]['reason']=='KIS_KRX_six_field_conflict'
    assert r['errors'][0]['details']['close']=={'kis':105,'krx':106,'delta':-1}
    assert r['received']==3 and r['validated']==2
    assert not any('FID_INPUT_ISCD=100000' in url for url in calls)


def test_authorization_block_has_no_blind_retry(env):
    u=universe();calls,open_=transport(u,forbidden=True)
    r=engine.run_round(env/'denied',1,u,{'status':'PASS','interval_seconds':1.05},'test',opener=open_)
    assert r['physical_http']==1 and len(calls)==1 and r['status']=='UNRESOLVED'
    assert not r['recovery_possible']


def test_transient_failure_recovers_only_missing_work(env):
    u=universe();calls,open_=transport(u,fail_once=True)
    r=engine.run_round(env/'recovered',1,u,{'status':'PASS','interval_seconds':1.05},'test',opener=open_)
    assert r['status']=='COMPLETE' and r['physical_http']==4704 and r['retries']>=1
    # Re-entering a round reuses hash-verified successes, not another full fetch.
    def forbidden(*a,**kw):raise AssertionError('cache replay must issue no HTTP')
    second=engine.run_round(env/'recovered',1,u,{'status':'PASS','interval_seconds':1.05},'next',opener=forbidden)
    assert second['status']=='COMPLETE' and second['physical_http_this_run']==0 and second['physical_http']==4704


def test_partial_journal_cannot_be_reset_or_reused_in_another_round(env):
    s=Store(env/'bad');(s.root/'http.jsonl').write_bytes(b'{"event":"started"')
    with pytest.raises(Failure):ExperimentBudget(s,1,Metrics(),'new')
    s=Store(env/'wrong');t={'kind':'flow','asof':policy.ASOF,'round':1,'symbol':'000720','market':'KOSPI'}
    from collector_v1.experiment.storage import append
    append(s.root/'tasks.jsonl',{'task':t,'status':'received','evidence':{}})
    with pytest.raises(Failure,match='cross_round_cache'):engine.replay(s,2)


def report(n,rows,target=('A',)):
    return dict(round=n,asof=policy.ASOF,population_hash='same',target_symbols=list(target),rows=rows,status='COMPLETE',observation_window={'first':str(n),'last':str(n)},elapsed_seconds=100-n,physical_http=4703)


def row(value=1,quality='PASS'):
    from collector_v1.experiment.compare import FIELDS
    return dict(symbol='A',market='KOSPI',asof=policy.ASOF,validation_status=quality,**{k:value for k in FIELDS})


def test_value_hash_ignores_observation_times_but_not_null_zero():
    x=row();y={**x,'observed_at':'new','source_hash':'new'}
    assert value_hash([x])==value_hash([y])
    assert compare(report(1,[x]),report(2,[y]))['classification']=='COMPLETE_MATCH'
    y['foreign_net_amount']=None;x['foreign_net_amount']=0
    r=compare(report(1,[x]),report(2,[y]));assert r['classification']=='VALUES_CHANGED'
    assert 'A' in r['changed_by_category']['foreign']


def test_incomplete_generations_never_claim_complete_equality():
    assert compare(report(1,[row()],('A','B')),report(2,[row()],('A','B')))['classification']=='PARTIAL_MATCH'
    assert compare(report(1,[row()]),report(2,[row(quality='HOLD')]))['classification']=='PARTIAL_MATCH'
    assert compare(report(1,[row()]),report(3,[row()]))['classification']=='NOT_COMPARABLE'
    b=report(2,[row()]);b['population_hash']='different'
    assert compare(report(1,[row()]),b)['classification']=='NOT_COMPARABLE'


def test_stability_and_speed_claims_require_real_complete_rounds():
    rs=[report(n,[row()]) for n in range(1,5)]
    for i in range(1,4):rs[i]['comparison']=compare(rs[i-1],rs[i])
    out=aggregate(rs)
    assert out['first_complete_equal_pair']==[1,2] and out['later_equality_maintained']
    rs[-1]['status']='UNRESOLVED';rs[-1]['comparison']={'classification':'NOT_COMPARABLE'}
    out=aggregate(rs);assert out['elapsed_improvement_percent'] is None
    assert 'Insufficient' in out['recommendation']


def test_retry_policy_is_bounded_and_does_not_retry_price_or_auth_errors():
    assert policy.retry_policy('http_429',1)==30 and policy.retry_policy('http_503',1)==5
    for code in ('authentication_failure','KIS_KRX_six_field_conflict','source_hash_mismatch'):
        assert policy.retry_policy(code,1) is None
    assert policy.retry_policy('network_failure',3) is None


def test_runtime_manifest_tamper_is_blocked(env):
    p=env/'artifact';p.mkdir();(p/'report.json').write_bytes(b'{}')
    (p/'manifest.json').write_bytes(encoded({'files':runtime.inventory(p)}));runtime.verify(p)
    (p/'report.json').write_bytes(b'{"forged":true}')
    with pytest.raises(Failure):runtime.verify(p)


def test_calendar_only_pilot_workflow_unchanged_and_experiment_has_cleanup():
    root=Path(__file__).resolve().parents[1]
    text=(root/'.github/workflows/full-market-experiment.yml').read_text()
    for h in (10,13,16,19):assert f"0 {h} 26 10 *" in text
    assert 'kis-validation-auth' in text and 'runtime finalize' in text
    assert 'actions: write' in text and 'contents: read' in text and 'contents: write' not in text
    assert 'runtime seal' in text and 'EXPERIMENT_SOURCE_SHA' in text
    assert (root/'.github/trials/collector-v1.json').read_bytes()==__import__('subprocess').check_output(['git','show','5e097fde:.github/trials/collector-v1.json'],cwd=root)


def test_lost_artifact_is_not_replaced_with_earlier_state(env,monkeypatch):
    monkeypatch.setattr(runtime,'ROOT',env)
    monkeypatch.setattr(runtime,'api',lambda *a,**k:{'artifacts':[]})
    with pytest.raises(Failure,match='unknown_requests_reserved'):runtime.restore_round({'id':123},env/'recovery')
    assert not (env/'recovery').exists()


def test_scheduled_round_is_not_inferred_from_late_wall_clock(env,monkeypatch):
    monkeypatch.setattr(policy,'utcnow',lambda:policy.STARTS[1]+dt.timedelta(minutes=5))
    with pytest.raises(Failure):policy.window(1)
    assert policy.window(2)[0]==policy.STARTS[1]


def test_optimizer_applies_only_lossless_local_compression(env):
    import gzip
    raw=env/'raw';raw.mkdir()
    for i in range(100):(raw/(str(i)+'.gz')).write_bytes(gzip.compress(encoded({'synthetic':'A'*1000,'i':i})))
    p=runtime.optimize(env)
    assert p['gzip_level'] in (1,9) and p['api_interval']==1.05
    assert p['calibration'][1]['bytes']>0 and p['calibration'][9]['bytes']>0
    assert 'next real round' in p['reason']


def test_watchdog_waits_for_active_final_round(env,monkeypatch):
    monkeypatch.setattr(runtime,'utcnow',lambda:policy.STARTS[-1]+dt.timedelta(hours=3,minutes=1))
    monkeypatch.setattr(runtime,'history',lambda:({},[{'id':9,'display_title':'full-market-20261026 R4','status':'in_progress'}]))
    monkeypatch.setattr(runtime,'finalize',lambda:pytest.fail('cannot claim a final report before active round ends'))
    assert runtime.watch() is None


def test_zero_row_or_unfinished_round_cannot_claim_stability():
    a=report(1,[],());b=report(2,[],())
    assert compare(a,b)['classification']=='NOT_COMPARABLE'
    a=report(1,[row()]);b=report(2,[row()]);b['status']='UNRESOLVED'
    assert compare(a,b)['classification']=='PARTIAL_MATCH'


def test_pinned_seed_can_be_revalidated_without_next_day_pilot_override(env,monkeypatch,source_zip):
    from collector_v1 import market_trial
    monkeypatch.setattr(runtime,'ROOT',env)
    monkeypatch.setattr(market_trial,'ZIP_SHA',sha(source_zip))
    def github(path,method='GET',data=None,binary=None):
        if binary:
            Path(binary).write_bytes(source_zip);return None
        return dict(id=int(market_trial.ARTIFACT_ID),name=trial.ARTIFACT,expired=False,digest='sha256:'+sha(source_zip),workflow_run={'id':int(market_trial.MASTER_RUN)})
    monkeypatch.setattr(runtime,'api',github)
    rows=runtime.seed()
    assert len(rows)==3950 and sum(r['eligible'] for r in rows)==2349
    assert (env/'seed/receipt.json').exists()


def test_large_comparison_is_compact_for_issue_and_summary_but_keeps_artifact_data():
    full={'classification':'VALUES_CHANGED','changed':[{'symbol':str(i),'fields':{'close':{'previous':1,'current':2}}} for i in range(2349)],'unavailable':['X'],'status_changed':[],'changed_by_category':{'price':['A','B'],'foreign':[]}}
    brief=runtime.compact_comparison(full)
    assert brief['changed_count']==2349 and brief['changed_by_category']['price']==2
    assert len(encoded(brief))<1000 and len(full['changed'])==2349


def test_admin_recovery_cannot_collect_or_dispatch_before_grace():
    from collector_v1.experiment.controller import validate
    c={'schema':1,'experiment':policy.EXPERIMENT_ID,'asof':policy.ASOF,'action':'recover_missing','round':1,'request_id':'test'}
    with pytest.raises(Failure):validate(c,policy.STARTS[0]+dt.timedelta(minutes=14))
    validate(c,policy.STARTS[0]+dt.timedelta(minutes=15))
    with pytest.raises(Failure):validate(c,policy.STARTS[0]+dt.timedelta(days=1))
    text=(Path(__file__).resolve().parents[1]/'.github/workflows/full-market-controller.yml').read_text()
    assert 'KIS_APP_KEY' not in text and 'KRX_API_KEY' not in text


def test_independent_clock_dispatches_only_missing_or_verified_recoverable_rounds():
    from collector_v1.experiment.timekeeper import recovery_decision
    t=policy.STARTS[1]
    assert recovery_decision(None,None,t,2)=='dispatch_missing'
    assert recovery_decision({'status':'in_progress'},None,t,2)=='wait'
    assert recovery_decision({'status':'completed'},None,t,2)=='blocked_unknown_usage'
    assert recovery_decision({'status':'completed'},{'status':'COMPLETE'},t,2)=='complete'
    assert recovery_decision({'status':'completed'},{'status':'PARTIAL','recovery_possible':True,'physical_http':100},t,2)=='dispatch_recovery'
    with pytest.raises(Failure):recovery_decision(None,None,t-dt.timedelta(seconds=1),2)
    text=(Path(__file__).resolve().parents[1]/'.github/workflows/full-market-clock.yml').read_text()
    assert 'KIS_APP_KEY' not in text and 'KRX_API_KEY' not in text
    assert text.count('timeout-minutes: 355')==4
