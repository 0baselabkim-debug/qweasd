import pandas as pd
from scripts.publish_recovery import prioritized_gaps

def test_priority_favors_constituent_coverage_not_smallest_sector(monkeypatch):
    def sector(name,n,codes,day='2026-10-14'):
        return {'members':n,'sector':name,'remaining_checks':[{'symbol':c,'date':day,'reason':'unresolved_price_conflict'} for c in codes]}
    sectors=[sector('small',2,['000001','000002']),sector('manufacturing',8,['000003','000004','000005']),sector('old',1,['000006'],'2026-10-13')]
    monkeypatch.setattr('scripts.diagnose_activity.diagnose',lambda *a:{'sectors':sectors})
    data={'flows':pd.DataFrame({'trade_date':[pd.Timestamp('2026-10-15')]})}
    result=prioritized_gaps(data)
    assert result[0]==('2026-10-13','000006')
    assert [v[1] for v in result[1:4]]==['000003','000004','000005']

def test_missing_turnover_is_not_scheduled_as_close_repair(monkeypatch):
    monkeypatch.setattr('scripts.diagnose_activity.diagnose',lambda *a:{'sectors':[{'members':5,'remaining_checks':[{'symbol':'000001','date':'2026-10-14','reason':'missing_price_row'}]}]})
    assert prioritized_gaps({'flows':pd.DataFrame({'trade_date':[pd.Timestamp('2026-10-15')]})})==[]

def test_matching_prices_complete_saved_evidence_without_http(tmp_path,monkeypatch):
    from tests.test_technical_collection import accepted_fixture
    from scripts import technical_batch as batch,recover_technical as recovery
    b=accepted_fixture();b['price_conflicts']=[];b['price_comparison']['kis_adjusted']=100;refs={b['symbol']:b['daily_acceptance']['krx_row']}
    b['daily_acceptance']['bars_sha256']='stale-evidence-hash'
    monkeypatch.setattr(batch,'ROOT',tmp_path);monkeypatch.setattr(batch,'CACHE',tmp_path/'market')
    monkeypatch.setattr(batch,'krx_reference',lambda *a,**k:(refs,{}))
    monkeypatch.setattr(batch,'refresh_summary',lambda *a:None)
    batch.write(batch.CACHE/(b['symbol']+'.json.gz'),b)
    class API:
        metrics=[]
        def request(self,*a,**k):raise AssertionError('saved evidence must not request HTTP')
    report=recovery.run(API(),{'flows':pd.DataFrame({'trade_date':[pd.Timestamp(b['asof'])]})},limit=1)
    assert report['promoted']==1
    assert report['results'][0]['verification_path']=='existing_prices_full_verification'
    assert report['physical_request_attempts']==0
    assert report['efficiency']['promotions_per_10_physical_requests'] is None
    assert batch.read(batch.CACHE/(b['symbol']+'.json.gz'))['finality']=='UNVERIFIED'
    again=recovery.run(API(),{'flows':pd.DataFrame({'trade_date':[pd.Timestamp(b['asof'])]})},limit=1)
    assert again['results']==[]
