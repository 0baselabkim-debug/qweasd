"""Append-only disclosure records, conservative period metrics and evidence review."""
import datetime as dt,hashlib,html,json,re
from html.parser import HTMLParser
from pathlib import Path
import duckdb
from .financials import ACCOUNTS,select_account

THEMES={
 'AIDC':['GPUaaS','AI 데이터센터','AI데이터센터','GPU 클라우드','GPU클라우드'],
 '전력 인프라':['변압기','전력케이블','전력 케이블','차단기','배전반','송배전'],
 '반도체':['반도체','웨이퍼','웨이퍼 테스트','프로브 카드'],
 '로봇':['산업용 Robot','로봇 등을 제조','산업용 로봇','산업용로봇','협동로봇','협동 로봇','로봇 감속기','로봇감속기'],
 '방산':['방위산업','방산','군용','유도무기','전투기'],
 '원전':['원자력','원전','핵연료']}
KINDS={'사업보고서':('annual','11011'),'반기보고서':('half','11012'),'분기보고서':('quarter',None)}
FLOW_FIELDS={'revenue','operating_profit','net_income','parent_net_income'}
ROOT=Path(__file__).resolve().parents[1]

def filing_record(row,symbol):
 name=row['report_nm'];match=re.search(r'\((\d{4})\.(\d{2})\)',name)
 kind=next((k for k in KINDS if k in name),None)
 if not match or not kind:return None
 year,month=map(int,match.groups());date=dt.date(year+(month==12),month%12+1,1)-dt.timedelta(days=1)
 form,code=KINDS[kind]
 if kind=='분기보고서':code={3:'11013',9:'11014'}.get(month)
 if form=='half' and month!=6:code=None
 if form=='annual' and month!=12:code=None
 receipt=row['rcept_no'];disclosed=dt.datetime.strptime(row['rcept_dt'],'%Y%m%d').date().isoformat()
 return {'symbol':symbol,'corp_code':row['corp_code'],'receipt':receipt,'name':name,'period_end':str(date),'year':year,
         'form':form,'report_code':code,'disclosed_on':disclosed,'correction':'정정' in name,
         'url':'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+receipt}

def business_section(xml):
 starts=list(re.finditer(r'<TITLE\b[^>]*>\s*(?:II|Ⅱ|2)\.\s*사업의\s*내용\s*</TITLE>',xml,re.I))
 if len(starts)!=1:raise ValueError('business_section_not_unique')
 remaining=xml[starts[0].end():]
 end=re.search(r'<TITLE\b[^>]*>\s*(?:III|Ⅲ|3)\.\s*재무에\s*관한\s*사항\s*</TITLE>',remaining,re.I)
 if not end:raise ValueError('business_section_end_missing')
 class Text(HTMLParser):
  def __init__(self):super().__init__();self.parts=[]
  def handle_data(self,s):self.parts.append(s)
  def handle_endtag(self,t):
   if t.lower() in ['p','tr','title','table','section-1','section-2']:self.parts.append('\n')
 p=Text();p.feed(remaining[:end.start()]);text='\n'.join(re.sub(r'\s+',' ',r).strip() for r in ''.join(p.parts).splitlines() if r.strip())
 if len(text)<200:raise ValueError('business_section_too_short')
 return text

def business_candidates(text,filing,document_sha):
 rows=[]
 for theme,words in THEMES.items():
  matches=[p for p in text.splitlines() if any(w.lower() in p.lower() for w in words)]
  matches.sort(key=lambda p:not (any(w in p for w in ['당사','당사의','당사는']) and any(w in p for w in ['제조','판매','공급','매출'])))
  excerpts=[]
  for p in matches:
   at=min([p.lower().find(w.lower()) for w in words if w.lower() in p.lower()]);excerpts.append(p[max(0,at-180):at+700])
  rows.append({'symbol':filing['symbol'],'theme':theme,'receipt':filing['receipt'],'disclosed_on':filing['disclosed_on'],
               'evidence_url':filing['url'],'status':'review_required','reason':'사업의 내용 관련 문맥 확인 필요' if matches else '키워드 미검출; 해당 없음으로 단정하지 않음',
               'excerpts':excerpts[:3],'document_sha256':document_sha,'section_sha256':hashlib.sha256(text.encode()).hexdigest()})
 return rows

def financial_record(response,filing,scope):
 rows=response.get('list',[])
 if response.get('status')!='000' or not rows:raise ValueError('statement_unavailable')
 if any(r.get('rcept_no')!=filing['receipt'] or r.get('reprt_code')!=filing['report_code'] or r.get('bsns_year')!=str(filing['year']) for r in rows):raise ValueError('receipt_or_period_mismatch')
 if {r.get('currency') for r in rows}!={'KRW'}:raise ValueError('currency_not_KRW')
 annual=filing['form']=='annual';accounts={};quarter={};evidence={};prior={}
 for field in ACCOUNTS:
  copied=[]
  for row in rows:
   r=dict(row)
   if not annual and field in FLOW_FIELDS:
    r['thstrm_amount']=row.get('thstrm_add_amount') # never substitute quarter for missing YTD
    r['frmtrm_amount']=row.get('frmtrm_add_amount')
   elif not annual and field=='ocf':r['frmtrm_amount']=row.get('frmtrm_q_amount') # CF already cumulative
   copied.append(r)
  item=select_account(copied,field);accounts[field]=item['value'];prior[field]=item['previous'];evidence[field]=item
  if field in FLOW_FIELDS:quarter[field]=select_account(rows,field)['value'] if not annual else None
 return {**filing,'scope':scope,'currency':'KRW','basis':'annual' if annual else 'YTD','values':accounts,'prior_values':prior,
         'standalone_quarter':quarter,'account_evidence':evidence,'response_sha256':hashlib.sha256(json.dumps(response,sort_keys=True).encode()).hexdigest(),
         'source':'OpenDART fnlttSinglAcntAll','status':'verified_period'}

def metrics(record,cap=None,annual_records=()):
 v=record['values'];p=record['prior_values'];out={k:None for k in ['revenue_growth','op_growth','margin','roe','debt_ratio','per','pbr']}
 def positive(x):return x is not None and x>0
 for field,key in [('revenue','revenue_growth'),('operating_profit','op_growth')]:
  if positive(p.get(field)) and v.get(field) is not None:out[key]=v[field]/p[field]-1
 out['turnaround']=bool(p.get('operating_profit') is not None and p['operating_profit']<=0 and positive(v.get('operating_profit')))
 if positive(v.get('revenue')) and v.get('operating_profit') is not None:out['margin']=v['operating_profit']/v['revenue']
 if positive(v.get('equity')) and v.get('liabilities') is not None:out['debt_ratio']=v['liabilities']/v['equity']
 net='parent_net_income' if record['scope']=='CFS' else 'net_income';eq='parent_equity' if record['scope']=='CFS' else 'equity'
 if positive(cap) and positive(v.get(eq)):out['pbr']=cap/v[eq]
 out['valuation_basis']='연간' if record['basis']=='annual' else 'PER·ROE 보류: 검증된 TTM 미구성; PBR은 해당 기간말 자본'
 if record['basis']=='annual':
  if positive(cap) and positive(v.get(net)):out['per']=cap/v[net]
  if positive(v.get('equity')) and positive(p.get('equity')) and v.get('net_income') is not None:out['roe']=v['net_income']/((v['equity']+p['equity'])/2)
 return out

def load_state(path=None):
 import gzip
 path=Path(path or ROOT/'snapshot/corporate-state.json.gz')
 return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {'schema':1,'filings':{},'jobs':{},'financials':{},'reviews':{},'events':[],'runs':[]}

def save_state(state,path):
 import gzip
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix('.tmp');tmp.write_bytes(gzip.compress(json.dumps(state,ensure_ascii=False,separators=(',',':')).encode(),mtime=0));tmp.replace(path)

def build_database(state,path):
 path=Path(path);temp=path.with_suffix('.candidate.duckdb');temp.unlink(missing_ok=True)
 with duckdb.connect(str(temp)) as c:
  c.execute('create table filings(receipt varchar primary key,symbol varchar,disclosed_on date,period_end date,payload json)')
  c.execute('create table financial_versions(id varchar primary key,symbol varchar,receipt varchar,scope varchar,period_end date,disclosed_on date,payload json)')
  c.execute('create table theme_reviews(id varchar primary key,symbol varchar,theme varchar,status varchar,receipt varchar,payload json)')
  c.execute('create table events(sequence integer,payload json)')
  for k,r in state['filings'].items():c.execute('insert into filings values (?,?,?,?,?)',[k,r['symbol'],r['disclosed_on'],r['period_end'],json.dumps(r,ensure_ascii=False)])
  for k,r in state['financials'].items():c.execute('insert into financial_versions values (?,?,?,?,?,?,?)',[k,r['symbol'],r['receipt'],r['scope'],r['period_end'],r['disclosed_on'],json.dumps(r,ensure_ascii=False)])
  for k,r in state['reviews'].items():c.execute('insert into theme_reviews values (?,?,?,?,?,?)',[k,r['symbol'],r['theme'],r['status'],r['receipt'],json.dumps(r,ensure_ascii=False)])
  for n,r in enumerate(state['events']):c.execute('insert into events values (?,?)',[n,json.dumps(r,ensure_ascii=False)])
 temp.replace(path)

def latest_financials(state,asof):
 result={}
 for r in state['financials'].values():
  if r['disclosed_on']>asof or r['period_end']>asof:continue
  key=(r['symbol'],r['scope']);previous=result.get(key)
  if not previous or (r['period_end'],r['disclosed_on'],r['receipt'])>(previous['period_end'],previous['disclosed_on'],previous['receipt']):result[key]=r
 return result


def apply_review(state,decision,business_text=None):
    key=decision['receipt']+'_'+decision['theme'];old=state['reviews'][key]
    if decision['symbol']!=old['symbol']:raise ValueError('review_symbol_mismatch')
    if decision['status'] not in ['confirmed','not_applicable','review_required','removed']:raise ValueError('review_status_invalid')
    if not decision.get('rationale') or not decision.get('reviewer') or not decision.get('reviewed_on'):raise ValueError('review_audit_missing')
    excerpt=decision.get('excerpt','')
    from_saved=any(excerpt in p for p in old['excerpts'])
    from_section=business_text is not None and hashlib.sha256(business_text.encode()).hexdigest()==old.get('section_sha256') and excerpt in business_text
    if decision['status'] in ['confirmed','not_applicable'] and (len(excerpt)<20 or not (from_saved or from_section)):raise ValueError('review_excerpt_not_in_document_evidence')
    new={**old,**decision,'effective_from':decision['reviewed_on']}
    if old==new:return
    state['events'].append({'type':'theme_review','symbol':old['symbol'],'theme':old['theme'],'receipt':old['receipt'],
                           'before':dict(old),'after':dict(new),'observed_at':decision['reviewed_on']})
    state['reviews'][key]=new


def theme_memberships(state,asof):
    latest={}
    for r in state['reviews'].values():
        effective=r.get('effective_from',r.get('observed_on','9999-12-31'))
        if r['disclosed_on']>asof or effective>asof:continue
        key=(r['symbol'],r['theme']);old=latest.get(key)
        if not old or (r['disclosed_on'],r['receipt'])>(old['disclosed_on'],old['receipt']):latest[key]=r
    return latest
