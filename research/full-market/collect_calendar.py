"""One manual validation batch using the SAME global ledger; no stock/flow calls."""
import csv,datetime as dt,gzip,hashlib,json,os,time,urllib.request,urllib.error
from pathlib import Path
from pilot_client import Budget,Client
from official_action_probe import probe as action_probe
from collect_batch import ROOT,BRANCH,GLOBAL_LIMIT,git,read,write,persist,utcnow

def reference_probe(budget,out):
 # Two official market reference requests at most. Authorization errors stop
 # immediately; never retry an inaccessible endpoint or use another route.
 key=os.environ.get('KRX_API_KEY');records=[]
 if not key:
  write(out/'official-reference-probe.json',{'asof':'2026-10-19','status':'credential_not_bound','records':[]});return
 for market,code in [('KOSPI','stk'),('KOSDAQ','ksq')]:
  path=f'/svc/apis/sto/{code}_bydd_trd';url='https://data-dbg.krx.co.kr'+path+'?basDd=20261019'
  m,timeout=budget.acquire(path,0);started=time.monotonic();record={'market':market,'url':url,'asof':'2026-10-19','observed_at':utcnow()};raw=b''
  try:
   req=urllib.request.Request(url,headers={'AUTH_KEY':key})
   with urllib.request.urlopen(req,timeout=timeout) as r:
    m['http']=r.status;raw=r.read(16000000);record['content_type']=r.headers.get('Content-Type')
   record['response_sha256']=hashlib.sha256(raw).hexdigest()
   if key.encode() in raw:raise ValueError('credential_echo_rejected')
   payload=json.loads(raw);rows=payload.get('OutBlock_1')
   if not isinstance(rows,list) or not rows or any(r.get('BAS_DD')!='20261019' for r in rows):raise ValueError('reference_date_or_schema_invalid')
   symbols=[r.get('ISU_CD') for r in rows]
   if len(symbols)!=len(set(symbols)):raise ValueError('duplicate_reference_symbol')
   wrapped={'observed_at':record['observed_at'],'url':url,'market':market,'response':payload}
   data=gzip.compress(json.dumps(wrapped,ensure_ascii=False).encode(),mtime=0);sha=hashlib.sha256(data).hexdigest();p=out/'reference-responses'/f'{market}-{sha}.json.gz';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
   record.update(status='received_unreconciled',file=str(p.relative_to(out)),sha256=sha,rows=len(rows))
  except urllib.error.HTTPError as e:
   m['http']=e.code;raw=e.read(8192);record.update(status='http_error',error_response_sha256=hashlib.sha256(raw).hexdigest(),content_type=e.headers.get('Content-Type'))
  except Exception as e:record.update(status='unverified',error=type(e).__name__)
  finally:
   m.update(code='',seconds=round(time.monotonic()-started,4));record['http']=m.get('http');records.append(record)
   write(out/'official-reference-probe.json',{'asof':'2026-10-19','records':records})
  if m.get('http') in [401,403,429] or record['status']!='received_unreconciled':break

