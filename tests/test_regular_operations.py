"""Regular operation safety; no provider network, token issue or production writes."""
import copy,datetime as dt,json,os,shutil,urllib.error,zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from collector_v1.core import Failure,Store,encoded,sha
from collector_v1.regular import policy,runner,operator,status,session_cleanup,handoff
from collector_v1.providers import network_diagnostics
from collector_v1.experiment.storage import events
from collector_v1.regular.github import APIError
from regular_fixtures import Client,context


def test_manual_start_is_real_only_with_explicit_approval_and_same_day(monkeypatch):
 run=dict(id=200,event='workflow_dispatch',created_at='2026-11-04T11:35:00Z',head_sha='a'*40)
 inp=dict(asof='2026-11-04',request_id='SYNTHETIC_START_1',approval='USER_APPROVED_REGULAR_START')
 monkeypatch.setattr(policy,'utcnow',lambda:policy.timestamp('2026-11-04T11:35:00Z'))
 c=policy.manual_start(run,inp)
 assert c['trigger']=='workflow_dispatch' and c['mode']=='MANUAL_COLLECTION'
 assert c['generation_id']=='kis-regular-20261104-2030'
 assert policy.control(c)['request_id']=='SYNTHETIC_START_1'
 for changes in [dict(approval=''),dict(asof='2026-11-03'),dict(request_id='bad'),dict(expires_at='2026-11-05T00:00:00Z')]:
  with pytest.raises(Failure):policy.manual_start(run,dict(inp,**changes))
 monkeypatch.setattr(policy,'utcnow',lambda:policy.timestamp('2026-11-04T14:21:00Z'))
 with pytest.raises(Failure,match='window_closed'):policy.manual_start(run,inp)


def test_scheduled_and_manual_share_same_date_slot_CAS():
 c=context('2026-11-04','100');client=Client(c)
 from collector_v1.regular.ledger import Ledger
 assert Ledger(client,c).claim()[0]
 assert not Ledger(client,dict(c,run_id='200',trigger='workflow_dispatch')).claim()[0]
 assert client.docs['ledger/2026.json']['slots']['2026-11-04/2030']['context']['run_id']=='100'


def test_unknown_network_never_retries_inside_task(tmp_path):
 from collector_v1.experiment import engine
 from collector_v1.experiment.storage import Metrics
 calls=[]
 def wire(t):calls.append(t);raise Failure('network_failure',True)
 api=SimpleNamespace(stop_on_unknown=True,store=SimpleNamespace(root=tmp_path),fetch=wire)
 budget=SimpleNamespace(check=lambda:None)
 with pytest.raises(Failure):engine.fetch_task({'kind':'price','asof':'2026-11-04'},api,budget,{}, {},Metrics())
 assert len(calls)==1 and len(events(tmp_path/'failures.jsonl'))==1


@pytest.mark.parametrize('phase',['connect_or_response_headers','response_read'])
def test_transport_diagnostics_never_persist_exception_text(phase):
 e=urllib.error.URLError(ConnectionResetError(104,'SYNTHETIC_SECRET https://host?appsecret=NO'))
 d=network_diagnostics(e,phase)
 assert d==dict(exception_class='URLError',reason_class='ConnectionResetError',phase=phase,errno=104)
 assert 'SECRET' not in json.dumps(d) and 'https' not in json.dumps(d)


@pytest.mark.parametrize('inflight,ticket,transport,auth,deadline,expected',[
 (False,None,None,False,None,'NORMAL_OWNED_RELEASE'),
 (False,None,None,True,'verified','NORMAL_OWNED_RELEASE'),
 (True,None,None,True,'verified','KEEP_UNRESOLVED'),
 (False,False,None,True,'verified','KEEP_UNRESOLVED'),
 (False,None,('ticket',),True,'verified','KEEP_UNRESOLVED'),
 (False,None,None,True,None,'KEEP_UNRESOLVED')])
def test_closed_failure_is_distinct_from_unknown_transport_or_oauth(inflight,ticket,transport,auth,deadline,expected):
 s=SimpleNamespace(network_inflight=inflight,dispatch_ticket=ticket,transport_pending=transport,auth_used=auth,token_deadline=deadline,uncertain=False)
 api=SimpleNamespace(token='SYNTHETIC_TOKEN',budget=SimpleNamespace(ledger=[]))
 assert session_cleanup.discard_for_normal_exit(s,[api])==expected and api.token is None
 assert s.uncertain==(expected=='KEEP_UNRESOLVED')


