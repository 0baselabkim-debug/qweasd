"""Offline scheduling, CAS and complete synthetic collection; zero real APIs."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from collector_v1.core import Failure,Stop,Store,encoded
from collector_v1.regular import policy,collector,runner
from collector_v1.regular.ledger import Ledger
from collector_v1.regular.github import check_runs,APIError
from regular_fixtures import Client,context,transport


@pytest.fixture
def env(monkeypatch,tmp_path):
    c=context();clock=policy.timestamp(c['scheduled_at'])+dt.timedelta(minutes=1)
    monkeypatch.setenv('GITHUB_RUN_ID',c['run_id']);monkeypatch.setenv('GITHUB_SHA',c['code_commit'])
    for key,value in [('GITHUB_REPOSITORY',policy.REPO),('GITHUB_REF','refs/heads/main'),('GITHUB_EVENT_NAME','schedule'),('GITHUB_RUN_ATTEMPT','1')]:monkeypatch.setenv(key,value)
    monkeypatch.setattr(policy,'utcnow',lambda:clock)
    monkeypatch.setattr('collector_v1.experiment.storage.time.sleep',lambda _:None)
    for name in ('KIS_APP_KEY','KIS_APP_SECRET'):monkeypatch.setenv(name,'SYNTHETIC_'+name)
    for module in ('collector_v1.providers','collector_v1.experiment.engine','collector_v1.kis_manual','collector_v1.core'):
        monkeypatch.setattr(module+'.now',lambda:clock.isoformat())
    return c,tmp_path


def test_slot_date_pinned_before_midnight_queue_and_170_minute_window():
    c=context();assert c['asof']=='2026-10-29' and c['scheduled_at']=='2026-10-29T20:30:00+09:00'
    for late in ['2026-10-30T00:01:00+09:00','2026-10-30T09:00:00+09:00','2026-10-30T20:30:00+09:00']:
        with pytest.raises(Failure,match='window_closed'):policy.window(c,policy.timestamp(late))
        assert c['asof']=='2026-10-29'
    policy.window(c,policy.timestamp('2026-10-29T23:19:59+09:00'))
    with pytest.raises(Failure):policy.window(c,policy.timestamp('2026-10-29T23:20:00+09:00'))


@pytest.mark.parametrize('created',['2026-10-29T11:29:00Z','2026-10-29T15:01:00Z','2026-10-30T00:01:00Z'])
def test_ambiguous_or_late_schedule_creation_rejected(created):
    with pytest.raises(Failure):policy.freeze(dict(id=1,event='schedule',created_at=created,head_sha='a'*40),policy.CRON)


def test_atomic_claim_only_one_owner_and_no_reset_on_duplicate(env):
    c,_=env;client=Client(c)
    contenders=[Ledger(client,dict(c,run_id=str(i))) for i in (99,100)]
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda l:l.claim()[0],contenders))
    assert sum(results)==1
    winner=next(l for l,won in zip(contenders,results) if won);winner.transition('FAILED',reason='SYNTHETIC_FAILURE')
    before=copy.deepcopy(client.docs)
    won,r=Ledger(client,c).claim();assert not won and r['status']=='FAILED' and client.docs==before


@pytest.mark.parametrize('status',policy.ACTIVE)
def test_earlier_pending_or_running_slot_blocks_duplicate(env,status):
    c,_=env;client=Client(c)
    client.runs=[dict(id=98,event='schedule',path=policy.WORKFLOW,created_at='2026-10-29T11:30:00Z',head_sha='a'*40,status=status)]
    with pytest.raises(Failure,match='earlier_active'):check_runs(client,c)


def test_complete_generation_not_recollected_and_failed_does_not_replace_latest(env):
    c,_=env;client=Client(c);l=Ledger(client,c);assert l.claim()[0]
    for state in ('PREPARING','COLLECTING','INTERNAL_PASS'):l.transition(state)
    l.transition('COMPLETE_PROVISIONAL',summary={'internal_validation':'PASS'},artifact={'digest':'sha256:'+'a'*64})
    assert not l.claim()[0]
    latest=copy.deepcopy(l.load()[0]['latest_complete'])
    next_c=context('2026-10-30','101');n=Ledger(client,next_c);assert n.claim()[0];n.transition('FAILED',reason='SYNTHETIC_FAILURE')
    assert n.load()[0]['latest_complete']==latest
    with pytest.raises(Failure,match='terminal'):n.transition('COMPLETE_PROVISIONAL',summary={'internal_validation':'PASS'},artifact={'digest':'x'})


def test_incomplete_or_artifact_missing_cannot_be_approved(env):
    c,_=env;client=Client(c);l=Ledger(client,c);l.claim()
    with pytest.raises(Failure,match='transition'):l.transition('COMPLETE_PROVISIONAL',summary={'internal_validation':'PASS'},artifact={'digest':'x'})
    for state in ('PREPARING','COLLECTING','INTERNAL_PASS'):l.transition(state)
    with pytest.raises(Failure,match='verified_artifact'):l.transition('COMPLETE_PROVISIONAL',summary={'internal_validation':'PASS'})


def test_account_isolation_unverified_blocks_before_auth(env,monkeypatch):
    c,root=env;client=Client(c);Ledger(client,c).claim()
    monkeypatch.setattr(runner,'ROOT',root/'runtime');Store(runner.ROOT).write('context.json',c)
    monkeypatch.setattr(runner,'GitHub',lambda:client)
    def forbidden(*args,**kwargs):pytest.fail('Unverified shared access must not open HTTP')
    monkeypatch.setattr('urllib.request.OpenerDirector.open',forbidden)
    assert runner.collect()==1
    r=Store(runner.ROOT).read('report.json')
    assert r['status']=='BLOCKED' and r['total_physical_http']==0
    assert Ledger(client,c).current()['status']=='BLOCKED'


def test_weekend_has_zero_provider_requests_even_without_credentials(env,monkeypatch):
    c,root=env;c=context('2026-10-31');monkeypatch.setattr(policy,'utcnow',lambda:policy.timestamp(c['scheduled_at'])+dt.timedelta(minutes=1))
    client=Client(c);Ledger(client,c).claim();monkeypatch.setattr(runner,'ROOT',root/'weekend')
    Store(runner.ROOT).write('context.json',c);monkeypatch.setattr(runner,'GitHub',lambda:client)
    assert runner.collect()==0
    assert Store(runner.ROOT).read('report.json')['total_physical_http']==0
    assert Ledger(client,c).current()['status']=='SKIPPED_WEEKEND'


def test_verified_identity_binding_expiry_and_missing_proof(env,monkeypatch):
    import os
    c,_=env
    cfg=dict(repository=policy.REPO,mode='dedicated-account-reviewed',verified=True,separate_account_verified=True,
        reviewed_by='SYNTHETIC',reviewed_at='2026-10-29T10:00:00Z',expires_at='2026-10-30T10:00:00Z',
        evidence_reference='SYNTHETIC_ONLY',account_rate_policy_reference='SYNTHETIC_ONLY',token_policy_reference='SYNTHETIC_ONLY',
        app_key_sha256=hashlib.sha256(os.environ['KIS_APP_KEY'].encode()).hexdigest())
    assert policy.access_proof(cfg,os.environ)['status']=='REVIEWED_ACCOUNT_ISOLATION'
    cfg['app_key_sha256']='0'*64
    with pytest.raises(Failure,match='does_not_match'):policy.access_proof(cfg,os.environ)
    cfg['verified']=False
    with pytest.raises(Failure,match='unverified'):policy.access_proof(cfg,os.environ)


def test_holiday_unknown_or_conflicting_calendar_stops_before_master(env):
    c,root=env
    calls,opener=transport(c,closed=True)
    r,u,_,_=collector.prepare(root/'closed',c,lambda:None,opener=opener)
    assert r['status']=='SKIPPED_CLOSED' and u is None and len(calls)==2
    calls,opener=transport(c,unknown_calendar=True)
    with pytest.raises(Failure,match='unverified'):collector.prepare(root/'unknown',c,lambda:None,opener=opener)
    assert len(calls)==2
    calls,opener=transport(c)
    ref=dict(source_kind='KRX_OFFICIAL',asof=c['asof'],open=False,source_url='SYNTHETIC_ONLY',sha256='a'*64,retrieved_at='2026-10-29T11:00:00Z')
    with pytest.raises(Failure,match='conflict'):collector.prepare(root/'conflict',c,lambda:None,opener=opener,reference=ref)
    assert len(calls)==2


@pytest.fixture
def full(env):
    c,root=env;calls,opener=transport(c)
    r,api=collector.run(root/'full',c,lambda:None,opener=opener)
    assert r['status']=='INTERNAL_PASS'
    return c,root/'full',r,calls,api


def test_dynamic_whole_population_single_auth_and_full_storage(full):
    c,root,r,calls,api=full
    assert r['target']==r['received_symbols']==r['kis_validated']==2003
    assert r['target']!=2348 and r['db_json_match']
    assert r['total_physical_http']==len(calls)==2*2003+6
    assert sum('/oauth2/tokenP' in u for u in calls)==1
    assert r['provider_finality']=='UNVERIFIED' and r['official_price_status']=='PENDING_KRX'
    assert all('20261028' not in u for u in calls)
    collector.verify(root,c,r)


def test_population_change_and_partial_failure_not_complete(env):
    c,root=env;calls,opener=transport(c,halt_one=True)
    r,_=collector.run(root/'changed',c,lambda:None,opener=opener)
    assert r['target']==2002 and r['master']['excluded']==1 and r['status']=='INTERNAL_PASS'
    calls,opener=transport(c,conflict=True)
    partial,_=collector.run(root/'partial',c,lambda:None,opener=opener)
    assert partial['status']=='FAILED' and partial['kis_validated']==partial['target']-1
    assert partial['validation_failed_symbols']==['100000'] and partial['collection_complete'] is False


def test_raw_tampering_and_DB_JSON_mismatch_rejected(full):
    c,root,r,_,_=full
    import duckdb
    path=root/'collection'/r['database_file']
    with duckdb.connect(str(path)) as db:db.execute("UPDATE observations SET payload='{}' WHERE symbol='000720'")
    with pytest.raises(Failure,match='DB_JSON'):collector.verify(root,c,r)
    blob=next((root/'collection'/'raw').glob('*.gz'));blob.write_bytes(b'CORRUPT')
    with pytest.raises((Failure,ValueError,OSError)):collector.verify(root,c,r)


def test_budget_cannot_be_enlarged_or_exceeded(env):
    c,root=env;control=policy.control(c)
    from collector_v1.kis_manual import ManualBudget
    from collector_v1.experiment.storage import Metrics
    b=ManualBudget(Store(root/'budget'),control,Metrics(),'99',validator=policy.RunPolicy(c,lambda:None).validate_control)
    b.used=5600
    with pytest.raises(Stop):b.before('KIS','/oauth2/tokenP','SYNTHETIC')
    control['max_total_http']=5601
    with pytest.raises(Failure,match='control'):policy.RunPolicy(c,lambda:None).validate_control(control)


def test_manifest_corruption_and_credentials_never_uploaded(env,monkeypatch):
    c,root=env;monkeypatch.setattr(runner,'ROOT',root/'seal');Store(runner.ROOT).write('context.json',c)
    assert runner.seal()==0;runner.verify_manifest(runner.ROOT)
    (runner.ROOT/'context.json').write_bytes(b'{}')
    with pytest.raises(Failure,match='manifest'):runner.verify_manifest(runner.ROOT)
    (runner.ROOT/'secret.txt').write_text('SYNTHETIC_KIS_APP_KEY')
    with pytest.raises(Failure,match='credential'):runner.seal()
    assert not (runner.ROOT/'UPLOAD_ALLOWED').exists()


def test_workflow_is_one_cron_no_wait_jobs_no_old_schedule_mutation():
    text=Path('.github/workflows/kis-market-2030.yml').read_text()
    assert text.count('cron:')==1 and "cron: '30 11 * * 1-5'" in text
    assert 'queue: max' in text and 'cancel-in-progress: false' in text
    assert 'runner wait' not in text and '23:30' not in text
    assert 'secrets.KIS_REGULAR_APP_KEY' in text and 'secrets.KIS_APP_KEY' not in text
    assert 'retention-days: 90' in text and 'ARTIFACT_DIGEST' in text
    assert "github.event_name == 'schedule'" in text


def test_end_to_end_artifact_gate_and_atomic_consumer_handoff(env,monkeypatch):
    import os
    c,root=env;client=Client(c);Ledger(client,c).claim()
    monkeypatch.setattr(runner,'ROOT',root/'runner');Store(runner.ROOT).write('context.json',c)
    monkeypatch.setattr(runner,'GitHub',lambda:client)
    cfg=dict(repository=policy.REPO,mode='dedicated-account-reviewed',verified=True,separate_account_verified=True,
        reviewed_by='SYNTHETIC',reviewed_at='2026-10-29T10:00:00Z',expires_at='2026-10-30T10:00:00Z',
        evidence_reference='SYNTHETIC_ONLY',account_rate_policy_reference='SYNTHETIC_ONLY',token_policy_reference='SYNTHETIC_ONLY',
        app_key_sha256=hashlib.sha256(os.environ['KIS_APP_KEY'].encode()).hexdigest())
    config=root/'config.json';config.write_bytes(encoded(cfg));monkeypatch.setattr(runner,'CONFIG',config)
    calls,opener=transport(c);original=collector.run
    monkeypatch.setattr(collector,'run',lambda folder,c,guard,**kwargs:original(folder,c,guard,opener=opener,**kwargs))
    assert runner.collect()==0 and Ledger(client,c).current()['status']=='INTERNAL_PASS'
    assert Ledger(client,c).load()[0]['latest_complete'] is None
    runner.seal();runner.verify_manifest(runner.ROOT)
    monkeypatch.setenv('ARTIFACT_ID','777');monkeypatch.setenv('ARTIFACT_DIGEST','b'*64)
    client.request=lambda path:dict(name=c['artifact_name'],digest='sha256:'+'b'*64,expired=False,workflow_run={'id':99},expires_at='2027-01-27T12:00:00Z')
    assert runner.finalize()==0
    data=Ledger(client,c).load()[0]
    assert data['slots'][Ledger(client,c).key]['status']=='COMPLETE_PROVISIONAL'
    assert data['latest_complete']['artifact']['id']==777
    assert data['latest_complete']['official_price_status']=='PENDING_KRX'
    assert data['latest_complete']['dashboard_publication']=='NOT_PUBLISHED'
    runner.verify_manifest(runner.ROOT) # final receipt does not modify the sealed source


def test_run_guard_rechecks_latest_owner_and_actual_status(env):
    c,_=env;client=Client(c)
    client.run=lambda _:dict(status='completed',head_sha=c['code_commit'])
    with pytest.raises(Failure,match='not_live'):check_runs(client,c)
    client=Client(c);l=Ledger(client,c);l.claim();l.transition('FAILED',reason='SYNTHETIC')
    with pytest.raises(Failure,match='terminal'):l.assert_owner()


def test_unverified_current_access_profile_cannot_be_enabled_by_a_boolean(env):
    import os
    cfg=json.loads(Path('.github/config/kis-regular-access.json').read_bytes())
    cfg['verified']=True
    with pytest.raises(Failure,match='unverified'):policy.access_proof(cfg,os.environ)


def test_later_active_duplicate_cannot_force_first_owner_to_abort(env):
    c,_=env;client=Client(c)
    client.runs=[dict(id=100,event='schedule',path=policy.WORKFLOW,created_at='2026-10-29T11:30:02Z',head_sha='a'*40,status='queued')]
    assert check_runs(client,c)['status']=='CURRENT_RUN_AND_LOCAL_QUEUE_RECHECKED'


def test_exact_date_handoff_never_labels_previous_day_or_failed_data_ready(env):
    from collector_v1.regular.handoff import by_date
    c,_=env;client=Client(c);l=Ledger(client,c);l.claim()
    assert by_date(client,c['asof'])['status']=='NOT_READY_FOR_REQUESTED_DATE'
    for state in ('PREPARING','COLLECTING','INTERNAL_PASS'):l.transition(state)
    s=dict(internal_validation='PASS',target=2,kis_validated=2,provider_finality='UNVERIFIED',official_price_status='PENDING_KRX')
    l.transition('COMPLETE_PROVISIONAL',summary=s,artifact={'digest':'sha256:'+'a'*64,'expires_at':'2027-01-27T12:00:00Z'})
    assert by_date(client,c['asof'])['status']=='READY_INTERNAL_PROVISIONAL'
    assert by_date(client,'2026-10-30')['status']=='NOT_READY_FOR_REQUESTED_DATE'
    assert by_date(client,'2027-01-01')['status']=='NOT_READY_FOR_REQUESTED_DATE'


def test_expired_artifact_cannot_be_announced_ready(env):
    from collector_v1.regular.handoff import by_date
    c,_=env;client=Client(c);l=Ledger(client,c);l.claim()
    for state in ('PREPARING','COLLECTING','INTERNAL_PASS'):l.transition(state)
    s=dict(internal_validation='PASS',target=2,kis_validated=2,provider_finality='UNVERIFIED',official_price_status='PENDING_KRX')
    l.transition('COMPLETE_PROVISIONAL',summary=s,artifact={'digest':'sha256:'+'a'*64,'expires_at':'2026-10-28T12:00:00Z'})
    assert by_date(client,c['asof'])['status']=='ARTIFACT_EXPIRED_OR_EXPIRATION_UNVERIFIED'


def test_secret_scan_failure_survives_process_boundary_and_never_completes(env,monkeypatch):
    c,root=env;client=Client(c);Ledger(client,c).claim();monkeypatch.setattr(runner,'ROOT',root/'secret-failure')
    monkeypatch.setattr(runner,'GitHub',lambda:client);s=Store(runner.ROOT)
    s.write('context.json',c);s.write('report.json',{'status':'INTERNAL_PASS'})
    s.write('failure.json',{'status':'FAILED','reason':'credential_in_regular_output'})
    (runner.ROOT/'UPLOAD_ALLOWED').touch()
    with pytest.raises(Failure,match='credential'):runner.seal()
    assert not (runner.ROOT/'UPLOAD_ALLOWED').exists()
    assert runner.finalize()==1 and Ledger(client,c).current()['status']=='FAILED'


def test_copied_context_cannot_authorize_another_run_or_manual_event(env,monkeypatch):
    c,_=env;runner.assert_runtime_actor(c)
    monkeypatch.setenv('GITHUB_RUN_ID','100')
    with pytest.raises(Failure,match='actor_binding'):runner.assert_runtime_actor(c)
    monkeypatch.setenv('GITHUB_RUN_ID',c['run_id']);monkeypatch.setenv('GITHUB_EVENT_NAME','workflow_dispatch')
    with pytest.raises(Failure,match='actor_binding'):runner.assert_runtime_actor(c)
