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
