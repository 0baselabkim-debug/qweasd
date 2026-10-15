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
from screener.flow_trends import screen_flows,stock_series

ROOT=Path(__file__).parent
st.set_page_config(page_title='한국 증시 수급 필터',page_icon='◈',layout='wide',initial_sidebar_state='collapsed')
for module in [indicators,eligibility,macro,ui,corporate_ui,themes,flow_ui]:importlib.reload(module)
ui.install()
st.markdown('''<style>
.st-key-main_navigation button{min-height:46px;font-weight:700}
.st-key-main_navigation button[kind="primary"]{background:linear-gradient(120deg,#287bff,#1856ed)}
.st-key-ui_sector_period [role="radiogroup"] label,.st-key-ui_stock_period [role="radiogroup"] label{padding:8px 20px;border:1px solid #29466d;border-radius:6px;background:#132640}
.st-key-ui_sector_period [role="radiogroup"] label:has(input:checked),.st-key-ui_stock_period [role="radiogroup"] label:has(input:checked){background:#2167ff}
.breadcrumb{font-size:14px;color:#b0caff;margin:7px 0 12px}
</style>''',unsafe_allow_html=True)
ss=st.session_state
for key,value in {'stage':'macro','nav_epoch':0,'preferences':{},'group':None,'symbol':None}.items():
 if key not in ss:ss[key]=value

def go(stage):ss.stage=stage;ss.nav_epoch+=1

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
 st.subheader('조회 조건')
 asof=select('수급 기준일',sorted(data['flows'].trade_date.dt.strftime('%Y-%m-%d').unique(),reverse=True),'asof')
 market=select('시장',['전체','KOSPI','KOSDAQ'],'market')
 classification=radio('분류체계',['공식 업종','투자 테마'],'classification')
 query=st.text_input('종목 검색',key='global_search',placeholder='종목명 또는 코드')
 st.link_button('수집·게시 상태','https://github.com/0baselabkim-debug/qweasd/actions/workflows/daily-dashboard.yml')
 corporate_ui.status_panel()
 with st.expander('데이터 출처 · 계산 기준'):
  st.write('수급: KIS KRX 금액. 현재 확인된 업종 구성으로 과거 수급을 합산합니다. 테마는 검증된 부분 분류이며 중복 소속을 허용합니다. 테마 합계를 전체 시장 합계로 사용하지 않습니다.')
  st.write('거래 활성도: 최근 5거래일 평균 거래대금 / 겹치지 않는 직전 20거래일 평균. 날짜·시장 범위가 불확실한 가격 관측은 사용하지 않습니다.')
  st.write('확산율: 선택 기간 순매수가 양수인 종목 수 / 해당 기간 수급이 완전한 종목 수. 0원도 평가 가능 종목에 포함합니다.')
  st.write('수급 원천 최종 확정 여부와 기존 종가 불일치는 계속 별도 관리합니다. 공시된 연간 재무와 최신 분기 자료를 혼합하지 않습니다.')
  st.write('매크로: 각 공급원이 공표한 일별값. 직전값은 직전 유효 공표값이며 실제 날짜를 함께 표시합니다. 공표 지연과 추후 수정이 가능하며 시장 휴장일은 서로 다릅니다.')

with st.container(key='header'):
 a,b,c=st.columns([6,2,1.7],vertical_alignment='center')
 with a:st.title('한국 증시 수급 필터')
 with b:st.markdown(f'<div class="date-label">수급 기준일 <b>{asof}</b></div>',unsafe_allow_html=True)
 with c:st.link_button('↻ 업데이트 상태','https://github.com/0baselabkim-debug/qweasd/actions',width='stretch')
with st.container(key='main_navigation'):
 a,b=st.columns(2)
 a.button('매크로 현황',type='primary' if ss.stage=='macro' else 'secondary',width='stretch',on_click=go,args=('macro',))
 b.button('전체 섹터',type='primary' if ss.stage!='macro' else 'secondary',width='stretch',on_click=go,args=('sectors',))

