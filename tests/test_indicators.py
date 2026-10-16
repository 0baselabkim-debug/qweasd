"""New score-free behavior; synthetic cases stay in test memory, actual snapshot checks are read-only."""
import copy,json,datetime as dt
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from test_dashboard import history
from screener.indicators import universe,sector_indicators,stock_activity,activity
from screener.macro import record,merge,catalog,cutoff


def test_score_free_universe_keeps_loss_makers_and_excludes_future_financials(history,monkeypatch):
 import screener.scoring as scoring
 monkeypatch.setattr(scoring,'evaluate',lambda *a,**k:(_ for _ in ()).throw(AssertionError('legacy score called')))
 history['financials'].loc[0,['equity','operating_profit','ocf']]=[-10,-20,-30]
 history['financials'].loc[1,'disclosed_on']=pd.Timestamp('2026-10-15')
 stocks,matrix=universe(history,'2026-10-14')
 assert len(stocks)==9 and stocks.loc[stocks.symbol=='000001','equity'].item()==-10
 assert stocks.loc[stocks.symbol=='000002','equity'].isna().all()
 assert not any('score' in c or 'points' in c for c in stocks)


def test_breadth_counts_zero_as_evaluable_and_does_not_zero_fill(history):
 last=history['calendar'].trade_date.max();prior=history['calendar'].trade_date.iloc[-2]
 history['flows'].loc[history['flows'].symbol=='000002','net_amount']=0
 history['flows']=history['flows'][~((history['flows'].symbol=='000003')&(history['flows'].trade_date==prior))]
 stocks,matrix=universe(history,last);groups,series=sector_indicators(history,stocks,matrix,last)
 r=groups.set_index('sector').loc['A']
 assert r.evaluable_5d==2 and r.positive_5d==1 and r.members==3 and r.breadth_5d==50
 assert pd.isna(r.net_5d) and pd.isna(r.positive_days_20d)
 assert series['KOSPI','A'].trade_date.nunique()==20
 assert pd.isna(series['KOSPI','A'].loc[lambda f:f.trade_date==prior,'net_amount']).all()
 assert not any('score' in c or 'points' in c for c in groups)


def test_activity_exact_disjoint_windows_and_unknown_scope(history):
 assert activity(pd.Series([10.]*20+[20.]*5))==(20,2)
 assert pd.isna(activity(pd.Series([0.]*20+[20.]*5))[1])
 assert pd.isna(activity(pd.Series([10.]*19+[20.]*5))[1])
 stocks,matrix=universe(history,'2026-10-14');x=stock_activity(history,stocks,'2026-10-14')
 assert x.activity_ratio.eq(2).all()
 history['metadata']['adjusted_price_conflicts']={'2026-10-14':[{'symbol':'000001'}]}
 y=stock_activity(history,stocks,'2026-10-14');assert pd.isna(y.loc[y.symbol=='000001','activity_ratio']).all()
 assert y.set_index('symbol').net_20d.equals(x.set_index('symbol').net_20d)



def test_theme_overlap_is_per_theme_not_double_counted_inside_group(history):
 stocks,matrix=universe(history,'2026-10-14')
 themes=pd.DataFrame([['000001','A'],['000001','A'],['000001','B']],columns=['symbol','theme'])
 groups,_=sector_indicators(history,stocks,matrix,'2026-10-14',themes)
 assert len(groups)==2 and groups.members.eq(1).all() and groups.net_5d.eq(500).all()


def test_macro_rates_bp_timezones_and_missing_publications():
 now=dt.datetime(2026,10,15,2,tzinfo=dt.timezone.utc)
 values=[{'date':'2026-10-09','value':'4.8'},{'date':'2026-10-12','value':'.'},{'date':'2026-10-13','value':'4.79'},{'date':'2026-10-15','value':'999'}]
 r=record(catalog()['us2y'],values,now)
 assert r['date']=='2026-10-13' and r['previous_date']=='2026-10-09'
 assert r['change']==pytest.approx(-1) and r['change_percent'] is None
 assert cutoff('Asia/Seoul',now)=='2026-10-14' and cutoff('America/New_York',now)=='2026-10-14'
 fx=record(catalog()['usdkrw']|{'daily_fixing':True},[{'date':'2026-10-14','value':1340.5},{'date':'2026-10-15','value':1338.6}],now)
 assert fx['date']=='2026-10-15' and fx['change']==pytest.approx(-1.9)
 assert fx['change_percent']==pytest.approx(-1.9/1340.5*100)


