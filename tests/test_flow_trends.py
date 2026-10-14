"""Test fixtures are synthetic and never enter the packaged database."""
import numpy as np
import pandas as pd
import pytest
from test_dashboard import history
from screener.scoring import evaluate
from screener.flow_trends import summarize,stock_trends,group_trends,stock_series


def test_windows_use_exact_trading_observations_and_signed_change():
    s=pd.Series([-4.]*10+[-2.]*5+[3.]*5)
    m=summarize(s,100)
    assert (m['net_5d'],m['previous_5d'],m['net_20d'],m['change_5d'])==(15,-10,-35,25)
    assert m['positive_days_20d']==5 and m['intensity_20d']==-.35
    assert np.isnan(summarize(s.tail(19))['net_20d'])
    s.iloc[1]=np.nan;m=summarize(s)
    assert m['net_5d']==15 and np.isnan(m['net_20d']) and np.isnan(m['positive_days_20d'])
    assert summarize(pd.Series([0.]*20))['net_20d']==0


def test_current_membership_aggregates_without_enabling_historical_rank(history):
    history['memberships']['valid_from']=history['calendar'].trade_date.max()
    x,old,_=evaluate(history);before=x.copy(deep=True)
    metrics,matrix=stock_trends(history,x,x.trade_date.max())
    groups,series=group_trends(history,x,matrix,x.trade_date.max())
    assert old.total_score.isna().all()
    assert groups.flow_points.notna().all() and groups.flow_points.between(0,70).all()
    assert groups.loc[groups.sector=='A','net_20d'].item()==20*(100+200+300)
    assert metrics.loc['000001','net_5d']==500
    pd.testing.assert_frame_equal(x,before)
    assert series['KOSPI','A'].cumulative_amount.iloc[-1]==12000


def test_missing_constituent_does_not_become_partial_sector_total(history):
    x,_,_=evaluate(history);day=history['calendar'].trade_date.iloc[-8]
    history['flows']=history['flows'][~((history['flows'].symbol=='000001')&(history['flows'].trade_date==day))]
    metrics,matrix=stock_trends(history,x,x.trade_date.max());groups,series=group_trends(history,x,matrix,x.trade_date.max())
    r=groups[groups.sector=='A'].iloc[0]
    assert pd.isna(r.net_20d) and pd.isna(r.previous_5d) and pd.isna(r.change_5d) and pd.isna(r.flow_points)
    assert r.net_5d==3000
    chart=series['KOSPI','A'];assert chart.loc[chart.trade_date==day,'observed_members'].item()==2
    assert chart[chart.trade_date>=day].cumulative_amount.isna().all()
    assert pd.isna(stock_series(matrix,'000001').cumulative_amount.iloc[-1])


def test_duplicate_source_and_theme_overlap(history):
    x,_,_=evaluate(history);_,matrix=stock_trends(history,x,x.trade_date.max())
    membership=pd.DataFrame([('000001','A'),('000001','A'),('000001','B'),('000002','B')],columns=['symbol','theme'])
    groups,_=group_trends(history,x,matrix,x.trade_date.max(),membership)
    assert groups.set_index('sector').net_20d.to_dict()=={'A':2000,'B':6000}
    history['flows']=pd.concat([history['flows'],history['flows'].tail(1)])
    with pytest.raises(ValueError,match='Duplicate'):stock_trends(history,x,x.trade_date.max())


def test_price_conflict_does_not_change_money_trends(history):
    x,_,_=evaluate(history);m,a=stock_trends(history,x,x.trade_date.max())
    history['prices']['close']=np.nan;history['prices']['adjusted_close']=np.nan
    history['metadata']['adjusted_price_conflicts']={'2026-10-14':[{'symbol':'000001'}]}
    n,b=stock_trends(history,x,x.trade_date.max())
    pd.testing.assert_frame_equal(m,n);pd.testing.assert_frame_equal(a,b)


def test_real_app_period_sort_and_whole_market():
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_file(str(__import__('pathlib').Path(__file__).resolve().parents[1]/'app.py'),default_timeout=90).run();assert not app.exception
    def select(label,value):
        next(w for w in app.selectbox if w.label==label).select(value);app.run();assert not app.exception
    select('데이터 기준일','2026-10-14');select('수급 기간','5거래일');select('섹터 수급 정렬','시가총액 대비 순매수 강도')
    select('종목 범위','전체 시장');select('종목 정렬','20일 외국인 순매수')
    tables=[w.value for w in app.dataframe]
    table=next(t for t in tables if '20일 수급 전체 순위' in t.columns)
    assert len(table)==2430 and table['20일 순매수(억원)'].dropna().is_monotonic_decreasing
    assert table['20일 순매수(억원)'].isna().sum()==3
    select('수급 기간','20거래일')
    assert len(app.get('vega_lite_chart'))==2