if ss.stage=='macro':
 state=macro.read();macro_frames=[]
 for group in ['금리','환율·원자재','미국 증시','한국 증시']:
  st.subheader(group)
  rows=[]
  for key,meta in macro.catalog().items():
   if meta['group']!=group:continue
   r=state.get('indicators',{}).get(key);attempt=state.get('attempts',{}).get(key,{})
   rows.append({'지표':meta['name'],'최신 공표값':r['value'] if r else np.nan,'단위':meta['unit'],'직전 공표값':r['previous_value'] if r else np.nan,'전일 대비':r['change'] if r else np.nan,'변화 단위':'bp' if meta['rate'] else meta['unit'],'등락률(%)':r.get('change_percent') if r else np.nan,'기준일':r['date'] if r else '미확보','직전 기준일':r['previous_date'] if r else '미확보','공급원':r.get('provider',meta['provider']) if r else meta['provider'],'갱신 상태':('공표값 확보' if attempt.get('status')=='success' else '이전 정상 자료 유지') if r else '미확보'})
  frame=pd.DataFrame(rows);macro_frames.append(frame);st.dataframe(frame.drop(columns=['등락률(%)']) if group=='금리' else frame,hide_index=True,width='stretch',column_config={c:st.column_config.NumberColumn(format='%.3f' if group=='금리' and c in ['최신 공표값','직전 공표값'] else '%.2f') for c in ['최신 공표값','직전 공표값','전일 대비','등락률(%)']})
 csv_button('매크로 현황 CSV',pd.concat(macro_frames,ignore_index=True),'macro_download')
 st.subheader('투자자별 당일 순매수')
 rows=[]
 for market_name in ['KOSPI','KOSDAQ']:
  r=state.get('investors',{}).get(market_name,{})
  for investor in ['개인','외국인','기관']:
   rows.append({'시장':market_name,'투자자':investor,'순매수(억원)':r.get('amounts',{}).get(investor,np.nan),'기준일':r.get('date','미확보'),'시장 범위':r.get('scope','미확인'),'상태':'확보' if r else {'unit_scope_pending':'원문 수신 · 단위·시장 범위 확인 대기','missing_credential':'인증정보 미확보','failed':'수집 실패'}.get(state.get('attempts',{}).get('investor_'+market_name,{}).get('status'),'미확보')})
 st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
 with st.expander('매크로 출처 · 공표 기준 · 갱신 결과'):
  for key,meta in macro.catalog().items():
   r=state.get('indicators',{}).get(key,{})
   st.write(meta['name']+' · '+(r.get('basis') or meta['basis'])+' · '+str(r.get('official_unit',meta['unit'])))
   if r.get('source_url'):st.link_button(meta['provider']+' 원문 · '+meta['name'],r['source_url'],key='source_'+key)
  st.write('매크로 수집 시도: '+str(state.get('last_attempt_at','미실행')))
  st.json(state.get('attempts',{}))
  st.link_button('매크로 갱신 기록','https://github.com/0baselabkim-debug/qweasd/actions/workflows/macro-refresh.yml')
 st.stop()

stocks,matrix=stock_data(stamp,asof,version)
visible=stocks if market=='전체' else stocks[stocks.market==market]
theme_mode=classification=='투자 테마'
theme_date=max(asof,str(dt.datetime.now(ZoneInfo('Asia/Seoul')).date())) if asof==str(data['flows'].trade_date.max().date()) else asof
sector_table,sector_series=groups(stamp,asof,version,market,theme_mode,theme_date,corporate_ui.version()+hashlib.sha256((ROOT/'theme_mapping.json').read_bytes()).hexdigest())
if sector_table.empty:st.info('이 기준일에 확인된 구성 이력이 없습니다.');st.stop()
options={(r.market,r.sector):f'{r.market} · {r.sector}' for r in sector_table.itertuples()}
if ss.group not in options:
 ss.group=next(iter(options))
 if ss.stage in ['stocks','detail']:ss.stage='sectors'
group=ss.group
if theme_mode:
 codes=set(themes.members(theme_date).loc[lambda f:f.theme==group[1],'symbol'])
 selected=visible[visible.symbol.isin(codes)].copy()
else:selected=visible[(visible.market==group[0])&(visible.sector==group[1])].copy()
crumb='전체 섹터'+(' > '+options[group] if ss.stage in ['stocks','detail'] else '')
if ss.stage=='detail' and ss.symbol in selected.symbol.values:crumb+=' > '+selected.set_index('symbol').loc[ss.symbol,'name']
st.markdown('<div class="breadcrumb">'+html.escape(crumb)+'</div>',unsafe_allow_html=True)
if ss.stage in ['stocks','detail']:st.button('← 이전 화면',on_click=go,args=('sectors' if ss.stage=='stocks' else 'stocks',))

def choose_group(g):ss.group=g;ss.symbol=None;go('stocks')
def choose_stock(code):ss.symbol=code;go('detail')

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
 event=st.dataframe(ui.styled(frame),key='sector_grid_'+str(ss.nav_epoch),on_select='rerun',selection_mode='single-row',hide_index=True,width='stretch',height=540,column_config={'시장':st.column_config.TextColumn(width='small'),'섹터':st.column_config.TextColumn(width='medium')})
 if event.selection.rows:
  row=ordered.iloc[event.selection.rows[0]];choose_group((row.market,row.sector));st.rerun()
 csv_button('섹터 현황 CSV',frame,'sector_download')
 choices=list(options.values());choice=select('섹터 선택',choices,'sector_fallback',options.get(ss.group))
 st.button('선택 섹터의 종목 보기',on_click=choose_group,args=(next(k for k,v in options.items() if v==choice),))
 st.stop()

