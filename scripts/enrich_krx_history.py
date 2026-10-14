import concurrent.futures,json,requests,time,threading
from pathlib import Path
from dotenv import dotenv_values
import os
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/enrichment';key=(os.environ.get('KRX_API_KEY') or dotenv_values(ROOT/'.env').get('KRX_API_KEY'));lock=threading.Lock();last=0
sample=json.loads((OUT/'kis_history_sample.json').read_text());dates=sorted(set(r['stck_bsop_date'] for s in sample['records'] for r in s['rows']),reverse=True)[:25]
def fetch(pair):
 global last
 market,date=pair;p=OUT/f'krx_{market}_{date}.json'
 if p.exists():return market,date,'cached',len(json.loads(p.read_text()).get('OutBlock_1',[]))
 with lock:
  time.sleep(max(0,.7-(time.monotonic()-last)));last=time.monotonic()
 try:
  r=requests.get('https://data-dbg.krx.co.kr/svc/apis/sto/'+('stk' if market=='KOSPI' else 'ksq')+'_bydd_trd',params={'basDd':date},headers={'AUTH_KEY':key},timeout=40);d=r.json() if r.status_code==200 else {'http':r.status_code}
 except requests.RequestException as e:d={'error':type(e).__name__}
 p.write_text(json.dumps(d,ensure_ascii=False));return market,date,d.get('http',200),len(d.get('OutBlock_1',[]))
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
 for r in ex.map(fetch,[(m,d) for d in dates for m in ['KOSPI','KOSDAQ']]):print(r,flush=True)
