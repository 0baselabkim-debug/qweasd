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

st.set_page_config(page_title='한국 주식 · 자금 흐름 스크리너',page_icon='◈',layout='wide',initial_sidebar_state='expanded')
st.markdown('''<style>
html,body,[class*="css"]{font-family:"Noto Sans CJK KR",sans-serif}
.block-container{padding-top:2.1rem;padding-bottom:3rem;max-width:1560px}
[data-testid="stHeader"]{background:transparent}
[data-testid="stSidebar"]{background:#eef2f6;border-right:1px solid #e0e7ee}
[data-testid="stMetric"]{background:white;border:1px solid #e1e8ee;border-radius:12px;padding:16px 20px}
[data-testid="stMetricLabel"]{font-size:12px;color:#687c8d}
[data-testid="stMetricValue"]{font-size:27px;color:#152c40;font-weight:650}
h1{font-size:2.05rem!important;letter-spacing:-.05em;font-weight:750!important}
h2{font-size:1.35rem!important;letter-spacing:-.025em;padding-top:.65rem!important}
h3{font-size:1.05rem!important}
.eyebrow{font-size:11px;letter-spacing:.18em;color:#147b72;font-weight:750;margin-bottom:6px}
.lead{color:#627587;font-size:14px;margin:-6px 0 18px}
.chip{display:inline-block;border-radius:5px;padding:5px 9px;font-size:11px;background:#e0f1ec;color:#176c5c;margin-right:7px;margin-bottom:13px}
.chip-wait{background:#fff0d6;color:#94621a}
.note{padding:12px 16px;background:#edf4fa;border-left:3px solid #6b94ba;border-radius:5px;font-size:12px;color:#48647c;margin:14px 0 20px}
.detail-title{font-size:18px;font-weight:700;margin-bottom:4px}.muted{font-size:12px;color:#708396}
[data-testid="stDataFrame"]{border-radius:8px}
</style>''',unsafe_allow_html=True)
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
 st.markdown('### ◈ 자금 흐름 스크리너')
 st.caption('한국 주식 · 실제 데이터 탐색')
 st.caption('일일 수집 → 검증 통과 → 웹 자동 반영. 누락·검증 실패 시 이전 정상 자료를 유지합니다.' if publication.get('automatic_snapshot_sync') else '저장된 실제 자료를 조회합니다. 자동 게시 연결을 검증 중입니다.')
 if publication.get('automatic_snapshot_sync'):
  st.caption('한국시간 20:30 수집 시도 · 미완료분 23:30 재시도. 예약 실행은 지연될 수 있습니다.')
  st.link_button('수집·게시 실행 상태','https://github.com/0baselabkim-debug/qweasd/actions/workflows/daily-dashboard.yml')
 asof=st.selectbox('데이터 기준일',dates(mtime,data_version))
 classification=st.radio('분류체계',['공식 업종','투자 테마'])
 market=st.selectbox('시장',['전체','KOSPI','KOSDAQ'])
 query=st.text_input('종목 검색',placeholder='종목명 또는 6자리 코드')
 st.divider()
 st.markdown('**평가 기준**')
 st.caption('섹터: 수급 40 · 지속성 30\n상대강도 20 · 거래 활성도 10')
 st.caption('종목: 수급 30 · 성장 25\n수익성·건전성 25 · 가치 15 · 거래 5')
 st.caption('점수는 초기 가설입니다.\n같은 시장·업종 안에서 비교합니다.')
 st.divider()
 st.caption('투자 후보 탐색용\n자동 주문·목표주가 예측 없음')
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
st.markdown('<div class="eyebrow">KOREA EQUITY · DAILY FLOW MONITOR</div>',unsafe_allow_html=True)
st.title('오늘의 자금 흐름, 다음 관찰 후보')
st.markdown('<div class="lead">외국인 수급으로 업종을 살펴보고, 공개된 실적으로 기업을 비교합니다.</div>',unsafe_allow_html=True)
history_days=int(stocks.flow_observations.max()) if len(stocks) else 0
complete_flow=int((stocks.flow_observations>=20).sum())
sector_ready=visible_sectors.total_score.notna().any()
status_label='섹터 가설 점수 산출' if sector_ready else '섹터 최종 평가 대기'
st.markdown(f'<span class="chip">실제 수급 {asof}</span><span class="chip">KRX 시장 기준</span><span class="chip chip-wait">{status_label} · 수급 20일 충족 {complete_flow:,}종목</span><span class="chip chip-wait">원천 최종 확정 미검증</span>',unsafe_allow_html=True)
columns=st.columns(4)
columns[0].metric('정상 거래 · 실제 수급',f'{len(stocks):,}종목')
columns[1].metric('공식 업종 매핑',f'{len(sectors[sectors.sector!="업종 미분류"]):,}개',help='시장별 중분류 우선, 없으면 대분류. 종목별 하나의 업종만 사용합니다.')
columns[2].metric('공시일 확인된 연간 재무',f'{stocks.period_end.notna().sum():,}종목',help='2025년 연간 재무와 2024년 비교. 원화 표시, 연결 우선. 실제 DART 공시일이 기준일 이전인 자료만 사용합니다.')
columns[3].metric('20일 수급 충족',f'{complete_flow:,}종목',help='종목별 수급 완전성을 확인합니다. 일부 표본의 이력이 전체 시장 이력으로 표시되지 않습니다.')
columns[3].caption(f'{complete_flow:,} / {len(stocks):,}종목')
krx_count=data['metadata'].get('snapshots',{}).get(asof.replace('-',''),{}).get('official_krx_price_rows',0)
st.markdown('<div class="note">자금 유입 규모와 최근 매수세 변화를 따로 살펴보세요. 수급 순위는 종합 강세 점수나 매수 추천이 아닙니다.</div>',unsafe_allow_html=True)
with st.sidebar.expander('데이터 상태 · 출처'):
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

