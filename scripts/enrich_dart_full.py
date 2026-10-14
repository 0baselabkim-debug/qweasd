"""Read-only, bounded full-statement retrieval. Secrets never enter output files."""
import concurrent.futures,gzip,json,time,threading,argparse
from pathlib import Path
import requests,duckdb
from dotenv import dotenv_values
import os
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/enrichment/dart_full'
OUT.mkdir(parents=True,exist_ok=True)
key=(os.environ.get('DART_API_KEY') or dotenv_values(ROOT/'.env').get('DART_API_KEY'));lock=threading.Lock();last=0;stop=threading.Event()
corps={r['stock_code']:r['corp_code'] for r in json.loads((ROOT/'data/raw/dart_corp_codes.json').read_text())}
with duckdb.connect(str(ROOT/'data/dashboard.duckdb'),read_only=True) as c:targets=c.execute('SELECT symbol,scope,receipt FROM financials ORDER BY symbol').fetchall()
def fetch(item):
 global last
 symbol,scope,receipt=item;p=OUT/f'{symbol}_{scope}_2025.json.gz'
 if p.exists():
  with gzip.open(p,'rt',encoding='utf-8') as f:old_payload=json.load(f)
  cached_rows=old_payload.get('response',{}).get('list',[])
  if old_payload.get('response',{}).get('status')=='000' and cached_rows and old_payload.get('scope')==scope and all(r.get('rcept_no')==receipt for r in cached_rows):return 'cached'
 if stop.is_set():return 'stopped'
 old=ROOT/f'data/raw/dart_full_{symbol}_2025.json'
 if old.exists() and scope=='CFS':
  d=json.loads(old.read_text());origin='existing'
 else:
  with lock:
   time.sleep(max(0,.5-(time.monotonic()-last)));last=time.monotonic()
  try:
   r=requests.get('https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json',params={'crtfc_key':key,'corp_code':corps[symbol],'bsns_year':'2025','reprt_code':'11011','fs_div':scope},timeout=(10,45))
   d=r.json() if r.status_code==200 else {'http':r.status_code};origin='live'
  except requests.RequestException as e:d={'error':type(e).__name__};origin='live'
 if d.get('status')=='020':stop.set()
 payload={'symbol':symbol,'scope':scope,'requested_receipt':receipt,'retrieved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'origin':origin,'response':d}
 with gzip.open(p,'wt',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False)
 rows=d.get('list',[]);cf=[r for r in rows if r.get('sj_div')=='CF']
 return {'symbol':symbol,'scope':scope,'status':d.get('status',d.get('http',d.get('error'))),'rows':len(rows),'cf_rows':len(cf),'receipt_matches':bool(rows) and all(r['rcept_no']==receipt for r in rows)}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int,default=0);args=ap.parse_args();batch=targets[:args.limit] if args.limit else targets
 stats={};started=time.monotonic()
 with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
  for i,res in enumerate(pool.map(fetch,batch),1):
   status=res if isinstance(res,str) else str(res['status']);stats[status]=stats.get(status,0)+1
   if args.limit or i%50==0:print(json.dumps({'done':i,'total':len(batch),'result':res,'counts':stats,'seconds':round(time.monotonic()-started)},ensure_ascii=False),flush=True)
 print(json.dumps({'done':len(batch),'counts':stats,'seconds':round(time.monotonic()-started)}),flush=True)
