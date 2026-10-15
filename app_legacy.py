"""Real-data investment candidate explorer. No orders, forecasts, or demo rows."""
from pathlib import Path
import html
import hashlib
import json
import importlib
import threading
import datetime as dt
from zoneinfo import ZoneInfo
import screener.themes as themes_module
import screener.corporate as corporate_module
import screener.corporate_ui as corporate_ui_module
import pandas as pd
import numpy as np
import altair as alt
import streamlit as st
from screener.store import DB,load
from screener.scoring import evaluate, CONFIG
import screener.scoring as scoring_module
import screener.flow_trends as flow_module
import screener.flow_ui as flow_ui_module
from screener.themes import aggregate as aggregate_themes, members as theme_members
from screener.flow_trends import stock_trends,group_trends,stock_series,screen_flows
from screener.flow_ui import chart as flow_chart
import screener.dashboard_style as ui

st.set_page_config(page_title='한국 증시 수급 필터',page_icon='◈',layout='wide',initial_sidebar_state='collapsed')
importlib.reload(ui)
ui.install()
if not DB.exists():
 st.error('실제 데이터 DB가 없습니다. scripts/build_dashboard_data.py로 보존된 실제 자료를 불러와야 합니다.');st.stop()
@st.cache_data(show_spinner=False,max_entries=3)
def snapshot(mtime,asof,config_mtime,calculation_version,data_version):
 data=load()
 stocks,sectors,metadata=evaluate(data,asof)
 return data,stocks,sectors,metadata
@st.cache_data(show_spinner=False,max_entries=4)
def flow_snapshot(mtime,asof,config_mtime,calculation_version,data_version):
 data,stocks,_,_=snapshot(mtime,asof,config_mtime,calculation_version,data_version)
 return stock_trends(data,stocks,asof)
@st.cache_data(show_spinner=False,max_entries=8)
def flow_groups(mtime,asof,config_mtime,calculation_version,data_version,market,theme_mode,theme_version,theme_asof):
 data,stocks,formal,_=snapshot(mtime,asof,config_mtime,calculation_version,data_version)
 _,matrix=flow_snapshot(mtime,asof,config_mtime,calculation_version,data_version)
 subset=stocks if market=='전체' else stocks[stocks.market==market]
 groups,series=group_trends(data,subset,matrix,asof,theme_members(theme_asof) if theme_mode else None)
 if not theme_mode:
  cols=['market','sector','total_score','evaluation_rank','score_relative_strength','score_activity','available_weight']
  groups=groups.merge(formal[cols].rename(columns={c:'formal_'+c for c in cols if c not in ['market','sector']}),on=['market','sector'],how='left',validate='one_to_one')
 else:
  for c in ['total_score','evaluation_rank','score_relative_strength','score_activity','available_weight']:groups['formal_'+c]=np.nan
 return groups,series
@st.cache_data(show_spinner=False,max_entries=2)
def dates(mtime,data_version):
 d=load();return sorted(d['flows'].trade_date.dt.strftime('%Y-%m-%d').unique(),reverse=True)
mtime=DB.stat().st_mtime_ns
calculation_version=hashlib.sha256(b''.join((Path(__file__).parent/p).read_bytes() for p in ['screener/scoring.py','screener/store.py','config.toml','screener/flow_trends.py','screener/flow_ui.py','screener/themes.py','screener/corporate.py','screener/corporate_ui.py'])+corporate_ui_module.version().encode()+b'enriched-schema-v3').hexdigest()
# Community Cloud can rerun app.py while retaining an imported dependency.
# Refresh once per source version before caching its results.
if not hasattr(scoring_module,'_dashboard_reload_lock'):scoring_module._dashboard_reload_lock=threading.RLock()
with scoring_module._dashboard_reload_lock:
 if getattr(scoring_module,'_dashboard_loaded_version',None)!=calculation_version:
  importlib.reload(scoring_module)
  importlib.reload(flow_module)
  importlib.reload(flow_ui_module)
  importlib.reload(corporate_module)
  importlib.reload(corporate_ui_module)
  importlib.reload(themes_module)
  scoring_module._dashboard_loaded_version=calculation_version