def period_caption(days):
 dates=flow_matrix.index[-days:]
 period_label='오늘' if days==1 else f'지난 {days}거래일'
 span=f'{dates[0]:%Y-%m-%d} ~ {dates[-1]:%Y-%m-%d}' if len(dates) else '자료 없음'
 st.caption(f'{period_label} · 실제 자료 기간 {span} · 오늘 탭은 선택 기준일 {asof}의 자료입니다. 최근 기간에는 기준일을 포함하며 휴장일은 제외합니다.')
 st.caption('누적 금액·강도는 선택 기간 기준, 변화 상태는 최근 5일과 직전 5일 비교입니다.')


def analysis_mode(prefix,options):
 mode=st.radio('섹터 보기' if prefix=='sector' else '종목 보기',options,horizontal=True,key=prefix+'_view')
 # A mode switch supplies a sensible sort; later manual sorting remains available.
 signature=(mode,st.session_state[prefix+'_period'])
 if st.session_state.get(prefix+'_view_signature')!=signature:
  st.session_state[prefix+'_sort']=('최근 5일 순매수 증감' if prefix=='sector' else '5일 순매수 증감') if mode=='매수세 강화' else '누적 순매수 금액'
 st.session_state[prefix+'_view_signature']=signature
 return mode


st.subheader('① 투자 테마별 자금 흐름' if theme_mode else '① 강세 섹터',anchor=False)
if theme_mode:
 st.caption(f'테마 검증 기준일 {theme_asof} · 가격·수급 기준일 {asof} · 현재 검증 구성으로 과거 수급을 합산합니다.')
 st.info(f'부분 검증 테마 · 현재 조회 대상 {len(visible_stocks):,}종목 중 {theme_stats["unique_members"]:,}종목의 사업 근거를 확인했습니다. 전체 시장의 테마 분류는 아직 완료되지 않았습니다.')
 st.caption(f'사업 근거 확인 종목 {theme_stats["unique_members"]}개 · 중복 제거 순매수 {display_number(theme_stats["unique_net_amount"]/1e8,2)}억원 · 중복 소속 {theme_stats["overlap_count"]}건. 테마별 금액을 더해 전체 유입액으로 표시하지 않습니다.')
 if not theme_stats['unique_members']:st.info('선택한 기준일·시장에는 근거를 확인한 테마 구성 이력이 없습니다.')
 st.caption('테마는 별도 분류입니다. 확인된 구성 이력이 부족해 테마 종합 점수는 보류하며, 종목 점수는 기존 공식 업종 비교 기준을 유지합니다.')
