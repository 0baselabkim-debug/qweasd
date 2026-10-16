"""Offline fixtures only; real KIS observations are verified separately."""
import copy
import pandas as pd
from scripts import technical_batch as c
from screener import technical_cache

def test_queue_priorities_checkpoint_and_failure_fairness():
    universe=['005930','003280','000001','003160']
    assert c.select_targets(universe,{},'2026-10-14',2)==['003280','003160']
    state={'003280':{'attempted_at':'2026-10-15','status':'api_or_schema_error'},'003160':{'completed_asof':'2026-10-14'}}
    assert c.select_targets(universe,state,'2026-10-14',4)==['005930','000001','003280']
    assert '003160' in c.select_targets(universe,state,'2026-10-15',4)

def raw(day,close=10):
    return {'stck_bsop_date':day,'stck_oprc':str(close),'stck_hgpr':str(close+1),'stck_lwpr':str(close-1),'stck_clpr':str(close),'acml_vol':'20','acml_tr_pbmn':'200','prtt_rate':'0','revl_issu_reas':'00'}

class API:
    def __init__(self,rows):self.rows=rows
    def request(self,*a,**kw):return {'output2':self.rows}

def test_incremental_and_adjustment_rebuild():
    oldrows=c.parsed([raw('20261013')],'2026-10-13')
    old={'asof':'2026-10-13','bars':[v['bar'] for v in oldrows.values()],'corporate_action_observations':[v['event'] for v in oldrows.values()]}
    def forbidden(*a,**kw):raise AssertionError('unchanged history must not be fetched')
    bars,events,mode=c.price_rows(API([raw('20261013'),raw('20261014')]),'000001','2026-10-14',old,forbidden)
    assert mode=='incremental_overlap_checked' and len(bars)==2
    calls=[]
    def rebuild(*a,**kw):calls.append(1);return [('2026-10-13',raw('20261013',5)),('2026-10-14',raw('20261014',5))]
    bars,_,mode=c.price_rows(API([raw('20261013',5),raw('20261014',5)]),'000001','2026-10-14',old,rebuild)
    assert mode=='full_window' and calls and bars[0]['close']==5
    assert old['bars'][0]['close']==10
    unchanged=c.price_rows(API([]),'000001','2026-10-13',old,forbidden)
    assert unchanged[2]=='same_date_cache_reused' and unchanged[0]==old['bars']
    delayed=c.price_rows(API([raw('20261013')]),'000001','2026-10-14',old,forbidden)
    assert delayed[2]=='incremental_no_new_date' and delayed[0]==old['bars']

def test_nonfinite_duplicate_and_action_unknown():
    import pytest
    with pytest.raises(ValueError):c.parsed([raw('20261014'),raw('20261014')],'2026-10-14')
    with pytest.raises(ValueError):c.parsed([raw('20261014',float('nan'))],'2026-10-14')
    assert not c.action_present({'rate':'0','reason':'00'})
    assert c.action_present({'rate':'0','reason':'01'})

def test_atomic_cache_and_public_lookup(tmp_path,monkeypatch):
    monkeypatch.setattr(technical_cache,'ROOT',tmp_path)
    b={'symbol':'000001','asof':'2026-10-14','bars':[]}
    c.write(tmp_path/'technical/market/000001.json.gz',b)
    assert technical_cache.get_bundle('000001','2026-10-14')[0]==b
    assert technical_cache.get_bundle('000001','2026-10-13')[0] is None
    assert technical_cache.get_bundle('../000001')[0] is None
    assert not list(tmp_path.rglob('*.tmp'))

def test_flow_reuse_does_not_fill_missing_days_or_verify_finality():
    from scripts.collect_technical import paged
    data={'flows':pd.DataFrame([{'symbol':'000001','trade_date':pd.Timestamp('2026-10-14'),'net_amount':0,'finality':'UNVERIFIED'}]),'prices':pd.DataFrame([{'symbol':'000001','trade_date':pd.Timestamp('2026-10-14'),'close':11}])}
    def pages(*a,**kw):return [('2026-10-14',raw('20261014'))]
    b=c.make_bundle(API([raw('20261014')]),'000001','2026-10-14',['2026-10-14'],data,None,pages)
    assert b['flows']==[{'trade_date':'2026-10-14','net_amount':0}]
    assert b['finality']=='UNVERIFIED' and not b['corporate_actions_verified']
    assert b['price_conflicts'] and not b['indicator_ready']
    assert c.classify(b,'2026-10-14')=='new_listing_short_history'

