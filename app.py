"""Real-data investment candidate explorer. No orders, forecasts, or demo rows."""
from pathlib import Path
import html
import pandas as pd
import numpy as np
import altair as alt
import streamlit as st
from screener.store import DB,load
from screener.scoring import evaluate, CONFIG

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
@st.cache_data(show_spinner=False)
def snapshot(mtime,asof,config_mtime):
 data=load();return data,*evaluate(data,asof)
@st.cache_data(show_spinner=False)
def dates(mtime):
 d=load();return sorted(d['flows'].trade_date.dt.strftime('%Y-%m-%d').unique(),reverse=True)
mtime=DB.stat().st_mtime_ns
with st.sidebar:
 st.markdown('### ◈ 자금 흐름 스크리너')
 st.caption('한국 주식 · 실제 데이터 탐색')
 st.caption('저장된 실제 자료를 조회합니다. 수집 환경과 웹 화면의 데이터 동기화는 별도입니다.')
 asof=st.selectbox('데이터 기준일',dates(mtime))
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
 data,stocks,sectors,meta=snapshot(mtime,asof,CONFIG.stat().st_mtime_ns)
visible_sectors=sectors if market=='전체' else sectors[sectors.market==market]
visible_stocks=stocks if market=='전체' else stocks[stocks.market==market]
searched=visible_stocks[visible_stocks.name.str.contains(query,case=False,regex=False)|visible_stocks.symbol.str.contains(query,regex=False)] if query else visible_stocks
st.markdown('<div class="eyebrow">KOREA EQUITY · DAILY FLOW MONITOR</div>',unsafe_allow_html=True)
st.title('오늘의 자금 흐름, 다음 관찰 후보')
st.markdown('<div class="lead">외국인 수급으로 업종을 살펴보고, 공개된 실적으로 기업을 비교합니다.</div>',unsafe_allow_html=True)
history_days=max(meta['days'].values(),default=0)
sector_ready=sectors.total_score.notna().any()
status_label='가설 점수 산출' if sector_ready else '최종 평가 대기'
st.markdown(f'<span class="chip">실제 수급 {asof}</span><span class="chip">KRX 시장 기준</span><span class="chip chip-wait">{status_label} · 이력 {history_days}일</span><span class="chip chip-wait">원천 최종 확정 미검증</span>',unsafe_allow_html=True)
columns=st.columns(4)
columns[0].metric('정상 거래 · 실제 수급',f'{len(stocks):,}종목')
columns[1].metric('공식 업종 매핑',f'{len(sectors[sectors.sector!="업종 미분류"]):,}개',help='시장별 중분류 우선, 없으면 대분류. 종목별 하나의 업종만 사용합니다.')
columns[2].metric('공시일 확인된 연간 재무',f'{stocks.period_end.notna().sum():,}종목',help='2025년 연간 재무와 2024년 비교. 원화 표시, 연결 우선. 실제 DART 공시일이 기준일 이전인 자료만 사용합니다.')
columns[3].metric('외국인 수급 이력',f'{max(meta["days"].values(),default=0)} / 20일',help='20일 수급·21일 가격·25일 거래대금과 비교 가능한 업종 표본이 필요합니다.')
krx_count=data['metadata'].get('snapshots',{}).get(asof.replace('-',''),{}).get('official_krx_price_rows',0)
note='계산에 필요한 이력이 충족된 항목의 가설 점수를 표시합니다.' if sector_ready else '5·20일 지표는 <b>데이터 축적 중</b>이며 종합 점수와 평가 순위는 아직 산출하지 않습니다.'
st.markdown(f'<div class="note">확인 가능한 당일 순매수와 공개 재무를 표시합니다. {note} 빈 점수의 가중치를 다른 항목에 배분하지 않습니다.</div>',unsafe_allow_html=True)
with st.sidebar.expander('데이터 상태 · 출처'):
 st.write(f'외국인 수급: {len(stocks):,}개 / KIS')
 st.write(f'제외: 거래정지 108개 · 상장폐지 5개')
 st.write(f'공식 업종 미분류: {(stocks.sector=="업종 미분류").sum()}개')
 st.write(f'KRX 공식 당일 시세: {krx_count}개')
 st.caption('KRX 공식 시세 API가 빈 응답을 반환했습니다. 표시 종가는 기존 KIS 수급 응답의 KRX 종가입니다. 시가총액·PER·PBR은 확보 전까지 보류합니다.')
 st.caption(f'매출 계정 확인 {stocks.revenue.notna().sum():,}개 · 영업현금흐름 확인 {stocks.ocf.notna().sum():,}개')
 st.caption('OpenDART: 2025년 연간/2024년 비교. 분기 최신 실적과 구분합니다. 현금흐름 미확보 기업은 건전성 평가를 보류합니다.')
 st.link_button('원 수집 실행 확인','https://github.com/0baselabkim-debug/qweasd/actions/runs/38560757360')