def test_macro_failure_and_older_response_preserve_last_good():
 now=dt.datetime(2026,10,15,2,tzinfo=dt.timezone.utc)
 r=record(catalog()['us2y'],[{'date':'2026-10-13','value':4.8},{'date':'2026-10-14','value':4.79}],now)
 state={'indicators':{'us2y':r},'investors':{},'attempts':{},'history':[]}
 result=merge(state,{'indicators':{},'attempts':{'us2y':{'status':'failed'}}})
 assert result['indicators']==state['indicators'] and result['attempts']['us2y']['status']=='failed'
 bad=r|{'change':999}
 rejected=merge(state,{'indicators':{'us2y':bad}})
 assert rejected['indicators']==state['indicators'] and rejected['attempts']['us2y']['status']=='validation_failed'
 assert not merge(state,{'investors':{'KOSPI':{'date':'2026-10-14','unit':'unknown'}}})['investors']
 older=r|{'date':'2026-10-12'};assert merge(state,{'indicators':{'us2y':older}})['indicators']==state['indicators']
 changed=record(catalog()['us2y'],[{'date':'2026-10-13','value':4.8},{'date':'2026-10-14','value':4.78}],now)
 revised=merge(state,{'indicators':{'us2y':changed}})
 assert len(revised['history'])==1 and revised['history'][0]['record']['value']==4.79


def test_actual_indicators_reconcile_original_raw_values_and_scores():
 from screener.store import load
 from screener.scoring import evaluate
 d=load();x,matrix=universe(d,'2026-10-14');legacy,_,_=evaluate(d,'2026-10-14')
 old=pd.read_csv('reports/flow-trends/stock-flows.csv',dtype={'symbol':str}).set_index('symbol').sort_index()
 current=x.set_index('symbol').sort_index()
 assert len(x)==2430 and legacy.total_score.notna().sum()==1161
 for c in ['net_1d','net_5d','net_20d','change_5d','positive_days_20d']:
  pd.testing.assert_series_equal(current[c],old[c],check_names=False,check_dtype=False)
 g,_=sector_indicators(d,x,matrix,'2026-10-14');oldg=pd.read_csv('reports/flow-trends/sector-flows.csv').set_index(['market','sector']).sort_index()
 for c in ['net_1d','net_5d','net_20d','change_5d']:
  pd.testing.assert_series_equal(g.set_index(['market','sector']).sort_index()[c],oldg[c],check_names=False,check_dtype=False)
 assert not any('score' in c for c in x)


def test_new_app_navigation_preserves_sort_and_has_no_scores():
 from streamlit.testing.v1 import AppTest
 from screener.exploration_ui import navigate
 app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=120).run();assert not app.exception
 def click(label):
  next(b for b in app.button if b.label==label).click();app.run();assert not app.exception
 def choose(label,value):
  next(w for w in app.selectbox if w.label==label).select(value);app.run();assert not app.exception
 def mode(label,value):
  next(w for w in app.radio if w.label==label).set_value(value);app.run();assert not app.exception
 assert app.session_state['stage']=='macro'
 click('수급 필터');mode('기간','지난 5일');choose('섹터 정렬','시총 대비 순매수 강도')
 navigate(app.session_state,'sector',('KOSPI','IT 서비스'));app.run();assert not app.exception
 choose('종목 정렬','종목명');navigate(app.session_state,'stock','035420');app.run();assert not app.exception
 assert app.session_state['stage']=='detail' and any('NAVER' in w.value for w in app.subheader)
 assert any('공시 재무정보' in w.value for w in app.subheader)
 assert not any('매수 가이드' in w.value or '투자 적격성' in w.value for w in app.markdown)
 for w in app.dataframe:assert not any('점수' in str(c) for c in w.value.columns)
 click('IT 서비스');assert next(w for w in app.selectbox if w.label=='종목 정렬').value=='종목명'
 click('전체섹터 조회');assert next(w for w in app.radio if w.label=='기간').value=='지난 5일'
 assert next(w for w in app.selectbox if w.label=='섹터 정렬').value=='시총 대비 순매수 강도'
 assert any(b.label=='NAVER' for b in app.button)