@pytest.mark.parametrize('http',[401,403])
def test_explicit_closed_auth_rejection_is_not_unknown(http):
 s=SimpleNamespace(network_inflight=False,dispatch_ticket=None,transport_pending=None,auth_used=True,token_deadline=None,uncertain=False)
 api=SimpleNamespace(token=None,budget=SimpleNamespace(ledger=[dict(endpoint='/oauth2/tokenP',status='http_failure',http=http)]))
 assert session_cleanup.discard_for_normal_exit(s,[api])=='NORMAL_OWNED_RELEASE'


def test_exception_is_reraised_only_after_safe_normal_exit(monkeypatch):
 seen=[]
 class Session:
  auth_used=False;token_deadline=None;network_inflight=False;dispatch_ticket=None;transport_pending=None;uncertain=False
  def __enter__(self):return self
  def __exit__(self,kind,*args):seen.append(kind)
 monkeypatch.setattr(session_cleanup,'configured',lambda _:Session())
 with pytest.raises(Failure,match='SYNTHETIC_KNOWN'):
  with session_cleanup.controlled_session('SYNTHETIC',[]):raise Failure('SYNTHETIC_KNOWN')
 assert seen==[None]


def test_unknown_auth_never_releases_even_when_exception_handled(monkeypatch):
 seen=[]
 class Session:
  auth_used=True;token_deadline=None;network_inflight=False;dispatch_ticket=None;transport_pending=None;uncertain=False
  def __enter__(self):return self
  def __exit__(self,kind,*args):seen.append((kind,self.uncertain))
 monkeypatch.setattr(session_cleanup,'configured',lambda _:Session())
 with pytest.raises(Failure):
  with session_cleanup.controlled_session('SYNTHETIC',[]):raise Failure('SYNTHETIC_UNKNOWN_AUTH')
 assert seen==[(Failure,True)]


def test_health_detects_missing_slot_without_dispatch_or_token():
 c=context('2026-11-04');client=Client(c)
 assert status.inspect_slot(client,c['asof'],policy.timestamp('2026-11-04T11:29:59Z'))['status']=='NOT_DUE'
 x=status.inspect_slot(client,c['asof'],policy.timestamp('2026-11-04T11:40:00Z'))
 assert x['status']=='NO_SLOT_AFTER_SCHEDULE' and x['late_seconds']==600 and x['market_HTTP']==0 and not x['actions_taken']
 assert not x['scheduler_exact_time_guaranteed']


def resume_context():
 return dict(schema=1,mode='MANUAL_RESUME',trigger='workflow_dispatch',execution_operation='resume',asof='2026-11-03',slot='2030',
  generation_id='kis-regular-20261103-2030',scheduled_at='2026-11-03T20:30:00+09:00',not_before='2026-11-03T15:30:00Z',expires_at='2026-11-03T18:00:00Z',
  source_run='41633101454',request_id='SYNTHETIC_RESUME_ONCE',run_id='999',code_commit='b'*40,approval='USER_APPROVED_REGULAR_RESUME')


def test_resume_window_never_changes_target_or_exceeds170minutes():
 c=resume_context();operator.check_window(c,policy.timestamp('2026-11-03T16:00:00Z'))
 for changes,at in [(dict(expires_at='2026-11-03T19:00:00Z'),'2026-11-03T16:00:00Z'),(dict(expires_at='2026-11-04T00:01:00Z'),'2026-11-04T00:00:00Z'),(dict(generation_id='new-root-bypass'),'2026-11-03T16:00:00Z'),({},'2026-11-04T00:00:00Z')]:
  with pytest.raises(Failure):operator.check_window(dict(c,**changes),policy.timestamp(at))



def test_actual_workflow_has_explicit_modes_and_no_extra_cron():
 import yaml
 w=yaml.load(Path('.github/workflows/kis-market-2030.yml').read_text(),Loader=yaml.BaseLoader)
 assert w['on']['schedule']==[{'cron':'30 11 * * 1-5'}]
 assert w['on']['workflow_dispatch']['inputs']['operation']['default']=='validate'
 assert w['on']['workflow_dispatch']['inputs']['operation']['options']==['validate','start','resume']
 assert w['concurrency']['cancel-in-progress']=='false'
 text=Path('.github/workflows/kis-market-2030.yml').read_text()
 assert text.index('operator wait')<text.index('operator collect')
 assert "steps.freeze.outputs.operation == 'start'" in text