def test_official_crosscheck_never_implies_finality():
    b={'symbol':'000001','asof':'2026-10-14','bars':[],'price_comparison':{'preserved_unadjusted':11,'kis_unadjusted':10,'kis_adjusted':10},'price_conflicts':['Preserved raw close differs from current KRX raw close; cause unresolved'],'finality':'UNVERIFIED','finality_evidence':None}
    ref={'000001':{'BAS_DD':'20261014','TDD_CLSPRC':'10'}}
    scope={'UN_1':[{'stck_clpr':'11'}],'J_1':[{'stck_clpr':'10'}],'flow_mismatches':[],'flow_checked_dates':20}
    checked=c.crosscheck(copy.deepcopy(b),ref,scope)
    assert checked['price_finality']=='CROSS_VALIDATED_DAILY' and checked['finality']=='UNVERIFIED'
    assert not checked['price_conflicts'] and checked['legacy_price_discrepancy']
    scope['flow_mismatches']=['2026-10-14']
    assert c.crosscheck(copy.deepcopy(b),ref,scope)['price_conflicts']
    assert c.crosscheck(copy.deepcopy(b),{'000001':{'BAS_DD':'20261013','TDD_CLSPRC':'10'}},scope)==b

def test_only_documented_public_corporate_endpoints_are_allowed():
    from scripts.kis_public_client import KIS,CORPORATE_PATHS
    import pytest
    api=object.__new__(KIS)
    assert len(CORPORATE_PATHS)==4
    for path in ['/uapi/domestic-stock/v1/trading/period-rights','/uapi/domestic-stock/v1/trading/order-cash','/uapi/domestic-stock/v1/ksdinfo/unknown']:
        with pytest.raises(ValueError):api.request(path)
    with pytest.raises(ValueError):api.request(next(iter(CORPORATE_PATHS)),body={'unsupported':'post'})

def test_new_history_and_stale_refresh_both_progress():
    state={'000001':{'completed_asof':'2026-10-13','attempted_at':'2026-10-13'}}
    assert set(c.select_targets(['000001','000002'],state,'2026-10-14',2))=={'000001','000002'}

def test_corporate_dates_are_not_assumed_effective_dates(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'ROOT',tmp_path)
    c.write(tmp_path/'corporate-actions.json.gz',{'sources':[{'endpoint':'official-test-fixture','rows':[{'sht_cd':'000001','record_date':'20261014'},{'sht_cd':'000001','record_date':'20261015'},{'sht_cd':'000002','record_date':'20261014'},{'sht_cd':'000001','record_date':'bad'}]}]})
    rows=c.corporate_observations('000001','2026-10-14')
    assert len(rows)==1 and rows[0]['record_date']=='2026-10-14'
    assert rows[0]['status']=='REVIEW_REQUIRED' and 'not_assumed' in rows[0]['date_kind']

def test_corporate_failure_and_shifted_page_keep_known_records():
    from scripts.technical_evidence import merge_corporate_source
    old={'rows':[{'sht_cd':'000001','record_date':'20260101'}],'through':'2026-10-14'}
    failed=merge_corporate_source(old,{'status':'failed','through':'2026-10-15'})
    assert failed['rows']==old['rows'] and failed['last_success_through']=='2026-10-14'
    newer=merge_corporate_source(old,{'status':'received','through':'2026-10-15','rows':[{'sht_cd':'000002','record_date':'20261015'}]})
    assert len(newer['rows'])==2 and newer['last_success_through']=='2026-10-15'


