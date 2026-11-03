"""Source-bound policy and real Lease conflict paths, without network or keys."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import pytest
from kis_shared.lease import initial,Blocked,REPOSITORIES
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('readiness_v6',ROOT/'.github/scripts/kis_lock_operational_readiness_v3.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
@pytest.fixture
def env(tmp_path):
    c=json.loads((ROOT/'.github/config/kis-shared-account-v2.proposed.json').read_bytes())
    proof=json.loads((ROOT/c['native_proof_file']).read_bytes())
    repo=next(k for k,v in proof['runtime_source_hashes'].items() if v==c['runtime_source_hashes'])
    return dict(GITHUB_REPOSITORY=repo,GITHUB_RUN_ID='123',GITHUB_SHA='a'*40,RUNNER_TEMP=str(tmp_path),GH_TOKEN='SYNTHETIC_ONLY',KIS_APP_KEY_PRESENT='true',KIS_APP_SECRET_PRESENT='true')
class Backend:
    def __init__(self,*a,**kw):self.state=initial();self.writes=0
    def read(self):return 'a'*40,copy.deepcopy(self.state)
    def compare_and_append(self,*a):self.writes+=1;raise AssertionError('read-only contender must not write')

def cfg():return dict(probe_branch='kis-shared-probe-v3-fixture',allow_isolated_GitHub_probe=True)

def test_active_policy_approves_sources_but_never_grants_new_HTTP(env):
    report=r.active_policy_review(ROOT,env)
    assert report['active_policy_status']=='SOURCE_BOUND_APPROVED'
    assert report['active_policy_schema']==2
    assert report['new_participant_HTTP_blocked']==(env['GITHUB_REPOSITORY']==REPOSITORIES[2])
    assert report['provider_credential_binding']=='PRESENCE_SURROGATE_POLICY_REVIEW_NOT_PROVIDER_AUTH'

@pytest.mark.parametrize('hold',['-1','121','2.5','not-a-number'])
def test_unbounded_hold_rejected_without_writing(env,hold):
    b=Backend();env['PROBE_HOLD_SECONDS']=hold
    with pytest.raises(Blocked,match='hold_duration'):
        r.probe(ROOT,env,cfg(),dict(status='PASS_READONLY'),backend_type=lambda *a,**k:b)
    assert b.writes==0

@pytest.mark.parametrize('repo,run',[('other/repo','999'),(REPOSITORIES[0],''),('', '999'),(REPOSITORIES[0],'not-digits')])
def test_forged_or_unbound_holder_rejected(env,repo,run):
    b=Backend();env.update(EXPECTED_OWNER_REPOSITORY=repo,EXPECTED_OWNER_RUN_ID=run)
    with pytest.raises(Blocked,match='expected_owner'):
        r.probe(ROOT,env,cfg(),dict(status='PASS_READONLY'),backend_type=lambda *a,**k:b)
    assert b.writes==0

def test_missing_expected_holder_is_not_a_successful_conflict(env):
    b=Backend();env.update(EXPECTED_OWNER_REPOSITORY=REPOSITORIES[0],EXPECTED_OWNER_RUN_ID='999')
    with pytest.raises(Blocked,match='expected_holder_not_current'):
        r.probe(ROOT,env,cfg(),dict(status='PASS_READONLY'),backend_type=lambda *a,**k:b)
    assert b.writes==0

def test_busy_exact_holder_uses_real_Lease_rejection_without_HTTP(env):
    b=Backend();now=dt.datetime.now(dt.timezone.utc)
    b.state['owner']=dict(repository=REPOSITORIES[0],run_id='999',commit='a'*40,id='b'*32,acquired_at=now.isoformat(),expires_at=(now+dt.timedelta(seconds=300)).isoformat())
    before=copy.deepcopy(b.state);env.update(EXPECTED_OWNER_REPOSITORY=REPOSITORIES[0],EXPECTED_OWNER_RUN_ID='999')
    report=r.probe(ROOT,env,cfg(),dict(status='PASS_READONLY'),backend_type=lambda *a,**k:b)
    assert report['status']=='PASS_CONFLICT_BLOCKED' and report['contender_HTTP']==0
    assert report['contender_acquired'] is False and b.state==before and b.writes==0