def display_number(v,decimals=1,suffix=''):
 return '—' if pd.isna(v) else f'{v:,.{decimals}f}{suffix}'
def csv(frame):return frame.to_csv(index=False).encode('utf-8-sig')

st.subheader('① 강세 섹터',anchor=False)
st.caption('같은 시장 안의 업종별 가설 점수입니다. 원천 최종 확정 상태를 함께 확인하세요.' if sector_ready else '오늘은 당일 외국인 순매수 순으로 탐색합니다. 강세 종합 순위는 데이터 축적 후 표시됩니다.')
left,right=st.columns([1.55,1],gap='large')
ordered=visible_sectors.sort_values(['total_score','net_amount'] if sector_ready else ['net_amount'],ascending=False,na_position='last').copy()
with left:
 table=pd.DataFrame({'시장':ordered.market,'섹터':ordered.sector,'종목 수':ordered.members,'외국인 순매수(억원)':ordered.net_amount/1e8,'시장 상대강도':ordered.relative_strength.map(lambda v:'데이터 축적 중' if pd.isna(v) else f'{v:+.2%}'),'평가 점수':ordered.total_score.map(lambda v:'데이터 축적 중' if pd.isna(v) else f'{v:.1f}'),'평가 순위':ordered.evaluation_rank.map(lambda v:'—' if pd.isna(v) else f'{v:.0f}')})
 st.dataframe(table,hide_index=True,height=330,width='stretch',column_config={'외국인 순매수(억원)':st.column_config.NumberColumn(format='%.2f')})
 st.download_button('섹터 현황 CSV',csv(table),f'sectors_{asof}.csv','text/csv',key='sector_download')
with right:
 st.markdown('**당일 유입 상위 업종**')
 chart=ordered[ordered.sector!='업종 미분류'].sort_values('net_amount',ascending=False).head(7).copy();chart['업종']=chart.market+' · '+chart.sector;chart['순매수(억원)']=chart.net_amount/1e8
 if not chart.empty:
  bars=alt.Chart(chart).mark_bar(cornerRadiusEnd=4,size=22).encode(x=alt.X('순매수(억원):Q',title='외국인 순매수 · 억원',axis=alt.Axis(grid=True)),y=alt.Y('업종:N',sort='-x',title=None,axis=alt.Axis(labelLimit=220)),color=alt.condition(alt.datum['순매수(억원)']>=0,alt.value('#128579'),alt.value('#869aaf')),tooltip=['업종',alt.Tooltip('순매수(억원):Q',format=',.2f')]).properties(height=270).configure_view(strokeWidth=0)
  st.altair_chart(bars,width='stretch')
 st.caption('당일 유입 규모는 강세 점수나 매수 추천이 아닙니다.')