class TerminalClient:
 def __init__(self,c,owner):
  self.c=c;self.count=0
  self.data=dict(id=int(c['source_run']),head_sha=owner['commit'],head_branch='main',path=policy.WORKFLOW,status='completed',conclusion='failure',run_attempt=1,workflow_id=10,check_suite_id=20,created_at='2026-11-03T12:00:00Z',run_started_at='2026-11-03T12:00:00Z',updated_at='2026-11-03T12:30:00Z')
  self.job=dict(id=30,run_id=int(c['source_run']),head_sha=owner['commit'],run_attempt=1,status='completed',runner_group_name='GitHub Actions',labels=['ubuntu-latest'],started_at='2026-11-03T12:00:00Z',completed_at='2026-11-03T12:30:00Z',steps=[dict(status='completed')])
 def run(self,id):self.count+=1;return copy.deepcopy(self.data)
 def request(self,path):return dict(total_count=1,jobs=[copy.deepcopy(self.job)]) if '/jobs?' in path else copy.deepcopy(self.data)


def test_current_attempt_and_all_hosted_steps_must_be_stopped(monkeypatch):
 c=resume_context();owner=dict(repository=policy.REPO,run_id=c['source_run'],commit='a'*40,id='d'*32,acquired_at='2026-11-03T12:10:00Z',expires_at='2026-11-03T15:20:00Z')
 monkeypatch.setattr(policy,'utcnow',lambda:policy.timestamp('2026-11-03T16:00:00Z'))
 client=TerminalClient(c,owner);assert operator.terminal_proof(client,c,owner)['all_workers_stopped']
 for scope,key,value in [('data','run_attempt',2),('data','status','queued'),('data','head_branch','other'),('job','status','in_progress'),('job','runner_group_name','Self-hosted'),('job','steps',[dict(status='in_progress')]),('job','run_attempt',2)]:
  client=TerminalClient(c,owner);getattr(client,scope)[key]=value
  with pytest.raises(Failure):operator.terminal_proof(client,c,owner)


def test_resume_review_cannot_whitelist_changed_or_unknown_failure():
 from collector_v1.experiment.storage import Metrics
 from collector_v1 import kis_manual
 event=dict(reason='shared_prior_HTTP_outcome_unresolved',task=dict(asof='2026-11-03',symbol='000720'))
 proof=dict(proof=dict(allowed_failure_hashes=[sha(encoded(event))]))
 review=operator.Review(proof);assert review.permits(event)
 assert not review.permits(dict(event,reason='network_failure'))
 assert not review.permits(dict(event,task=dict(asof='2026-11-04',symbol='000720')))
 with pytest.raises(Failure):kis_manual.capacity_plan(7000,5000)
 assert kis_manual.capacity_plan(7000,5000,bounded_resume=True)['projected_complete_within_window'] is False


def test_delayed_start_lag_is_not_forged_as_on_time():
 c=policy.freeze(dict(id=100,event='schedule',created_at='2026-11-04T12:07:41Z',head_sha='a'*40),policy.CRON)
 assert (policy.timestamp(c['run_created_at'])-policy.timestamp(c['scheduled_at'])).total_seconds()==2261
 assert c['scheduled_at']=='2026-11-04T20:30:00+09:00'


@pytest.mark.parametrize('state',policy.ACTIVE)
def test_health_distinguishes_existing_queued_trigger_from_missing_trigger(state):
 c=context('2026-11-04');client=Client(c)
 client.runs=[dict(id=50,event='schedule',path=policy.WORKFLOW,status=state,created_at='2026-11-04T11:35:00Z',head_sha='a'*40)]
 r=status.inspect_slot(client,c['asof'],policy.timestamp('2026-11-04T11:40:00Z'))
 assert r['status']=='SCHEDULE_RUN_EXISTS_SLOT_NOT_YET_CLAIMED'
 assert r['active_runs']==[dict(run_id='50',status=state)] and r['market_HTTP']==0


def test_access_token_scan_is_sticky_after_memory_discard(tmp_path,monkeypatch):
 monkeypatch.setattr(runner,'ROOT',tmp_path/'runtime');store=Store(runner.ROOT)
 store.write('raw-error.json',dict(message='SYNTHETIC_ACCESS_TOKEN_MUST_NOT_UPLOAD'))
 s=SimpleNamespace(network_inflight=False,dispatch_ticket=None,transport_pending=None,auth_used=True,token_deadline='verified',uncertain=False)
 api=SimpleNamespace(token='SYNTHETIC_ACCESS_TOKEN_MUST_NOT_UPLOAD',budget=SimpleNamespace(ledger=[]))
 with pytest.raises(Failure,match='credential_in_regular_output'):session_cleanup.discard_for_normal_exit(s,[api])
 assert api.token is None and s.uncertain
 assert store.read('failure.json')['reason']=='credential_in_regular_output'
 with pytest.raises(Failure,match='credential_in_regular_output'):runner.seal()


