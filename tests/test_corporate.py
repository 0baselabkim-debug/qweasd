"""All fabricated statements stay in fixtures; never written to snapshot/."""
import copy,datetime as dt,importlib.util,json
from pathlib import Path
import pytest
from screener.corporate import filing_record,financial_record,business_section,business_candidates,metrics,apply_review,latest_financials,load_state,save_state,build_database

def filing(receipt='20260814000001'):
 return filing_record({'report_nm':'반기보고서 (2026.06)','rcept_no':receipt,'corp_code':'00000001','rcept_dt':'20260814'},'000001')

def response():
 f=filing();rows=[]
 for field,account,sj,now,ytd,prior in [('매출액','ifrs-full_Revenue','IS','40','70','50'),('영업이익','dart_OperatingIncomeLoss','IS','4','7','5'),('영업활동현금흐름','ifrs-full_CashFlowsFromUsedInOperatingActivities','CF','-3',None,None),('자본총계','ifrs-full_Equity','BS','100',None,None)]:
  rows.append({'rcept_no':f['receipt'],'reprt_code':'11012','bsns_year':'2026','sj_div':sj,'account_nm':field,'account_id':account,'account_detail':'-',
               'thstrm_amount':now,'thstrm_add_amount':ytd,'frmtrm_add_amount':prior,'frmtrm_q_amount':'-2','frmtrm_amount':'90','currency':'KRW'})
 return {'status':'000','list':rows}

def test_interim_cumulative_and_standalone_never_mixed():
 r=financial_record(response(),filing(),'CFS')
 assert r['values']['revenue']==70 and r['standalone_quarter']['revenue']==40
 assert r['values']['ocf']==-3 and r['prior_values']['ocf']==-2
 m=metrics(r,1000);assert m['revenue_growth']==pytest.approx(.4) and m['per'] is None and m['roe'] is None
 d=response();d['list'][0]['thstrm_add_amount']=None
 assert financial_record(d,filing(),'CFS')['values']['revenue'] is None

@pytest.mark.parametrize('field,value',[('rcept_no','20260815000001'),('reprt_code','11011'),('bsns_year','2025'),('currency','USD')])
def test_statement_identity_required(field,value):
 d=response();d['list'][0][field]=value
 with pytest.raises(ValueError):financial_record(d,filing(),'CFS')

def test_business_keywords_are_only_candidates_and_missing_not_none():
 text='당사는 반도체 공정 장비를 제조 판매합니다. '+('실제 사업과 매출 설명입니다. '*30)
 xml='<TITLE>II. 사업의 내용</TITLE><P>'+text+'</P><TITLE>III. 재무에 관한 사항</TITLE>원전'
 section=business_section(xml);assert '원전' not in section
 rows=business_candidates(section,filing(),'hash')
 assert all(r['status']=='review_required' for r in rows)
 assert next(r for r in rows if r['theme']=='반도체')['excerpts']
 assert not next(r for r in rows if r['theme']=='원전')['excerpts']
 with pytest.raises(ValueError):business_section('<P>사업의 내용 키워드만 있음</P>')

def test_revision_history_and_disclosure_asof(tmp_path):
 state=load_state(tmp_path/'absent');a=financial_record(response(),filing(),'CFS')
 b=copy.deepcopy(a);b.update(receipt='20261016000001',disclosed_on='2026-10-16');b['values']['revenue']=80
 state['financials']={'old':a,'new':b};assert latest_financials(state,'2026-10-14')['000001','CFS']['values']['revenue']==70
 assert latest_financials(state,'2026-10-16')['000001','CFS']['values']['revenue']==80
 state['filings']={a['receipt']:filing(),b['receipt']:{**filing(),**{k:b[k] for k in ['receipt','disclosed_on']}}}
 save_state(state,tmp_path/'state.gz');assert load_state(tmp_path/'state.gz')==state
 build_database(state,tmp_path/'corporate.duckdb');assert (tmp_path/'corporate.duckdb').exists()

def test_theme_confirmation_requires_exact_evidence_and_keeps_event():
 r=business_candidates('당사는 반도체 공정 장비를 제조 판매하며 반도체 장비 매출을 인식합니다.',filing(),'hash')[2]
 state={'reviews':{r['receipt']+'_반도체':r},'events':[]}
 decision={'symbol':'000001','theme':'반도체','receipt':r['receipt'],'status':'confirmed','reviewer':'fixture','reviewed_on':'2026-10-15','rationale':'test','excerpt':'없는 증거를 충분히 긴 문장으로 만들어 주장하는 경우입니다.'}
 with pytest.raises(ValueError):apply_review(state,decision)
 decision['excerpt']=r['excerpts'][0];apply_review(state,decision)
 assert state['events'][0]['before']['status']=='review_required'
 assert state['reviews'][r['receipt']+'_반도체']['status']=='confirmed'

def test_market_replay_and_skip_do_not_count_as_three_day_operation():
 from scripts.record_market_run import make_record
 env={'DAY':'20261014','GITHUB_RUN_ID':'1','MODE':'verify_saved'}
 ledger={'2026-10-14':{'run_id':'1','mode':'verify_saved','published_at_utc':'2026-10-15'}}
 b={'status':'complete','records':{'000001':{}},'failures':{},'excluded':{}}
 assert not make_record(env,b,ledger)['new_trading_day_published']
 env['MODE']='collect';ledger['2026-10-14']['mode']='collect';assert make_record(env,b,ledger)['new_trading_day_published']
 env['GITHUB_RUN_ID']='2';assert not make_record(env,b,ledger)['new_trading_day_published']

def test_existing_financial_versions_cannot_be_changed():
 from scripts.publish_corporate import validate
 old={'filings':{},'financials':{'x':{'value':1}},'events':[]}
 new=copy.deepcopy(old);new['financials']['x']['value']=2
 with pytest.raises(ValueError,match='existing_financials_changed'):validate(old,new)

def test_three_day_gate_counts_distinct_actual_dates_only():
 from screener.operations import summarize_runs
 row={'mode':'collect','new_trading_day_published':True,'published_at':'2026-10-16'}
 r=[{**row,'trade_date':'2026-10-14'},{**row,'trade_date':'2026-10-14'},{**row,'trade_date':'2026-10-15'},
    {**row,'trade_date':'2026-10-16'},{**row,'trade_date':'2026-10-13','mode':'verify_saved'}]
 assert not summarize_runs(r,'2026-10-15')['verified_three_days']
 assert summarize_runs(r,'2026-10-16')['verified_three_days']