def accepted_fixture():
    """Synthetic unit-test observations only; never written into public caches."""
    from screener.technical_quality import POLICY,FIELDS,digest,LEGACY_CONFLICT
    dates=pd.bdate_range('2020-01-01',periods=200).strftime('%Y-%m-%d').tolist()
    bars=[dict(trade_date=d,open=100.,high=110.,low=90.,close=100.,volume=20.,turnover=2000.) for d in dates]
    flows=[dict(trade_date=d,net_amount=0) for d in dates[-20:]]
    day=dates[-1];url='https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd='+day.replace('-','')
    b=dict(symbol='999999',market='KOSPI',asof=day,exchange='KRX',adjustment='adjusted',
           bars=bars,calendar=dates,flows=flows,finality='UNVERIFIED',finality_evidence=None,
           price_comparison={'kis_unadjusted':100},price_conflicts=[LEGACY_CONFLICT])
    row={v:str(bars[-1][k]) for k,v in FIELDS.items()}
    row.update(ISU_CD='999999',BAS_DD=day.replace('-',''),_retrieved_at='2020-12-31T00:00:00+00:00',_source_url=url)
    b['daily_acceptance']=dict(policy=POLICY,symbol='999999',asof=day,observed_at='2020-12-31T00:00:00+00:00',
        krx_row=row,krx_url=url,bars_sha256=digest(bars),flows_sha256=digest(flows),checked_flows=copy.deepcopy(flows),
        flow_endpoint='KIS FHPTJ04160001 / J / million KRW to KRW')
    b['daily_acceptance'].update(flow_check_kind='live_requery',raw_flow_rows=[{'stck_bsop_date':r['trade_date'].replace('-',''),'frgn_ntby_tr_pbmn':'0'} for r in flows])
    return b


def test_daily_acceptance_is_distinct_from_finality_and_preserves_legacy_conflict():
    from screener.technical import validate,buy_guide
    from screener.technical_quality import daily_accepted,LEGACY_CONFLICT
    b=accepted_fixture()
    assert daily_accepted(b) and not validate(b)
    assert b['finality']=='UNVERIFIED' and b['price_conflicts']==[LEGACY_CONFLICT]
    assert buy_guide(b)['status']=='관망'
    b['price_conflicts'].append('KIS raw / independent KRX daily close mismatch')
    assert validate(b) and buy_guide(b)['status']=='판정 보류'


def test_daily_acceptance_rejects_missing_or_modified_evidence():
    from screener.technical_quality import daily_accepted,digest
    changes=[lambda b:b['bars'][-2].update(close=101),
        lambda b:b['flows'][-1].update(net_amount=1),
        lambda b:b['daily_acceptance']['krx_row'].update(ACC_TRDVOL='21'),
        lambda b:b['daily_acceptance']['krx_row'].update(ISU_CD='000001'),
        lambda b:b['daily_acceptance']['krx_row'].update(_source_url='https://example.com'),
        lambda b:b['daily_acceptance']['krx_row'].update(_retrieved_at=b['asof']+'T10:00:00+00:00'),
        lambda b:b['daily_acceptance'].update(observed_at=b['asof']+'T10:00:00+00:00'),
        lambda b:b['daily_acceptance'].update(krx_url='https://example.com'),
        lambda b:b['daily_acceptance'].update(flow_endpoint='UN'),
        lambda b:b['daily_acceptance'].update(checked_flows=[]),
        lambda b:b['daily_acceptance'].update(raw_flow_rows=[]),
        lambda b:b['daily_acceptance'].update(flow_check_kind='unknown'),
        lambda b:b.update(asof='2021-01-01'),lambda b:b.update(exchange='UN')]
    for change in changes:
        b=accepted_fixture();change(b);assert not daily_accepted(b)
    b=accepted_fixture();b['flows'][-1]['net_amount']=None;b['daily_acceptance']['flows_sha256']=digest(b['flows'])
    assert not daily_accepted(b)
    assert daily_accepted(accepted_fixture()) # Actual zero is a valid observation.


def test_requery_mismatch_does_not_replace_original_flow():
    from scripts.technical_acceptance import verify_bundle
    from screener.technical_quality import daily_accepted
    b=accepted_fixture();b['price_comparison']['kis_adjusted']=100
    row=b['daily_acceptance']['krx_row'];original=copy.deepcopy(b['flows'])
    response=[{'stck_bsop_date':r['trade_date'].replace('-',''),'frgn_ntby_tr_pbmn':'0'} for r in original]
    api=API([]);api.request=lambda *a,**kw:{'output2':response}
    good=verify_bundle(api,b,{'999999':row});assert daily_accepted(good)
    response[-1]['frgn_ntby_tr_pbmn']='1'
    bad=verify_bundle(api,b,{'999999':row})
    assert not daily_accepted(bad) and bad['flows']==original
    response.append(response[-1])
    import pytest
    with pytest.raises(ValueError):verify_bundle(api,b,{'999999':row})


