import datetime as dt,json,urllib.request
import pytest
from official_probe import Probe,window

def test_window():
 for h in [9,12,13]:
  with pytest.raises(RuntimeError):window(dt.datetime(2026,10,21,h,tzinfo=dt.timezone.utc))
 for h in [5,14]:window(dt.datetime(2026,10,21,h,tzinfo=dt.timezone.utc))
def test_no_replay_or_budget_reset(tmp_path):
 p=Probe(tmp_path,lambda:None);p.ledger['requests']=[{'key':str(i),'status':'rejected'} for i in range(8)];p.checkpoint()
 q=Probe(tmp_path,lambda:None)
 assert q.request('0','unused') is None
 with pytest.raises(RuntimeError,match='physical_limit'):q.request('new','unused')
def test_uncertain_attempt_blocks(tmp_path):
 p=Probe(tmp_path,lambda:None);p.ledger['requests']=[{'status':'reserved'}];p.checkpoint()
 with pytest.raises(RuntimeError,match='uncertain'):Probe(tmp_path,lambda:None)
def test_out_of_scope_not_sent(tmp_path):
 p=Probe(tmp_path,lambda:None)
 with pytest.raises(AssertionError):p.request('bad','https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd',{'basDd':'20261019'})
 assert not p.ledger['requests']
