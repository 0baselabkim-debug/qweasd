"""Actual-data exploration: macro → sectors → stocks → financial facts. No composite scores."""
import datetime as dt
import hashlib,html,importlib,json
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
import streamlit as st
from screener.store import load,DB
import screener.indicators as indicators
import screener.eligibility as eligibility
import screener.macro as macro
import screener.dashboard_style as ui
import screener.corporate_ui as corporate_ui
import screener.themes as themes
import screener.flow_ui as flow_ui
import screener.exploration_ui as explorer
from screener.flow_trends import screen_flows,stock_series

ROOT=Path(__file__).parent
st.set_page_config(page_title='한국 증시 수급 필터',page_icon='◈',layout='wide',initial_sidebar_state='expanded')
for module in [indicators,eligibility,macro,ui,corporate_ui,themes,flow_ui,explorer]:importlib.reload(module)
ui.install()
explorer.install()
st.markdown('''<style>
.st-key-main_navigation button{min-height:46px;font-weight:700}
.st-key-main_navigation button[kind="primary"]{background:linear-gradient(120deg,#287bff,#1856ed)}
.st-key-ui_sector_period [role="radiogroup"] label,.st-key-ui_stock_period [role="radiogroup"] label{padding:8px 20px;border:1px solid #29466d;border-radius:6px;background:#132640}
.st-key-ui_sector_period [role="radiogroup"] label:has(input:checked),.st-key-ui_stock_period [role="radiogroup"] label:has(input:checked){background:#2167ff}
.breadcrumb{font-size:14px;color:#b0caff;margin:7px 0 12px}
</style>''',unsafe_allow_html=True)
ss=st.session_state
for key,value in {'stage':'macro','nav_epoch':0,'preferences':{},'group':None,'symbol':None,'sector_open':False,'stock_open':False}.items():
 if key not in ss:ss[key]=value

def go(stage):explorer.navigate(ss,stage)
def choose_group(g):explorer.navigate(ss,'sector',g)
def choose_stock(code):explorer.navigate(ss,'stock',code)

def remember(key):ss.preferences[key]=ss['ui_'+key]
def select(label,options,key,default=None):
 options=list(options);value=ss.preferences.get(key,default if default is not None else options[0])
 if value not in options:value=options[0]
 ss['ui_'+key]=value
 return st.selectbox(label,options,key='ui_'+key,on_change=remember,args=(key,))
def radio(label,options,key,default=None):
 value=ss.preferences.get(key,default if default is not None else options[0])
 if value not in options:value=options[0]
 ss['ui_'+key]=value
 return st.radio(label,options,horizontal=True,key='ui_'+key,on_change=remember,args=(key,))
def period(section):return {'오늘(1일)':1,'지난 5일':5,'지난 20일':20}[radio('기간',['오늘(1일)','지난 5일','지난 20일'],section+'_period')]
def csv_button(label,frame,key):st.download_button(label,frame.to_csv(index=False).encode('utf-8-sig'),key+'.csv','text/csv',key=key,on_click='ignore')
def amount(value,unit='억원',decimals=2):return '미확보' if pd.isna(value) else f'{value:,.{decimals}f}{unit}'

version=hashlib.sha256(b''.join((ROOT/p).read_bytes() for p in ['screener/indicators.py','screener/eligibility.py','screener/flow_trends.py','eligibility_rules.json'])).hexdigest()
@st.cache_data(show_spinner=False,max_entries=2)
def raw_data(stamp):return load()
@st.cache_data(show_spinner=False,max_entries=3)
def stock_data(stamp,asof,version):
 data=raw_data(stamp);x,matrix=indicators.universe(data,asof);x=indicators.stock_activity(data,x,asof)
 x['eligibility']=[eligibility.assess(r)['status'] for r in x.to_dict('records')]
 return x,matrix
@st.cache_data(show_spinner=False,max_entries=8)
def groups(stamp,asof,version,market,theme_mode,theme_date,theme_version):
 data=raw_data(stamp);stocks,matrix=stock_data(stamp,asof,version)
 stocks=stocks if market=='전체' else stocks[stocks.market==market]
 return indicators.sector_indicators(data,stocks,matrix,asof,themes.members(theme_date) if theme_mode else None)

