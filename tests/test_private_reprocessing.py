"""Offline integration only; no claim of hosted Supabase/RLS verification."""
import copy
import pytest
from test_technical_collection import accepted_fixture
from scripts import private_technical as worker, technical_batch as batch
from screener import technical_cache
from screener.technical_quality import daily_accepted, digest

class NoAPI:
    def request(self,*a,**kw):raise AssertionError('unnecessary provider request')

def test_private_long_payload_replays_evidence_without_copying_acceptance(tmp_path,monkeypatch):
    public=accepted_fixture();public['price_comparison']['kis_adjusted']=100
    monkeypatch.setattr(worker,'CACHE',tmp_path/'market')
    batch.write(worker.CACHE/'999999.json.gz',public)
    private=copy.deepcopy(public);private.pop('daily_acceptance')
    older=copy.deepcopy(private['bars'][0]);older['trade_date']='2019-12-31'
    private['bars'].insert(0,older);private['calendar'].insert(0,older['trade_date'])
    checked=worker.review(NoAPI(),private,{'999999':public['daily_acceptance']['krx_row']})
    assert daily_accepted(checked)
    assert checked['daily_acceptance']['bars_sha256']==digest(private['bars'])
    assert checked['daily_acceptance']['bars_sha256']!=public['daily_acceptance']['bars_sha256']
    assert not checked.get('corporate_actions_verified')
    checked['bars'][0]['close']=99
    assert not daily_accepted(checked)

@pytest.mark.parametrize('entry_index,requested_count',[(170,320),(0,5500)])
def test_worker_same_day_reprocesses_positions_without_collecting(monkeypatch,tmp_path,entry_index,requested_count):
    b=accepted_fixture();b.update(structure_valid=True,corporate_actions_verified=False,history_request_count=requested_count)
    b['price_comparison']['kis_adjusted']=100
    monkeypatch.setattr(worker,'CACHE',tmp_path/'market')
    monkeypatch.setattr(worker,'ROOT',tmp_path)
    monkeypatch.setattr(worker,'krx_reference',lambda day:({'999999':b['daily_acceptance']['krx_row']},{}))
    class Store:
        signals=[]
        def positions(self,code):return [dict(symbol=code,buy_date=b['bars'][entry_index]['trade_date'],average_price=100,quantity=1,risk={},purchase_signature='changed')]
        def cache(self,code):return b
        def publish(self,code,bundle):self.published=bundle
        def save_signal(self,p,r):self.signals.append((p,r))
        def fail(self,*a):raise AssertionError('worker failed')
    store=Store()
    def forbidden(*a,**kw):raise AssertionError('historical recollection')
    worker.run(store,NoAPI(),{},b['asof'],['999999'],forbidden)
    assert daily_accepted(store.published) and not store.published['worker_verified']
    assert store.signals[0][0]['purchase_signature']=='changed'
    assert store.signals[0][1]['reasons']==['매수 이후 기업행사 검증 미완료']

def test_archive_exact_date_preserves_accepted_and_pending(tmp_path,monkeypatch):
    monkeypatch.setattr(batch,'ROOT',tmp_path/'technical')
    monkeypatch.setattr(batch,'CACHE',batch.ROOT/'market')
    monkeypatch.setattr(technical_cache,'ROOT',tmp_path)
    old=accepted_fixture();new=copy.deepcopy(old);new['asof']='2020-12-31'
    batch.write(batch.CACHE/'999999.json.gz',old)
    batch.write(batch.CACHE/'999999.json.gz',new)
    assert technical_cache.get_bundle('999999',old['asof'])[0]==old
    assert technical_cache.get_bundle('999999','2020-12-30')[0] is None
    assert technical_cache.get_bundle('999999')[0]==new

def test_missing_reference_remains_pending_without_api(monkeypatch,tmp_path):
    monkeypatch.setattr(worker,'CACHE',tmp_path/'market')
    b=accepted_fixture();b.pop('daily_acceptance')
    result=worker.review(NoAPI(),b,{})
    assert not daily_accepted(result)
    assert result['acceptance_error']=='REFERENCE_NOT_AVAILABLE_FOR_SYMBOL'
