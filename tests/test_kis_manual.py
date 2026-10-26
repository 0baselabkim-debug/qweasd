"""Synthetic manual execution and 12-request accounting. No live API access."""
import copy,datetime as dt,json
from pathlib import Path
import pytest
from collector_v1 import kis_manual as manual,kis_manual_actions as actions
from collector_v1.core import Failure,Stop,Store,encoded,sha
from collector_v1.experiment import engine
from collector_v1.experiment.storage import Metrics,events
from test_full_market_experiment import universe,transport

@pytest.fixture
def setup(monkeypatch,tmp_path):
    current=dt.datetime(2026,10,26,12,tzinfo=dt.timezone.utc)
    real=manual.validate_control
    monkeypatch.setattr(manual,'validate_control',lambda c,current=None:real(c,current or dt.datetime(2026,10,26,12,tzinfo=dt.timezone.utc)))
    monkeypatch.setattr('collector_v1.experiment.storage.time.sleep',lambda _:None)
    for key in ('KIS_APP_KEY','KIS_APP_SECRET'):monkeypatch.setenv(key,'SYNTHETIC_'+key)
    control=dict(schema=1,trial_id='kis-sample-20261026',request_id='SYNTHETIC',mode='sample',asof='2026-10-26',previous_run=None,max_total_http=12,not_before='2026-10-26T11:00:00+00:00',expires_at='2026-10-27T03:00:00+00:00',approval=manual.SAMPLE_APPROVAL)
    return tmp_path,control,universe()


def test_sample_actual_shape_and_default_nine_budget(setup):
    root,c,u=setup;calls,opener=transport(u)
    r=manual.run(root/'sample',c,u,'SYNTHETIC',opener=opener)
    assert r['status']=='PASS' and r['kis_validated']==3 and r['physical_http']==len(calls)==9
    assert r['official_accepted']==0 and r['official_price_status']=='PENDING_KRX'
    assert set(r['target_symbols'])==set(manual.TARGETS) and len(r['indices'])==2
    assert all(row['asof']=='2026-10-26' and row['foreign_net_quantity']==3 and row['foreign_net_amount']==2000000 and row['institution_net_quantity']==-1 for row in r['rows'])
    assert not any('krx.co.kr' in x or 'master' in x for x in calls)
    actions.verify_mode(root/'sample',r)
    plan=manual.full_plan(r,u)
    assert plan['target']==2349 and plan['baseline_http']==4701 and plan['max_total_http']==5600
    def no_http(*a,**kw):pytest.fail('success replay must not call API')
    second=manual.run(root/'sample',c,u,'SYNTHETIC_REPLAY',opener=no_http)
    assert second['physical_http']==9 and second['physical_http_this_run']==0


def test_auth_denial_stops_after_one_request(setup):
    root,c,u=setup;calls,opener=transport(u,forbidden=True)
    r=manual.run(root/'denied',c,u,'SYNTHETIC',opener=opener)
    assert r['status']=='FAIL' and r['physical_http']==len(calls)==1
    assert r['errors'][0]['reason']=='authentication_failure' and r['errors'][0]['endpoint']=='/oauth2/tokenP'


def test_retry_and_failures_share_twelve_limit(setup):
    root,c,u=setup;calls,opener=transport(u,fail_once=True)
    r=manual.run(root/'recover',c,u,'SYNTHETIC',opener=opener)
    assert r['status']=='PASS' and r['physical_http']==len(calls)==10 and r['retries']==1
    store=Store(root/'recover');budget=manual.ManualBudget(store,c,Metrics(),'SYNTHETIC')
    for _ in range(2):budget.before('KIS','/oauth2/tokenP','test')
    with pytest.raises(Stop):budget.before('KIS','/oauth2/tokenP','test')
    restored=manual.ManualBudget(store,c,Metrics(),'SYNTHETIC_RESTORE')
    assert restored.used==12
    with pytest.raises(Stop):restored.before('KIS','/oauth2/tokenP','test')
    with pytest.raises(Failure):restored.before('KRX','/svc/apis/sto/stk_bydd_trd','test')