stamp=DB.stat().st_mtime_ns;data=raw_data(stamp)
with st.sidebar:
 st.markdown('<div class="sidebar-title">한국 증시 수급 필터</div>',unsafe_allow_html=True)
 with st.container(key='sidebar_menu'):
  st.button('매크로 지표',type='primary' if ss.stage=='macro' else 'secondary',width='stretch',on_click=go,args=('macro',))
  st.button('수급 필터',type='primary' if ss.stage!='macro' else 'secondary',width='stretch',on_click=go,args=('sectors',))
 st.divider()
 st.subheader('조회 조건')
 asof=select('수급 기준일',sorted(data['flows'].trade_date.dt.strftime('%Y-%m-%d').unique(),reverse=True),'asof')
 market=select('시장',['전체','KOSPI','KOSDAQ'],'market')
 classification=radio('분류체계',['공식 업종','투자 테마'],'classification')
if ss.stage!='macro':
 with st.container(key='header'):
  a,b=st.columns([4,1.5],vertical_alignment='center')
  with a:st.markdown(f'<div class="date-label">수급 기준일 <b>{asof}</b></div>',unsafe_allow_html=True)
  with b:st.link_button('↻ 업데이트 상태','https://github.com/0baselabkim-debug/qweasd/actions',width='stretch')

if ss.stage=='macro':
 explorer.reading_layout('macro')
 state=macro.read();macro_frames=[]
 with st.container(key='macro_cards'):
  cards=[explorer.macro_card_html('증시 지수',explorer.macro_rows(state,macro.catalog(),'미국 증시')+explorer.macro_rows(state,macro.catalog(),'한국 증시'))]
  rows=[]
  for market_name in ['KOSPI','KOSDAQ']:
   r=state.get('investors',{}).get(market_name,{})
   for investor in ['개인','외국인','기관']:
    value=r.get('amounts',{}).get(investor)
    market_cell='<td class="investor-market" rowspan="3">'+market_name+'</td>' if investor=='개인' else ''
    rows.append('<tr>'+market_cell+'<td class="investor-name">'+investor+'</td><td class="num">'+('미확보' if value is None else f'{value:+,.2f}')+'</td></tr>')
  cards.append('<section class="macro-card"><div class="macro-card-head"><h3>투자자별 거래 동향</h3></div><div class="mini-scroll investor-table"><table class="mini-table"><thead><tr><th>시장</th><th>투자자</th><th class="num">당일 순매수(억원)</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div></section>')
  for group,title in [('금리','금리'),('환율·원자재','기타 경제지표')]:
   cards.append(explorer.macro_card_html(title,explorer.macro_rows(state,macro.catalog(),group)))
  st.markdown('<div class="macro-card-grid">'+''.join(cards)+'</div>',unsafe_allow_html=True)
  for key,meta in macro.catalog().items():
   r=state.get('indicators',{}).get(key,{})
   macro_frames.append({'지표':meta['name'],'최신 공표값':r.get('value'),'단위':meta['unit'],'직전 공표값':r.get('previous_value'),'전일 대비':r.get('change'),'변화 단위':r.get('change_unit'),'등락률(%)':r.get('change_percent'),'기준일':r.get('date'),'직전 기준일':r.get('previous_date'),'공급원':r.get('provider',meta['provider']),'갱신 상태':('공표값 확보' if state.get('attempts',{}).get(key,{}).get('status')=='success' else '이전 정상 자료 유지') if r else '미확보'})
 csv_button('매크로 현황 CSV',pd.DataFrame(macro_frames),'macro_download')
 st.stop()