evaluate=scoring_module.evaluate
stock_trends=flow_module.stock_trends;group_trends=flow_module.group_trends;stock_series=flow_module.stock_series;screen_flows=flow_module.screen_flows
flow_chart=flow_ui_module.chart
theme_members=themes_module.members;aggregate_themes=themes_module.aggregate
manifest=Path(__file__).parent/'snapshot/manifest.json'
data_version=hashlib.sha256(manifest.read_bytes() if manifest.exists() else b'local-db').hexdigest()
publication=json.loads(manifest.read_text()) if manifest.exists() else {}
with st.sidebar:
 st.markdown('### 조회 조건')
 asof=st.selectbox('데이터 기준일',dates(mtime,data_version))
 classification=st.radio('분류체계',['공식 업종','투자 테마'])
 market=st.selectbox('시장',['전체','KOSPI','KOSDAQ'])
 query=st.text_input('종목 검색',placeholder='종목명 또는 6자리 코드')
 st.link_button('수집·게시 실행 상태','https://github.com/0baselabkim-debug/qweasd/actions/workflows/daily-dashboard.yml')
with st.spinner('실제 수급과 공시 데이터를 연결하고 있습니다…'):
 data,stocks,sectors,meta=snapshot(mtime,asof,CONFIG.stat().st_mtime_ns,calculation_version,data_version)
flow_metrics,flow_matrix=flow_snapshot(mtime,asof,CONFIG.stat().st_mtime_ns,calculation_version,data_version)
stocks=stocks.join(flow_metrics,on='symbol',validate='one_to_one')
visible_sectors=sectors if market=='전체' else sectors[sectors.market==market]
visible_stocks=stocks if market=='전체' else stocks[stocks.market==market]
theme_mode=classification=='투자 테마'
theme_asof=max(asof,str(dt.datetime.now(ZoneInfo('Asia/Seoul')).date())) if asof==dates(mtime,data_version)[0] else asof
theme_table,theme_stocks,theme_stats=aggregate_themes(visible_stocks,theme_asof)
corporate_ui_module.status_panel()
if theme_mode:visible_sectors=theme_table
searched=visible_stocks[visible_stocks.name.str.contains(query,case=False,regex=False)|visible_stocks.symbol.str.contains(query,regex=False)] if query else visible_stocks
with st.container(key='header'):
 title_col,date_col,update_col=st.columns([6,2,1.6],vertical_alignment='center')
 with title_col:st.title('한국 증시 수급 필터')
 with date_col:st.markdown(f'<div class="date-label">기준일&nbsp; <b>{html.escape(asof)}</b></div>',unsafe_allow_html=True)
 with update_col:st.link_button('↻ 업데이트 상태','https://github.com/0baselabkim-debug/qweasd/actions/workflows/daily-dashboard.yml',width='stretch')
