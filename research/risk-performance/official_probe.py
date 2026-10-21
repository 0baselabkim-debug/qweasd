"""Explicit six-request research probe; no retries, redirects, production writes."""
import datetime as dt
import gzip, hashlib, json, os, subprocess, time, urllib.request, urllib.error, urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo
BRANCH='research/full-market-transition-20261019'
OUT=Path(__file__).resolve().parent/'official-crosscheck'
KIS='https://openapi.koreainvestment.com:9443'
HOLIDAY='/uapi/domestic-stock/v1/quotations/chk-holiday'

def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def sha(b):return hashlib.sha256(b).hexdigest()
def save(p,x):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix('.tmp');t.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');t.replace(p)
def window(t):
 h=t.astimezone(ZoneInfo('Asia/Seoul')).hour
 if 18<=h<23:raise RuntimeError('research_window_closed')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

def persist():
 subprocess.run(['git','add','--','research/risk-performance/official-crosscheck'],check=True)
 if subprocess.run(['git','diff','--cached','--quiet']).returncode:
  subprocess.run(['git','commit','-q','-m','Preserve official crosscheck request ledger'],check=True)
  subprocess.run(['git','push','-q','origin','HEAD:refs/heads/'+BRANCH],check=True)
class Probe:
 def __init__(self,out=OUT,durable=persist):
  self.out=out;self.path=out/'API_EXECUTION.json';self.durable=durable;self.last=0
  self.ledger=json.loads(self.path.read_text()) if self.path.exists() else {'authorization':'official-crosscheck-20261021','maximum':8,'requests':[],'created_at':now()}
  assert self.ledger['maximum']==8 and self.ledger['authorization']=='official-crosscheck-20261021'
  if any(r['status']=='reserved' for r in self.ledger['requests']):raise RuntimeError('uncertain_prior_request_requires_review')
  self.secrets=[os.environ.get(k,'') for k in ['KIS_APP_KEY','KIS_APP_SECRET','KRX_API_KEY','KIS_ACCESS_TOKEN']]
 def checkpoint(self):save(self.path,self.ledger);self.durable()
 def request(self,key,url,params=None,headers=None,body=None,auth=False):
  if any(r['key']==key for r in self.ledger['requests']):return None # never repeat success OR failure
  if len(self.ledger['requests'])>=8:raise RuntimeError('physical_limit')
  allowed={f'https://data-dbg.krx.co.kr/svc/apis/sto/{p}_bydd_trd' for p in ['stk','ksq']}
  if url in allowed:
   assert params in [{'basDd':'20261016'},{'basDd':'20261020'}] and not body
  elif url==KIS+HOLIDAY:assert params=={'BASS_DT':'20261020','CTX_AREA_FK':'','CTX_AREA_NK':''} and not body
  else:assert url==KIS+'/oauth2/tokenP' and auth
  window(dt.datetime.now(dt.timezone.utc))
  r={'key':key,'url':url,'params':params,'authentication':auth,'run_id':os.environ.get('GITHUB_RUN_ID'),'reserved_at':now(),'status':'reserved','retry':False}
  self.ledger['requests'].append(r);self.checkpoint() # durable charge before every physical attempt
  time.sleep(max(0,1.05-(time.monotonic()-self.last)))
  try:window(dt.datetime.now(dt.timezone.utc))
  except RuntimeError:
   r.update(status='not_sent_window_closed',physical_request=False);self.checkpoint();raise
  r.update(started_at=now(),physical_request=True);save(self.path,self.ledger)
  self.last=time.monotonic();payload=None;raw=b''
  try:
   req=urllib.request.Request(url+('?' +urllib.parse.urlencode(params) if params else ''),headers=headers or {},data=json.dumps(body).encode() if body else None)
   try:
    with urllib.request.build_opener(NoRedirect).open(req,timeout=25) as response:
     raw=response.read(16_000_001);r.update(http=response.status,content_type=response.headers.get('Content-Type'),tr_cont=response.headers.get('tr_cont'))
   except urllib.error.HTTPError as e:
    r.update(http=e.code,content_type=e.headers.get('Content-Type'));raw=e.read(16_000_001)
   if len(raw)>16_000_000:raise ValueError('response_too_large')
   r['response_sha256']=sha(raw)
   try:payload=json.loads(raw)
   except (ValueError,UnicodeError):payload=None
   if auth:
    token=(payload or {}).get('access_token')
    if token:self.secrets.append(token)
    r['authenticated']=bool(r['http']==200 and token)
    r['status']='received' if r['authenticated'] else 'rejected'
   else:
    if any(v and v.encode() in raw for v in self.secrets):raise ValueError('credential_echo_rejected')
    p=self.out/'responses'/(sha(raw)+'.bin.gz');p.parent.mkdir(parents=True,exist_ok=True)
    data=gzip.compress(raw,mtime=0)
    if p.exists():assert gzip.decompress(p.read_bytes())==raw
    else:p.write_bytes(data)
    r.update(file=str(p.relative_to(self.out)),file_sha256=sha(p.read_bytes()),status='received' if r['http']==200 else 'rejected')
   return payload if r.get('http')==200 else None
  except Exception as e:r.update(status='error',error=type(e).__name__);return None
  finally:
   r.update(completed_at=now(),seconds=round(time.monotonic()-self.last,4));self.checkpoint()
 def main(self):
  krx=os.environ.get('KRX_API_KEY')
  if krx:
   for day in ['20261016','20261020']:
    for market,code in [('KOSPI','stk'),('KOSDAQ','ksq')]:
     self.request(market+'-'+day,f'https://data-dbg.krx.co.kr/svc/apis/sto/{code}_bydd_trd',{'basDd':day},{'AUTH_KEY':krx})
     last=self.ledger['requests'][-1] if self.ledger['requests'] else {}
     if last.get('http') in [401,403,429]:break
    if self.ledger['requests'] and self.ledger['requests'][-1].get('http') in [401,403,429]:break
  else:self.ledger['krx_blocker']='existing_secret_not_bound'
  if any(r['key']=='holiday' for r in self.ledger['requests']):return
  key=os.environ.get('KIS_APP_KEY');secret=os.environ.get('KIS_APP_SECRET');token=os.environ.get('KIS_ACCESS_TOKEN')
  if not key or not secret:self.ledger['kis_blocker']='existing_secret_not_bound';self.checkpoint();return
  if not token:
   response=self.request('auth',KIS+'/oauth2/tokenP',headers={'Content-Type':'application/json'},body={'grant_type':'client_credentials','appkey':key,'appsecret':secret},auth=True)
   token=(response or {}).get('access_token')
  if not token:self.ledger['kis_blocker']='auth_failed_or_already_spent';self.checkpoint();return
  self.request('holiday',KIS+HOLIDAY,{'BASS_DT':'20261020','CTX_AREA_FK':'','CTX_AREA_NK':''},{'Content-Type':'application/json','authorization':'Bearer '+token,'appkey':key,'appsecret':secret,'custtype':'P','tr_id':'CTCA0903R'})
  self.ledger['finished_at']=now();self.checkpoint()
if __name__=='__main__':
 assert os.environ.get('GITHUB_REF_NAME')==BRANCH
 assert subprocess.check_output(['git','branch','--show-current']).decode().strip()==BRANCH
 Probe().main()