def test_failed_crosscheck_preserves_prior_cache_and_does_not_complete(tmp_path,monkeypatch):
    from scripts import technical_acceptance
    from screener.technical import validate
    b=accepted_fixture();b.update(indicator_ready=True,structure_valid=True,structure_errors=[],price_finality='CROSS_VALIDATED_DAILY')
    b['validation']=validate(b)
    monkeypatch.setattr(c,'ROOT',tmp_path);monkeypatch.setattr(c,'CACHE',tmp_path/'market');monkeypatch.setattr(c,'STATE',tmp_path/'state.json')
    path=c.CACHE/'999999.json.gz';c.write(path,b);before=path.read_bytes()
    candidate=copy.deepcopy(b);candidate.update(price_finality='CONFLICT',price_conflicts=['Current KIS / KRX conflict'])
    candidate['validation']=validate(candidate)
    monkeypatch.setattr(c,'make_bundle',lambda *a,**kw:copy.deepcopy(candidate))
    monkeypatch.setattr(c,'crosscheck',lambda b,*a,**kw:b)
    monkeypatch.setattr(technical_acceptance,'verify_bundle',lambda api,b,refs:b)
    monkeypatch.setattr(c,'krx_reference',lambda day:({},{}))
    data={'flows':pd.DataFrame([{'trade_date':pd.Timestamp(b['asof'])}]),
          'instruments':pd.DataFrame([{'symbol':'999999','listing_date':pd.Timestamp('2010-01-01')}]),
          'exclusions':pd.DataFrame(columns=['symbol','trade_date'])}
    api=API([]);api.metrics=[]
    summary=c.run(api,data,lambda *a,**kw:[],limit=1)
    assert path.read_bytes()==before and (tmp_path/'rejected/999999.json.gz').exists()
    assert summary['batch_collected']==0 and summary['completed_current_asof']==0 and summary['remaining']==1
    assert c.read(c.STATE)['symbols']['999999']['status']=='source_conflict'


def test_evidence_queue_failure_rotates_without_refetching_history(tmp_path,monkeypatch):
    from scripts import technical_acceptance
    monkeypatch.setattr(c,'CACHE',tmp_path)
    refs={}
    for code in ['000001','000002']:
        b=accepted_fixture();b['symbol']=code
        refs[code]={**b['daily_acceptance']['krx_row'],'ISU_CD':code}
        b.pop('daily_acceptance');c.write(tmp_path/(code+'.json.gz'),b)
    calls=[]
    def failing(api,b,refs):calls.append(b['symbol']);raise TimeoutError()
    monkeypatch.setattr(technical_acceptance,'verify_bundle',failing)
    for _ in range(2):technical_acceptance.review_pending(None,refs,b['asof'],limit=1)
    assert calls==['000001','000002']
    assert all(c.read(p)['acceptance_error']=='TimeoutError' for p in tmp_path.glob('*.gz'))


def test_completed_public_date_needs_no_authentication(tmp_path,monkeypatch):
    from scripts import collect_technical
    import sys
    b=accepted_fixture();monkeypatch.setattr(c,'CACHE',tmp_path);monkeypatch.setattr(c,'STATE',tmp_path/'state.json')
    c.write(tmp_path/'999999.json.gz',b);c.write(c.STATE,{'symbols':{'999999':{'completed_asof':b['asof']}}})
    data={'flows':pd.DataFrame([{'trade_date':pd.Timestamp(b['asof'])}]),'instruments':pd.DataFrame([{'symbol':'999999'}])}
    monkeypatch.setattr(collect_technical,'load',lambda:data)
    def forbidden():raise AssertionError('No authentication or API calls for a completed date')
    monkeypatch.setattr(collect_technical,'KIS',forbidden);monkeypatch.setattr(c,'refresh_summary',lambda *a:None)
    monkeypatch.setattr(sys,'argv',['collect_technical.py','--mode','public'])
    collect_technical.main()