if ss.stage=='stocks':
 st.subheader(options[group]+' · 종목 수급')
 days=period('stock');a,b=st.columns([1.4,1])
 with a:mode=radio('종목 보기',['전체 종목','매수세 강화','꾸준한 순매수','자금 이탈'],'stock_view')
 mapping={'순매수 금액':f'net_{days}d','매수세 개선액':'change_5d','시총 대비 순매수 강도':f'intensity_{days}d','순매수 지속일수':'positive_days_20d','거래 활성도':'activity_ratio','평균 거래대금':'average_turnover','종목명':'name'}
 with b:sort=select('종목 정렬',mapping,'stock_sort')
 if query:selected=selected[selected.name.str.contains(query,case=False,regex=False)|selected.symbol.str.contains(query,regex=False)]
 key=mapping[sort];ascending=key=='name' or (mode=='자금 이탈' and key in [f'net_{days}d','change_5d',f'intensity_{days}d'])
 ordered=screen_flows(selected,mode,days).sort_values([key,'symbol'],ascending=[ascending,True],na_position='last').reset_index(drop=True)
 frame=pd.DataFrame({'종목명':ordered.name,'코드':ordered.symbol,'선택 기간 순매수(억원)':ordered[f'net_{days}d']/1e8,'오늘 순매수(억원)':ordered.net_1d/1e8,'5일 순매수(억원)':ordered.net_5d/1e8,'20일 순매수(억원)':ordered.net_20d/1e8,'5일 변화(억원)':ordered.change_5d/1e8,'수급 변화':ordered.change_state,'20일 순매수 발생일':ordered.positive_days_20d,'순매수 강도(%)':ordered[f'intensity_{days}d']*100,'거래 활성도(배)':ordered.activity_ratio,'5일 평균 거래대금(억원)':ordered.average_turnover/1e8,'투자 적격성':ordered.eligibility})
 event=st.dataframe(ui.styled(frame),key='stock_grid_'+str(ss.nav_epoch),on_select='rerun',selection_mode='single-row',hide_index=True,width='stretch',height=540)
 if event.selection.rows:choose_stock(ordered.iloc[event.selection.rows[0]].symbol);st.rerun()
 csv_button('선택 종목 CSV',frame,'stocks_download')
 if len(ordered):
  labels={r.symbol:f'{r["name"]} · {r.symbol}' for _,r in ordered.iterrows()}
  label=select('종목 선택',labels.values(),'stock_fallback');st.button('선택 종목 상세 보기',on_click=choose_stock,args=(next(k for k,v in labels.items() if v==label),))
 else:st.info('선택 조건을 충족하는 종목이 없습니다.')
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
a,b,c=st.columns(3)
a.metric('5일 평균 거래대금',amount(r.average_turnover/1e8));b.metric('거래 활성도',amount(r.activity_ratio,'배'));c.metric('시가총액',amount(r.market_cap/1e8))
st.subheader('투자 적격성 · 재무 판단 근거')
ui.badges([(assessment['status'],'hold'),*assessment['reasons'][:1],('감사의견·중대한 재무 위험 정보 미확보','hold')])
financial={'매출액':(r.revenue/1e8,'억원'),'영업이익':(r.operating_profit/1e8,'억원'),'전년 영업이익':(r.previous_operating_profit/1e8,'억원'),'당기순이익':(r.net_income/1e8,'억원'),'자본총계':(r.equity/1e8,'억원'),'부채총계':(r.liabilities/1e8,'억원'),'영업현금흐름':(r.ocf/1e8,'억원'),'매출 성장률':(r.revenue_growth*100,'%'),'영업이익 성장률':(r.op_growth*100,'%'),'ROE':(r.roe*100,'%'),'부채비율':(r.debt_ratio*100,'%'),'PER':(r.per,'배'),'PBR':(r.pbr,'배')}
rows=[{'항목':k,'실제 값':('흑자전환' if k=='영업이익 성장률' and r.turnaround else amount(v,u)),'기준':'공시된 동일 연간 기간'} for k,(v,u) in financial.items()]
rows += [{'항목':'자본잠식 여부','실제 값':'판정 보류','기준':'자본금 미확보 · 자본총계만으로 일부 자본잠식 여부 추정 안 함'},{'항목':'감사의견','실제 값':'미확보','기준':'공식 감사의견 확인 필요'},{'항목':'중대한 재무 위험','실제 값':'미확보','기준':'미확보를 위험 없음으로 판단하지 않음'}]
st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
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
