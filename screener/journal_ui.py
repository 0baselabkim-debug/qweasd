"""Private journal UI. No credentials or positions are cached globally or written locally."""
import datetime as dt,html,os
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from .journal_store import JournalStore,StoreError,aggregate,valuation,display_decimal
from . import technical,technical_cache,exploration_ui as explorer

SETUP='https://github.com/0baselabkim-debug/qweasd/blob/main/docs/private-journal-setup.md'

def settings():
    def value(name):
        if os.getenv(name):return os.getenv(name)
        try:return st.secrets.get(name,'')
        except (FileNotFoundError,st.errors.StreamlitSecretNotFoundError):return ''
    return value('SUPABASE_URL'),value('SUPABASE_ANON_KEY')

def signed_store():
    url,key=settings();session=st.session_state.get('journal_auth')
    if not url or not key or not session:return None
    try:
        session=JournalStore.refresh(url,key,session);store=JournalStore(url,key,session);st.session_state.journal_auth=session;return store
    except StoreError:
        st.session_state.pop('journal_auth',None);st.session_state['journal_notice']='로그인 유효기간 또는 연결 상태를 확인하고 다시 로그인하세요.';return None

def _finish(message):
    st.session_state['journal_notice']=message;st.session_state['journal_action']=None;st.rerun()

def _dismiss():st.session_state['journal_action']=None

@st.dialog('매수 기록 등록·수정',width='small',on_dismiss=_dismiss)
def purchase_dialog(store,instruments,record=None):
    master=instruments[instruments.market.isin(['KOSPI','KOSDAQ'])].drop_duplicates('symbol');codes=master.symbol.tolist();names=master.set_index('symbol')['name'].to_dict()
    with st.form('purchase_form',clear_on_submit=False):
        code=st.selectbox('종목명 또는 코드 검색',codes,index=codes.index(record['symbol']) if record and record['symbol'] in codes else None,format_func=lambda s:f'{names[s]} ({s})',placeholder='종목명 일부 또는 코드 입력',disabled=bool(record))
        day=st.date_input('매수일',value=dt.date.fromisoformat(record['buy_date']) if record else dt.datetime.now(ZoneInfo('Asia/Seoul')).date(),max_value=dt.datetime.now(ZoneInfo('Asia/Seoul')).date())
        price=st.number_input('평균매수가 (원)',min_value=0.0,max_value=1_000_000_000.0,value=float(record['average_price']) if record else 0.0,step=1.0,format='%.4f')
        quantity=st.number_input('보유수량 (주)',min_value=0,max_value=1_000_000_000,value=int(record['quantity']) if record else 0,step=1)
        a,b=st.columns(2);cancel=a.form_submit_button('취소',width='stretch');save=b.form_submit_button('저장',type='primary',width='stretch')
    if cancel:_finish('입력을 취소했습니다.')
    if save:
        try:
            if not code or code not in names:raise StoreError('종목을 선택하세요.')
            store.save(code,str(day),price,quantity,record)
        except StoreError as e:st.error(str(e));return
        # Purchase table itself is the worker's target list, so queue failure cannot undo a successful save.
        _finish('매수 기록을 저장했습니다.')

@st.dialog('매수 기록 삭제',on_dismiss=_dismiss)
def delete_dialog(store,record,name):
    st.write(f"{name} · {record['buy_date']} · {record['quantity']}주 기록을 삭제합니다.")
    a,b=st.columns(2)
    if a.button('취소',width='stretch'):_finish('삭제를 취소했습니다.')
    if b.button('삭제 확인',type='primary',width='stretch'):
        try:store.delete(record)
        except StoreError as e:st.error(str(e));return
        _finish('선택한 매수 기록을 삭제했습니다.')