krx_count=data['metadata'].get('snapshots',{}).get(asof.replace('-',''),{}).get('official_krx_price_rows',0)
with st.sidebar.expander('데이터 상태 · 출처'):
 st.metric('정상 거래 · 실제 수급',f'{len(stocks):,}종목')
 st.metric('공시일 확인된 연간 재무',f'{stocks.period_end.notna().sum():,}종목')
 st.write(f'외국인 수급: {len(stocks):,}개 / KIS')
 excluded=data['exclusions'][data['exclusions'].trade_date==pd.Timestamp(asof)]
 st.write(f'선택일 제외 기록: {len(excluded):,}개')
 st.write(f'공식 업종 미분류: {(stocks.sector=="업종 미분류").sum()}개')
 st.write(f'KRX 공식 당일 시세: {krx_count}개')
 st.caption('초기 과거 시세는 KRX 공식 API, 일일 추가 자료는 KIS의 KRX 기준 응답입니다. 수정주가는 KIS 공식 수정주가 옵션, 기준지수는 KOSPI·KOSDAQ 지수입니다. KIS 시총은 종가·거래량·당시 상장주식수 범위·단위를 대조한 경우만 사용합니다.')
 historical_prices=data['prices'][data['prices'].trade_date<=pd.Timestamp(asof)]
 historical_indices=data['benchmarks'][data['benchmarks'].trade_date<=pd.Timestamp(asof)]
 st.write(f'수정주가 관측: {historical_prices.adjusted_close.notna().sum():,}행 · 기준지수: {len(historical_indices):,}행')
 conflicts=data['metadata'].get('adjusted_price_conflicts',{}).get(asof,[])
 if conflicts:
  st.warning(f'수정주가 재조회 시 {len(conflicts):,}종목의 종가가 기존 관측값과 다릅니다. 기존 가격·시총·종목 평가를 유지하며 해당 관측은 섹터 수익률 평가에서 보류합니다.')
  st.caption('3종목 실측: 보존 종가는 NXT·통합 종가와 일치하고 KRX 종가와 달랐습니다. 전체 종목의 동일 원인은 미확인입니다. 수급은 KRX 금액을 유지합니다.')
 st.caption(f'확인된 업종 구성 시작일: {data["memberships"].valid_from.min().date()} · 이전 구성은 미확보입니다.')
 st.caption('KIS 현재가 응답에는 거래일 필드가 없습니다. 시총의 수신 시점과 가격 기준일을 구분하며, 과거 날짜로 소급하지 않습니다.')
 st.caption(f'매출 계정 확인 {stocks.revenue.notna().sum():,}개 · 영업현금흐름 확인 {stocks.ocf.notna().sum():,}개')
 st.caption('OpenDART: 2025년 연간/2024년 비교, 동일 공시·연결/별도 범위·원화 확인. PER·PBR은 연결이면 지배지분, 별도이면 해당 기업 손익·자본 기준으로 직접 계산합니다. 공급자의 TTM PER과 구분합니다. ROE는 총순이익/평균 총자본 기준입니다.')
 st.caption('지배지분 총액이 따로 없으면 같은 공시에 총액과 비지배지분이 모두 명시된 경우에만 차감 계산합니다. 비지배지분 누락을 0으로 가정하지 않습니다.')
 fields={'영업현금흐름':'ocf','시가총액':'market_cap','PER':'per','PBR':'pbr','ROE':'roe','부채비율':'debt_ratio'}
 st.dataframe([{'지표':name,'계산 가능':int(stocks[col].notna().sum()),'비율':f'{stocks[col].notna().mean():.1%}'} for name,col in fields.items()],hide_index=True)
 st.link_button('원 수집 실행 확인','https://github.com/0baselabkim-debug/qweasd/actions/runs/38560757360')

def display_number(v,decimals=1,suffix=''):
 return '—' if pd.isna(v) else f'{v:,.{decimals}f}{suffix}'
def csv(frame):return frame.to_csv(index=False).encode('utf-8-sig')

def period_tabs(prefix,sort_key):
 labels=['오늘','지난 5일','지난 20일']
 tabs=st.tabs(labels,key=prefix+'_period',on_change='rerun')
 selected_period=st.session_state[prefix+'_period']
 if st.session_state.get(prefix+'_previous_period')!=selected_period:
  st.session_state[sort_key]='누적 순매수 금액'
 st.session_state[prefix+'_previous_period']=selected_period
 index=labels.index(selected_period)
 return tabs[index],[1,5,20][index]

def analysis_mode(prefix,options):
 mode=st.radio('섹터 보기' if prefix=='sector' else '종목 보기',options,horizontal=True,key=prefix+'_view')
 # A mode switch supplies a sensible sort; later manual sorting remains available.
 signature=(mode,st.session_state[prefix+'_period'])
 if st.session_state.get(prefix+'_view_signature')!=signature:
  st.session_state[prefix+'_sort']=('최근 5일 순매수 증감' if prefix=='sector' else '5일 순매수 증감') if mode=='매수세 강화' else '누적 순매수 금액'
 st.session_state[prefix+'_view_signature']=signature
 return mode