stocks,matrix=stock_data(stamp,asof,version)
visible=stocks if market=='전체' else stocks[stocks.market==market]
theme_mode=classification=='투자 테마'
theme_date=max(asof,str(dt.datetime.now(ZoneInfo('Asia/Seoul')).date())) if asof==str(data['flows'].trade_date.max().date()) else asof
sector_table,sector_series=groups(stamp,asof,version,market,theme_mode,theme_date,corporate_ui.version()+hashlib.sha256((ROOT/'theme_mapping.json').read_bytes()).hexdigest())
if sector_table.empty:st.info('이 기준일에 확인된 구성 이력이 없습니다.');st.stop()
options={(r.market,r.sector):f'{r.market} · {r.sector}' for r in sector_table.itertuples()}
if ss.group not in options:
 ss.group=next(iter(options));ss.sector_open=False;ss.stock_open=False;ss.symbol=None
 if ss.stage in ['stocks','detail']:ss.stage='sectors'
group=ss.group
if theme_mode:
 codes=set(themes.members(theme_date).loc[lambda f:f.theme==group[1],'symbol'])
 selected=visible[visible.symbol.isin(codes)].copy()
else:selected=visible[(visible.market==group[0])&(visible.sector==group[1])].copy()
if ss.stock_open and ss.symbol not in selected.symbol.values:
 ss.stock_open=False;ss.symbol=None
 if ss.stage=='detail':ss.stage='stocks'
explorer.reading_layout(ss.stage)
with st.container(horizontal=True,key='hierarchy'):
 st.button('전체섹터 조회',key='tab_all',type='primary' if ss.stage=='sectors' else 'secondary',on_click=go,args=('sectors',))
 if ss.sector_open:
  with st.container(horizontal=True,width='content',key='hierarchy_sector'):
   st.button(group[1],key='tab_sector',help=options[group],type='primary' if ss.stage=='stocks' else 'secondary',on_click=go,args=('stocks',))
   st.button('×',key='close_sector',help='섹터 탭 닫기',on_click=go,args=('close_sector',))
 if ss.stock_open:
  with st.container(horizontal=True,width='content',key='hierarchy_stock'):
   st.button(str(selected.set_index('symbol').loc[ss.symbol,'name']),key='tab_stock',help=ss.symbol,type='primary' if ss.stage=='detail' else 'secondary',on_click=go,args=('detail',))
   st.button('×',key='close_stock',help='종목 탭 닫기',on_click=go,args=('close_stock',))

if ss.stage=='sectors':
 st.subheader('전체 섹터 수급')
 ui.badges(['현재 구성 기준 수급',('검증된 부분 테마 · 중복 소속 가능' if theme_mode else f'공식 업종 · 구성 {asof}','')])
 days=period('sector');a,b=st.columns([1.3,1])
 with a:mode=radio('섹터 보기',['자금 유입 상위','매수세 강화','자금 이탈','전체 섹터'],'sector_view')
 mapping={'순매수 금액':f'net_{days}d','매수세 개선액':'change_5d','시총 대비 순매수 강도':f'intensity_{days}d','자금 확산율':f'breadth_{days}d','순매수 지속일수':'positive_days_20d','거래 활성도':'activity_ratio'}
 with b:sort=select('섹터 정렬',mapping,'sector_sort')
 key=mapping[sort];ascending=mode=='자금 이탈' and key in [f'net_{days}d','change_5d',f'intensity_{days}d']
 ordered=screen_flows(sector_table,mode,days).sort_values([key,'market','sector'],ascending=[ascending,True,True],na_position='last').reset_index(drop=True)
 frame=pd.DataFrame({'시장':ordered.market,'섹터':ordered.sector,'선택 기간 순매수(억원)':ordered[f'net_{days}d']/1e8,'오늘 순매수(억원)':ordered.net_1d/1e8,'5일 순매수(억원)':ordered.net_5d/1e8,'20일 순매수(억원)':ordered.net_20d/1e8,'5일 변화(억원)':ordered.change_5d/1e8,'수급 변화':ordered.change_state,'확산율(%)':ordered[f'breadth_{days}d'],'순매수 종목':ordered[f'positive_{days}d'],'평가 가능 종목':ordered[f'evaluable_{days}d'],'전체 대상':ordered.members,'20일 순매수 발생일':ordered.positive_days_20d,'거래 활성도(배)':ordered.activity_ratio,'순매수 강도(%)':ordered[f'intensity_{days}d']*100})
 ids=[json.dumps([r.market,r.sector],ensure_ascii=False) for r in ordered.itertuples()]
 event=explorer.table(frame,ids,'섹터','sector_grid_'+str(ss.nav_epoch),f'sectors:{asof}:{market}:{classification}:{days}:{sort}:{mode}')
 if event and event.get('interaction') in ['double_click','touch','keyboard'] and event.get('id') in ids:
  choose_group(json.loads(event['id']));st.rerun()
 csv_button('섹터 현황 CSV',frame,'sector_download')
 st.stop()