def test_saved_response_acceptance_keeps_replay_distinct_from_new_calls():
    from scripts.technical_acceptance import accept_saved_responses
    from screener.technical_quality import daily_accepted
    b=accepted_fixture();b['price_comparison']['kis_adjusted']=100
    e=b['daily_acceptance'];sources=[{'kind':'unit_test_fixture_only'}]
    result=accept_saved_responses(b,{'999999':e['krx_row']},e['raw_flow_rows'],sources)
    assert daily_accepted(result) and result['finality']=='UNVERIFIED'
    assert result['daily_acceptance']['flow_check_kind']=='saved_api_response_replay'
    assert result['bars']==b['bars'] and result['flows']==b['flows']
    result['daily_acceptance']['flow_sources']=[];assert not daily_accepted(result)


def test_reference_response_categories_and_alpha_codes():
    from scripts.technical_reference import classify_rows
    b=accepted_fixture();row=b['daily_acceptance']['krx_row'];day=b['asof']
    assert classify_rows({'OutBlock_1':[]},day)[0]=='REFERENCE_NOT_AVAILABLE'
    assert classify_rows({},day)[0]=='REFERENCE_SCHEMA_ERROR'
    assert classify_rows({'OutBlock_1':[dict(row,BAS_DD='20200101')]},day)[0]=='REFERENCE_DATE_MISMATCH'
    assert classify_rows({'OutBlock_1':[row,row]},day)[0]=='REFERENCE_SCHEMA_ERROR'
    assert classify_rows({'OutBlock_1':[dict(row,ISU_CD='00088K')]},day)[0]=='RECEIVED'
    assert classify_rows({'OutBlock_1':[dict(row,ACC_TRDVOL='nan')]},day)[0]=='REFERENCE_SCHEMA_ERROR'


def test_empty_reference_keeps_previous_cache_and_records_http(tmp_path,monkeypatch):
    import json,urllib.request
    from scripts.technical_reference import fetch_references
    old={'asof':'2020-01-01','observed_at':'2020-01-02T00:00:00+00:00','rows':[{'unchanged':True}]}
    path=tmp_path/'reference/KOSPI.json.gz';c.write(path,old);before=path.read_bytes()
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'OutBlock_1':[]}).encode()
    monkeypatch.setenv('KRX_API_KEY','unit-test-only-not-a-real-key')
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**k:Response())
    refs,status=fetch_references('2020-01-03',tmp_path)
    assert not refs and set(status.values())=={'REFERENCE_NOT_AVAILABLE'}
    assert path.read_bytes()==before
    record=c.read(tmp_path/'reference-status.json')['dates']['2020-01-03']['KOSPI']
    assert record['http']==200 and record['received_rows']==0
    assert 'unit-test-only' not in (tmp_path/'reference-status.json').read_text()


def test_reference_cache_is_date_scoped_and_needs_no_credentials(tmp_path,monkeypatch):
    from scripts.technical_reference import fetch_references
    import urllib.request
    b=accepted_fixture();e=b['daily_acceptance'];day=b['asof']
    c.write(tmp_path/'reference'/day/'KOSPI.json.gz',{'asof':day,'url':e['krx_url'],
            'observed_at':e['krx_row']['_retrieved_at'],'rows':[e['krx_row']]})
    monkeypatch.delenv('KRX_API_KEY',raising=False)
    def forbidden(*a,**k):raise AssertionError('No network for usable cache / missing key')
    monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    refs,status=fetch_references(day,tmp_path)
    assert refs[b['symbol']]['BAS_DD']==day.replace('-','')
    assert status=={'KOSPI':'CACHED_SAME_DATE','KOSDAQ':'CREDENTIAL_NOT_BOUND'}
    assert fetch_references('2021-01-01',tmp_path)[0]=={}


def test_missing_or_wrong_reference_does_not_issue_kis_token_or_erase_proof():
    from scripts.technical_acceptance import LazyAPI,verify_bundle
    b=accepted_fixture();before=copy.deepcopy(b)
    def forbidden():raise AssertionError('No KIS authentication')
    api=LazyAPI(forbidden)
    result=verify_bundle(api,b,{})
    assert api.metrics==[] and result['daily_acceptance']==before['daily_acceptance']
    assert result['bars']==before['bars'] and result['flows']==before['flows']
    row=dict(b['daily_acceptance']['krx_row'],BAS_DD='19990101')
    assert verify_bundle(api,b,{b['symbol']:row})['acceptance_error']=='REFERENCE_NOT_AVAILABLE_FOR_SYMBOL'