def main():
 assert os.environ.get('GITHUB_REF_NAME')==BRANCH and git('branch','--show-current')==BRANCH
 out=ROOT/'collection';dest=out/'calendar.json'
 if dest.exists() and read(dest,{})['status']=='passed':print('Validated calendar exists; no requests');return
 previous=read(dest,{})
 if dest.exists():
  raw=dest.read_bytes();archive=out/'calendar-audit'/(hashlib.sha256(raw).hexdigest()+'.json');archive.parent.mkdir(parents=True,exist_ok=True);archive.write_bytes(raw)
 ledger=read(out/'ledger.json',{'batches':[],'limit':GLOBAL_LIMIT})
 assert not any(b['status']=='reserved' for b in ledger['batches'])
 remaining=GLOBAL_LIMIT-sum(b['charged'] for b in ledger['batches']);assert remaining>1
 run=os.environ['GITHUB_RUN_ID']+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
 entry={'run':run,'kind':'calendar_validation','status':'reserved','charged':min(15,remaining),'reserved_at':utcnow()}
 ledger['batches'].append(entry);write(out/'ledger.json',ledger);persist('Reserve official index/reference validation within research budget')
 budget=Budget(maximum=entry['charged'],seconds=600,interval=1.05);client=Client(budget);by_market={};observations=[];error=None
 try:
  client.auth()
  # Reuse earlier successful index pages, then request only missing earlier ranges.
  expected=[r['trade_date'] for r in csv.DictReader((ROOT/'inputs/calendar.csv').open()) if '2026-06-01'<=r['trade_date']<='2026-10-19']
  for market,code in [('KOSPI','0001'),('KOSDAQ','1001')]:
   dates=set()
   for observation in previous.get('observations',[]):
    if observation['market']!=market:continue
    p=out/observation['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==observation['sha256']
    saved=json.loads(gzip.decompress(p.read_bytes()));observations.append(observation)
    dates.update(dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat() for r in saved['response']['output2'] if r.get('stck_bsop_date'))
   for page in range(4):
    covered={d for d in dates if '2026-06-01'<=d<='2026-10-19'}
    if covered==set(expected):break
    end=(dt.date.fromisoformat(min(dates))-dt.timedelta(days=1)).strftime('%Y%m%d') if dates else '20261019'
    params={'FID_COND_MRKT_DIV_CODE':'U','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':'20260601','FID_INPUT_DATE_2':end,'FID_PERIOD_DIV_CODE':'D'}
    x=client.request('/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice',params,'FHKUP03500100')
    wrapped={'observed_at':utcnow(),'params':params,'market':market,'response':x};raw=json.dumps(wrapped,ensure_ascii=False).encode()
    if any(v and v.encode() in raw for v in [client.key,client.secret,client.token]):raise RuntimeError('credential_echo_rejected')
    data=gzip.compress(raw,mtime=0);sha=hashlib.sha256(data).hexdigest();p=out/'calendar-responses'/f'{market}-{sha}.json.gz';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    observations.append({'market':market,'file':str(p.relative_to(out)),'sha256':sha})
    received=[r for r in x.get('output2',[]) if r.get('stck_bsop_date')]
    new=[dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat() for r in received]
    if not new or len(new)!=len(set(new)) or any(d.replace('-','')>end for d in new) or not(set(new)-dates):raise ValueError('index_calendar_no_progress_or_scope')
    if any(float(r['bstp_nmix_prpr'])<=0 for r in received):raise ValueError('invalid_index_close')
    dates.update(new)
   by_market[market]=sorted(d for d in dates if '2026-06-01'<=d<='2026-10-19')
   if by_market[market]!=expected:raise ValueError('preserved_calendar_mismatch')
  if by_market['KOSPI']!=by_market['KOSDAQ']:raise ValueError('market_session_mismatch')
 except Exception as e:error=type(e).__name__
 finally:
  try:
   if not (out/'official-reference-probe.json').exists():reference_probe(budget,out)
  except Exception as e:write(out/'reference-probe-stop.json',{'error':type(e).__name__})
  try:
   if not (out/'sangji-official-probe.json').exists():action_probe(budget,out)
  except Exception as e:write(out/'action-probe-stop.json',{'error':type(e).__name__})
  sessions=by_market.get('KOSPI',[]) if not error else []
  write(dest,{'source':'KIS official FHKUP03500100 / KOSPI 0001 and KOSDAQ 1001 daily index trading sessions','from':'2026-06-01','through':'2026-10-19','observations':observations,'sessions':sessions,'market_sessions':by_market,'status':'passed' if not error and len(by_market)==2 else 'unverified','error':error,'holiday_API_not_called':'Official documentation recommends preferably once daily; existing regular collector uses it.'})
  elapsed=time.monotonic()-budget.started;entry.update(status='complete',charged=budget.count,completed_at=utcnow(),elapsed_seconds=elapsed,error=error)
  write(out/'ledger.json',ledger);write(out/'runs'/f'{run}.json',{'run':run,'kind':'calendar_validation','physical_requests':budget.count,'elapsed_seconds':elapsed,'metrics':budget.metrics,'error':error})
  print(json.dumps({'physical_requests':budget.count,'elapsed_seconds':elapsed,'sessions':len(sessions),'error':error}))
if __name__=='__main__':main()