if ss.stage=='stocks':
 st.subheader(options[group]+' · 종목 수급')
 days=period('stock');a,b=st.columns([1.4,1])
 with a:mode=radio('종목 보기',['전체 종목','매수세 강화','꾸준한 순매수','자금 이탈'],'stock_view')
 mapping={'순매수 금액':f'net_{days}d','매수세 개선액':'change_5d','시총 대비 순매수 강도':f'intensity_{days}d','순매수 지속일수':'positive_days_20d','거래 활성도':'activity_ratio','평균 거래대금':'average_turnover','종목명':'name'}
 with b:sort=select('종목 정렬',mapping,'stock_sort')
 key=mapping[sort];ascending=key=='name' or (mode=='자금 이탈' and key in [f'net_{days}d','change_5d',f'intensity_{days}d'])
 ordered=screen_flows(selected,mode,days).sort_values([key,'symbol'],ascending=[ascending,True],na_position='last').reset_index(drop=True)
 frame=pd.DataFrame({'종목명':ordered.name,'코드':ordered.symbol,'선택 기간 순매수(억원)':ordered[f'net_{days}d']/1e8,'오늘 순매수(억원)':ordered.net_1d/1e8,'5일 순매수(억원)':ordered.net_5d/1e8,'20일 순매수(억원)':ordered.net_20d/1e8,'5일 변화(억원)':ordered.change_5d/1e8,'수급 변화':ordered.change_state,'20일 순매수 발생일':ordered.positive_days_20d,'순매수 강도(%)':ordered[f'intensity_{days}d']*100,'거래 활성도(배)':ordered.activity_ratio,'5일 평균 거래대금(억원)':ordered.average_turnover/1e8,'투자 적격성':ordered.eligibility})
 ids=ordered.symbol.tolist()
 event=explorer.table(frame,ids,'종목명','stock_grid_'+str(ss.nav_epoch),f'stocks:{asof}:{market}:{classification}:{group}:{days}:{sort}:{mode}')
 if event and event.get('interaction') in ['double_click','touch','keyboard'] and event.get('id') in ids:
  choose_stock(event['id']);st.rerun()
 csv_button('선택 종목 CSV',frame,'stocks_download')
 st.stop()

if ss.symbol not in selected.symbol.values:st.info('현재 조건에서 선택 종목을 조회할 수 없습니다.');st.stop()
r=selected[selected.symbol==ss.symbol].iloc[0];assessment=eligibility.assess(r)
st.subheader(r['name']+' · '+r.symbol)
ui.badges([r.market,r.sector,('투자 적격성: '+assessment['status'],'hold'),f'종가 {ui.number(r.close,0)}원'])
with st.container(key='stock_kpis'):
 cols=st.columns(4)
 for col,days in zip(cols[:3],[1,5,20]):
  with col:ui.kpi(f'{days}일 '+('순매수' if days==1 else '누적 순매수'),ui.number(r[f'net_{days}d']/1e8),'억원',ui.tone(r[f'net_{days}d']))
 with cols[3]:ui.kpi('20일 순매수 발생일',ui.number(r.positive_days_20d,0),'일')
flow_ui.chart(stock_series(matrix,r.symbol),20,'stock_flow_chart','외국인 순매수 추이 · 최근 20거래일')
with st.container(key='detail_metrics'):
 a,b,c=st.columns(3)
 a.metric('5일 평균 거래대금',amount(r.average_turnover/1e8));b.metric('거래 활성도',amount(r.activity_ratio,'배'));c.metric('시가총액',amount(r.market_cap/1e8))