st.caption(f'현재 구성 기준 수급 추이 · {asof} 구성 · 수급 순위와 종합 강세 평가는 별개입니다.')
with st.expander('수급 변화 해석 · 집계 기준'):
 st.write('강화는 최근 5일 순매수 − 직전 5일 순매수가 양수인 경우입니다. 순매수 증가·순매도 축소·순매수 전환을 구분하며, 실제 0원은 중립으로 처리합니다.')
 st.write('꾸준한 순매수: 최근 20일 중 15일 이상 순매수이고 최근·직전 5일 합계가 모두 양수. 관찰용 분류이며 기존 점수에는 반영하지 않습니다.')
 st.write('순매수 개선은 신규 자금 유입이나 향후 주가 상승을 입증하지 않습니다. 현재 구성 기준으로 합산하며 결측은 0원으로 채우지 않습니다. 강도는 보존된 기준일 시총 대비 값입니다. 종가·시총 정합성 문제는 별도로 관리합니다.')
group_flows,group_series=flow_groups(mtime,asof,CONFIG.stat().st_mtime_ns,calculation_version,data_version,market,theme_mode,(Path(__file__).parent/'theme_mapping.json').stat().st_mtime_ns,theme_asof)
sector_tab,period_days=period_tabs('sector','sector_sort')
with sector_tab:
 period_caption(period_days)
 sector_mode=analysis_mode('sector',['자금 유입 상위','매수세 강화','자금 이탈'])
 sector_sort=st.selectbox('섹터 수급 정렬',['누적 순매수 금액','시가총액 대비 순매수 강도','최근 5일 순매수 증감','수급 부분 점수','종합 강세 점수'],key='sector_sort')
 st.caption('정식 종합 점수 순서 · 모든 업종 표시 · 조건 미충족은 평가 보류' if sector_sort=='종합 강세 점수' else {'자금 유입 상위':'선택 기간 합계가 양수인 섹터 · 큰 순매수부터 확인','매수세 강화':'최근 5일 − 직전 5일 > 0 · 순매도 축소도 포함','자금 이탈':'선택 기간 합계가 음수인 섹터 · 순매도 규모가 큰 순서'}[sector_mode])
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
 st.dataframe(table,hide_index=True,height=320,width='stretch',column_order=[rank_label,'시장','섹터','수급 변화','선택 기간 순매수(억원)','5일 증감(억원)','선택 기간 강도(%)','수급 부분 점수 /70','종합 강세 점수 /100'],column_config={c:st.column_config.NumberColumn(format='%.2f') for c in table if any(t in c for t in ['(억원)','강도(%)','점수 /'])})
 with st.expander('섹터 상세 지표 · 5일/20일 비교'):
  st.dataframe(table,hide_index=True,width='stretch')
 st.caption(f'완전한 기간 합계: 5일 {group_flows.net_5d.notna().sum()}/{len(group_flows)}개 · 20일 {group_flows.net_20d.notna().sum()}/{len(group_flows)}개. 구성 종목 중 하나라도 해당 기간 자료가 부족하면 전체 기간 합계를 보류합니다.')
 st.download_button('섹터 현황 CSV',csv(table),f'sectors_{asof}_{period_days}d.csv','text/csv',key='sector_download')
 st.caption(f'수급 부분 점수 70점과 정식 종합 점수 100점은 별도입니다. 실제 날짜별 구성·가격·지수·거래대금 조건 충족 {group_flows.formal_total_score.notna().sum()}/{len(group_flows)}업종. 미확보 배점은 보류합니다.')
 options=sorted(group_flows.sector.unique()) if theme_mode else (group_flows.market+' · '+group_flows.sector).sort_values().tolist()
 if not options:st.info('조회 가능한 구성 종목이 없습니다.');st.stop()
 default='KOSPI · 전기·전자' if 'KOSPI · 전기·전자' in options else options[0]
 choice=st.selectbox('테마 선택' if theme_mode else '섹터 선택',options,index=options.index(default))
 group_key=('테마',choice) if theme_mode else tuple(choice.split(' · ',1))
 flow_chart(group_series[group_key],period_days,'sector_flow_chart',f'선택 섹터 {period_days}거래일 일별·누적 순매수')

