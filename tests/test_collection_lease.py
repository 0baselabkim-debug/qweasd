"""Elapsed time cannot end full-market ownership; safety checks still fail closed."""
import copy,datetime as dt,threading
import pytest
from kis_shared.lease import Lease,Blocked,initial,validate,REPOSITORIES,UTC

PURPOSE='REGULAR_FULL_MARKET_2030_V1'
class Backend:
 def __init__(self):self.state=initial();self.revision=0;self.lock=threading.Lock();self.down=False
 def read(self):
  if self.down:raise Blocked('unavailable')
  with self.lock:return str(self.revision),copy.deepcopy(self.state)
 def compare_and_append(self,rev,state):
  with self.lock:
   if rev!=str(self.revision):return False
   self.state=copy.deepcopy(state);self.revision+=1;return True

def fixture():
 at=[dt.datetime(2026,11,4,tzinfo=UTC)];b=Backend();a=Lease(b,REPOSITORIES[2],'100','a'*40,clock=lambda:at[0],scope_id='same-budget');a.acquire(seconds=None,purpose=PURPOSE)
 return at,b,a

@pytest.mark.parametrize('minutes',[171,191,361,1441,10080])
def test_elapsed_time_never_ends_collection_or_resets_request_scope(minutes):
 at,b,a=fixture();original=copy.deepcopy(b.state);at[0]+=dt.timedelta(minutes=minutes)
 a.check();a.reserve();validate(b.state)
 assert b.state==original and b.state['owner']['expires_at'] is None
 for repo in REPOSITORIES:
  with pytest.raises(Blocked,match='busy'):Lease(b,repo,'200','b'*40,clock=lambda:at[0]).acquire()
 a.release();assert b.state['owner'] is None and b.state['request_scopes']==original['request_scopes']

@pytest.mark.parametrize('failure',['owner','ledger'])
def test_no_deadline_does_not_disable_ownership_or_ledger_checks(failure):
 at,b,a=fixture();at[0]+=dt.timedelta(days=1)
 if failure=='owner':b.state['owner']['id']='f'*32
 else:b.down=True
 with pytest.raises(Blocked):a.check()
 with pytest.raises(Blocked):a.reserve(auth=True)
 assert b.state['last_auth'] is None

def test_proven_termination_alone_cannot_clear_unresolved_requests():
 at,b,a=fixture();operator=Lease(b,REPOSITORIES[2],'200','b'*40,clock=lambda:at[0]);rev,state=b.read()
 terminal=dict(repository=a.identity['repository'],run_id='100',commit='a'*40,status='completed',all_workers_stopped=True,external_issuers_stopped=True)
 with pytest.raises(Blocked,match='request_safety'):operator.recover(rev,lambda _:terminal)
 assert b.state==state
 complete=dict(terminal,requests_closed=True,budget_preserved=True,quarantine_elapsed=True)
 b.state['auth_not_before']=(at[0]+dt.timedelta(seconds=500)).isoformat();before=copy.deepcopy(b.state)
 operator.recover(rev,lambda _:complete)
 assert b.state['owner'] is None and b.state['request_scopes']==before['request_scopes']
 assert b.state['auth_not_before']==before['auth_not_before'] and b.state['last_auth']==before['last_auth']

@pytest.mark.parametrize('field',['requests_closed','budget_preserved','quarantine_elapsed'])
def test_each_missing_recovery_proof_retains_owner(field):
 at,b,a=fixture();operator=Lease(b,REPOSITORIES[2],'200','b'*40,clock=lambda:at[0]);rev,state=b.read()
 proof=dict(repository=a.identity['repository'],run_id='100',commit='a'*40,status='completed',all_workers_stopped=True,external_issuers_stopped=True,requests_closed=True,budget_preserved=True,quarantine_elapsed=True);proof[field]=False
 with pytest.raises(Blocked):operator.recover(rev,lambda _:proof)
 assert b.state==state

@pytest.mark.parametrize('repo',REPOSITORIES[:2])
def test_unrelated_callers_cannot_enable_open_collection_lease(repo):
 b=Backend();a=Lease(b,repo,'100','a'*40)
 with pytest.raises(Blocked,match='duration_invalid'):a.acquire(seconds=None,purpose=PURPOSE)
 assert b.state['owner'] is None

def test_null_expiry_is_not_accepted_without_explicit_collection_policy():
 at,b,a=fixture();b.state['owner'].pop('lease_policy')
 with pytest.raises(Blocked,match='ledger_invalid'):validate(b.state)