st.divider()
st.subheader('② 강세 섹터 내 우수 종목',anchor=False)
st.caption('종가와 당일 수급, 공시된 재무를 비교합니다. 가격은 실시간 현재가가 아닙니다. 미완성 점수는 순위를 부여하지 않습니다.')
if krx_count==0:st.caption('KRX 공식 당일 시세 미확보 → 종가는 KIS·KRX 수집값으로 표시, 시가총액·PER·PBR은 평가 보류')
options=(searched.market+' · '+searched.sector).drop_duplicates().sort_values().tolist()
if not options:st.info('검색 조건에 맞는 종목이 없습니다.');st.stop()
default='KOSPI · 전기·전자' if 'KOSPI · 전기·전자' in options else options[0]
a,b=st.columns([2,1])
choice=a.selectbox('섹터 선택',options,index=options.index(default))
sort=b.selectbox('종목 정렬',['당일 외국인 순매수','종합 점수','매출 성장률','영업이익 성장률','ROE','종목명'])
selected=searched[(searched.market+' · '+searched.sector)==choice].copy()
key={'당일 외국인 순매수':'net_amount','종합 점수':'total_score','매출 성장률':'revenue_growth','영업이익 성장률':'op_growth','ROE':'roe','종목명':'name'}[sort]
selected=selected.sort_values([key,'symbol'],ascending=[key=='name',True],na_position='last')
if sort=='종합 점수' and selected.total_score.isna().all():st.info('종합 점수는 데이터 축적 중입니다. 현재 종목코드 순으로 표시합니다.')
view=pd.DataFrame({'종목명':selected.name,'코드':selected.symbol,'종가(원)':selected.close,'외국인 순매수(억원)':selected.net_amount/1e8,'매출 성장률':selected.revenue_growth.map(lambda v:display_number(v*100,1,'%')),'영업이익 성장률':['흑자전환' if t else display_number(v*100,1,'%') for v,t in zip(selected.op_growth,selected.turnaround)],'ROE':selected.roe.map(lambda v:display_number(v*100,1,'%')),'부채비율':selected.debt_ratio.map(lambda v:display_number(v*100,1,'%')),'PER':selected.per.map(lambda v:display_number(v,1,'배')),'PBR':selected.pbr.map(lambda v:display_number(v,1,'배')),'종합 점수':selected.total_score.map(lambda v:'데이터 축적 중' if pd.isna(v) else f'{v:.1f}'),'재무 상태':selected.financial_hold.replace('', '확인 완료')})
st.dataframe(view,hide_index=True,width='stretch',height=300,column_config={'종가(원)':st.column_config.NumberColumn(format='%d'),'외국인 순매수(억원)':st.column_config.NumberColumn(format='%.2f')})
st.download_button('선택 종목 CSV',csv(view),f'stocks_{asof}.csv','text/csv',key='stocks_download')
labels={r.symbol:f'{r["name"]} · {r.symbol}' for _,r in selected.iterrows()}
code=st.selectbox('종목 상세정보',selected.symbol.tolist(),format_func=lambda x:labels[x],index=selected.symbol.tolist().index('005930') if '005930' in selected.symbol.values else 0)
r=selected[selected.symbol==code].iloc[0]
with st.container(border=True):
 st.markdown(f'<div class="detail-title">{html.escape(r["name"])} <span class="muted">{r.symbol} · {html.escape(choice)}</span></div>',unsafe_allow_html=True)
 st.caption(f'가격·수급 {asof} / 종가 출처: {r.price_source} / 재무: 2025년 연간, 2024년 비교')
 info,score=st.columns([1.3,1])
 with info:
  st.markdown('**확인 가능한 근거**')
  st.write(f'당일 외국인 {"순매수" if r.net_amount>=0 else "순매도"} **{abs(r.net_amount)/1e8:,.2f}억원** · {abs(r.net_quantity):,}주')
  if pd.notna(r.revenue):
   st.write(f'매출 **{r.revenue/1e8:,.0f}억원** · 영업이익 **{display_number(r.operating_profit/1e8,0)}억원**')
   st.write(f'영업현금흐름 **{display_number(r.ocf/1e8,0)}억원** · 자본총계 **{display_number(r.equity/1e8,0)}억원**')
   st.caption(f'회계기간 종료 {pd.Timestamp(r.period_end):%Y-%m-%d} · 실제 공시일 {pd.Timestamp(r.disclosed_on):%Y-%m-%d} · {r.scope} · 원화')
   st.link_button('DART 원문 공시',f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.receipt}')
  else:st.info('공시일·단위·계정이 확인된 재무가 없습니다. 평가 보류입니다.')
  if r.financial_hold:st.caption('평가 보류 사유: '+r.financial_hold)
 with score:
  st.markdown('**항목별 확보 점수**')
  names={'foreign_flow':'외국인 수급','growth':'실적 성장성','profitability_health':'수익성·건전성','valuation':'가치평가','activity':'거래 활성도'}
  rows=[]
  for k,weight in meta['config']['stock_weights'].items():
   v=r['score_'+k];rows.append({'항목':names[k],'배점':weight,'점수':f'{v:.1f}' if pd.notna(v) else '데이터 축적 중 / 평가 보류'})
  st.dataframe(rows,hide_index=True,width='stretch')
  st.caption(f'계산 가능한 배점 {int(r.available_weight)}/100 · 부분 점수를 100점으로 환산하지 않습니다.')
with st.expander('평가 방식과 남은 데이터'):
 st.markdown('외국인 수급은 20거래일, 상대강도는 21개 가격 관측, 거래 활성도는 최근 5일과 이전 20일을 사용합니다. 섹터 가격지수는 전일 시가총액 가중·일별 리밸런싱이며, 당시 업종 매핑과 조정주가가 모두 있어야 계산합니다.')
 st.markdown('종목 점수는 같은 시장·공식 업종 내 백분위입니다. 재무 비교는 같은 회계기간·연결/별도 범위끼리 수행하며 최소 3개 비교 기업이 필요합니다. 적자, 자본 0 이하, 영업현금흐름 누락, 금융·보험·리츠·SPAC 등은 평가 보류로 처리합니다.')
 st.markdown('공식 업종 미분류 종목은 별도 표시하며 점수를 부여하지 않습니다. 투자 테마는 이번 화면에 합산하지 않습니다. 현재 시가총액과 기준지수, 가격 이력이 없어 PER·PBR·상대강도는 보류입니다.')
 st.caption('정상 부재 108개와 상장폐지 5개는 평가 대상에서 제외했습니다. 원천 최종 확정은 미검증이며, 화면은 투자 후보 탐색용입니다.')
