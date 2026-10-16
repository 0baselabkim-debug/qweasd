"""Bounded OpenDART delta collection. Only safe metadata enters logs; no credentials."""
import argparse,datetime as dt,gzip,hashlib,io,json,os,sys,time,urllib.request,urllib.parse,zipfile
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.corporate import *

class BudgetExceeded(Exception):pass
class Dart:
 def __init__(self,max_calls=600,max_seconds=2100):
  self.key=os.environ.get('DART_API_KEY');self.calls=0;self.started=time.monotonic();self.last=0;self.max_calls=max_calls;self.max_seconds=max_seconds
  if not self.key:raise ValueError('DART_API_KEY_missing')
 def get(self,path,params):
  if self.calls>=self.max_calls or time.monotonic()-self.started>self.max_seconds:raise BudgetExceeded()
  time.sleep(max(0,1-(time.monotonic()-self.last)));self.calls+=1;self.last=time.monotonic()
  try:
   url='https://opendart.fss.or.kr/api/'+path+'?'+urllib.parse.urlencode({'crtfc_key':self.key,**params})
   with urllib.request.urlopen(url,timeout=45) as r:
    data=r.read(40000000);self.last_response={'http':r.status,'content_type':r.headers.get('Content-Type'),'bytes':len(data)}
  except Exception as e:raise ValueError('transport_'+type(e).__name__) from None
  if self.key.encode() in data:raise ValueError('secret_in_response')
  if path.endswith('.json'):
   d=json.loads(data)
   if d.get('status')=='020':raise BudgetExceeded()
   if d.get('status') not in ['000','013']:raise ValueError('DART_status_'+str(d.get('status')))
   return d
  return data