st.divider()
st.subheader('② 선택한 테마의 종목' if theme_mode else '② 강세 섹터 내 우수 종목',anchor=False)
st.caption('종가와 당일 수급, 공시된 재무를 비교합니다. 가격은 실시간 현재가가 아닙니다. 미완성 점수는 순위를 부여하지 않습니다.')
st.caption(f'종목 종합 평가 가능 {stocks.total_score.notna().sum():,}개 · 기준일 시가총액 {stocks.market_cap.notna().sum():,}개 · PER {stocks.per.notna().sum():,}개 · PBR {stocks.pbr.notna().sum():,}개. 결측은 빈칸으로 유지하며 0으로 바꾸지 않습니다.')
if st.session_state.get('previous_group')!=(theme_mode,choice):
 st.session_state['stock_scope']='선택 섹터'
 st.session_state['stock_view']='전체 종목'
st.session_state['previous_group']=(theme_mode,choice)
scope=st.selectbox('종목 범위',['선택 섹터','전체 시장'],key='stock_scope')
selected=searched.copy() if scope=='전체 시장' else (searched[searched.symbol.isin(theme_stocks.loc[theme_stocks.theme==choice,'symbol'])].copy() if theme_mode else searched[(searched.market+' · '+searched.sector)==choice].copy())
if selected.empty:st.info('검색 조건에 맞는 종목이 없습니다.');st.stop()
stock_tab,stock_days=period_tabs('stock','stock_sort')
with stock_tab:
 period_caption(stock_days)
 stock_mode=analysis_mode('stock',['전체 종목','매수세 강화','꾸준한 순매수','자금 이탈'])
 selected=screen_flows(selected,stock_mode,stock_days)
 if selected.empty:st.info('선택 조건을 충족하는 종목이 없습니다. 다른 보기나 기간을 선택하세요.');st.stop()
 sort=st.selectbox('종목 정렬',['누적 순매수 금액','시가총액 대비 순매수 강도','5일 순매수 증감','종합 점수','실적 성장성 점수','수익성·건전성 점수','가치평가 점수','거래 활성도 점수','매출 성장률','영업이익 성장률','ROE','종목명'],key='stock_sort')
 key={'누적 순매수 금액':f'net_{stock_days}d','시가총액 대비 순매수 강도':f'intensity_{stock_days}d','5일 순매수 증감':'change_5d','종합 점수':'total_score','매출 성장률':'revenue_growth','영업이익 성장률':'op_growth','ROE':'roe','종목명':'name','실적 성장성 점수':'score_growth','수익성·건전성 점수':'score_profitability_health','가치평가 점수':'score_valuation','거래 활성도 점수':'score_activity'}[sort]
 stock_ascending=key=='name' or (stock_mode=='자금 이탈' and key in [f'net_{stock_days}d',f'intensity_{stock_days}d','change_5d'])
 selected=selected.sort_values([key,'symbol'],ascending=[stock_ascending,True],na_position='last')
 if sort=='종합 점수' and selected.total_score.isna().all():st.info('종합 점수는 데이터 축적 중입니다. 현재 종목코드 순으로 표시합니다.')
 view=pd.DataFrame({'현재 정렬 순위':selected[key].rank(ascending=stock_ascending,method='min') if key!='name' else np.nan,'수급 변화':selected.change_direction+' · '+selected.change_state,'매수 지속':selected.buying_pattern,'선택 기간 강도(%)':selected[f'intensity_{stock_days}d']*100,'선택 기간 수급 순위':selected[f'net_{stock_days}d'].rank(ascending=False,method='min'),'종목명':selected.name,'선택 기간 순매수(억원)':selected[f'net_{stock_days}d']/1e8,'코드':selected.symbol,'종가(원)':selected.close,'시가총액(억원)':selected.market_cap/1e8,'외국인 순매수(억원)':selected.net_amount/1e8,'5일 순매수(억원)':selected.net_5d/1e8,'20일 순매수(억원)':selected.net_20d/1e8,'직전 5일(억원)':selected.previous_5d/1e8,'5일 증감(억원)':selected.change_5d/1e8,'20일 순매수 일수':selected.positive_days_20d,'5일 수급 전체 순위':selected.net_5d_rank,'20일 수급 전체 순위':selected.net_20d_rank,'5일 강도(%)':selected.intensity_5d*100,'20일 강도(%)':selected.intensity_20d*100,'매출 성장률':selected.revenue_growth.map(lambda v:display_number(v*100,1,'%')),'영업이익 성장률':['흑자전환' if t else display_number(v*100,1,'%') for v,t in zip(selected.op_growth,selected.turnaround)],'ROE':selected.roe.map(lambda v:display_number(v*100,1,'%')),'부채비율':selected.debt_ratio.map(lambda v:display_number(v*100,1,'%')),'PER':selected.per.map(lambda v:display_number(v,1,'배')),'PBR':selected.pbr.map(lambda v:display_number(v,1,'배')),'종합 점수':selected.total_score.map(lambda v:'데이터 축적 중' if pd.isna(v) else f'{v:.1f}'),'업종 내 평가 순위':selected.evaluation_rank.map(lambda v:'—' if pd.isna(v) else f'{v:.0f}'),'확보 배점':selected.available_weight.map(lambda v:f'{int(v)}/100'),'수급 이력':selected.flow_observations.map(lambda v:f'{int(v)}/20일'),'재무 상태':selected.financial_hold.replace('', '확인 완료')})
 st.dataframe(view,hide_index=True,width='stretch',height=300,column_order=['현재 정렬 순위','종목명','수급 변화','매수 지속','5일 순매수(억원)','20일 순매수(억원)','5일 증감(억원)','20일 순매수 일수','선택 기간 순매수(억원)','선택 기간 강도(%)','종합 점수','업종 내 평가 순위'],column_config={'종가(원)':st.column_config.NumberColumn(format='%d'),'외국인 순매수(억원)':st.column_config.NumberColumn(format='%.2f')})
 with st.expander('종목 상세 지표 · 재무·실적·가치평가'):
  st.dataframe(view,hide_index=True,width='stretch')
 st.download_button('선택 종목 CSV',csv(view),f'stocks_{asof}_{stock_days}d.csv','text/csv',key='stocks_download')
 labels={r.symbol:f'{r["name"]} · {r.symbol}' for _,r in selected.iterrows()}
 code=st.selectbox('종목 상세정보',selected.symbol.tolist(),format_func=lambda x:labels[x],index=selected.symbol.tolist().index('005930') if '005930' in selected.symbol.values else 0)
 r=selected[selected.symbol==code].iloc[0]
 flow_chart(stock_series(flow_matrix,code),stock_days,'stock_flow_chart',f'선택 종목 {stock_days}거래일 일별·누적 순매수')
 st.caption('수급 정렬은 현재 조회 대상 간 비교입니다. 기존 재무·가치평가 점수와 업종 내 평가 순위는 유지됩니다.')
 with st.container(border=True):
  st.markdown(f'<div class="detail-title">{html.escape(r["name"])} <span class="muted">{r.symbol} · {html.escape(r['sector'])}</span></div>',unsafe_allow_html=True)
  st.caption(f'가격·수급 {asof} / 종가 출처: {r.price_source} / 재무: 2025년 연간, 2024년 비교')
  info,score=st.columns([1.3,1])
  with info:
   st.markdown('**확인 가능한 근거**')
   st.write(f'당일 외국인 {"순매수" if r.net_amount>0 else "순매도" if r.net_amount<0 else "중립"} **{abs(r.net_amount)/1e8:,.2f}억원** · {abs(r.net_quantity):,}주')
   if asof<'2026-10-14':st.caption('과거 수량은 KIS 원문 값입니다. 분할·병합 반영 기준은 미확인이며, 평가에는 수량 대신 순매수 금액을 사용합니다.')
   if pd.notna(r.period_end):
    st.write(f'매출 **{display_number(r.revenue/1e8,0)}억원** · 영업이익 **{display_number(r.operating_profit/1e8,0)}억원**')
    st.write(f'영업현금흐름 **{display_number(r.ocf/1e8,0)}억원** · 자본총계 **{display_number(r.equity/1e8,0)}억원**')
    st.caption(f'회계기간 종료 {pd.Timestamp(r.period_end):%Y-%m-%d} · 실제 공시일 {pd.Timestamp(r.disclosed_on):%Y-%m-%d} · {r.scope} · 원화')
    st.link_button('DART 원문 공시',f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.receipt}')
   else:st.info('공시일·단위·계정이 확인된 재무가 없습니다. 평가 보류입니다.')
   if r.financial_hold:st.caption('평가 보류 사유: '+r.financial_hold)
   cap_source=str(r.get('market_cap_source','미확보'))
   st.caption('시가총액 출처: '+('KIS 공식 시세 · KRX · 억원 단위 원천' if cap_source.startswith('KIS') else cap_source))
   if pd.notna(r.get('market_cap_observed_at')):
    received=pd.Timestamp(r.market_cap_observed_at).tz_convert('Asia/Seoul')
    st.caption(f'시총 수신 시점: {received:%Y-%m-%d %H:%M:%S} KST · 가격 기준일: {asof}')
   if pd.notna(r.period_end):
    valuation_ni=r.get('parent_net_income',np.nan) if r.scope=='CFS' else r.net_income
    valuation_equity=r.get('parent_equity',np.nan) if r.scope=='CFS' else r.equity
    st.caption('PER·PBR 분모: '+('지배주주 귀속' if r.scope=='CFS' else '별도')+f' 순이익 {display_number(valuation_ni/1e8,0)}억원 / 자본 {display_number(valuation_equity/1e8,0)}억원')
   evidence=theme_members(theme_asof);evidence=evidence[evidence.symbol==code]
   with st.expander('투자 테마 소속 · 확인 근거'):
    if evidence.empty:st.write('확인 필요 · 사업 근거를 검토하기 전에는 테마를 임의로 부여하지 않습니다.')
    for _,item in evidence.iterrows():
     st.markdown(f'**{item.theme}** · {item.rationale}')
     st.caption(f'공시일 {item.disclosed_on} · 분류 적용 시작 {item.valid_from}')
     st.write(item.excerpt)
     st.link_button(item.evidence_title,item.evidence_url,key='theme_'+code+'_'+item.theme)
  with score:
   st.markdown('**항목별 확보 점수**')
   names={'foreign_flow':'외국인 수급','growth':'실적 성장성','profitability_health':'수익성·건전성','valuation':'가치평가','activity':'거래 활성도'}
   rows=[]
   for k,weight in meta['config']['stock_weights'].items():
    v=r['score_'+k];rows.append({'항목':names[k],'배점':weight,'점수':f'{v:.1f}' if pd.notna(v) else '데이터 축적 중 / 평가 보류'})
   st.dataframe(rows,hide_index=True,width='stretch')
   st.caption(f'계산 가능한 배점 {int(r.available_weight)}/100 · 부분 점수를 100점으로 환산하지 않습니다.')
