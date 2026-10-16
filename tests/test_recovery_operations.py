"""Physical transport and resumable evidence, independent of production credentials."""
import copy, io, json
import pytest
from test_dashboard import history
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

def test_liquidity_release_is_date_and_payload_specific():
    import pandas as pd
    from tests.test_technical_collection import accepted_fixture
    from screener.liquidity_evidence import permits,price_identity
    from screener.technical_quality import digest
    b=accepted_fixture();bar=b['bars'][-1]
    row={'symbol':b['symbol'],'trade_date':pd.Timestamp(b['asof']),'close':99.0,'adjusted_close':99.0,'volume':bar['volume'],'turnover':bar['turnover'],'exchange':'KRX','source':'preserved source'}
    proof={'bundle':b,'bundle_sha256':digest(b),'original_price_sha256':digest(price_identity(row))}
    assert permits(row,proof,b['asof'])
    assert not permits(row,proof,'2000-01-01')
    assert not permits(dict(row,turnover=row['turnover']+1),proof,b['asof'])
    bad=copy.deepcopy(proof);bad['bundle']['daily_acceptance']['raw_flow_rows'][0]['frgn_ntby_tr_pbmn']='999'
    bad['bundle_sha256']=digest(bad['bundle'])
    assert not permits(row,bad,b['asof'])

def test_mixed_publication_falls_back_to_preserved_generation(tmp_path):
    from screener.publication import selected
    import hashlib
    def make(root,payload):
        root.mkdir(parents=True,exist_ok=True);(root/'dashboard.duckdb').write_bytes(payload)
        sha=hashlib.sha256(payload).hexdigest()
        (root/'manifest.json').write_text(json.dumps({'asof':'2026-10-15','sha256':sha,'daily_validation':{'sha256':sha}}))
        (root/'daily-validation.json').write_text(json.dumps({'sha256':sha}))
        (root/'publication-ledger.json').write_text(json.dumps({'2026-10-15':{'sha256':sha}}))
    make(tmp_path,b'old');old=tmp_path/'publication-history'/'old';make(old,b'old')
    (tmp_path/'dashboard.duckdb').write_bytes(b'new-but-manifest-not-published')
    assert selected(tmp_path/'dashboard.duckdb')==old/'dashboard.duckdb'
    make(tmp_path,b'new');assert selected(tmp_path/'dashboard.duckdb')==tmp_path/'dashboard.duckdb'

def test_date_rotation_and_budget_resume_do_not_starve_old_work():
    from scripts.recover_technical import fair_tasks
    def t(day,code,attempt=''):return (attempt,day+'/'+code,None,{'asof':day,'symbol':code})
    tasks=[t('2026-10-14','000001'),t('2026-10-15','000002'),t('2026-10-15','000003','2026-10-15T01:00:00+00:00')]
    ledger={'jobs':{'2026-10-15/000003':{'last_result':{'error_type':'RecoveryBudget'}}},'last_processed_date':'2026-10-14'}
    order=list(fair_tasks(tasks,ledger,'2026-10-16T01:00:00+00:00'))
    assert [x[1] for x in order]==['2026-10-15/000003','2026-10-14/000001','2026-10-15/000002']

def test_liquidity_mask_release_requires_every_date_and_entire_sector(history):
    import pandas as pd
    from screener.indicators import universe,stock_activity,sector_indicators
    from screener.technical_quality import digest,POLICY
    from screener.liquidity_evidence import price_identity
    from tests.test_technical_collection import accepted_fixture
    # The fixture already has 25 complete turnover days; only masking changes.
    day='2026-10-14';code='000001';b=accepted_fixture()
    # Prove the real gate separately above. Here use a deliberately invalid proof:
    # neither an old date nor a boolean flag can unmask a stock/sector.
    history['metadata']['adjusted_price_conflicts']={day:[{'symbol':code}]}
    history['metadata']['liquidity_price_evidence']={day:{code:{'verified':True,'bundle':b}}}
    x,m=universe(history,day);x=stock_activity(history,x,day);s,_=sector_indicators(history,x,m,day)
    assert pd.isna(x.set_index('symbol').loc[code,'activity_ratio'])
    assert pd.isna(s.set_index('sector').loc['A','activity_ratio'])
