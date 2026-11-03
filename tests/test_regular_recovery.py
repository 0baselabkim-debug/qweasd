"""Explicit recovery approval, real preserved prefix, zero provider HTTP."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import zipfile
import pytest
from collector_v1.core import Failure, Store, encoded, sha
from collector_v1.regular import recovery as r
from collector_v1.experiment.storage import Metrics, events
from collector_v1.kis_manual import ManualBudget
from kis_shared.lease import Lease, Blocked, initial

START=dt.datetime.fromisoformat('2026-11-03T15:21:12.590782+00:00')
OWNER=dict(repository=r.REPO,run_id=r.SOURCE_RUN,commit=r.SOURCE_SHA,id=r.EXPECTED_OWNER,
 acquired_at='2026-11-03T12:10:12.590782+00:00',expires_at='2026-11-03T15:20:12.590782+00:00')

def control():
 c=r.read_control();c.update(enabled=True,expected_revision='a'*40,reviewed_code_commit='b'*40,offline_ci_run='999')
 return c

@pytest.fixture(scope='module')
def restored(tmp_path_factory):
 path=Path(os.getenv('REGULAR_SOURCE_ZIP','/workspace/regular-41633101454-diagnostics/13230751197.zip'))
 assert path.exists(),'Approved original artifact must be supplied to offline tests'
 root=tmp_path_factory.mktemp('verified-prefix')/'source';receipt=r.restore_zip(path,root)
 return path,root,receipt


def test_real_partial_artifact_and_hashes(restored):
 path,root,receipt=restored
 assert receipt['validated_symbols']==280 and receipt['successful_observations']==563
 assert receipt['total_http']==568 and receipt['remaining_tasks']==4135
 assert len(receipt['allowed_failure_hashes'])==4135
 assert r.inventory(root)==Store(root).read('manifest.json')['files']


def test_source_zip_corruption_before_restore(restored,tmp_path):
 path,_,_=restored;p=tmp_path/'bad.zip';p.write_bytes(path.read_bytes()+b'corrupt')
 with pytest.raises(Failure,match='zip_digest'):r.restore_zip(p,tmp_path/'output')
 assert not (tmp_path/'output').exists()


def test_existing_root_never_reset(restored):
 path,root,_=restored;before=r.inventory(root)
 with pytest.raises(Failure,match='root_exists'):r.restore_zip(path,root)
 assert r.inventory(root)==before


def test_manifest_corruption_is_independently_detected(restored,tmp_path,monkeypatch):
 path,_,_=restored;out=tmp_path/'modified.zip'
 with zipfile.ZipFile(path) as source,zipfile.ZipFile(out,'w') as target:
  for name in source.namelist():
   raw=source.read(name)
   if name=='context.json':raw=raw+b' '
   target.writestr(name,raw)
 monkeypatch.setattr(r,'ZIP_SHA',sha(out.read_bytes()))
 with pytest.raises(Failure,match='manifest_mismatch'):r.restore_zip(out,tmp_path/'extract')


@pytest.mark.parametrize('field,value',[
 ('enabled',False),('approval','not-approved'),('source_run','1'),('artifact_id',1),('artifact_sha256','a'*64),
 ('asof','2026-11-04'),('generation_id','new-root'),('max_total_http',5601),('request_id','bad/identity'),
 ('expected_revision',None),('reviewed_code_commit','wrong'),('offline_ci_run',None),
 ('not_before','2026-11-03T15:20:12.590782+00:00'),('expires_at','2026-11-04T10:00:00+00:00')])
def test_control_fail_closed(field,value):
 c=control();c[field]=value
 with pytest.raises(Failure):r.check_control(c,START)


def test_control_wait_is_not_early_collection_permission():
 c=control();before=START-dt.timedelta(seconds=1)
 assert r.check_control(c,before,allow_wait=True)==c
 with pytest.raises(Failure):r.check_control(c,before)
 assert r.check_control(c,START)==c
 with pytest.raises(Failure):r.check_control(c,START+dt.timedelta(seconds=10200))


def test_control_extra_fields_rejected():
 c=control();c['force']=True
 with pytest.raises(Failure):r.check_control(c,START)


class Client:
 def __init__(self):
  self.changed=False;self.calls=0
  self.run_value=dict(id=int(r.SOURCE_RUN),head_sha=r.SOURCE_SHA,run_attempt=1,status='completed',path=r.policy.WORKFLOW,conclusion='failure',updated_at='2026-11-03T12:29:53Z')
  self.job=dict(id=125907375199,run_attempt=1,run_id=int(r.SOURCE_RUN),head_sha=r.SOURCE_SHA,status='completed',started_at='2026-11-03T12:07:45Z',completed_at='2026-11-03T12:29:53Z',runner_group_name='GitHub Actions',labels=['ubuntu-latest'],steps=[dict(status='completed')])
 def run(self,_):
  self.calls+=1;v=copy.deepcopy(self.run_value)
  if self.changed and self.calls>1:v['run_attempt']=2
  return v
 def request(self,path):
  if '/jobs?' in path:return dict(total_count=1,jobs=[copy.deepcopy(self.job)])
  return copy.deepcopy(self.run_value)


def test_exact_terminal_attempt_and_job():
 proof=r.terminal_proof(Client(),OWNER,START)
 assert proof['all_workers_stopped'] and proof['external_issuers_stopped'] and proof['attempt']==1

@pytest.mark.parametrize('scope,field,value',[
 ('owner','repository',r.REPOSITORIES[1]),('owner','id','f'*32),('owner','run_id','123'),
 ('run','run_attempt',2),('run','status','queued'),('run','head_sha','a'*40),('run','path','other.yml'),
 ('job','status','in_progress'),('job','run_attempt',2),('job','id',99),('job','runner_group_name','Self-hosted'),
 ('job','head_sha','b'*40),('job','completed_at',None),('job','steps',[dict(status='in_progress')])])
def test_terminal_mismatch(scope,field,value):
 c=Client();owner=copy.deepcopy(OWNER)
 target=owner if scope=='owner' else c.run_value if scope=='run' else c.job
 target[field]=value
 with pytest.raises(Failure):r.terminal_proof(c,owner,START)


def test_metadata_change_and_API_failure_block():
 c=Client();c.changed=True
 with pytest.raises(Failure):r.terminal_proof(c,OWNER,START)
 c=Client();c.run=lambda _: (_ for _ in ()).throw(TimeoutError())
 with pytest.raises(TimeoutError):r.terminal_proof(c,OWNER,START)


class Backend:
 def __init__(self):self.rev='a'*40;self.state=initial();self.state.update(owner=copy.deepcopy(OWNER),last_auth='2026-11-03T12:10:17.974227+00:00');self.conflict=False
 def read(self):return self.rev,copy.deepcopy(self.state)
 def compare_and_append(self,rev,state):
  if self.conflict or rev!=self.rev:return False
  self.rev='b'*40;self.state=copy.deepcopy(state);return True


def test_existing_190minute_lease_and_grace_are_not_weakened():
 b=Backend();at=[START-dt.timedelta(microseconds=1)]
 lease=Lease(b,r.REPO,'999','c'*40,clock=lambda:at[0]);old=copy.deepcopy(b.state)
 with pytest.raises(Blocked,match='quarantine'):lease.recover('a'*40,lambda o:r.terminal_proof(Client(),o,START))
 assert b.state==old
 at[0]=START;lease.recover('a'*40,lambda o:r.terminal_proof(Client(),o,START))
 assert b.state['owner'] is None and b.state['last_auth']==old['last_auth']
 assert b.state['auth_not_before']==old['auth_not_before']
 with pytest.raises(Blocked):lease.recover('a'*40,lambda o:{})


def test_CAS_conflict_does_not_release_owner():
 b=Backend();b.conflict=True;lease=Lease(b,r.REPO,'999','c'*40,clock=lambda:START)
 with pytest.raises(Blocked):lease.recover(b.rev,lambda o:r.terminal_proof(Client(),o,START))
 assert b.state['owner']==OWNER


def test_budget_restores_all_prior_physical_attempts(restored,tmp_path):
 _,root,_=restored;folder=tmp_path/'collection';shutil.copytree(root/r.GENERATION/'collection',folder)
 budget=ManualBudget(Store(folder),dict(mode='full',max_total_http=5600,expires_at=control()['expires_at']),Metrics(),'999',validator=lambda c:None)
 assert budget.used==budget.initial==564 and budget.used+4==568
 budget.before('KIS','/oauth2/tokenP','authentication')
 assert budget.used==565 and budget.used+4==569
 assert events(folder/'http.jsonl')[-1]['sequence']==565
 assert len(events(root/r.GENERATION/'collection/http.jsonl'))==1128


def test_review_only_adjudicates_exact_guard_failures(restored):
 _,root,proof=restored;review=r.ResumeReview(dict(status='RECOVERED_STOPPED_OWNER',source=proof))
 failures=events(root/r.GENERATION/'collection/failures.jsonl')
 assert review.permits(failures[1])
 assert not review.permits(failures[0])
 e=dict(failures[1],reason='source_schema_mismatch');assert not review.permits(e)
 e=copy.deepcopy(failures[1]);e['task']['asof']='2026-11-04';assert not review.permits(e)
 with pytest.raises(Failure):r.ResumeReview(dict(status='WAITING_FOR_SAFE_RECOVERY',source=proof))


def test_workflow_is_one_shot_and_no_regular_schedule_change():
 text=Path('.github/workflows/kis-regular-recovery-20261103.yml').read_text()
 assert '[execute-regular-recovery]' in text and 'schedule:' not in text
 assert 'timeout-minutes: 360' in text and 'cancel-in-progress: false' in text
 assert text.index('recovery wait')<text.index('Resume missing tasks')
 assert 'cron: \'30 11 * * 1-5\'' in Path('.github/workflows/kis-market-2030.yml').read_text()


def test_real_prefix_resumes_only_missing_price_with_mocked_wire(restored,tmp_path,monkeypatch):
 from collector_v1 import kis_manual
 from collector_v1.providers import task
 from collector_v1.validation import parse
 _,source,proof=restored;folder=tmp_path/'collection';shutil.copytree(source/r.GENERATION/'collection',folder)
 store=r.Evidence(folder,Metrics());cache=r.engine.replay(store,1,asof=r.ASOF,generation_id=r.GENERATION)
 universe=Store(source/r.GENERATION/'preparation').read('universe.json');selected=[x for x in universe if x['symbol']=='009290'];assert len(selected)==1
 flow_task=task('flow',r.ASOF,round=1,symbol='009290',market='KOSPI',generation_id=r.GENERATION)
 flow=parse(store.raw(cache[r.engine.identity(flow_task)]),cache[r.engine.identity(flow_task)])[0]
 before=(folder/'http.jsonl').read_bytes();calls=[]
 for key in ('KIS_APP_KEY','KIS_APP_SECRET'):monkeypatch.setenv(key,'SYNTHETIC_ONLY')
 monkeypatch.setattr('collector_v1.kis_manual.time.sleep',lambda _:None)
 class Policy:
  eligible=staticmethod(lambda _:selected)
  validate_control=staticmethod(lambda _:None)
  stop_on_unknown=True
 class Response:
  status=200
  def __init__(self,value):self.value=encoded(value)
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def read(self,n):return self.value
 def wire(req,timeout):
  calls.append(req.full_url)
  if req.full_url.endswith('/oauth2/tokenP'):return Response(dict(access_token='SYNTHETIC_TOKEN'))
  assert 'inquire-daily-itemchartprice' in req.full_url and 'FID_INPUT_ISCD=009290' in req.full_url
  return Response(dict(rt_cd='0',output1=dict(stck_shrn_iscd='009290'),output2=[dict(stck_bsop_date='20261103',stck_oprc=str(flow['close']),stck_hgpr=str(flow['close']),stck_lwpr=str(flow['close']),stck_clpr=str(flow['close']),acml_vol=str(flow['volume']),acml_tr_pbmn=str(flow['turnover']),mod_yn='N')]))
 c=dict(schema=1,mode='full',asof=r.ASOF,trial_id=r.GENERATION,generation_id=r.GENERATION,request_id='SYNTHETIC_RESUME',previous_run=r.SOURCE_RUN,max_total_http=5600,not_before=control()['not_before'],expires_at=control()['expires_at'],approval=control()['approval'])
 result=kis_manual.run(folder,c,universe,'999',opener=wire,policy=Policy(),resume_review=r.ResumeReview(dict(status='RECOVERED_STOPPED_OWNER',source=proof)))
 assert result['status']=='PASS' and result['target']==1 and result['kis_validated']==1
 assert len(calls)==2 and result['physical_http_this_run']==2 and result['physical_http']==566
 assert (folder/'http.jsonl').read_bytes().startswith(before)
 assert (source/r.GENERATION/'collection/http.jsonl').read_bytes()==before
 assert result['rows'][0]['close']==flow['close'] and result['db_json_match']
