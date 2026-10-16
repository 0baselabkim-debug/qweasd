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
 env={'JOB_STATUS':'success','DAY':'20261014','GITHUB_RUN_ID':'1','MODE':'verify_saved'}
 ledger={'2026-10-14':{'run_id':'1','mode':'verify_saved','published_at_utc':'2026-10-15'}}
 b={'status':'complete','records':{'000001':{}},'failures':{},'excluded':{}}
 assert not make_record(env,b,ledger)['new_trading_day_published']
 env['MODE']='collect';ledger['2026-10-14']['mode']='collect';assert make_record(env,b,ledger)['new_trading_day_published']
 env['JOB_STATUS']='failure';assert not make_record(env,b,ledger)['new_trading_day_published']
 env['JOB_STATUS']='success';env['GITHUB_RUN_ID']='2';assert not make_record(env,b,ledger)['new_trading_day_published']

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

def test_unreviewed_new_disclosure_holds_previous_theme_and_no_backdating():
 from screener.corporate import theme_memberships
 old={'symbol':'000001','theme':'반도체','receipt':'20260301000001','disclosed_on':'2026-03-01','effective_from':'2026-10-14','status':'confirmed'}
 new={**old,'receipt':'20260814000001','disclosed_on':'2026-08-14','effective_from':'2026-10-15','status':'review_required'}
 s={'reviews':{'old':old,'new':new}}
 assert not theme_memberships(s,'2026-10-13')
 assert theme_memberships(s,'2026-10-14')['000001','반도체']['status']=='confirmed'
 assert theme_memberships(s,'2026-10-15')['000001','반도체']['status']=='review_required'

def test_interim_missing_equity_loss_and_zero_not_filled():
 r=financial_record(response(),filing(),'CFS');r['values'].update(equity=0,revenue=0,operating_profit=0,parent_equity=-5,parent_net_income=-2)
 m=metrics(r,1000)
 assert m['revenue_growth']==-1 and m['margin'] is None and m['debt_ratio'] is None and m['pbr'] is None and m['per'] is None

def test_real_pilot_preserves_scores_and_separates_periods():
 import hashlib
 from screener.corporate import ROOT
 s=load_state()
 if not s['runs']:pytest.skip('Corporate pilot not yet published')
 assert hashlib.sha256((ROOT/'snapshot/dashboard.duckdb').read_bytes()).hexdigest()=='58f7f23797ada71e80a067ecd8c410a66125b72a163d17751f8cb0ee65054bf8' or __import__('json').loads((ROOT/'snapshot/manifest.json').read_text())['asof']>'2026-10-14'
 records=[r for r in s['financials'].values() if r['symbol']=='240810' and r['receipt']=='20260814001845']
 assert len(records)==1
 r=records[0];assert r['values']['revenue']==381372590019 and r['standalone_quarter']['revenue']==216471641903
 assert r['values']['ocf']==49194134963 and metrics(r)['roe'] is None

def test_retry_queue_does_not_starve_after_120_failures(tmp_path):
 from scripts.collect_corporate import pending_filings
 state=load_state(tmp_path/'absent')
 for i in range(121):
  f={**filing(str(i)), 'symbol':str(i).zfill(6)};state['filings'][f['receipt']]=f
  if i<120:state['jobs'][f['receipt']]={'business_status':'failed','financial_status':'failed','attempted_at':'2026-10-15T00:00:00+00:00'}
 pending=pending_filings(state,None,'2026-10-16T00:00:00+00:00')
 assert pending[0]['symbol']=='000120'

def test_discovery_failure_still_processes_saved_pending(tmp_path,monkeypatch):
 from scripts import collect_corporate as c
 state=load_state(tmp_path/'absent');f=filing();state['filings'][f['receipt']]=f;state['discovery_through']='2026-10-14'
 class API:
  calls=0;started=0;max_calls=600;max_seconds=2100
  def get(self,*a):raise ValueError('transport_TimeoutError')
 seen=[]
 monkeypatch.setattr(c,'process',lambda api,state,f,cache:seen.append(f['receipt']))
 result=c.collect_cycle(API(),state,[{'symbol':f['symbol'],'corp_code':f['corp_code'],'fiscal_month':12}],None,120,None,'2026-10-16',False,lambda:None)
 assert seen==[f['receipt']] and state['discovery_through']=='2026-10-14'
 assert state['last_discovery_error']=='transport_TimeoutError' and not result['budget_exhausted']

def test_failed_document_and_period_are_not_success_and_backoff_is_per_component(tmp_path):
 from scripts import collect_corporate as c
 state=load_state(tmp_path/'absent');f=filing()
 class API:
  calls=[]
  def get(self,path,params):
   self.calls.append(path)
   if path=='document.xml':return b'invalid ZIP fixture'
   d=response();d['list'][0]['rcept_no']='wrong';return d
 api=API();c.process(api,state,f,None);job=state['jobs'][f['receipt']]
 assert job['business_error']=='BadZipFile' and job['business_error_class']=='retryable_response'
 assert job['financial_error']=='receipt_or_period_mismatch' and job['financial_error_class']=='evidence_review_required'
 assert not state['financials'] and not state['reviews']
 count=len(api.calls);c.process(api,state,f,None);assert len(api.calls)==count
 job['business_retry_after']='2000-01-01';c.process(api,state,f,None)
 assert len(api.calls)==count+1 and api.calls[-1]=='document.xml'
 assert job['business_attempts']==2 and job['financial_attempts']==1


