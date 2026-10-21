"""Manual two-stock probe. Hard five physical attempts; no automatic retries."""
import datetime as dt,gzip,json,os,subprocess,time,urllib.request,urllib.error,urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo
from official_probe import NoRedirect,save,sha,now
BRANCH='research/full-market-transition-20261019'
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'kis-price-version-study'
HOST='https://openapi.koreainvestment.com:9443'
CHART='/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice'
START='20261001';END='20261020';TARGETS=('095570','006840')
NOT_BEFORE=dt.datetime(2026,10,21,14,tzinfo=dt.timezone.utc)

def time_gate(t):
 if t.tzinfo is None:raise RuntimeError('timezone_required')
 local=t.astimezone(ZoneInfo('Asia/Seoul'))
 if t<NOT_BEFORE or not(local.hour>=23 or local.hour<9):raise RuntimeError('before_23_or_outside_overnight_window')

def active_operations(runsets,own_run):
 active=[]
 for runs in runsets:
  if len(runs)>=100:raise RuntimeError('active_run_listing_truncated')
  for r in runs:
   if str(r['id'])==str(own_run):continue
   # CI is non-market, but any other executing/queued workflow conservatively blocks.
   if r.get('name')=='Dashboard deployment check':continue
   active.append({k:r.get(k) for k in ['id','name','status','head_branch']})
 return list({r['id']:r for r in active}.values())

def operation_gate():
 sets=[]
 for status in ['in_progress','queued','waiting','pending','requested']:
  r=subprocess.run(['gh','api',f'repos/0baselabkim-debug/qweasd/actions/runs?status={status}&per_page=100'],capture_output=True,text=True)
  if r.returncode:raise RuntimeError('cannot_verify_operating_jobs')
  sets.append(json.loads(r.stdout)['workflow_runs'])
 active=active_operations(sets,os.environ.get('GITHUB_RUN_ID'))
 if active:raise RuntimeError('operating_jobs_active_or_waiting')
 return {'checked_at':now(),'active_other_market_or_operating_runs':[],'statuses_checked':['in_progress','queued','waiting','pending','requested']}

def persist():
 subprocess.run(['git','add','--',str(OUT.relative_to(ROOT.parents[1]))],check=True)
 if subprocess.run(['git','diff','--cached','--quiet']).returncode:
  subprocess.run(['git','commit','-q','-m','Preserve bounded KIS version research checkpoint'],check=True)
  subprocess.run(['git','push','-q','origin','HEAD:refs/heads/'+BRANCH],check=True)