def test_reference_conflict_blocks_before_flow_call():
    from scripts.technical_acceptance import LazyAPI,verify_bundle
    b=accepted_fixture();b['price_comparison']['kis_adjusted']=100
    row=dict(b['daily_acceptance']['krx_row'],TDD_CLSPRC='101')
    def forbidden():raise AssertionError('No requery can repair an OHLCV mismatch')
    result=verify_bundle(LazyAPI(forbidden),b,{b['symbol']:row})
    assert result['acceptance_error']=='REFERENCE_PRICE_CONFLICT'
    assert not result.get('daily_acceptance') and result['flows']==b['flows']


def test_short_history_waits_for_a_new_date_but_api_failure_retries():
    day='2026-10-15'
    state={'000001':{'status':'new_listing_short_history','attempted_asof':day},
           '000002':{'status':'api_or_schema_error','attempted_asof':day}}
    assert c.select_targets(list(state),state,day,10)==['000002']
    assert set(c.select_targets(list(state),state,'2026-10-16',10))==set(state)


def test_saved_raw_flow_reuse_requires_matching_original_payload():
    from scripts.technical_acceptance import LazyAPI,verify_saved_or_live
    from screener.technical_quality import daily_accepted
    b=accepted_fixture();b['price_comparison']['kis_adjusted']=100
    row=b['daily_acceptance']['krx_row']
    def forbidden():raise AssertionError('Already received exact raw flows must not be requested again')
    api=LazyAPI(forbidden);result=verify_saved_or_live(api,b,{b['symbol']:row})
    assert daily_accepted(result) and not api.metrics
    assert result['daily_acceptance']['flow_check_kind']=='saved_api_response_replay'
    assert result['bars']==b['bars'] and result['flows']==b['flows']
    b['daily_acceptance']['raw_flow_rows'][-1]['frgn_ntby_tr_pbmn']='1'
    import pytest
    with pytest.raises(AssertionError):verify_saved_or_live(api,b,{b['symbol']:row})


def test_pending_date_survives_rollover_and_empty_reference_run(tmp_path,monkeypatch):
    from scripts import technical_acceptance as a
    b=accepted_fixture();b.pop('daily_acceptance');before=copy.deepcopy(b)
    monkeypatch.setattr(c,'ROOT',tmp_path);monkeypatch.setattr(c,'CACHE',tmp_path/'market')
    monkeypatch.setattr(c,'STATE',tmp_path/'state.json')
    a.archive_pending(b);path=tmp_path/'pending'/b['asof']/(b['symbol']+'.json.gz')
    assert c.read(path)==before
    b['acceptance_error']='new failure';a.archive_pending(b);assert c.read(path)==before
    days=[]
    def refs(day):days.append(day);return {},{'KOSPI':'REFERENCE_NOT_AVAILABLE','KOSDAQ':'REFERENCE_NOT_AVAILABLE'}
    monkeypatch.setattr(c,'krx_reference',refs)
    def forbidden():raise AssertionError('No KIS auth for an absent reference')
    data={'flows':pd.DataFrame([{'trade_date':pd.Timestamp('2021-01-04')}])}
    result=a.run(a.LazyAPI(forbidden),data,limit=1)
    assert days==[b['asof']] and result['outcome']=='WAITING_REFERENCE'
    assert result['deferred_no_reference']==1 and result['requests']==0 and c.read(path)==before


def test_repeated_price_crosscheck_recomputes_active_conflicts():
    b=accepted_fixture();b['price_comparison']['kis_adjusted']=100
    row=b['daily_acceptance']['krx_row'];refs={b['symbol']:dict(row,ACC_TRDVOL='21')}
    c.crosscheck(b,refs);c.crosscheck(b,refs)
    assert len(b['price_conflicts'])==len(set(b['price_conflicts']))
    c.crosscheck(b,{b['symbol']:row})
    assert 'Same-date KIS / KRX official OHLCV fields differ' not in b['price_conflicts']
    assert b.get('conflict_history')


