"""Read-only corporate disclosure and actual operations panels."""
import hashlib,json
from pathlib import Path
import pandas as pd
import streamlit as st
from . import corporate
from .operations import read_runs,summarize_runs
ROOT=Path(__file__).resolve().parents[1]

@st.cache_data(show_spinner=False,max_entries=2)
def state(version):return corporate.load_state()

def version():
 p=ROOT/'snapshot/corporate-state.json.gz'
 return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'absent'

def status_panel():
 s=state(version());run=s['runs'][-1] if s['runs'] else {};ops=read_runs();progress=summarize_runs(ops)
 with st.sidebar.expander('공시 자동화 · 운영 검증'):
  count=len({s['filings'][r]['symbol'] for r,j in s['jobs'].items() if j.get('business_status')=='success'})
  st.write(f'사업 내용 조사 {count:,} / 2,430기업')
  st.write(f'재무 버전 {len(s["financials"]):,}건 · 공시별 보존')
  st.caption('사업 후보는 자동 추출합니다. 신규 테마 소속은 근거 검증 후 반영하며, 새 공시에서 기존 검증 문장이 그대로 확인되면 자동 재검증합니다.')
  if run:st.caption(f'최근 공시 수집: {run.get("completed_at")} · 호출 {run.get("calls")}회 · 처리 오류 {len(run.get("failed_jobs",{}))}건')
  if run and 'pending_filings' in run:st.caption(f'해당 조회 범위 대기 공시 {run["pending_filings"]:,}건 · 재무 자료 없음 {len(run.get("unavailable_financials",[]))}건 · 비12월 결산 등 기간 검증 보류 {len(run.get("held_financials",[]))}건')
  st.write(f'신규 거래일 실제 수집·게시 검증 {len(progress["actual_success_days"])}/3거래일')
  st.caption('저장 자료 재실행과 미래 날짜는 성공 거래일에 포함하지 않습니다. 일정은 수집 시도 시각이며 원천 최종 확정 보장은 아닙니다.')
  if ops:st.dataframe(pd.DataFrame(ops)[['trade_date','mode','planned_start_at','actual_job_started_at','finished_at','success_symbols','failed_symbols','published_at','new_trading_day_published']],hide_index=True)
  if run.get('failed_jobs'):st.json(run['failed_jobs'])
  st.link_button('공시 수집 실행 기록','https://github.com/0baselabkim-debug/qweasd/actions/workflows/corporate-refresh.yml')
 return s

def company_panel(symbol,asof,cap,classification_asof=None):
 s=state(version());records=[r for (code,scope),r in corporate.latest_financials(s,asof).items() if code==symbol]
 with st.expander('최신 공시 재무 · 사업 테마 검토'):
  st.caption('기존 2025년 연간 평가 점수는 유지합니다. 최신 분기·반기는 별도 조회하며, 검증된 TTM이 없으면 PER·ROE는 보류합니다. 성장률은 동일 기간의 전년 비교값이 있을 때만 계산합니다.')
  if not records:st.info('이 기준일에 공개된 추가 공시 재무가 없습니다.')
  for r in records:
   st.markdown(f'**{r["period_end"]} · {dict(annual='연간',half='반기',quarter='분기')[r["form"]]} · {r["scope"]} · {'누적 실적' if r['basis']=='YTD' else '연간 실적'}**')
   st.caption(f'실제 공시일 {r["disclosed_on"]} · 수정공시도 별도 접수번호로 보존 · 원화 / PBR 시총 기준일 {asof}')
   fields={'매출액':'revenue','영업이익':'operating_profit','당기순이익':'net_income','자본총계':'equity','부채총계':'liabilities','영업현금흐름':'ocf'}
   def amount(value):return '미확보' if value is None else f'{value:,.0f}'
   rows=[{'항목':name,'해당 기간 금액(원)':amount(r['values'].get(key)),'개별 분기 손익(원)':amount(r.get('standalone_quarter',{}).get(key)) if key in corporate.FLOW_FIELDS and r['basis']=='YTD' else '해당 없음'} for name,key in fields.items()]
   st.dataframe(rows,hide_index=True)
   m=corporate.metrics(r,None if pd.isna(cap) else cap)
   names={'revenue_growth':'전년동기 매출 성장률','op_growth':'전년동기 영업이익 성장률','margin':'영업이익률','roe':'ROE','debt_ratio':'부채비율','per':'PER','pbr':'PBR'}
   st.dataframe([{'지표':name,'값':'평가 보류' if m[k] is None else f'{m[k]:.2f}배' if k in ['per','pbr'] else f'{m[k]*100:.2f}%'} for k,name in names.items()],hide_index=True)
   st.caption(m['valuation_basis']);st.link_button('해당 공시 원문',r['url'],key='latest_disclosure_'+r['receipt']+'_'+r['scope'])
  classification_asof=classification_asof or asof
  st.caption(f'사업 테마 검토 기준일 {classification_asof} · 재무 공개 여부 판단일 {asof}')
  reviews=[r for (code,theme),r in corporate.theme_memberships(s,classification_asof).items() if code==symbol]
  if reviews:
   st.dataframe([{'테마':r['theme'],'상태':{'confirmed':'검증 완료','not_applicable':'해당 없음','removed':'제외','review_required':'검토 필요'}.get(r['status'],r['status']),'공시일':r['disclosed_on'],'검토 근거':r.get('rationale',r.get('reason','')),'원문':r['evidence_url']} for r in reviews],hide_index=True)
  else:st.caption('추가 사업보고서 조사 대기 · 미조사를 해당 없음으로 처리하지 않습니다.')
