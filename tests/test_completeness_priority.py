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

def test_publication_gain_never_borrows_previous_run(tmp_path,monkeypatch):
    import json
    from scripts.recovery_outcome import emit
    monkeypatch.chdir(tmp_path);monkeypatch.setenv('GITHUB_RUN_ID','new')
    root=tmp_path/'technical';root.mkdir()
    (root/'recovery-report.json').write_text(json.dumps({'run_id':'new','results':[],'promoted':0}))
    path=root/'recovery-publication-report.json'
    path.write_text(json.dumps({'run_id':'old','stock_activity_gain':9,'sector_activity_gain':2}))
    assert 'indicator_effect' not in emit(published=True)
    path.write_text(json.dumps({'run_id':'new','stock_activity_gain':1,'sector_activity_gain':0}))
    result=emit(publication_error=True)
    assert result['indicator_effect']['publication_confirmed'] is False
    assert result['status']=='PUBLICATION_FAILED'

def test_summary_reuses_validation_within_one_immutable_pass(tmp_path,monkeypatch):
    from scripts import technical_batch as batch
    from tests.test_technical_collection import accepted_fixture
    b=accepted_fixture();root=tmp_path/'technical'
    monkeypatch.setattr(batch,'ROOT',root);monkeypatch.setattr(batch,'CACHE',root/'market');monkeypatch.setattr(batch,'STATE',root/'collection-state.json')
    batch.write(batch.CACHE/(b['symbol']+'.json.gz'),b)
    batch.write(batch.STATE,{'symbols':{b['symbol']:{}},'runs':[]})
    original=batch.validate;calls=[]
    def counted(bundle,day):calls.append(bundle['symbol']);return original(bundle,day)
    monkeypatch.setattr(batch,'validate',counted)
    batch.refresh_summary(b['asof'])
    assert calls==[b['symbol']]
    report=batch.read(root/'summary.json')
    assert report['daily_accepted']==report['signal_ready']==1
    assert report['guide_status_counts']=={'관망':1}