def test_program_reparse_reuses_raw_instead_of_failed_price_request(setup,monkeypatch):
    root,c,u=setup;calls,opener=transport(u);original=engine.parse
    def buggy(raw,e):
        if e['task'].get('kind')=='price' and e['task'].get('symbol')=='000720':raise Failure('source_schema_mismatch')
        return original(raw,e)
    monkeypatch.setattr(engine,'parse',buggy)
    first=manual.run(root/'fix',c,u,'SYNTHETIC_OLD',opener=opener)
    assert first['status']=='FAIL' and first['physical_http']==5
    monkeypatch.setattr(engine,'parse',original)
    second=manual.run(root/'fix',c,u,'SYNTHETIC_FIXED',opener=opener)
    assert second['status']=='PASS' and second['physical_http']==10
    assert sum('FID_INPUT_ISCD=000720' in x and 'itemchartprice' in x for x in calls)==1


def test_full_execution_requires_its_own_approval_and_sample_plan_rejects_fail(setup):
    root,c,u=setup
    c.update(mode='full',trial_id='kis-full-20261026',max_total_http=5600)
    with pytest.raises(Failure,match='approval'):manual.validate_control(c)
    with pytest.raises(Failure):manual.full_plan({'status':'FAIL'},u)


def test_missing_ledger_never_restarts_budget(setup):
    root,c,u=setup;calls,opener=transport(u)
    manual.run(root/'lost',c,u,'SYNTHETIC',opener=opener)
    (root/'lost/http.jsonl').unlink()
    def no_http(*a,**kw):pytest.fail('lost ledger cannot be reset')
    with pytest.raises(Failure,match='lost_ledger'):manual.run(root/'lost',c,u,'SYNTHETIC',opener=no_http)


def test_new_workflow_has_no_schedule_and_old_control_is_not_used():
    text=Path('.github/workflows/kis-manual-validation.yml').read_text()
    assert 'schedule:' not in text and 'KRX_API_KEY' not in text
    assert 'kis-manual-20261026.json' in text and 'kis-validation-auth' in text
    assert 'contents: write' in text and 'actions: read' in text and 'actions: write' not in text
    assert 'kis_manual_actions publish' in text and 'kis_manual_actions seal' in text


def test_full_population_executor_fresh_data_and_full_DB_readback(setup):
    root,c,u=setup;sample_calls,opener=transport(u)
    sample=manual.run(root/'sample',c,u,'SYNTHETIC_SAMPLE',opener=opener)
    manual.full_plan(sample,u)
    c.update(mode='full',trial_id='kis-full-20261026',approval=manual.FULL_APPROVAL,max_total_http=5600,not_before='2026-10-26T11:45:00+00:00',expires_at='2026-10-26T14:35:00+00:00')
    calls,opener=transport(u)
    r=manual.run(root/'full',c,u,'SYNTHETIC_FULL',opener=opener)
    assert r['status']=='PASS' and r['target']==r['kis_validated']==2349
    assert r['physical_http']==len(calls)==4701 and len(sample_calls)==9
    assert r['db_json_match'] and r['http_failure_rate']==0
    assert r['metrics']['seconds']['DuckDB_write_and_readback']>=0 and r['bottleneck']
    assert not any('krx.co.kr' in x for x in calls)
    actions.verify_mode(root/'full',r)


def test_full_interruption_resumes_without_repeating_successful_symbols(setup):
    root,c,u=setup
    c.update(mode='full',trial_id='kis-full-20261026',approval=manual.FULL_APPROVAL,max_total_http=5600,not_before='2026-10-26T11:45:00+00:00',expires_at='2026-10-26T14:35:00+00:00')
    first_calls,original=transport(u)
    def interrupted(req,timeout):
        if 'FID_INPUT_ISCD=100000' in req.full_url:raise Stop('SYNTHETIC_INTERRUPTION')
        return original(req,timeout)
    first=manual.run(root/'resume',c,u,'SYNTHETIC_INTERRUPTED',opener=interrupted)
    assert first['status']=='FAIL' and first['kis_validated']==3 and first['physical_http']==10
    calls,opener=transport(u)
    second=manual.run(root/'resume',c,u,'SYNTHETIC_RESUMED',opener=opener)
    assert second['status']=='PASS' and second['physical_http']==4703
    assert len(calls)==4693
    assert not any(any('FID_INPUT_ISCD='+code in url for code in manual.TARGETS) or 'indexchartprice' in url for url in calls)
    assert second['failed_http_requests']==1
