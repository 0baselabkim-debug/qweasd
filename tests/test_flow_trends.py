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
    assert m['net_1d']==3 and m['intensity_1d']==.03
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
    # AppTest does not yet serialize tab-container widgets; browser check clicks real tabs.
    periods={'sector':'오늘','stock':'오늘'}
    def run():
        for section,label in periods.items():app.session_state[section+'_period']=label
        app.run();assert not app.exception
    def select(label,value):
        next(w for w in app.selectbox if w.label==label).select(value);run()
    def tab(section,label):
        periods[section]=label;run()
    def table(column):return next(w.value for w in app.dataframe if column in w.value.columns)
    select('데이터 기준일','2026-10-14');select('종목 범위','전체 시장')
    original=table('20일 수급 전체 순위').set_index('코드')[['종합 점수','업종 내 평가 순위']].sort_index()
    assert [t.label for t in app.tabs if t.label not in ['강세 섹터','강세 섹터 내 우수 종목']]==['오늘','지난 5일','지난 20일']*2
    for label,days in [('오늘',1),('지난 5일',5),('지난 20일',20)]:
        tab('sector',label);tab('stock',label)
        sector=table('수급 정렬 순위');stock=table('20일 수급 전체 순위')
        for frame in [sector,stock]:assert frame['선택 기간 순매수(억원)'].dropna().is_monotonic_decreasing
        assert len(stock)==2430 and stock['선택 기간 순매수(억원)'].isna().sum()==(3 if days==20 else 0)
        flow_column=f'{days}일 순매수(억원)' if days>1 else '외국인 순매수(억원)'
        pd.testing.assert_series_equal(stock['선택 기간 순매수(억원)'],stock[flow_column],check_names=False)
        pd.testing.assert_frame_equal(original,stock.set_index('코드')[['종합 점수','업종 내 평가 순위']].sort_index())
    select('섹터 수급 정렬','시가총액 대비 순매수 강도');select('종목 정렬','종합 점수')
    tab('sector','지난 5일')
    assert app.session_state['sector_sort']=='누적 순매수 금액' and app.session_state['stock_period']=='지난 20일'
    tab('stock','지난 5일')
    assert app.session_state['stock_sort']=='누적 순매수 금액'
    assert table('20일 수급 전체 순위').iloc[0]['종목명']=='이수페타시스'
    assert len(app.get('vega_lite_chart'))==2
    def mode(label,value):
        next(w for w in app.radio if w.label==label).set_value(value);run()
    mode('섹터 보기','매수세 강화')
    sector=table('수급 정렬 순위')
    assert (sector['5일 증감(억원)']>0).all() and sector['5일 증감(억원)'].is_monotonic_decreasing
    mode('종목 보기','매수세 강화')
    stock=table('20일 수급 전체 순위')
    assert (stock['5일 증감(억원)']>0).all() and stock['5일 증감(억원)'].is_monotonic_decreasing
    mode('종목 보기','꾸준한 순매수')
    stock=table('20일 수급 전체 순위')
    assert (stock['20일 순매수 일수']>=15).all() and (stock['직전 5일(억원)']>0).all()
    mode('종목 보기','자금 이탈')
    stock=table('20일 수급 전체 순위')
    assert (stock['선택 기간 순매수(억원)']<0).all() and stock['선택 기간 순매수(억원)'].is_monotonic_increasing
    mode('섹터 보기','자금 이탈')
    sector=table('수급 정렬 순위')
    assert (sector['선택 기간 순매수(억원)']<0).all() and sector['선택 기간 순매수(억원)'].is_monotonic_increasing
    select('섹터 선택','KOSPI · IT 서비스')
    assert app.session_state['stock_scope']=='선택 섹터' and app.session_state['stock_view']=='전체 종목'

@pytest.mark.parametrize('recent,previous,state,direction',[
 (8,3,'순매수 증가','강화'),(3,8,'순매수 감소','약화'),(3,3,'순매수 유지','유지'),
 (-3,-8,'순매도 축소','강화'),(-8,-3,'순매도 확대','약화'),(-3,-3,'순매도 유지','유지'),
 (3,-3,'순매수 전환','강화'),(-3,3,'순매도 전환','약화'),
 (3,0,'순매수 시작','강화'),(-3,0,'순매도 시작','약화'),
 (0,3,'순매수 종료','약화'),(0,-3,'순매도 해소','강화'),(0,0,'중립 유지','유지'),
 (np.nan,0,'판정 보류','자료 부족'),(0,np.nan,'판정 보류','자료 부족')])
