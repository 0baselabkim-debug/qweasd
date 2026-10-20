import json,types
from pathlib import Path
import pytest
import collect_batch as cb
from pilot_client import Budget as RealBudget,BudgetExceeded

class Clock:
 def __init__(self):self.t=0.
 def now(self):return self.t
 def sleep(self,s):self.t+=s

def setup(tmp_path,monkeypatch,count):
 clock=Clock();calls=[];persisted=[]
 (tmp_path/'plan.json').write_text(json.dumps({'asof':'2026-10-19','start':'2026-06-01','targets':[{'symbol':f'{i:06}'} for i in range(count)]}))
 monkeypatch.setattr(cb,'ROOT',tmp_path);monkeypatch.setenv('GITHUB_REF_NAME',cb.BRANCH);monkeypatch.setenv('GITHUB_RUN_ID','test-only');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
 monkeypatch.setattr(cb,'git',lambda *args:cb.BRANCH)
 monkeypatch.setattr(cb,'persist',lambda message:persisted.append(json.loads((tmp_path/'collection/ledger.json').read_text())))
 class Datetime(cb.dt.datetime):
  @classmethod
  def now(cls,tz=None):return cls(2026,10,21,1,0,tzinfo=tz)
 monkeypatch.setattr(cb,'dt',types.SimpleNamespace(datetime=Datetime))
 monkeypatch.setattr(cb,'Budget',lambda **kw:RealBudget(**kw,clock=clock.now,sleep=clock.sleep))
 monkeypatch.setattr(cb.time,'monotonic',clock.now)
 class Client:
  key='test-secret-key';secret='test-secret-secret';token='test-secret-token'
  def __init__(self,b):self.b=b
  def auth(self):
   assert persisted[-1]['batches'][-1]['status']=='reserved'
   self.b.acquire('/oauth2/tokenP',0);calls.append(('auth',))
  def chart(self,code,start,end,adjusted):
   self.b.acquire('/chart',0);calls.append((code,adjusted))
   return {'observed_at':'2026-10-21T00:00:00Z','symbol':code,'adjusted':adjusted,'output2':[]}
 monkeypatch.setattr(cb,'Client',Client)
 return calls,persisted

def test_batch_limit_and_restart_never_repeat_successes(tmp_path,monkeypatch):
 calls,persisted=setup(tmp_path,monkeypatch,151)
 cb.main();state=json.loads((tmp_path/'collection/state.json').read_text())
 assert len(calls)==300 and sum(map(len,state['records'].values()))==299
 old={p.name:p.read_bytes() for p in (tmp_path/'collection/responses').glob('*')}
 monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2');cb.main()
 assert len(calls)==304
 market=[c for c in calls if c[0]!='auth'];assert len(market)==len(set(market))==302
 assert all((tmp_path/'collection/responses'/n).read_bytes()==raw for n,raw in old.items())
 ledger=json.loads((tmp_path/'collection/ledger.json').read_text());assert [b['charged'] for b in ledger['batches']]==[300,4]
 before=len(calls);cb.main();assert len(calls)==before

def test_global_budget_includes_authentication_and_reservations(tmp_path,monkeypatch):
 calls,_=setup(tmp_path,monkeypatch,10)
 cb.write(tmp_path/'collection/ledger.json',{'limit':3800,'batches':[{'status':'complete','charged':3798}]})
 cb.main();assert len(calls)==2
 with pytest.raises(BudgetExceeded):cb.main()
 assert len(calls)==2

def test_unsettled_previous_batch_blocks_all_new_http(tmp_path,monkeypatch):
 calls,_=setup(tmp_path,monkeypatch,10)
 cb.write(tmp_path/'collection/ledger.json',{'limit':3800,'batches':[{'status':'reserved','charged':300}]})
 with pytest.raises(RuntimeError,match='unsettled'):cb.main()
 assert calls==[]