main_sector,main_stock=st.tabs(['강세 섹터','강세 섹터 내 우수 종목'],key='workspace_view',on_change='rerun')
group_flows,group_series=flow_groups(mtime,asof,CONFIG.stat().st_mtime_ns,calculation_version,data_version,market,theme_mode,(Path(__file__).parent/'theme_mapping.json').stat().st_mtime_ns,theme_asof)
with main_sector:
 sector_tab,period_days=period_tabs('sector','sector_sort')
 with sector_tab:
  sector_left,sector_right=st.columns([1.05,1],gap='small')
  with sector_left,st.container(key='sector_panel'):
   st.subheader('테마 수급 정렬' if theme_mode else '섹터 수급 정렬',anchor=False)
   ui.badges(['현재 구성 기준 수급 추이',('검증된 일부 테마' if theme_mode else '수급 부분 70점',''),('종합 100점 평가 보류' if not group_flows.formal_total_score.notna().any() else '종합 100점','hold')])
   sector_mode=analysis_mode('sector',['자금 유입 상위','매수세 강화','자금 이탈'])
   sector_sort=st.selectbox('섹터 수급 정렬',['누적 순매수 금액','시가총액 대비 순매수 강도','최근 5일 순매수 증감','수급 부분 점수','종합 강세 점수'],key='sector_sort')
   rank_label='종합 점수 정렬 순위' if sector_sort=='종합 강세 점수' else '수급 정렬 순위'
   sector_key={'누적 순매수 금액':f'net_{period_days}d','시가총액 대비 순매수 강도':f'intensity_{period_days}d','최근 5일 순매수 증감':'change_5d','수급 부분 점수':'flow_points','종합 강세 점수':'formal_total_score'}[sector_sort]
   sector_ascending=sector_mode=='자금 이탈' and sector_key not in ['flow_points','formal_total_score']
   ordered=(group_flows if sector_key=='formal_total_score' else screen_flows(group_flows,sector_mode,period_days)).sort_values([sector_key,'market','sector'],ascending=[sector_ascending,True,True],na_position='last')
   ordered['sort_rank']=ordered[sector_key].rank(ascending=sector_ascending,method='min')
   if ordered.empty:st.info('선택 조건을 충족하는 섹터가 없습니다. 다른 보기나 기간을 선택하세요.')
   table=pd.DataFrame({'시장':ordered.market,'섹터':ordered.sector,'종목 수':ordered.members,'수급 변화':ordered.change_direction+' · '+ordered.change_state,'매수 지속':ordered.buying_pattern,rank_label:ordered.sort_rank,
    '선택 기간 순매수(억원)':ordered[f'net_{period_days}d']/1e8,'당일 순매수(억원)':ordered.net_1d/1e8,
    '5일 순매수(억원)':ordered.net_5d/1e8,'20일 순매수(억원)':ordered.net_20d/1e8,'직전 5일(억원)':ordered.previous_5d/1e8,'5일 증감(억원)':ordered.change_5d/1e8,
    '선택 기간 강도(%)':ordered[f'intensity_{period_days}d']*100,'20일 순매수 일수':ordered.positive_days_20d,
    '유입 강도 점수 /40':ordered.flow_intensity_points,'지속성 점수 /30':ordered.flow_persistence_points,'수급 부분 점수 /70':ordered.flow_points,
    '20일 중 완전 수신 일수':ordered.observed_days_20d,'상대강도 /20':ordered.formal_score_relative_strength.map(lambda v:display_number(v) if pd.notna(v) else '평가 보류'),'거래 활성도 /10':ordered.formal_score_activity.map(lambda v:display_number(v) if pd.notna(v) else '평가 보류'),'종합 강세 점수 /100':ordered.formal_total_score.map(lambda v:display_number(v) if pd.notna(v) else '평가 보류'),'종합 강세 순위':ordered.formal_evaluation_rank.map(lambda v:display_number(v,0) if pd.notna(v) else '평가 보류')})
   st.dataframe(ui.styled(table),hide_index=True,height=420,width='stretch',column_order=[rank_label,'시장','섹터','선택 기간 순매수(억원)','선택 기간 강도(%)','5일 증감(억원)','수급 변화','수급 부분 점수 /70','종합 강세 점수 /100'],column_config={rank_label:st.column_config.NumberColumn('순위',format='%d',width='small'),'시장':st.column_config.TextColumn('시장',width='small'), '선택 기간 순매수(억원)':st.column_config.NumberColumn('순매수(억원)',format='%.2f'),'선택 기간 강도(%)':st.column_config.NumberColumn('시총 대비(%)',format='%.2f'),'5일 증감(억원)':st.column_config.NumberColumn('5일 변화(억원)',format='%.2f')})
   st.download_button('섹터 현황 CSV',csv(table),f'sectors_{asof}_{period_days}d.csv','text/csv',key='sector_download',on_click='ignore')
  with sector_right,st.container(key='sector_context'):
   st.subheader('선택 섹터 수급 추이',anchor=False)
   options=sorted(group_flows.sector.unique()) if theme_mode else (group_flows.market+' · '+group_flows.sector).sort_values().tolist()
   if not options:st.info('이 기준일의 테마 구성 이력이 없습니다.' if theme_mode else '조회 가능한 구성 종목이 없습니다.');st.stop()
   default='KOSPI · 전기·전자' if 'KOSPI · 전기·전자' in options else options[0]
   choice=st.selectbox('테마 선택' if theme_mode else '섹터 선택',options,index=options.index(default))
   group_key=('테마',choice) if theme_mode else tuple(choice.split(' · ',1))
   flow_chart(group_series[group_key],period_days,'sector_flow_chart','')
   st.subheader('선택 섹터 주요 종목',anchor=False)
   group_stocks=searched[searched.symbol.isin(theme_stocks.loc[theme_stocks.theme==choice,'symbol'])] if theme_mode else searched[(searched.market+' · '+searched.sector)==choice]
   ui.major_stocks(group_stocks,period_days)