def test_same_day_observations_remain_content_addressed(tmp_path,monkeypatch):
    from screener.technical_quality import digest
    monkeypatch.setattr(c,'CACHE',tmp_path/'market')
    b=accepted_fixture();path=c.CACHE/(b['symbol']+'.json.gz');c.write(path,b)
    changed=copy.deepcopy(b);changed['bars'][-1]['close']=101;c.write(path,changed)
    root=tmp_path/'observations'/b['asof']/b['symbol']
    assert c.read(root/(digest(b)+'.json.gz'))==b
    assert c.read(root/(digest(changed)+'.json.gz'))==changed
    assert c.read(root/(digest(b)+'.json.gz'))['daily_acceptance']==b['daily_acceptance']


def repair_sample():
    b=accepted_fixture();row=copy.deepcopy(b['daily_acceptance']['krx_row'])
    b['price_comparison'].update(kis_adjusted=99,kis_unadjusted=99,preserved_unadjusted=99)
    b['bars'][-1]['close']=99;b['price_conflicts'].append('KIS raw / independent KRX daily close mismatch')
    b['price_finality']='CONFLICT'
    good=copy.deepcopy(b['bars']);good[-1]['close']=100
    def convert(bar):return {'stck_bsop_date':bar['trade_date'].replace('-',''),**{field:str(bar[k]) for k,field in c.FIELDS.items()},'prtt_rate':'0','revl_issu_reas':'00'}
    class RepairAPI:
        metrics=[]
        def __init__(self,match=True,badflow=False,baddate=False):self.calls=0;self.match=match;self.badflow=badflow;self.baddate=baddate
        def request(self,path,params=None,tr=None):
            self.calls+=1
            if 'investor-trade' in path:
                rows=copy.deepcopy(b['daily_acceptance']['raw_flow_rows'])
                if self.badflow:rows[-1]['frgn_ntby_tr_pbmn']='1'
                return {'output2':rows}
            bars=copy.deepcopy(good if self.match else b['bars'])
            rows=[convert(x) for x in bars if params['FID_INPUT_DATE_1']<=x['trade_date'].replace('-','')<=params['FID_INPUT_DATE_2']]
            if self.baddate:rows[-1]['stck_bsop_date']='20990101'
            return {'output2':rows}
    def pages(api,*args,**kwargs):return [(x['trade_date'],convert(x)) for x in (good if api.match else b['bars'])]
    return b,{b['symbol']:row},RepairAPI,pages


def test_conflict_candidate_repairs_only_with_all_original_gates():
    from scripts.recover_technical import candidate
    from screener.technical import validate,buy_guide
    from screener.technical_quality import daily_accepted,digest
    b,refs,API,pages=repair_sample();before=digest(b)
    new=candidate(API(),b,refs,pages)
    assert daily_accepted(new) and not validate(new,new['asof'])
    assert new['recovery']['rebuilt_history'] and new['finality']=='UNVERIFIED'
    assert digest(b)==before and new['flows']==b['flows']
    assert b['daily_acceptance'] in new['acceptance_history'].values()
    assert 'KIS raw / independent KRX daily close mismatch' not in new['price_conflicts']
    assert buy_guide(new)['status'] in ['관망','판정 보류']
    assert not daily_accepted(candidate(API(badflow=True),b,refs,pages))
    import pytest
    with pytest.raises(ValueError):candidate(API(baddate=True),b,refs,pages)


def test_unresolved_conflict_keeps_original_and_does_not_fetch_flows():
    from scripts.recover_technical import candidate
    from screener.technical_quality import daily_accepted,digest
    b,refs,API,pages=repair_sample();before=digest(b);api=API(match=False)
    new=candidate(api,b,refs,pages)
    assert not daily_accepted(new) and digest(b)==before and api.calls==2
    assert new['acceptance_error']=='REFERENCE_PRICE_CONFLICT'
    assert b['daily_acceptance'] in new['acceptance_history'].values()


