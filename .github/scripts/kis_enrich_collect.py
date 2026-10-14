"""Resumable market-only collection. Historical mode must be explicitly dispatched."""
import argparse,concurrent.futures,datetime as dt,gzip,json,os,re,threading,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
import duckdb

ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['quotes','history'],required=True);args=ap.parse_args()
MODE=args.mode;BASE='https://openapi.koreainvestment.com:9443';ASOF='20261014'
SECRETS=[os.environ.get(k,'') for k in ['KIS_APP_KEY','KIS_APP_SECRET']]
for s in SECRETS:
    if s:print('::add-mask::'+s,flush=True)
assert all(SECRETS)
lock=threading.Lock();interval=.6 if MODE=='quotes' else 1.05;last=0;metrics=[]
def request(path,params=None,tr=None,body=None):
    global last,interval
    for attempt in range(3):
        with lock:
            time.sleep(max(0,interval-(time.monotonic()-last)));last=time.monotonic()
        started=time.monotonic();headers={'Content-Type':'application/json'}
        if tr:headers.update(authorization='Bearer '+TOKEN,appkey=SECRETS[0],appsecret=SECRETS[1],custtype='P',tr_id=tr)
        status=None;data={}
        try:
            url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
            req=urllib.request.Request(url,headers=headers,data=json.dumps(body).encode() if body else None)
            with urllib.request.urlopen(req,timeout=25) as r:status=r.status;data=json.load(r)
        except urllib.error.HTTPError as e:
            status=e.code
            try:data=json.load(e)
            except Exception:pass
        except Exception as e:data={'error_type':type(e).__name__}
        code=str(data.get('msg_cd',''));metric={'http':status,'code':code,'seconds':round(time.monotonic()-started,4)};metrics.append(metric)
        if status==429 or code=='EGW00201':
            with lock:interval=max(interval,1.05)
            time.sleep(3*(attempt+1));continue
        if status and status>=500:time.sleep(2*(attempt+1));continue
        break
    return data,metric

auth,m=request('/oauth2/tokenP',body={'grant_type':'client_credentials','appkey':SECRETS[0],'appsecret':SECRETS[1]})
TOKEN=auth.get('access_token');assert m['http']==200 and TOKEN,'authentication_failed'
SECRETS.append(TOKEN);print('::add-mask::'+TOKEN,flush=True)
with duckdb.connect('snapshot/dashboard.duckdb',read_only=True) as c:
    targets=[r[0] for r in c.execute("SELECT symbol FROM flows WHERE trade_date='2026-10-14' ORDER BY symbol").fetchall()]
sample=json.loads(gzip.decompress(Path('research/kis_history_sample.json.gz').read_bytes()))
records={r['symbol']: {'symbol':r['symbol'],'quote':r['quote'],'metric':r['quote_metric'],'received_at_utc':None,'source':'probe '+sample['run_id']} if MODE=='quotes' else r for r in sample['records']}
out=Path('staging');out.mkdir(exist_ok=True)
path=out/f'kis_{MODE}_{ASOF}.json.gz';started=time.monotonic();failures={}
if path.exists():
    old=json.loads(gzip.decompress(path.read_bytes()));records.update(old.get('records',{}))
def fetch(symbol):
    params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol}
    if MODE=='quotes':
        d,m=request('/uapi/domestic-stock/v1/quotations/inquire-price',params,'FHKST01010100')
        row=d.get('output',{});required=['stck_shrn_iscd','stck_prpr','acml_vol','lstn_stcn','hts_avls']
        ok=m['http']==200 and d.get('rt_cd')=='0' and isinstance(row,dict) and all(re.fullmatch(r'\d+',str(row.get(k,''))) for k in required) and row['stck_shrn_iscd']==symbol
        keep=required+['rprs_mrkt_kor_name','bstp_kor_isnm','acml_tr_pbmn','stck_tr_hour','iscd_stat_cls_code']
        record={'symbol':symbol,'quote':{k:row[k] for k in keep if k in row},'metric':m,'received_at_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
    else:
        params.update(FID_INPUT_DATE_1='20261013',FID_ORG_ADJ_PRC='',FID_ETC_CLS_CODE='')
        d,m=request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',params,'FHPTJ04160001')
        rows=d.get('output2',[]);rows=rows if isinstance(rows,list) else []
        required=['stck_bsop_date','frgn_ntby_tr_pbmn','frgn_ntby_qty','stck_clpr','acml_vol','acml_tr_pbmn']
        valid=[{k:r[k] for k in required} for r in rows if isinstance(r,dict) and all(re.fullmatch(r'-?\d+',str(r.get(k,''))) for k in required) and str(r['stck_bsop_date'])<='20261013']
        ok=m['http']==200 and d.get('rt_cd')=='0' and bool(valid) and len({r['stck_bsop_date'] for r in valid})==len(valid)
        record={'symbol':symbol,'rows':valid,'metric':m,'received_at_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
    return symbol,record,ok

def save():
    result={'mode':MODE,'analysis_date':ASOF,'exchange':'KRX','run_id':os.environ['GITHUB_RUN_ID'],'records':records,'failures':failures,'metrics':metrics,'elapsed_seconds':round(time.monotonic()-started,3),'ending_interval':interval,'finality':'UNVERIFIED'}
    raw=json.dumps(result,ensure_ascii=False,separators=(',',':'))
    assert not any(s in raw for s in SECRETS)
    temporary=path.with_suffix('.tmp');temporary.write_bytes(gzip.compress(raw.encode(),mtime=0));temporary.replace(path)
    return result

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    for i,(symbol,record,ok) in enumerate(pool.map(fetch,[s for s in targets if s not in records]),1):
        if ok:records[symbol]=record;failures.pop(symbol,None)
        else:failures[symbol]=record
        if i%50==0:save();print(json.dumps({'mode':MODE,'success':len(records),'failures':len(failures),'elapsed':round(time.monotonic()-started)}),flush=True)
result=save()
summary={'mode':MODE,'success':len(records),'failures':len(failures),'elapsed':result['elapsed_seconds'],'rate_limit_responses':sum(m['http']==429 or m['code']=='EGW00201' for m in metrics),'requests':len(metrics),'ending_interval':interval}
print('::notice title=Collection summary::'+json.dumps(summary),flush=True)
Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('```json\n'+json.dumps(summary,indent=2)+'\n```\n')