corporate_ui_module.company_panel(code,asof,r.market_cap)
with st.expander('평가 방식과 남은 데이터'):
 st.caption('공식 업종의 확인된 적용 시작일은 2026-10-14입니다. 과거 구성에 따른 섹터 종합 평가는 보류합니다. 별도 수급 추이는 화면에 명시한 구성 기준일의 종목 집합을 고정해 과거 순매수를 합산합니다. 과거 날짜 화면의 미분류는 당시 매핑 미확보를 뜻합니다.')
 st.markdown('외국인 수급은 20거래일, 상대강도는 21개 가격 관측, 거래 활성도는 최근 5일과 이전 20일을 사용합니다. 섹터 가격지수는 전일 시가총액 가중·일별 리밸런싱이며, 당시 업종 매핑과 조정주가가 모두 있어야 계산합니다.')
 st.markdown('종목 점수는 같은 시장·공식 업종 내 백분위입니다. 재무 비교는 같은 회계기간·연결/별도 범위끼리 수행하며 최소 3개 비교 기업이 필요합니다. 적자, 자본 0 이하, 영업현금흐름 누락, 금융·보험·리츠·SPAC 등은 평가 보류로 처리합니다.')
 st.markdown('공식 업종 미분류 종목은 별도 표시하며 점수를 부여하지 않습니다. 투자 테마는 공식 업종과 별도로 집계하고 전체 합계는 종목코드로 중복을 제거합니다. 시가총액과 재무가 확보된 종목의 PER·PBR은 실제 값으로 계산합니다. 과거 업종 구성·조정주가·기준지수가 부족하면 상대강도는 보류합니다.')
 st.caption('정상 부재 108개와 상장폐지 5개는 평가 대상에서 제외했습니다. 원천 최종 확정은 미검증이며, 화면은 투자 후보 탐색용입니다.')