def operation_environment(tmp_path,monkeypatch):
 c=context('2026-11-04','100');client=Client(c)
 monkeypatch.setattr(runner,'ROOT',tmp_path/'SYNTHETIC-operation')
 Store(runner.ROOT).write('context.json',c)
 monkeypatch.setattr(runner,'GitHub',lambda:client)
 monkeypatch.setattr(policy,'utcnow',lambda:policy.timestamp(c['scheduled_at'])+dt.timedelta(minutes=1))
 for key,value in dict(GITHUB_REPOSITORY=policy.REPO,GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='schedule',GITHUB_RUN_ATTEMPT='1',GITHUB_RUN_ID=c['run_id'],GITHUB_SHA=c['code_commit']).items():monkeypatch.setenv(key,value)
 config=tmp_path/'access.json';config.write_bytes(encoded(dict(mode='shared-account-exclusive',shared_policy='SYNTHETIC')))
 monkeypatch.setattr(runner,'CONFIG',config)
 return c,client


def test_busy_account_does_not_consume_daily_slot_or_call_provider(tmp_path,monkeypatch):
 from kis_shared.lease import Blocked
 from collector_v1.regular.ledger import Ledger
 c,client=operation_environment(tmp_path,monkeypatch)
 def busy(*args):raise Blocked('SYNTHETIC_ACCOUNT_BUSY')
 monkeypatch.setattr(session_cleanup,'configured',busy)
 monkeypatch.setattr('collector_v1.regular.collector.run',lambda *a,**k:pytest.fail('provider must not run'))
 assert runner.collect()==1 and Ledger(client,c).current() is None
 assert client.docs['attempts/100.json']['summary']['total_physical_http']==0
 # A later explicitly approved start can acquire the still-unused date slot.
 assert Ledger(client,dict(c,run_id='101',trigger='workflow_dispatch')).claim()[0]


def test_daily_claim_is_after_account_acquisition_before_provider(tmp_path,monkeypatch):
 from contextlib import contextmanager
 from collector_v1.regular.ledger import Ledger
 c,client=operation_environment(tmp_path,monkeypatch);seen=[]
 @contextmanager
 def account(*args):
  seen.append(('account',Ledger(client,c).current()));yield None
 monkeypatch.setattr(session_cleanup,'controlled_session',account)
 monkeypatch.setattr(runner.RuntimeGuard,'__call__',lambda self:None)
 def stop(*args,**kwargs):
  seen.append(('provider',Ledger(client,c).assert_owner()['context']['run_id']))
  raise Failure('SYNTHETIC_REQUEST_BEFORE_SEND_FAILURE')
 monkeypatch.setattr('collector_v1.regular.collector.run',stop)
 assert runner.collect()==1 and seen==[('account',None),('provider','100')]
 assert Ledger(client,c).current()['status']=='FAILED'


def test_after_account_duplicate_CAS_never_calls_or_overwrites_winner(tmp_path,monkeypatch):
 from contextlib import contextmanager
 from collector_v1.regular.ledger import Ledger
 c,client=operation_environment(tmp_path,monkeypatch);winner=dict(c,run_id='101')
 @contextmanager
 def account(*args):
  assert Ledger(client,winner).claim()[0];yield None
 monkeypatch.setattr(session_cleanup,'controlled_session',account)
 monkeypatch.setattr('collector_v1.regular.collector.run',lambda *a,**k:pytest.fail('duplicate must not call provider'))
 assert runner.collect()==1
 record=Ledger(client,winner).current()
 assert record['status']=='CLAIMED' and record['context']==winner
 assert Store(runner.ROOT).read('report.json')['total_physical_http']==0


def test_freeze_and_resume_metadata_import_before_dependency_install():
 import subprocess,sys
 result=subprocess.run([sys.executable,'-S','-c',
  'import collector_v1.regular.runner; import collector_v1.regular.operator; import sys; assert "duckdb" not in sys.modules'],capture_output=True,text=True)
 assert result.returncode==0,result.stderr
