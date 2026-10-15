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
        krx_row=row,krx_url=url,bars_sha256=digest(bars),flows_sha256=digest(flows),requeried_flows=copy.deepcopy(flows),
        flow_endpoint='KIS FHPTJ04160001 / J / million KRW to KRW')
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
        lambda b:b['daily_acceptance'].update(requeried_flows=[]),
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