def test_discovery_budget_reserves_processing_and_cursor_on_incomplete_chunk(tmp_path,monkeypatch):
 from scripts import collect_corporate as c
 state=load_state(tmp_path/'absent');f=filing();state['filings'][f['receipt']]=f;state['discovery_through']='2026-10-14'
 class API:
  calls=0;max_calls=6;max_seconds=60
  def get(self,*args):self.calls+=1;return {'list':[],'total_page':100}
 api=API();seen=[];monkeypatch.setattr(c,'process',lambda *args:seen.append(args[2]['receipt']))
 c.collect_cycle(api,state,[{'symbol':f['symbol'],'corp_code':f['corp_code'],'fiscal_month':12}],None,120,None,'2026-10-16',False,lambda:None)
 assert api.calls==2 and seen==[f['receipt']]
 assert state['discovery_through']=='2026-10-14'
 assert state['last_discovery_error']=='discovery_budget_exhausted'

def test_document_error_payload_and_exact_business_selection():
 import io,zipfile
 from scripts.corporate_documents import document_section
 f=filing()
 with pytest.raises(ValueError,match='DART_document_status_014'):
  document_section(b'<result><status>014</status><message>no file</message></result>',f)
 text='실제 사업의 내용을 설명하는 테스트 문장입니다. '*50
 xml='<DOCUMENT-NAME>반기보고서</DOCUMENT-NAME><COMPANY-NAME AREGCIK="'+f['corp_code']+'">기업</COMPANY-NAME><TITLE>II. 사업의 내용</TITLE><P>'+text+'</P><TITLE>III. 재무에 관한 사항</TITLE>'
 raw=io.BytesIO()
 with zipfile.ZipFile(raw,'w') as z:
  z.writestr(f['receipt']+'_audit.xml','<DOCUMENT-NAME>감사보고서</DOCUMENT-NAME>'+('audit '*3000))
  z.writestr(f['receipt']+'_main.xml',xml)
 name,section=document_section(raw.getvalue(),f)
 assert name.endswith('_main.xml') and '실제 사업' in section
 with zipfile.ZipFile(raw,'a') as z:z.writestr(f['receipt']+'_duplicate.xml',xml)
 with pytest.raises(ValueError,match='business_document_not_unique'):document_section(raw.getvalue(),f)

def test_attachment_mapping_requires_exact_official_receipt_and_period():
 from scripts.corporate_documents import attachment_parent
 f=filing();a=dict(f,receipt='20260815000001',name='[첨부정정]'+f['name'],disclosed_on='2026-08-15')
 d=response()
 for r in d['list']:r['corp_code']=f['corp_code']
 class API:
  def get(self,path,params):
   if path=='list.json':return {'status':'000','total_page':1,'list':[{'corp_code':f['corp_code'],'report_nm':x['name'],'rcept_no':x['receipt'],'rcept_dt':x['disclosed_on'].replace('-','')} for x in [a,f]]}
   return d
 parent,r,proof=attachment_parent(API(),a)
 assert parent['receipt']==f['receipt'] and r['receipt']!=a['receipt']
 d['list'][0]['corp_code']='wrong'
 with pytest.raises(ValueError,match='corporate_identity_mismatch'):attachment_parent(API(),a)
 d['list'][0]['corp_code']=f['corp_code'];d['list'][0]['bsns_year']='2000'
 with pytest.raises(ValueError,match='receipt_or_period_mismatch'):attachment_parent(API(),a)

def test_attachment_repair_preserves_seed_metadata_and_valid_components(monkeypatch):
 import scripts.repair_corporate_attachments as repair
 f=filing();parent=dict(f,name='[기재정정]'+f['name'],correction=True)
 attachment=dict(f,receipt='20260815000001',name='[첨부정정]'+f['name'],disclosed_on='2026-08-15')
 record=financial_record(response(),f,'CFS')
 state={'filings':{f['receipt']:copy.deepcopy(f),attachment['receipt']:attachment},'jobs':{},'financials':{f['receipt']+'_CFS':copy.deepcopy(record)},'events':[]}
 monkeypatch.setattr(repair,'attachment_parent',lambda *a:(parent,record,{}))
 monkeypatch.setattr(repair,'process',lambda *a:None)
 result=repair.repair(None,state,[attachment['receipt']])
 assert result[0]['parent']==f['receipt'] and not result[0]['new_financial']
 assert state['filings'][f['receipt']]==f and state['financials'][f['receipt']+'_CFS']==record
 assert state['events'][0]['evidence']['official_parent_metadata']==parent
 parent['disclosed_on']='2099-01-01'
 assert repair.repair(None,state,[attachment['receipt']])[0]['error']=='parent_metadata_revision_requires_review'

def test_audit_facts_use_current_term_label_and_exact_filing():
 from scripts.replay_corporate_documents import audit_facts
 f=filing();row={'corp_code':f['corp_code'],'rcept_no':f['receipt'],'stlm_dt':f['period_end'],'bsns_year':'제59기\n(당기)','adt_opinion':'적정의견'}
 r={'status':'000','list':[row,dict(row,bsns_year='제58기 (전기)',adt_opinion='전기 의견')]}
 facts=audit_facts(r,f)
 assert len(facts['raw_current_rows'])==1 and facts['material_financial_risk']=='unverified' and not facts['eligibility_rule_applied']
 r['list'][0]['rcept_no']='incorrect'
 assert audit_facts(r,f) is None
