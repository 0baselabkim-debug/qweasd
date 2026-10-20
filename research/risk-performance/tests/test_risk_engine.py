import copy,datetime as dt,json
import pytest
from engine import cohort,decision_gate,trade,summary,suppress_duplicates,nav_statistics
from forward import exclusive_json,record_observation

CAL=['2026-10-15','2026-10-16','2026-10-19','2026-10-20','2026-10-21','2026-10-22','2026-10-23']

def sig(**kw):
    return {'symbol':'000001','group':'A','signal_date':CAL[0],'pit_eligible':True,
            'record_kind':'historical_partial_universe','L3_raw':98,'raw_signal_close':99,**kw}

def bars():
    return {d:{'open':100,'high':110,'low':95,'close':105,'volume':1000,
       'basis':'raw','execution_verified':True,'action_interval_verified':True,'tradable':True} for d in CAL[1:]}

def test_asof_and_available_time_are_different():
    assert decision_gate(CAL[0],CAL,['2026-10-15T21:00:00+00:00'],'2026-10-15T23:40:00+00:00')[0]
    assert not decision_gate(CAL[0],CAL,['2026-10-16T00:00:00+00:00'],'2026-10-16T00:00:00+00:00')[0]

def test_requeried_future_evidence_never_unlocks_old_signal():
    assert not decision_gate(CAL[0],CAL,['2026-10-15T21:00:00+00:00'],'2026-10-20T10:00:00+00:00')[0]

def test_prospective_freeze_cannot_be_backdated():
    assert not decision_gate(CAL[0],CAL,['2026-10-15T21:00:00+00:00'],'2026-10-15T23:40:00+00:00','2026-10-20T10:00:00+00:00')[0]
    assert not decision_gate(CAL[0],CAL,['2026-10-15T21:00:00+00:00'],'2026-10-15T23:40:00+00:00','2026-10-15T23:00:00+00:00')[0]

@pytest.mark.parametrize('price,expected',[(100,'A'),(110,'A'),(110.01,'B'),(120,'B'),(120.01,'D'),(99,'D')])
def test_thresholds_mutually_exclusive(price,expected):
    assert cohort('중간 전환',True,price,100,130)==expected

def test_above_H3_is_not_middle_control_overlap():
    assert cohort('중간 전환',True,109,100,108)=='D'
    assert cohort('상승 전환 확정',True,109,100,108)=='C'
    assert cohort('중간 전환',False,109,100,110) is None

def test_no_reference_trade_or_synthetic_zero_return():
    t=trade(sig(record_kind='retrospective_reference'),bars(),CAL,5)
    assert t['status']=='진입 불가' and t['net_return'] is None
    t=trade(sig(),bars(),CAL[:4],5)
    assert t['status']=='관측 대기' and t['net_return'] is None

def test_entry_is_next_open_and_entry_day_counts_as_day1():
    b=bars(); b[CAL[1]]['open']=102
    t=trade(sig(),b,CAL,5)
    assert t['entry_date']==CAL[1] and t['exit_date']==CAL[5]
    assert t['gross_return']==pytest.approx(105/102-1)

def test_L3_breach_exit_next_open_excludes_exit_day_low():
    b=bars();b[CAL[2]]['close']=97;b[CAL[3]]['open']=94;b[CAL[3]]['low']=20
    t=trade(sig(),b,CAL,5,'L3')
    assert t['exit_date']==CAL[3] and t['exit_price']==94
    assert t['mae']==pytest.approx(-.06)

def test_fixed_exit_precedes_stop_next_open():
    b=bars();b[CAL[5]]['close']=97
    t=trade(sig(),b,CAL,5,'L3')
    assert t['exit_date']==CAL[5] and t['exit_price']==97

@pytest.mark.parametrize('field,value,reason',[
 ('tradable',False,'not_tradable'),('volume',0,'not_tradable'),
 ('basis','adjusted','entry_price_basis_unverified'),('execution_verified',False,'entry_price_basis_unverified')])
def test_no_fill_for_nontradable_or_unverified_basis(field,value,reason):
    b=bars();b[CAL[1]][field]=value
    assert trade(sig(),b,CAL,5)['reason']==reason

def test_missing_day_and_unknown_action_never_shortens_period():
    b=bars();del b[CAL[2]]
    assert trade(sig(),b,CAL,5)['reason']=='holding_day_missing'
    b=bars();b[CAL[2]]['action_interval_verified']=False
    assert trade(sig(),b,CAL,5)['net_return'] is None

def test_cost_sensitivity_is_explicit_not_tax_claim():
    t=trade(sig(),bars(),CAL,5,roundtrip_bps=30)
    assert t['net_return']==pytest.approx(105*.9985/(100*1.0015)-1)
    assert t['net_return']<t['gross_return']

def test_C_new_event_required_and_duplicate_patterns_limited():
    a=sig(L2_date='2026-10-01',L3_date='2026-10-13')
    b={**a,'signal_date':CAL[1]};old_c={**a,'group':'C','confirmed_date':'2026-10-14'}
    new_c={**old_c,'confirmed_date':CAL[0]}
    assert len(suppress_duplicates([a,b,old_c],5,CAL))==1
    assert len(suppress_duplicates([a,b,new_c],5,CAL))==2

def test_empty_sample_is_missing_not_success_or_zero():
    assert all(x is None for x in summary([]).values())
    assert nav_statistics([])=={'mdd':None,'sharpe':None,'sortino':None}

def test_NAV_not_cross_sectional_trade_compounding():
    x=nav_statistics([(CAL[0],100),(CAL[1],80),(CAL[2],90)])
    assert x['mdd']==pytest.approx(-.2) and x['sharpe'] is None

def test_immutable_records_and_observations(tmp_path):
    p=exclusive_json(tmp_path,'x',{'input_sha256':'a','frozen_at':'first'})
    exclusive_json(tmp_path,'x',{'input_sha256':'a','frozen_at':'later'})
    assert json.loads(p.read_text())['frozen_at']=='first'
    with pytest.raises(ValueError):exclusive_json(tmp_path,'x',{'input_sha256':'b'})
    source=tmp_path/'raw.json';source.write_text('{"value":1}')
    x=record_observation(source,tmp_path/'forward');before=x.read_bytes()
    assert record_observation(source,tmp_path/'forward').read_bytes()==before
