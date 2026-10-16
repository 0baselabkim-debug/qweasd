"""Physical transport and resumable evidence, independent of production credentials."""
import copy, io, json
import pytest
from scripts.kis_public_client import KIS
from scripts.recover_technical import BoundedAPI, RecoveryBudget
from scripts.technical_acceptance import LazyAPI

class Reply(io.BytesIO):
    status=200;headers={}
    def __init__(self,data):super().__init__(json.dumps(data).encode())

def transport(monkeypatch,responses):
    monkeypatch.setenv('KIS_APP_KEY','test-key');monkeypatch.setenv('KIS_APP_SECRET','test-secret')
    monkeypatch.setattr('scripts.kis_public_client.time.sleep',lambda _:None)
    calls=[]
    def send(request,**kw):
        calls.append(request.full_url.split('?')[0])
        value=responses.pop(0)
        if isinstance(value,Exception):raise value
        return Reply(value)
    monkeypatch.setattr('scripts.kis_public_client.urllib.request.urlopen',send)
    return calls

PATH='/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice'

def test_physical_cap_includes_lazy_auth_and_every_retry(monkeypatch):
    calls=transport(monkeypatch,[{'access_token':'test-token'},OSError('offline'),{'rt_cd':'0','output2':[]}])
    b=BoundedAPI(LazyAPI(KIS),max_calls=3)
    b.request(PATH,{},'test')
    assert len(calls)==b.used==3 and b.reserved==3
    assert [x['kind'] for x in b.physical]==['authentication','market','market']
    assert [x['attempt'] for x in b.physical]==[1,1,2]
    with pytest.raises(RecoveryBudget):b.request(PATH,{},'test')
    assert len(calls)==3

def test_unused_retry_slots_do_not_exhaust_budget(monkeypatch):
    calls=transport(monkeypatch,[{'access_token':'test-token'}]+[{'rt_cd':'0','output2':[]}] * 20)
    b=BoundedAPI(LazyAPI(KIS),max_calls=21)
    for _ in range(20):b.request(PATH,{},'test')
    assert len(calls)==b.used==21
    with pytest.raises(RecoveryBudget):b.request(PATH,{},'test')

def test_auth_retry_failure_and_renewal_are_capped(monkeypatch):
    calls=transport(monkeypatch,[OSError('offline')]*3)
    b=BoundedAPI(LazyAPI(KIS),max_calls=2)
    with pytest.raises(RecoveryBudget):b.request(PATH,{},'test')
    assert len(calls)==2 and all(x['kind']=='authentication' for x in b.physical)
    calls=transport(monkeypatch,[{'access_token':'test-token'},{'rt_cd':'0','output2':[]},{'access_token':'test-token2'}])
    b=BoundedAPI(LazyAPI(KIS),max_calls=3)
    b.request(PATH,{},'test');b.request('/oauth2/tokenP',body={'grant_type':'client_credentials'})
    assert b.used==3 and sum(x['kind']=='authentication' for x in b.physical)==2

def test_deadline_prevents_transport_after_rate_wait(monkeypatch):
    calls=transport(monkeypatch,[{'access_token':'test-token'}])
    b=BoundedAPI(LazyAPI(KIS),seconds=1)
    monkeypatch.setattr('scripts.kis_public_client.time.sleep',lambda _:setattr(b,'start',b.start-2))
    with pytest.raises(RecoveryBudget):b.request(PATH,{},'test')
    assert calls==[]

def test_resume_requires_two_matching_fresh_anchors_and_saves_each_response(monkeypatch):
    old=[{'path':PATH,'params':{'part':i},'output':{'output2':[{'value':i}]},'observed_at':'old'} for i in range(3)]
    for changed in [False,True]:
        replies=[{'access_token':'test-token'}]+[{'rt_cd':'0','output2':[{'value':i+(10 if changed and i==0 else 0)}]} for i in range(3)]
        calls=transport(monkeypatch,replies);saved=[]
        b=BoundedAPI(LazyAPI(KIS));b.begin(copy.deepcopy(old),saved.append)
        for i in range(3):b.request(PATH,{'part':i},'test')
        assert b.cache_hits==(0 if changed else 1)
        assert len(saved)==(3 if changed else 2)
        assert len(calls)==(4 if changed else 3)