class Probe:
 def __init__(self,out=OUT,durable=persist,gate=operation_gate):
  self.out=out;self.path=out/'API_EXECUTION.json';self.durable=durable;self.gate=gate;self.last=0;self.started=time.monotonic()
  self.ledger=json.loads(self.path.read_text()) if self.path.exists() else {'authorization':'kis-two-price-versions-after-20261021-2300','maximum':5,'requests':[],'checks':[],'created_at':now()}
  if self.ledger['maximum']!=5 or self.ledger['authorization']!='kis-two-price-versions-after-20261021-2300':raise RuntimeError('wrong_budget_ledger')
  if any(r['status']=='reserved' for r in self.ledger['requests']):raise RuntimeError('prior_attempt_uncertain_no_replay')
  self.secrets=[os.environ.get(k,'') for k in ['KIS_APP_KEY','KIS_APP_SECRET','KIS_ACCESS_TOKEN']]
 def checkpoint(self):save(self.path,self.ledger);self.durable()
 def preflight(self):
  time_gate(dt.datetime.now(dt.timezone.utc));check=self.gate();self.ledger['checks'].append(check)
  if time.monotonic()-self.started>240:raise RuntimeError('probe_elapsed_limit')
 def request(self,key,params=None,auth=False,token=None):
  if any(r['key']==key for r in self.ledger['requests']):return None
  if len(self.ledger['requests'])>=5:raise RuntimeError('physical_request_limit')
  if auth:
   if key!='auth' or any(r['authentication'] for r in self.ledger['requests']):raise RuntimeError('authentication_limit')
  else:
   if not params or params.get('FID_INPUT_ISCD') not in TARGETS or params.get('FID_ORG_ADJ_PRC') not in ['0','1']:raise RuntimeError('request_outside_authorization')
   expected={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':params['FID_INPUT_ISCD'],'FID_INPUT_DATE_1':START,'FID_INPUT_DATE_2':END,'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':params['FID_ORG_ADJ_PRC']}
   if params!=expected or key!=params['FID_INPUT_ISCD']+'-'+params['FID_ORG_ADJ_PRC']:raise RuntimeError('request_outside_authorization')
   if sum(not r['authentication'] for r in self.ledger['requests'])>=4:raise RuntimeError('price_request_limit')
  self.preflight()
  r={'key':key,'params':params,'endpoint':HOST+('/oauth2/tokenP' if auth else CHART),'authentication':auth,'run_id':os.environ.get('GITHUB_RUN_ID'),'reserved_at':now(),'status':'reserved','retry':False}
  self.ledger['requests'].append(r);self.checkpoint()
  time.sleep(max(0,1.05-(time.monotonic()-self.last)))
  try:self.preflight()
  except RuntimeError as e:
   r.update(status='not_sent_guard_block',physical_request=False,blocker=str(e));self.checkpoint();raise
  app=os.environ['KIS_APP_KEY'];secret=os.environ['KIS_APP_SECRET']
  headers={'Content-Type':'application/json'};body=None
  if auth:body=json.dumps({'grant_type':'client_credentials','appkey':app,'appsecret':secret}).encode()
  else:headers.update(authorization='Bearer '+token,appkey=app,appsecret=secret,custtype='P',tr_id='FHKST03010100')
  url=r['endpoint']+('?' +urllib.parse.urlencode(params) if params else '')
  r.update(started_at=now(),physical_request=True);save(self.path,self.ledger);self.last=time.monotonic()
  try:
   try:
    with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url,headers=headers,data=body),timeout=25) as response:
     raw=response.read(16_000_001);r.update(http=response.status,content_type=response.headers.get('Content-Type'))
   except urllib.error.HTTPError as e:raw=e.read(16_000_001);r.update(http=e.code,content_type=e.headers.get('Content-Type'))
   if len(raw)>16_000_000:raise ValueError('response_too_large')
   r['response_sha256']=sha(raw)
   try:x=json.loads(raw)
   except (ValueError,UnicodeError):x={}
   if auth:
    if x.get('access_token'):self.secrets.append(x['access_token'])
    r['authenticated']=r['http']==200 and bool(x.get('access_token'))
    r['status']='received' if r['authenticated'] else 'rejected'
   else:
    if any(s and s.encode() in raw for s in self.secrets):raise ValueError('credential_echo_rejected')
    path=self.out/'responses'/(sha(raw)+'.json.gz');path.parent.mkdir(parents=True,exist_ok=True);data=gzip.compress(raw,mtime=0)
    if path.exists():assert gzip.decompress(path.read_bytes())==raw
    else:path.write_bytes(data)
    r.update(file=str(path.relative_to(self.out)),file_sha256=sha(path.read_bytes()),status='received' if r['http']==200 and x.get('rt_cd')=='0' else 'rejected',provider_code=x.get('msg_cd'))
   return x if r['status']=='received' else None
  except Exception as e:r.update(status='error',error=type(e).__name__);return None
  finally:r.update(completed_at=now(),seconds=round(time.monotonic()-self.last,4));self.checkpoint()
 def run(self):
  try:
   self.preflight()
   self.ledger.pop('blocker',None)
   pending=[(s,k) for s in TARGETS for k in ['0','1'] if not any(r['key']==s+'-'+k for r in self.ledger['requests'])]
   if not pending:self.ledger['status']='all_approved_requests_already_attempted';self.checkpoint();return
   if not all(os.environ.get(k) for k in ['KIS_APP_KEY','KIS_APP_SECRET']):raise RuntimeError('existing_credentials_not_bound')
   token=os.environ.get('KIS_ACCESS_TOKEN')
   if not token:
    x=self.request('auth',auth=True);token=(x or {}).get('access_token')
   if not token:raise RuntimeError('no_reusable_token_and_auth_failed_or_already_attempted')
   for symbol,kind in pending:
    p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':START,'FID_INPUT_DATE_2':END,'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':kind}
    self.request(symbol+'-'+kind,p,token=token)
    if self.ledger['requests'][-1].get('http') in [401,403,429]:break
   self.ledger['status']='attempts_finished_no_automatic_retry';self.checkpoint()
  except RuntimeError as e:
   self.ledger.update(status='blocked',blocker=str(e),checked_at=now());self.checkpoint()
if __name__=='__main__':
 assert os.environ.get('GITHUB_REF_NAME')==BRANCH
 assert subprocess.check_output(['git','branch','--show-current']).decode().strip()==BRANCH
 Probe().run()
