import copy,datetime as dt,math
import pytest
from analyze import band,within,inspect_response,price_events,current_state
from test_stages import shaped

def test_thresholds_are_nested_not_exclusive():
 for price,group in [(100,'0~10%'),(110,'0~10%'),(110.001,'10% 초과~15%'),(115,'10% 초과~15%'),(120,'15% 초과~20%'),(121,'20% 초과'),(99,'저점 대비 음수')]:
  assert band((price/100-1)*100)==group
  flags=[within(price,100,n) for n in [10,15,20]]
  assert flags==sorted(flags)
 assert band(None)=='산출 불가'

def response():
 return {'rt_cd':'0','params':{'FID_INPUT_ISCD':'000001','FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_DATE_1':'20260601','FID_INPUT_DATE_2':'20261019','FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0'},'output1':{'stck_shrn_iscd':'000001'},'observed_at':'2026-10-21T00:00:00Z','output2':[{'stck_bsop_date':'20261019','stck_oprc':'100','stck_hgpr':'110','stck_lwpr':'90','stck_clpr':'105','acml_vol':'10','acml_tr_pbmn':'1000'}]}
@pytest.mark.parametrize('fault',['scope','date','duplicate','ohlc','listing','kind','symbol','status'])
def test_invalid_observations_remain_quarantined(fault):
 x=response();listing='2000-01-01';kind='adjusted'
 if fault=='scope':x['params']['FID_COND_MRKT_DIV_CODE']='UN'
 if fault=='date':x['output2'][0]['stck_bsop_date']='20261020'
 if fault=='duplicate':x['output2']*=2
 if fault=='ohlc':x['output2'][0]['stck_hgpr']='80'
 if fault=='listing':listing='2026-10-20'
 if fault=='kind':kind='raw'
 if fault=='symbol':x['output1']['stck_shrn_iscd']='000002'
 if fault=='status':x['rt_cd']='1'
 assert inspect_response(x,'000001',kind,listing,{'2026-10-19'})[1]

def bars(prices):
 dates=[(dt.date(2026,1,1)+dt.timedelta(days=i)).isoformat() for i in range(len(prices))]
 return dates,{d:dict(open=p,high=p,low=p,close=p,volume=10,turnover=p*10) for d,p in zip(dates,prices)}

def test_stage_events_do_not_read_future_prices():
 prices=shaped({0:130,5:140,12:110,19:125,26:100,32:108,37:103,39:109})
 d,b=bars(prices);original=price_events(d,b)
 fd,fb=bars(prices+[1,1000,2,1200])
 assert original==[e for e in price_events(fd,fb) if e['confirmed_date']<=d[-1]]
 assert {e['stage'] for e in original}=={'중간','확정'}
 assert current_state(d,b,original)[0]=='상승 전환 확정'
 assert current_state(fd,fb,price_events(fd,fb))[0]!='상승 전환 확정'

def test_current_structure_is_not_invented_historical_event():
 prices=shaped({0:130,5:140,12:110,19:125,26:100,31:108,35:103,39:106})
 d,b=bars(prices);events=price_events(d,b)
 assert events==[]
 state,e,_=current_state(d,b,events)
 assert state=='중간 전환' and e['confirmed_date'] is None and e['current_structure_only']
 assert e['L3_confirmed_date']==d[37]

def test_low_breach_invalidates_retained_state_without_rewriting_event():
 prices=shaped({0:130,5:140,12:110,19:125,26:100,32:108,37:103,39:107})
 d,b=bars(prices);events=price_events(d,b);saved=copy.deepcopy(events)
 fd,fb=bars(prices+[99]);state,_,_=current_state(fd,fb,events)
 assert state not in ['중간 전환','상승 전환 확정','조기 반등 관찰'] and events==saved

def test_middle_history_above_H3_is_not_mislabeled_before_breakout():
 from analyze import classify_candidate
 e={'reference_low':100,'H3_price':108}
 assert classify_candidate('중간 전환',e,107,True,True,True,True)=='핵심 연구 후보'
 assert classify_candidate('중간 전환',e,108,True,True,True,True)=='핵심 연구 후보'
 assert classify_candidate('중간 전환',e,109,True,True,True,True)=='중간 이력·현재 H3 초과·확정 사건 미확인'
 assert classify_candidate('상승 전환 확정',e,109,True,True,True,True)=='확정 전환 연구군'
 assert classify_candidate('중간 전환',e,107,True,True,True,False).startswith('잠정')
 assert classify_candidate('해당 단계 사건 없음',None,107,True,True,True,False)=='검증 보류'
 assert classify_candidate('판정 불가',None,107,True,False,True,False)=='판정 불가'

def test_reference_cannot_clear_other_date_or_different_basis_or_six_field_mismatch():
 from analyze import reference_resolves,FIELDS
 b=dict(open=100,high=110,low=90,close=105,volume=10,turnover=1000)
 ref={tuple(b[k] for k in FIELDS)}
 assert reference_resolves(b,b,ref)
 assert not reference_resolves(b,None,ref)
 assert not reference_resolves(b,b,None)
 changed={**b,'volume':11}
 assert not reference_resolves(changed,changed,ref)
 assert not reference_resolves({**b,'close':104},b,ref)
 assert not reference_resolves(b,b,ref|{tuple(changed[k] for k in FIELDS)})
