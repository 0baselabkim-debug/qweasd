"""Manual research batches only. Never imports production collectors or writes main."""
import datetime as dt,gzip,hashlib,json,os,subprocess,time
from pathlib import Path
from zoneinfo import ZoneInfo
from pilot_client import Budget, BudgetExceeded, Client, utcnow
ROOT=Path(__file__).resolve().parent
BRANCH='research/full-market-transition-20261019'
GLOBAL_LIMIT=3800; BATCH_LIMIT=300

def write(path,x):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)
def git(*args):return subprocess.check_output(['git',*args],stderr=subprocess.STDOUT).decode().strip()
def persist(message):
 git('add','--','research/full-market/collection');
 if subprocess.run(['git','diff','--cached','--quiet']).returncode:
  git('commit','-m',message);git('push','origin','HEAD:refs/heads/'+BRANCH)
def read(path,default):return json.loads(path.read_text()) if path.exists() else default

def main():
 assert os.environ.get('GITHUB_REF_NAME')==BRANCH, 'research branch only'
 assert git('branch','--show-current')==BRANCH
 # Shared concurrency prevents overlap. Do not occupy regular collection slots.
 now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
 if now.weekday()<5 and (18<=now.hour<=21 or now.hour==8):raise RuntimeError('operational_window_reserved')
 out=ROOT/'collection';state=read(out/'state.json',{'records':{},'failures':{},'attempts':{}})
 ledger=read(out/'ledger.json',{'limit':GLOBAL_LIMIT,'batches':[]})
 plan=json.loads((ROOT/'plan.json').read_text());pending=[]
 for row in plan['targets']:
  for kind in row.get('kinds',['adjusted','raw']):
   item=state['records'].get(row['symbol'],{}).get(kind)
   if item:
    p=out/item['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
   else:pending.append((row['symbol'],kind))
 if not pending:print('No missing responses; no authentication');return
 if any(x['status']=='reserved' for x in ledger['batches']):raise RuntimeError('prior_batch_unsettled; inspect artifacts before continuing')
 available=GLOBAL_LIMIT-sum(x['charged'] for x in ledger['batches'])
 if available<=0:raise BudgetExceeded('global_request_limit')
 run=os.environ['GITHUB_RUN_ID']+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
 entry={'run':run,'status':'reserved','charged':min(available,BATCH_LIMIT),'reserved_at':utcnow()}
 ledger['batches'].append(entry);write(out/'ledger.json',ledger)
 persist('Reserve bounded research requests '+run) # Durable BEFORE any HTTP, including auth.
 budget=Budget(maximum=entry['charged'],seconds=720,interval=1.05)
 client=Client(budget);started=utcnow();error=None
 pending.sort(key=lambda pair:(state['attempts'].get(':'.join(pair),0),pair))
 try:
  client.auth()
  for code,kind in pending:
   if budget.count>=budget.maximum or time.monotonic()-budget.started>=710:break
   key=code+':'+kind;state['attempts'][key]=state['attempts'].get(key,0)+1
   try:
    x=client.chart(code,plan['start'],plan['asof'],kind=='adjusted')
    raw=json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    if any(v and v.encode() in raw for v in [client.key,client.secret,client.token]):raise RuntimeError('credential_echo_rejected')
    data=gzip.compress(raw,mtime=0);sha=hashlib.sha256(data).hexdigest();name=f'responses/{code}-{kind}-{sha}.json.gz'
    p=out/name;p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('xb') as f:f.write(data)
    state['records'].setdefault(code,{})[kind]={'file':name,'sha256':sha,'observed_at':x['observed_at'],'run':run}
    state['failures'].pop(key,None)
   except BudgetExceeded:break
   except Exception as e:state['failures'][key]=type(e).__name__ # Never serialize arbitrary credential-bearing errors.
   write(out/'state.json',state)
   write(out/'runs'/f'{run}.json',{'started_at':started,'metrics':budget.metrics,'physical_requests':budget.count,'in_progress':True})
 except Exception as e:error=type(e).__name__
 finally:
  elapsed=time.monotonic()-budget.started
  entry.update(status='complete',charged=budget.count,completed_at=utcnow(),elapsed_seconds=elapsed,error=error)
  metrics={'run':run,'started_at':started,'completed_at':entry['completed_at'],'elapsed_seconds':elapsed,'physical_requests':budget.count,'metrics':budget.metrics,'error':error,'complete_pairs':sum(set(v)=={'adjusted','raw'} for v in state['records'].values()),'saved_responses':sum(map(len,state['records'].values())),'failures':state['failures'],'global_charged':sum(x['charged'] for x in ledger['batches'])}
  write(out/'state.json',state);write(out/'ledger.json',ledger);write(out/'runs'/f'{run}.json',metrics)
  print(json.dumps({k:v for k,v in metrics.items() if k not in ['metrics','failures']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