st.subheader('투자 적격성 · 재무 판단 근거')
ui.badges([(assessment['status'],'hold'),*assessment['reasons'][:1],('감사의견·중대한 재무 위험 정보 미확보','hold')])
financial={'매출액':(r.revenue/1e8,'억원'),'영업이익':(r.operating_profit/1e8,'억원'),'전년 영업이익':(r.previous_operating_profit/1e8,'억원'),'당기순이익':(r.net_income/1e8,'억원'),'자본총계':(r.equity/1e8,'억원'),'부채총계':(r.liabilities/1e8,'억원'),'영업현금흐름':(r.ocf/1e8,'억원'),'매출 성장률':(r.revenue_growth*100,'%'),'영업이익 성장률':(r.op_growth*100,'%'),'ROE':(r.roe*100,'%'),'부채비율':(r.debt_ratio*100,'%'),'PER':(r.per,'배'),'PBR':(r.pbr,'배')}
rows=[{'항목':k,'실제 값':('흑자전환' if k=='영업이익 성장률' and r.turnaround else amount(v,u)),'기준':'공시된 동일 연간 기간'} for k,(v,u) in financial.items()]
rows += [{'항목':'자본잠식 여부','실제 값':'판정 보류','기준':'자본금 미확보 · 자본총계만으로 일부 자본잠식 여부 추정 안 함'},{'항목':'감사의견','실제 값':'미확보','기준':'공식 감사의견 확인 필요'},{'항목':'중대한 재무 위험','실제 값':'미확보','기준':'미확보를 위험 없음으로 판단하지 않음'}]
st.dataframe(pd.DataFrame(rows),hide_index=True,width='content',row_height=44,column_config={'항목':st.column_config.TextColumn(width=170),'실제 값':st.column_config.TextColumn(width=190,alignment='right'),'기준':st.column_config.TextColumn(width=260)})
if pd.notna(r.period_end):
 ui.badges([f'회계기간 {pd.Timestamp(r.period_end):%Y-%m-%d}',f'실제 공시 {pd.Timestamp(r.disclosed_on):%Y-%m-%d}',r.scope])
 st.link_button('연간 DART 원문',f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.receipt}')
corporate_ui.company_panel(r.symbol,asof,r.market_cap,theme_date)
st.subheader('실제 사업 내용 · 투자 테마')
evidence=themes.members(theme_date);evidence=evidence[evidence.symbol==r.symbol]
if evidence.empty:st.write('검증된 테마 소속 없음 · 사업 근거 확인 필요')
for i,item in evidence.iterrows():
 with st.expander(item.theme+' · 검증된 사업 근거'):
  st.write(item.rationale);st.write(item.excerpt)
  st.write(f'공시일 {item.disclosed_on} · 분류 적용 {item.valid_from}')
  st.link_button(item.evidence_title,item.evidence_url,key='evidence_'+str(i))
with st.expander('추가 사업보고서 조사 기록'):
 state=corporate_ui.state(corporate_ui.version());reviews=[v for v in state['reviews'].values() if v['symbol']==r.symbol and v['disclosed_on']<=theme_date]
 if not reviews:st.write('추가 조사 대기')
 for review_index,review in enumerate(reviews[-12:]):
  st.write(review['theme']+' · '+review['status']+' · '+review.get('reason',review.get('rationale','')))
  for excerpt in review.get('excerpts',[])[:2]:st.write(excerpt)
  st.link_button('사업보고서 원문 '+review['receipt'],review['evidence_url'],key='review_'+str(review_index))
with st.expander('상세 출처 · 판정 규칙'):
 st.write('종가 출처: '+str(r.price_source));st.write('시총 출처: '+str(r.get('market_cap_source','미확보')))
 st.write('거래 활성도는 동일한 KRX 범위와 25거래일 완전성을 확인한 경우만 산출합니다. 기존 가격 정합성 문제는 수급 금액 집계와 분리합니다.')
 st.write('PER/PBR은 연간 손익·자본 기준, 연결이면 지배주주 지분 기준. ROE는 총순이익/평균 총자본. 최신 분기 자료와 혼합하지 않습니다.')
 st.write(assessment['reasons']);st.json(json.loads((ROOT/'eligibility_rules.json').read_text()))
