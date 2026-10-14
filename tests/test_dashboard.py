"""Synthetic fixtures below are test-only and are never imported by the application."""
from pathlib import Path
import copy
import numpy as np
import pandas as pd
import pytest
from screener.scoring import evaluate,percentile,config

@pytest.fixture
def history():
 days=pd.bdate_range(end='2026-10-14',periods=26) # explicit synthetic test calendar, not a production KRX calendar
 instruments=[];memberships=[];flows=[];prices=[];financials=[]
 for n in range(9):
  code=f'{n+1:06}';sector=['A','B','C'][n//3]
  instruments.append(dict(symbol=code,name='TEST ONLY '+code,market='KOSPI',listing_date=pd.Timestamp('2020-01-01'),special=False))
  memberships.append(dict(symbol=code,market='KOSPI',sector=sector,valid_from=days[0],valid_to=pd.NaT,source='TEST'))
  for i,day in enumerate(days):
   price=100*(1+.001*(n+1))**i
   flows.append(dict(symbol=code,trade_date=day,net_amount=(n+1)*100,net_quantity=1,exchange='KRX',source='TEST',finality='VERIFIED'))
   prices.append(dict(symbol=code,trade_date=day,close=price,adjusted_close=price,turnover=(n+1)*10000*(2 if i>=21 else 1),volume=100,market_cap=price*1000,source='TEST',exchange='KRX'))
  financials.append(dict(symbol=code,period_end=pd.Timestamp('2025-12-31'),disclosed_on=pd.Timestamp('2026-03-01'),disclosure_verified=True,scope='CFS',currency='KRW',revenue=2000+n*100,operating_profit=200+n*10,net_income=100+n*10,equity=1000+n*100,liabilities=500+n*10,ocf=150+n*10,previous_revenue=1800,previous_operating_profit=150,previous_equity=900+n*100,receipt='test'+str(n),source='TEST'))
 return {k:pd.DataFrame(v) for k,v in dict(instruments=instruments,memberships=memberships,flows=flows,prices=prices,financials=financials,benchmarks=[dict(market='KOSPI',trade_date=d,close=1000) for d in days],calendar=[dict(market='KOSPI',trade_date=d) for d in days],exclusions=[]).items()}|{'exclusions':pd.DataFrame(columns=['symbol','name','trade_date','reason']),'metadata':{}}

def test_one_day_never_invents_long_horizon_scores(history):
 day=history['calendar'].trade_date.max()
 for table in ['flows','prices','benchmarks','calendar']:history[table]=history[table][history[table].trade_date==day]
 x,s,_=evaluate(history)
 assert x.total_score.isna().all() and s.total_score.isna().all()
 assert x.score_foreign_flow.isna().all() and x.score_activity.isna().all()
 assert (x.available_weight<=65).all()
 assert np.allclose(x.earned_score,x[['score_growth','score_profitability_health','score_valuation']].sum(axis=1))

def test_complete_history_automatically_enables_scores(history):
 x,s,_=evaluate(history)
 assert x.total_score.notna().all() and s.total_score.notna().all()
 assert (x.available_weight==100).all() and (s.available_weight==100).all()
 assert np.allclose(x.activity_ratio,2) and np.allclose(s.activity,2)
 row=x[x.symbol=='000001'].iloc[0]
 assert row.flow_intensity==pytest.approx(20*100/row.market_cap)
 assert row.roe==pytest.approx(100/950)
 # Sector return is a daily self-financing prior-close-cap-weighted index.
 pp=history['prices'];codes=['000001','000002','000003'];returns=[]
 days=history['calendar'].trade_date.iloc[-21:].tolist()
 for prev,day in zip(days[:-1],days[1:]):
  a=pp[(pp.trade_date==prev)&pp.symbol.isin(codes)].set_index('symbol')
  b=pp[(pp.trade_date==day)&pp.symbol.isin(codes)].set_index('symbol')
  returns.append(sum((b.loc[c,'adjusted_close']/a.loc[c,'adjusted_close']-1)*a.loc[c,'market_cap']/a.market_cap.sum() for c in codes))
 assert s[s.sector=='A'].iloc[0].relative_strength==pytest.approx(np.prod(1+np.array(returns))-1)

def test_missing_day_is_not_zero_and_no_date_forward_fill(history):
 day=history['calendar'].trade_date.max();prev=history['calendar'].trade_date.iloc[-2]
 history['flows']=history['flows'][~((history['flows'].symbol=='000001')&(history['flows'].trade_date==prev))]
 history['prices']=history['prices'][~((history['prices'].symbol=='000001')&(history['prices'].trade_date==day))]
 x,_,_=evaluate(history);r=x[x.symbol=='000001'].iloc[0]
 assert pd.isna(r.close) and pd.isna(r.score_foreign_flow) and pd.isna(r.total_score)

def test_future_disclosure_and_unknown_currency_are_not_used(history):
 history['financials'].loc[0,'disclosed_on']=pd.Timestamp('2026-10-15')
 history['financials'].loc[1,'currency']='USD'
 x,_,_=evaluate(history)
 assert x[x.symbol.isin(['000001','000002'])].revenue.isna().all()

def test_future_membership_not_applied_backwards(history):
 history['memberships']['valid_from']=history['calendar'].trade_date.max()
 _,s,_=evaluate(history)
 assert s.relative_strength.isna().all() and s.foreign_intensity.isna().all()

@pytest.mark.parametrize('field,value,reason',[('equity',0,'자본'),('net_income',-1,'적자'),('ocf',np.nan,'현금흐름')])
def test_financial_hold(history,field,value,reason):
 history['financials'].loc[0,field]=value
 x,_,_=evaluate(history);r=x[x.symbol=='000001'].iloc[0]
 assert reason in r.financial_hold and pd.isna(r.total_score) and pd.isna(r.score_profitability_health)

def test_special_unmapped_and_excluded(history):
 history['instruments'].loc[0,'special']=True
 history['memberships'].loc[1,'sector']='업종 미분류'
 history['exclusions']=pd.DataFrame([dict(symbol='000003',name='TEST',trade_date=history['calendar'].trade_date.max(),reason='halted')])
 x,s,_=evaluate(history)
 assert '000003' not in set(x.symbol)
 assert pd.isna(x[x.symbol=='000001'].iloc[0].total_score)
 assert x[x.symbol=='000002'].iloc[0].available_weight==0

def test_percentile_ties_small_cohorts_and_config():
 assert percentile(pd.Series([1,1,1])).tolist()==[.5,.5,.5]
 assert percentile(pd.Series([1,2])).isna().all()
 assert percentile(pd.Series([1,2,3])).tolist()==[0,.5,1]
 assert percentile(pd.Series([1,2,3]),higher=False).tolist()==[1,.5,0]
 assert sum(config()['sector_weights'].values())==100


def test_actual_dashboard_and_filters():
 from streamlit.testing.v1 import AppTest
 at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=60).run()
 assert not at.exception
 next(s for s in at.selectbox if s.label=='데이터 기준일').select('2026-10-14');at.run()
 assert any(m.value=='2,430종목' for m in at.metric)
 market=next(s for s in at.selectbox if s.label=='시장');market.select('KOSDAQ');at.run()
 assert not at.exception and all(str(s).startswith('KOSDAQ') for s in next(s for s in at.selectbox if s.label=='섹터 선택').options)
 next(s for s in at.selectbox if s.label=='시장').select('전체');at.run()
 next(t for t in at.text_input if t.label=='종목 검색').input('005930');at.run()
 assert not at.exception
 assert next(s for s in at.selectbox if s.label=='종목 상세정보').value=='005930'