def test_signed_change_states(recent,previous,state,direction):
    from screener.flow_trends import change_state
    assert change_state(recent,previous)==(direction,state)


def test_steady_buying_needs_full_history_and_both_positive_windows():
    assert summarize(pd.Series([0.]*5+[1.]*15))['buying_pattern']=='꾸준한 순매수'
    assert summarize(pd.Series([0.]*6+[1.]*14))['buying_pattern']=='기준 미충족'
    assert summarize(pd.Series([1.]*15+[-1.]*5))['buying_pattern']=='기준 미충족'
    assert summarize(pd.Series([np.nan]+[1.]*19))['buying_pattern']=='자료 부족'
    m=summarize(pd.Series([1.]*10+[0.]*5+[-2.]*5),100)
    assert m['previous_5d']==0 and m['change_5d']==-10 and m['change_intensity_5d']==-.1
    assert m['change_state']=='순매도 시작'


def test_flow_views_exclude_missing_but_keep_real_zero_neutral():
    from screener.flow_trends import screen_flows
    frame=pd.DataFrame({'net_5d':[3.,-3.,0.,np.nan],'change_5d':[6.,2.,-3.,np.nan],
                        'buying_pattern':['기준 미충족']*4})
    assert screen_flows(frame,'자금 유입 상위',5).index.tolist()==[0]
    assert screen_flows(frame,'자금 이탈',5).index.tolist()==[1]
    assert screen_flows(frame,'매수세 강화',5).index.tolist()==[0,1]
    assert len(screen_flows(frame,'전체 종목',5))==4


def test_new_trading_day_rolls_windows_without_future_leakage(history):
    days=history['calendar'].trade_date.sort_values().tolist()
    for i,day in enumerate(days):history['flows'].loc[history['flows'].trade_date==day,'net_amount']=i
    previous=days[-2];current=days[-1]
    a,_,_=evaluate(history,previous);b,_,_=evaluate(history,current)
    old,_=stock_trends(history,a,previous);new,matrix=stock_trends(history,b,current)
    assert old.loc['000001','net_5d']==sum(range(20,25))
    assert new.loc['000001','net_5d']==sum(range(21,26))
    assert new.loc['000001','previous_5d']==sum(range(16,21))
    assert new.loc['000001','net_20d']==sum(range(6,26))
    assert len(matrix.index)==20 and matrix.index.max()==current


@pytest.mark.parametrize('mode',['일별 순매수','누적 순매수','함께 보기'])
def test_chart_mode_preserves_real_zero_and_missing_gaps(monkeypatch,mode):
    """Presentation switches must never silently complete an incomplete cumulative sum."""
    import io
    from screener import flow_ui
    frame=pd.DataFrame({'trade_date':pd.date_range('2026-01-01',periods=3),
                        'net_amount':[100.,np.nan,0.],'observed_members':[1,0,1],'expected_members':[1,1,1]})
    original=frame.copy(deep=True);captured={}
    monkeypatch.setattr(flow_ui.st,'markdown',lambda *a,**k:None)
    monkeypatch.setattr(flow_ui.st,'radio',lambda *a,**k:mode)
    monkeypatch.setattr(flow_ui.st,'altair_chart',lambda chart,**kwargs:captured.update(chart=chart.to_dict()))
    monkeypatch.setattr(flow_ui.st,'download_button',lambda label,data,*a,**k:captured.update(csv=data))
    flow_ui.chart(frame,3,'test_only','')
    result=pd.read_csv(io.BytesIO(captured['csv']))
    assert result.net_amount.iloc[0]==100 and pd.isna(result.net_amount.iloc[1]) and result.net_amount.iloc[2]==0
    assert result.cumulative_amount.iloc[0]==100 and result.cumulative_amount.iloc[1:].isna().all()
    assert ('layer' in captured['chart'])==(mode=='함께 보기')
    pd.testing.assert_frame_equal(frame,original)
