"""At most one public filing list + two relevant originals for Sangji only."""
import datetime as dt,gzip,hashlib,json,os,re,time,urllib.request,urllib.parse,urllib.error,zipfile,io
from collect_batch import write,utcnow

def fetch(budget,path,public_params,key,out):
 metric,timeout=budget.acquire(path,0);started=time.monotonic();meta={'path':path,'params':public_params,'observed_at':utcnow()};raw=b''
 try:
  url='https://opendart.fss.or.kr'+path+'?'+urllib.parse.urlencode({'crtfc_key':key,**public_params})
  with urllib.request.urlopen(url,timeout=timeout) as r:metric['http']=r.status;raw=r.read(16000001);meta['content_type']=r.headers.get('Content-Type')
  if len(raw)>16000000:raise ValueError('response_too_large')
  if key.encode() in raw:raise ValueError('credential_echo_rejected')
  sha=hashlib.sha256(raw).hexdigest();suffix='.zip' if raw.startswith(b'PK') else '.json' if raw.lstrip().startswith(b'{') else '.xml'
  p=out/'sangji-responses'/(sha+suffix);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
  meta.update(file=str(p.relative_to(out)),sha256=sha,bytes=len(raw),status='received')
 except urllib.error.HTTPError as e:
  metric['http']=e.code;meta.update(status='http_error',error_response_sha256=hashlib.sha256(e.read(8192)).hexdigest())
 except Exception as e:meta.update(status='unverified',error=type(e).__name__)
 finally:
  metric.update(code='',seconds=round(time.monotonic()-started,4));meta['http']=metric.get('http')
 return meta,raw

def probe(budget,out):
 key=os.environ.get('DART_API_KEY');result={'symbol':'042940','corp_code':'00232007','asof':'2026-10-19','status':'credential_not_bound','observations':[],'action_confirmed':False}
 if not key:write(out/'sangji-official-probe.json',result);return
 m,raw=fetch(budget,'/api/list.json',{'corp_code':'00232007','bgn_de':'20260101','end_de':'20261019','page_no':'1','page_count':'100','sort':'date','sort_mth':'desc'},key,out);result['observations'].append(m)
 try:
  if m['status']!='received':raise ValueError('list_unavailable')
  x=json.loads(raw)
  if budget.metrics:budget.metrics[-1]['code']='DART_'+str(x.get('status',''))
  result['provider_status']=x.get('status');result['total_page']=x.get('total_page');result['total_count']=x.get('total_count')
  if x.get('status')!='000':raise ValueError('list_not_available')
  rows=x.get('list',[])
  if any(r.get('corp_code')!='00232007' or r.get('stock_code')!='042940' or r.get('rcept_dt','')>'20261019' for r in rows):raise ValueError('filing_identity_date_mismatch')
  result['list_complete']=int(x.get('total_page',0))==1;result['filings']=rows
  names=re.compile('유상증자|무상증자|감자|주식분할|주식병합|권리락|주식배당')
  selected=[r for r in rows if names.search(r.get('report_nm','')) and r.get('rcept_dt','')<='20260922'][:2]
  result['selected_originals']=selected
  for row in selected:
   receipt=row['rcept_no']
   if not re.fullmatch(r'\d{14}',receipt):raise ValueError('invalid_receipt')
   doc,body=fetch(budget,'/api/document.xml',{'rcept_no':receipt},key,out)
   doc['receipt']=receipt;doc['public_url']='https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+receipt
   if doc['status']=='received':
    try:
     with zipfile.ZipFile(io.BytesIO(body)) as z:
      doc['zip_members']=z.namelist();doc['zip_valid']=z.testzip() is None
    except zipfile.BadZipFile:doc['zip_valid']=False
   result['observations'].append(doc)
  result['status']='received_for_offline_review'
 except Exception as e:result.update(status='unverified',error=type(e).__name__)
 finally:write(out/'sangji-official-probe.json',result)