def test_completed_conflict_has_separate_bounded_recovery_queue(tmp_path,monkeypatch):
    from scripts import recover_technical as recovery,collect_technical
    b,refs,API,pages=repair_sample()
    monkeypatch.setattr(c,'ROOT',tmp_path);monkeypatch.setattr(c,'CACHE',tmp_path/'market');monkeypatch.setattr(c,'STATE',tmp_path/'state.json')
    c.write(c.CACHE/(b['symbol']+'.json.gz'),b)
    c.write(c.STATE,{'symbols':{b['symbol']:{'completed_asof':b['asof']}}})
    assert not c.select_targets([b['symbol']],c.read(c.STATE)['symbols'],b['asof'],1)
    monkeypatch.setattr(c,'krx_reference',lambda day,**kw:(refs,{}))
    monkeypatch.setattr(collect_technical,'paged',lambda api,*args,**kwargs:pages(api.api,*args,**kwargs))
    data={'flows':pd.DataFrame([{'trade_date':pd.Timestamp(b['asof'])}])}
    result=recovery.run(API(),data,limit=1,max_calls=12)
    assert result['promoted']==1 and len(result['results'])==1
    assert result['reserved_request_attempts']<=12
    assert list((tmp_path/'observations'/b['asof']/b['symbol']).glob('*.gz'))
    assert recovery.run(API(),data,limit=1)['results']==[]

def test_failed_candidate_is_archived_backed_off_and_never_overwrites(tmp_path,monkeypatch):
 from scripts import recover_technical as recovery
 b,refs,API,pages=repair_sample()
 monkeypatch.setattr(c,'ROOT',tmp_path);monkeypatch.setattr(c,'CACHE',tmp_path/'market')
 path=c.CACHE/(b['symbol']+'.json.gz');c.write(path,b);original=path.read_bytes()
 monkeypatch.setattr(c,'krx_reference',lambda day,**kw:(refs,{}))
 data={'flows':pd.DataFrame([{'trade_date':pd.Timestamp(b['asof'])}])}
 result=recovery.run(API(match=False),data,limit=1,max_calls=6)
 assert result['promoted']==0 and path.read_bytes()==original
 assert len(list((tmp_path/'provider-observations'/b['asof']/b['symbol']).glob('*.gz')))==2
 assert recovery.run(API(match=False),data,limit=1)['results']==[]


def test_recovery_reserves_auth_retries_and_rejects_bad_ohlcv():
 import pytest
 from scripts.recover_technical import BoundedAPI,RecoveryBudget,candidate
 from scripts.technical_acceptance import LazyAPI
 b,refs,API,pages=repair_sample();api=API();lazy=LazyAPI(lambda:api)
 with pytest.raises(RecoveryBudget):BoundedAPI(lazy,max_calls=3).request('unreachable')
 assert lazy.client is None and api.calls==0
 broken=copy.deepcopy(b);broken['bars'][-1]['volume']=float('nan')
 # Provider malformed numeric data is never silently repaired with a reference value.
 class Bad(API):
  def request(self,*args,**kwargs):
   d=super().request(*args,**kwargs);d['output2'][-1]['acml_vol']='NaN';return d
 with pytest.raises(ValueError,match='nonfinite_value'):candidate(Bad(),b,refs,pages)

def test_explicit_reference_refresh_archives_without_replacing_selected_reference(tmp_path,monkeypatch):
 import os,json
 from scripts.technical_reference import fetch_references
 from screener.technical_quality import digest
 b=accepted_fixture();e=b['daily_acceptance'];day=b['asof']
 old={'asof':day,'url':e['krx_url'],'observed_at':e['krx_row']['_retrieved_at'],'rows':[e['krx_row']]}
 path=tmp_path/'reference'/day/'KOSPI.json.gz';c.write(path,old);original=path.read_bytes()
 new=dict(e['krx_row'],TDD_CLSPRC='101')
 class Reply:
  status=200
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def read(self):return json.dumps({'OutBlock_1':[new]}).encode()
 monkeypatch.setenv('KRX_API_KEY','unit-test-only')
 monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:Reply())
 refs,status=fetch_references(day,tmp_path,refresh=True)
 assert refs[b['symbol']]['TDD_CLSPRC']=='101' and path.read_bytes()==original
 assert list((tmp_path/'reference-observations'/day/'KOSPI').glob('*.json.gz'))