with main_stock,st.container(key='stock_panel'):
 ui.badges([choice,f'종합 평가 {stocks.total_score.notna().sum():,}종목'])
 if st.session_state.get('previous_group')!=(theme_mode,choice):
  st.session_state['stock_scope']='선택 섹터'
  st.session_state['stock_view']='전체 종목'
 st.session_state['previous_group']=(theme_mode,choice)
 scope=st.selectbox('종목 범위',['선택 섹터','전체 시장'],key='stock_scope')
 selected=searched.copy() if scope=='전체 시장' else (searched[searched.symbol.isin(theme_stocks.loc[theme_stocks.theme==choice,'symbol'])].copy() if theme_mode else searched[(searched.market+' · '+searched.sector)==choice].copy())
 if selected.empty:st.info('검색 조건에 맞는 종목이 없습니다.');st.stop()
 stock_tab,stock_days=period_tabs('stock','stock_sort')
 with stock_tab:
  stock_mode=analysis_mode('stock',['전체 종목','매수세 강화','꾸준한 순매수','자금 이탈'])
  selected=screen_flows(selected,stock_mode,stock_days)
  if selected.empty:st.info('선택 조건을 충족하는 종목이 없습니다. 다른 보기나 기간을 선택하세요.');st.stop()
  sort=st.selectbox('종목 정렬',['누적 순매수 금액','시가총액 대비 순매수 강도','5일 순매수 증감','종합 점수','실적 성장성 점수','수익성·건전성 점수','가치평가 점수','거래 활성도 점수','매출 성장률','영업이익 성장률','ROE','종목명'],key='stock_sort')
  key={'누적 순매수 금액':f'net_{stock_days}d','시가총액 대비 순매수 강도':f'intensity_{stock_days}d','5일 순매수 증감':'change_5d','종합 점수':'total_score','매출 성장률':'revenue_growth','영업이익 성장률':'op_growth','ROE':'roe','종목명':'name','실적 성장성 점수':'score_growth','수익성·건전성 점수':'score_profitability_health','가치평가 점수':'score_valuation','거래 활성도 점수':'score_activity'}[sort]
  stock_ascending=key=='name' or (stock_mode=='자금 이탈' and key in [f'net_{stock_days}d',f'intensity_{stock_days}d','change_5d'])
  selected=selected.sort_values([key,'symbol'],ascending=[stock_ascending,True],na_position='last')
  if sort=='종합 점수' and selected.total_score.isna().all():st.info('종합 점수는 데이터 축적 중입니다. 현재 종목코드 순으로 표시합니다.')
  view=pd.DataFrame({'현재 정렬 순위':selected[key].rank(ascending=stock_ascending,method='min') if key!='name' else np.nan,'수급 변화':selected.change_direction+' · '+selected.change_state,'매수 지속':selected.buying_pattern,'선택 기간 강도(%)':selected[f'intensity_{stock_days}d']*100,'선택 기간 수급 순위':selected[f'net_{stock_days}d'].rank(ascending=False,method='min'),'종목명':selected.name,'선택 기간 순매수(억원)':selected[f'net_{stock_days}d']/1e8,'코드':selected.symbol,'종가(원)':selected.close,'시가총액(억원)':selected.market_cap/1e8,'외국인 순매수(억원)':selected.net_amount/1e8,'5일 순매수(억원)':selected.net_5d/1e8,'20일 순매수(억원)':selected.net_20d/1e8,'직전 5일(억원)':selected.previous_5d/1e8,'5일 증감(억원)':selected.change_5d/1e8,'20일 순매수 일수':selected.positive_days_20d,'5일 수급 전체 순위':selected.net_5d_rank,'20일 수급 전체 순위':selected.net_20d_rank,'5일 강도(%)':selected.intensity_5d*100,'20일 강도(%)':selected.intensity_20d*100,'매출 성장률':selected.revenue_growth.map(lambda v:display_number(v*100,1,'%')),'영업이익 성장률':['흑자전환' if t else display_number(v*100,1,'%') for v,t in zip(selected.op_growth,selected.turnaround)],'ROE':selected.roe.map(lambda v:display_number(v*100,1,'%')),'부채비율':selected.debt_ratio.map(lambda v:display_number(v*100,1,'%')),'PER':selected.per.map(lambda v:display_number(v,1,'배')),'PBR':selected.pbr.map(lambda v:display_number(v,1,'배')),'종합 점수':selected.total_score.map(lambda v:'데이터 축적 중' if pd.isna(v) else f'{v:.1f}'),'업종 내 평가 순위':selected.evaluation_rank.map(lambda v:'—' if pd.isna(v) else f'{v:.0f}'),'확보 배점':selected.available_weight.map(lambda v:f'{int(v)}/100'),'수급 이력':selected.flow_observations.map(lambda v:f'{int(v)}/20일'),'재무 상태':selected.financial_hold.replace('', '확인 완료')})
  st.dataframe(ui.styled(view),hide_index=True,width='stretch',height=420,column_order=['현재 정렬 순위','종목명','선택 기간 순매수(억원)','수급 변화','매수 지속','5일 순매수(억원)','20일 순매수(억원)','5일 증감(억원)','20일 순매수 일수','선택 기간 강도(%)','종합 점수','업종 내 평가 순위'],column_config={'종가(원)':st.column_config.NumberColumn(format='%d'),'외국인 순매수(억원)':st.column_config.NumberColumn(format='%.2f')})
  with st.expander('종목 상세 지표 · 재무·실적·가치평가'):
   st.dataframe(view,hide_index=True,width='stretch')
  st.download_button('선택 종목 CSV',csv(view),f'stocks_{asof}_{stock_days}d.csv','text/csv',key='stocks_download',on_click='ignore')