def seed(state,universe):
 from screener.store import load
 data=load('snapshot/dashboard.duckdb');bycode={r['symbol']:r for r in universe}
 for row in data['financials'].to_dict('records'):
  receipt=row['receipt'];symbol=row['symbol'];u=bycode.get(symbol)
  if not u:continue
  f={'symbol':symbol,'corp_code':u['corp_code'],'receipt':receipt,'name':'사업보고서 ('+str(row['period_end'].date())[:7].replace('-','.')+')',
     'period_end':str(row['period_end'].date()),'year':row['period_end'].year,'form':'annual','report_code':'11011' if row['period_end'].month==12 else None,
     'disclosed_on':str(row['disclosed_on'].date()),'correction':False,'url':'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+receipt}
  state['filings'].setdefault(receipt,f)
  def val(key):
   v=row.get(key);return None if v is None or __import__('pandas').isna(v) else float(v)
  key=receipt+'_'+row['scope']
  state['financials'].setdefault(key,{**f,'scope':row['scope'],'currency':row['currency'],'basis':'annual','values':{k:val(k) for k in ACCOUNTS},
   'prior_values':{k:val('previous_'+k) for k in ACCOUNTS},'standalone_quarter':{},'account_evidence':{},'source':'preserved verified annual snapshot','status':'verified_period'})

 for row in json.loads(Path('theme_mapping.json').read_text())['memberships']:
  receipt=row['evidence_url'].split('rcpNo=')[-1];key=receipt+'_'+row['theme']
  if row['status']=='confirmed':
   state['reviews'].setdefault(key,{**row,'receipt':receipt,'reviewer':'preserved_official_business_review','reviewed_on':row['verified_on'],
    'effective_from':row['valid_from'],'observed_on':row['verified_on'],'excerpts':[row['excerpt']]})

def discover(api,state,universe,end,symbols=None):
 bycorp={r['corp_code']:r['symbol'] for r in universe if r['corp_code']}
 if symbols:
  for u in universe:
   if u['symbol'] not in symbols:continue
   d=api.get('list.json',{'corp_code':u['corp_code'],'bgn_de':'20260101','end_de':end.replace('-',''),'pblntf_ty':'A','last_reprt_at':'N','page_count':100})
   if d.get('total_page',0)>1:raise ValueError('pilot_listing_requires_pagination')
   for row in d.get('list',[]):
    f=filing_record(row,u['symbol'])
    if f:state['filings'].setdefault(f['receipt'],f)
  return
 start=dt.date.fromisoformat(state.get('discovery_through','2026-01-01'))-dt.timedelta(days=7) if state.get('discovery_through') else dt.date(2026,1,1)
 stop=dt.date.fromisoformat(end)
 while start<=stop:
  last=min(start+dt.timedelta(days=89),stop)
  for market in ['Y','K']:
   page=1
   while True:
    d=api.get('list.json',{'bgn_de':start.strftime('%Y%m%d'),'end_de':last.strftime('%Y%m%d'),'corp_cls':market,'pblntf_ty':'A','last_reprt_at':'N','page_count':100,'page_no':page})
    for row in d.get('list',[]):
     if row['corp_code'] not in bycorp:continue
     f=filing_record(row,bycorp[row['corp_code']])
     if f:state['filings'].setdefault(f['receipt'],f)
    if page>=d.get('total_page',0):break
    page+=1
  state['discovery_through']=str(last);start=last+dt.timedelta(days=1)

def utc_now():return dt.datetime.now(dt.timezone.utc).isoformat()

def component_due(job,component,now):
 if job.get(component+'_status') in ['success','held_nondec_fiscal_period','held_attachment_only']:return False
 return job.get(component+'_retry_after',job.get('retry_after','') if component=='financial' else '')<=now

def mark_attempt(job,component):
 job['attempted_at']=utc_now();job[component+'_attempted_at']=job['attempted_at']
 job[component+'_attempts']=job.get(component+'_attempts',0)+1

def error_class(error):
 if error in ['receipt_or_period_mismatch','business_section_not_unique','business_document_not_unique','document_xml_missing','DART_document_status_014']:
  return 'evidence_review_required'
 if error=='BadZipFile' or error.startswith(('transport_','DART_status_')):return 'retryable_response'
 if error in ['unavailable','budget_exhausted']:return 'retryable_unavailable'
 return 'review_required'

def pending_filings(state,symbols,now):
 groups={}
 for f in state['filings'].values():
  if not symbols or f['symbol'] in symbols:groups.setdefault(f['symbol'],[]).append(f)
 pending=[]
 for filings in groups.values():
  for annual in [True,False]:
   candidates=[f for f in filings if (f['form']=='annual')==annual]
   if not candidates:continue
   f=max(candidates,key=lambda f:(f['period_end'],f['disclosed_on'],f['receipt']))
   linked=state['jobs'].get(f['receipt'],{}).get('linked_periodic_receipt')
   if linked and linked in state['filings']:f=state['filings'][linked]
   j=state['jobs'].get(f['receipt'],{})
   if any(component_due(j,c,now) for c in ['business','financial']):pending.append(f)
 # Unattempted receipts first, then oldest attempt; deterministic round-robin ties.
 return sorted(pending,key=lambda f:(state['jobs'].get(f['receipt'],{}).get('attempted_at',''),f['symbol'],f['receipt']))

class DiscoveryAllowance:
 """Reserve at least 2/3 of calls and time for already discovered work."""
 def __init__(self,api):self.api=api;self.calls=0;self.started=time.monotonic()
 def get(self,*args):
  if self.calls>=max(1,self.api.max_calls//3) or time.monotonic()-self.started>=max(1,self.api.max_seconds//3):raise BudgetExceeded()
  self.calls+=1;return self.api.get(*args)

def collect_cycle(api,state,universe,symbols,limit,cache,end,skip_discovery,checkpoint):
 budget=False;processed=[]
 if not skip_discovery:
  try:
   discover(DiscoveryAllowance(api),state,universe,end,symbols);state.pop('last_discovery_error',None)
  except BudgetExceeded:state['last_discovery_error']='discovery_budget_exhausted'
  except Exception as e:state['last_discovery_error']=str(e)[:100] if isinstance(e,ValueError) else type(e).__name__
  checkpoint()
 pending=pending_filings(state,symbols,utc_now());codes=list(dict.fromkeys(f['symbol'] for f in pending))[:limit]
 fiscal_month={r['symbol']:r.get('fiscal_month') for r in universe}
 for f in pending:
  if f['symbol'] not in codes:continue
  if str(fiscal_month.get(f['symbol'])) not in ['12','12.0']:f={**f,'report_code':None}
  try:process(api,state,f,cache);processed.append(f['receipt'])
  except BudgetExceeded:budget=True;break
  finally:checkpoint()
 return {'processed':processed,'pending':pending,'budget_exhausted':budget}

def _process_once(api,state,f,cache):
 receipt=f['receipt'];job=state['jobs'].setdefault(receipt,{})
 if f['name'].startswith('[첨부정정]'):
  if not any(component_due(job,c,utc_now()) for c in ['business','financial']):return
  for c in ['business','financial']:mark_attempt(job,c)
  from scripts.repair_corporate_attachments import repair
  result=repair(api,state,[receipt])[0]
  if result.get('error'):
   for c in ['business','financial']:job.update({c+'_status':'failed',c+'_error':result['error']})
  return
 if component_due(job,'business',utc_now()):
  mark_attempt(job,'business')
  try:
   raw=api.get('document.xml',{'rcept_no':receipt})
   from scripts.corporate_documents import document_section
   document_name,text=document_section(raw,f);sha=hashlib.sha256(raw).hexdigest()
   rows=business_candidates(text,f,sha)
   today=str(dt.datetime.now(ZoneInfo('Asia/Seoul')).date())
   previous=list(state['reviews'].values())
   for row in rows:
    row['observed_on']=today
    old=sorted([r for r in previous if r['symbol']==f['symbol'] and r['theme']==row['theme'] and r['status']=='confirmed' and r['disclosed_on']<f['disclosed_on']],key=lambda r:r['disclosed_on'],reverse=True)
    if old and old[0].get('excerpt') and old[0]['excerpt'] in text:
     row.update(status='confirmed',excerpt=old[0]['excerpt'],rationale='새 공시 사업의 내용에서 기존 검증 문장 재확인',reviewer='exact_evidence_revalidation',reviewed_on=today,effective_from=today)
     state['events'].append({'type':'theme_revalidated','symbol':f['symbol'],'theme':row['theme'],'receipt':receipt,'previous_receipt':old[0]['receipt'],'observed_at':today})
   for r in rows:
    key=receipt+'_'+r['theme'];state['reviews'].setdefault(key,r)
   job.update(business_status='success',document_name=document_name,document_sha256=sha,section_sha256=hashlib.sha256(text.encode()).hexdigest(),characters=len(text));job.pop('business_error',None)
   if cache:
    cache.mkdir(parents=True,exist_ok=True);(cache/(receipt+'.txt')).write_text(text)
   state['events'].append({'type':'business_observed','symbol':f['symbol'],'receipt':receipt,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'section_sha256':job['section_sha256']})
  except BudgetExceeded:raise
  except Exception as e:job['business_status']='failed';job['business_error']=str(e)[:100] if isinstance(e,ValueError) else type(e).__name__
 if any(r['receipt']==receipt for r in state['financials'].values()):job['financial_status']='success';return
 if not f['report_code']:job['financial_status']='held_nondec_fiscal_period';return
 if not component_due(job,'financial',utc_now()):return
 mark_attempt(job,'financial')
 for scope in ['CFS','OFS']:
  try:
   d=api.get('fnlttSinglAcntAll.json',{'corp_code':f['corp_code'],'bsns_year':str(f['year']),'reprt_code':f['report_code'],'fs_div':scope})
   if d.get('status')=='013':continue
   r=financial_record(d,f,scope);state['financials'].setdefault(receipt+'_'+scope,r)
   job['financial_status']='success';job.pop('financial_error',None);return
  except BudgetExceeded:raise
  except Exception as e:job['financial_status']='failed';job['financial_error']=str(e)[:100] if isinstance(e,ValueError) else type(e).__name__;return
 job['financial_status']='unavailable';job['retry_after']=str(dt.datetime.now(ZoneInfo('Asia/Seoul')).date()+dt.timedelta(days=1))

def process(api,state,f,cache):
 job=state['jobs'].setdefault(f['receipt'],{});before={c:job.get(c+'_attempts',0) for c in ['business','financial']}
 exhausted=False
 try:_process_once(api,state,f,cache)
 except BudgetExceeded:exhausted=True;raise
 finally:
  for c in ['business','financial']:
   if job.get(c+'_attempts',0)==before[c]:continue
   if job.get(c+'_status') in ['success','held_attachment_only']:
    for suffix in ['retry_after','error_class']:job.pop(c+'_'+suffix,None)
    continue
   error=job.get(c+'_error','budget_exhausted' if exhausted else job.get(c+'_status','unavailable'))
   category=error_class(error);hours=24 if category in ['evidence_review_required','review_required'] else 1
   delay=min(168,hours*2**min(job[c+'_attempts']-1,8))
   job[c+'_error_class']=category
   job[c+'_retry_after']=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=delay)).isoformat()