def render(instruments):
    if st.session_state.pop('journal_clear_password',False):st.session_state.pop('journal_password',None)
    explorer.reading_layout('journal')
    store=signed_store();a,b=st.columns([5,1],vertical_alignment='center')
    with a:st.subheader('매매현황')
    with b:
        if st.button('+ 추가',type='primary',width='stretch',disabled=store is None):st.session_state['journal_action']=('add',None)
    if st.session_state.get('journal_notice'):st.info(st.session_state.pop('journal_notice'))
    url,key=settings()
    if not url or not key:
        st.info('개인 매매일지 연결 대기 · 인증 및 비공개 영구 저장소를 설정한 뒤 사용할 수 있습니다.')
        st.link_button('무료 Supabase 설정 안내',SETUP)
        empty=pd.DataFrame(columns=['종목명','매수일','보유수량','평균매수가','현재 종가','종가 기준일','평가손익','수익률','매도 가이드'])
        st.dataframe(empty,hide_index=True,width='stretch');return
    if store is None:
        st.write('개인 매매 기록은 로그인한 사용자에게만 표시됩니다.')
        with st.form('journal_login'):
            email=st.text_input('이메일');password=st.text_input('비밀번호',type='password',key='journal_password')
            submit=st.form_submit_button('로그인',type='primary')
        if submit:
            try:
                session=JournalStore.login(url,key,email,password);JournalStore(url,key,session)
                st.session_state.journal_auth=session;st.session_state.journal_clear_password=True;st.rerun()
            except StoreError as e:st.error(str(e))
        return
    if st.button('로그아웃'):
        try:store.logout()
        except StoreError:pass
        for keyname in ['journal_auth','journal_action','journal_password','journal_rows']:st.session_state.pop(keyname,None)
        st.rerun()
    try:records=store.purchases()
    except StoreError as e:st.error(str(e));return
    names=instruments.drop_duplicates('symbol').set_index('symbol')['name'].to_dict();rows=[];details={}
    for position in aggregate(records):
        code=position['symbol'];error=None;bundle=None
        try:
            bundle,error=technical_cache.get_bundle(code,store=store);risk=store.risk(code)
            guide=technical.sell_guide(bundle,position,risk)
            if 'trailing_stop' in guide:store.save_risk(code,guide)
        except StoreError:
            guide={'status':'판정 보류','reasons':['비공개 시세·위험 기준 저장 연결 오류']}
        if error:guide={'status':'판정 보류','reasons':[error,'이전 정상 데이터 보존']}
        close=technical_cache.verified_close(bundle);v=valuation(position,close);details[code]=guide
        rows.append({'종목명':f"{names.get(code,code)} ({code})",'매수일':position['buy_date'],'보유수량':position['quantity'],'평균매수가':display_decimal(position['average_price'])+'원','현재 종가':display_decimal(close)+'원' if close is not None else '검증 대기','종가 기준일':bundle['asof'] if bundle else '미확보','평가손익':display_decimal(v['pnl'])+'원' if v['pnl'] is not None else '미확보','수익률':display_decimal(v['return'])+'%' if v['return'] is not None else '미확보','매도 가이드':guide['status']})
    st.dataframe(pd.DataFrame(rows,columns=['종목명','매수일','보유수량','평균매수가','현재 종가','종가 기준일','평가손익','수익률','매도 가이드']),hide_index=True,width='stretch',row_height=44)
    st.caption('평가손익·수익률은 거래비용 제외. 최신 검증 종가가 없으면 계산 보류. 자동 매도 주문은 실행하지 않습니다.')
    if records:
        with st.expander('개별 매수 기록 수정·삭제 / 매도 판단 근거'):
            selection=st.selectbox('매수 기록 선택',[r['id'] for r in records],format_func=lambda i:next(f"{names.get(r['symbol'],r['symbol'])} ({r['symbol']}) · {r['buy_date']} · {r['quantity']}주 · {display_decimal(r['average_price'])}원" for r in records if r['id']==i))
            record=next(r for r in records if r['id']==selection);a,b=st.columns(2)
            if a.button('수정',width='stretch'):st.session_state['journal_action']=('edit',record)
            if b.button('삭제',width='stretch'):st.session_state['journal_action']=('delete',record)
            st.write(details.get(record['symbol'],{}))
    action=st.session_state.get('journal_action')
    if action:
        if action[0]=='delete':delete_dialog(store,action[1],names.get(action[1]['symbol'],action[1]['symbol']))
        else:purchase_dialog(store,instruments,action[1])

def buy_result(symbol,asof):
    store=signed_store();error=None
    try:
        bundle,error=technical_cache.get_bundle(symbol,asof,store)
        if store:
            # RPC updates at most once per day. Market data is collected only by the scheduled worker.
            request_key=f'technical_request:{symbol}:{asof}'
            if not st.session_state.get(request_key):store.request(symbol);st.session_state[request_key]=True
    except StoreError:bundle=None;error='기술 데이터 연결 오류'
    result=technical.buy_guide(bundle,asof)
    if error:result.update(status='판정 보류',reasons=[error])
    return result,bundle

def buy_label(result):
    label='매수 가이드: '+result['status']+(' ('+' · '.join(result['types'])+')' if result.get('types') else '')
    st.markdown('<div class="buy-guide">'+html.escape(label)+'</div>',unsafe_allow_html=True)

def technical_details(result,bundle):
    with st.expander('매수 가이드 · 기술 지표 근거'):
        st.write('기준일: '+str(result.get('asof') or '미확보')+' · KRX 수정주가 · 규칙 v'+str(technical.rules()['version']))
        for reason in result.get('reasons',[]):st.write(reason)
        st.write('검증된 160거래일 OHLCV·동일 날짜 수급·장마감 확정 근거가 모두 필요합니다.')
        if result.get('checks'):st.json(result['checks'])
        metrics=result.get('indicators') or (bundle.get('computed') if bundle and bundle.get('structure_valid') else None)
        if metrics:
            if result['status']=='판정 보류':st.write('관측 일봉 계산값 · 매매 판정에는 사용하지 않음')
            st.dataframe(pd.DataFrame([{'지표':k,'값':v} for k,v in metrics.items()]),hide_index=True)
        if result.get('levels'):st.json({k:result.get(k) for k in ['levels','entry','stop','target','reward_risk']})
        st.caption('현재 구름대는 26거래일 이전 계산값, 미래 구름대는 현재 계산 후 26거래일 앞에 표시하는 값입니다. 기준은 개발 기본값이며 수익을 보장하지 않습니다.')
        if bundle:st.write(f"수신 일봉 {len(bundle.get('bars',[]))}거래일 · 최종 확정 {bundle.get('finality','미확인')}")
        st.link_button('기술적 판정 규칙', 'https://github.com/0baselabkim-debug/qweasd/blob/main/docs/trading-guidelines.md')