with st.container(key='detail_panel'):
 heading,selector=st.columns([1,2],vertical_alignment='center')
 with heading:st.subheader('선택 종목 상세 분석',anchor=False)
 with selector:
  labels={r.symbol:f'{r["name"]} · {r.symbol}' for _,r in selected.iterrows()}
  code=st.selectbox('종목 상세정보',selected.symbol.tolist(),format_func=lambda x:labels[x],index=selected.symbol.tolist().index('005930') if '005930' in selected.symbol.values else 0,label_visibility='collapsed')
 r=selected[selected.symbol==code].iloc[0]
 info,chart_area=st.columns([1,3],gap='small')
 with info:
  st.markdown('**종목 정보**')
  st.markdown(f'<div class="stock-name">{html.escape(r["name"])}</div><div class="stock-code">{r.symbol} · {html.escape(r.market)}</div><div class="stock-price">{ui.number(r.close,0)} <small>원</small></div>',unsafe_allow_html=True)
  details={'업종':r.sector,'시가총액':ui.number(r.market_cap/1e8,0)+'억원','가격 기준일':asof,'종합 점수':ui.number(r.total_score,1) if pd.notna(r.total_score) else '평가 보류','업종 내 평가 순위':ui.number(r.evaluation_rank,0)}
  st.markdown('<table class="company-info">'+''.join(f'<tr><td>{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>' for k,v in details.items())+'</table>',unsafe_allow_html=True)
  ui.badges([(r.change_state,'up' if r.change_direction=='강화' else 'down'),r.buying_pattern])
 with chart_area:
  with st.container(key='stock_kpis'):
   kpis=st.columns(4)
   for col,days in zip(kpis[:3],[1,5,20]):
    value=r[f'net_{days}d']
    with col:ui.kpi('1일 순매수' if days==1 else f'{days}일 누적 순매수',ui.number(value/1e8),'억원',ui.tone(value))
   with kpis[3]:ui.kpi('순매수 발생 일수',ui.number(r.positive_days_20d,0),' / 20일')
  detail_days=period_days if st.session_state.workspace_view=='강세 섹터' else stock_days
  flow_chart(stock_series(flow_matrix,code),detail_days,'stock_flow_chart','외국인 순매수 추이')
 financial,score=st.columns([1.35,1],gap='small')
 with financial,st.container(key='financial_panel'):
  st.subheader('재무 · 가치평가',anchor=False)
  financial_fields={'매출액':(r.revenue/1e8,'억원'),'영업이익':(r.operating_profit/1e8,'억원'),'영업현금흐름':(r.ocf/1e8,'억원'),'자본총계':(r.equity/1e8,'억원'),'매출 성장률':(r.revenue_growth*100,'%'),'영업이익 성장률':(r.op_growth*100,'%'),'ROE':(r.roe*100,'%'),'부채비율':(r.debt_ratio*100,'%'),'PER':(r.per,'배'),'PBR':(r.pbr,'배')}
  st.dataframe(pd.DataFrame([{'지표':k,'값':('흑자전환' if k=='영업이익 성장률' and r.turnaround else ui.number(v,1)+unit if pd.notna(v) else '평가 보류')} for k,(v,unit) in financial_fields.items()]),hide_index=True,width='stretch',height=260)
  if pd.notna(r.period_end):
   ui.badges([f'회계기간 {pd.Timestamp(r.period_end):%Y-%m-%d}',f'공시 {pd.Timestamp(r.disclosed_on):%Y-%m-%d}',r.scope])
   st.link_button('DART 원문 공시',f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.receipt}')
  if r.financial_hold:ui.badges([(r.financial_hold,'hold')])
 with score,st.container(key='score_panel'):
  st.subheader('항목별 평가 점수',anchor=False)
  names={'foreign_flow':'외국인 수급','growth':'실적 성장성','profitability_health':'수익성·건전성','valuation':'가치평가','activity':'거래 활성도'}
  rows=[]
  for k,weight in meta['config']['stock_weights'].items():
   v=r['score_'+k];rows.append({'항목':names[k],'배점':weight,'점수':f'{v:.1f}' if pd.notna(v) else '평가 보류'})
  st.dataframe(rows,hide_index=True,width='stretch')
  ui.badges([f'확보 배점 {int(r.available_weight)}/100',('종합 '+ui.number(r.total_score,1) if pd.notna(r.total_score) else '종합 평가 보류','')])
 with st.expander('선택 종목 정보 · 출처 및 테마 근거'):
  st.write(f'종가 출처: {r.price_source} · 기준일 {asof}')
  st.write(f'시가총액 출처: {r.get("market_cap_source","미확보")} · 수신 {r.get("market_cap_observed_at","미확보")}')
  st.write('PER·PBR: 연간 재무, 연결은 지배주주 귀속 손익·자본 / ROE: 총순이익 ÷ 평균 총자본')
  evidence=theme_members(theme_asof);evidence=evidence[evidence.symbol==code]
  if evidence.empty:st.write('테마 근거: 확인 필요')
  for _,item in evidence.iterrows():
   st.markdown(f'**{item.theme}** · {item.rationale}')
   st.write(f'공시일 {item.disclosed_on} · 분류 적용 시작 {item.valid_from}')
   st.write(item.excerpt)
   st.link_button(item.evidence_title,item.evidence_url,key='theme_'+code+'_'+item.theme)
corporate_ui_module.company_panel(code,asof,r.market_cap,theme_asof)