def main():
 p=argparse.ArgumentParser();p.add_argument('--symbols',default='');p.add_argument('--limit',type=int,default=120);p.add_argument('--max-calls',type=int,default=600);p.add_argument('--max-seconds',type=int,default=2100);p.add_argument('--state',default='snapshot/corporate-state.json.gz');p.add_argument('--db',default='snapshot/corporate.duckdb');p.add_argument('--cache',type=Path);p.add_argument('--skip-discovery',action='store_true');a=p.parse_args()
 state=load_state(a.state);universe=json.loads(Path('corporate/universe.json').read_text());seed(state,universe)
 symbols=set(a.symbols.split(',')) if a.symbols else None
 api=Dart(a.max_calls,a.max_seconds);started=dt.datetime.now(dt.timezone.utc).isoformat();budget=False;processed=[];pending=[]
 cycle=collect_cycle(api,state,universe,symbols,a.limit,a.cache,str(dt.datetime.now(ZoneInfo('Asia/Seoul')).date()),a.skip_discovery,lambda:save_state(state,a.state))
 processed=cycle['processed'];pending=cycle['pending'];budget=cycle['budget_exhausted']
 run={'id':os.environ.get('GITHUB_RUN_ID','local')+'-'+str(time.time_ns()),'started_at':started,'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'calls':api.calls,'seconds':round(time.monotonic()-api.started,2),'processed_filings':len(processed),'budget_exhausted':budget,'discovery_through':state.get('discovery_through'),'discovery_error':state.get('last_discovery_error'),
      'failed_jobs':{r:{k:v for k,v in j.items() if k.endswith('error')} for r,j in state['jobs'].items() if j.get('business_status')=='failed' or j.get('financial_status')=='failed'}}
 run['scope']=sorted(symbols) if symbols else 'entire_universe'
 run['pending_filings']=sum(state['jobs'].get(f['receipt'],{}).get('business_status')!='success' or state['jobs'].get(f['receipt'],{}).get('financial_status') not in ['success','held_nondec_fiscal_period','held_attachment_only'] for f in pending)
 run['unavailable_financials']=[k for k,j in state['jobs'].items() if j.get('financial_status')=='unavailable']
 run['held_financials']=[k for k,j in state['jobs'].items() if j.get('financial_status')=='held_nondec_fiscal_period']
 state['runs'].append(run);save_state(state,a.state);build_database(state,a.db)
 Path('corporate-run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2)+'\n');print(json.dumps(run,ensure_ascii=False))
if __name__=='__main__':main()
