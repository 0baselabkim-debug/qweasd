"""Ten-symbol, read-only KRX history probe. Never query an account or order endpoint."""
import base64, gzip, json, os, re, statistics, time, urllib.request, urllib.parse, urllib.error
from pathlib import Path

BASE = 'https://openapi.koreainvestment.com:9443'
SYMBOLS = ['005930','000660','035420','034020','012450','001000','010280','277810','058470','247540']
secrets = [os.environ.get('KIS_APP_KEY',''), os.environ.get('KIS_APP_SECRET','')]
for value in secrets:
    if value: print('::add-mask::'+value, flush=True)
assert all(secrets), 'Missing configured KIS secrets'
last = 0
metrics = []

def call(path, params=None, tr=None, body=None):
    global last
    for attempt in range(3):
        time.sleep(max(0,1.05-(time.monotonic()-last)))
        last=time.monotonic()
        headers={'Content-Type':'application/json'}
        if tr: headers.update(authorization='Bearer '+token, appkey=secrets[0], appsecret=secrets[1], custtype='P', tr_id=tr)
        url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
        status=None
        try:
            req=urllib.request.Request(url, headers=headers, data=json.dumps(body).encode() if body else None)
            with urllib.request.urlopen(req,timeout=30) as response:
                status=response.status; data=json.load(response)
        except urllib.error.HTTPError as e:
            status=e.code
            try:data=json.load(e)
            except Exception:data={}
        except Exception as e:data={'error_type':type(e).__name__}
        metric={'seconds':round(time.monotonic()-last,4),'http':status,'code':str(data.get('msg_cd','')),'attempt':attempt+1}
        metrics.append(metric)
        if status!=429 and metric['code']!='EGW00201':break
        time.sleep(3*(attempt+1))
    return data,metric

auth,m=call('/oauth2/tokenP',body={'grant_type':'client_credentials','appkey':secrets[0],'appsecret':secrets[1]})
token=auth.get('access_token')
assert m['http']==200 and token, 'KIS token request failed; response omitted for secret safety'
secrets.append(token); print('::add-mask::'+token,flush=True)
records=[];started=time.monotonic()
for symbol in SYMBOLS:
    begin=time.monotonic()
    params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':'20261013','FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''}
    history,hm=call('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',params,'FHPTJ04160001')
    quote,qm=call('/uapi/domestic-stock/v1/quotations/inquire-price',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol},'FHKST01010100')
    rows=history.get('output2',[])
    if not isinstance(rows,list):rows=[]
    required=['stck_bsop_date','frgn_ntby_tr_pbmn','frgn_ntby_qty','stck_clpr','acml_vol','acml_tr_pbmn']
    valid=[r for r in rows if all(re.fullmatch(r'-?\d+',str(r.get(k,''))) for k in required) and str(r['stck_bsop_date'])<='20261013']
    dates=[r['stck_bsop_date'] for r in valid]
    record={'symbol':symbol,'exchange':'KRX','history_request_date':'20261013','history_metric':hm,'quote_metric':qm,'rows':valid,'output1':history.get('output1',{}),'quote':quote.get('output',{}),'invalid_rows':len(rows)-len(valid),'duplicate_dates':len(dates)-len(set(dates)),'seconds':round(time.monotonic()-begin,4)}
    records.append(record)
old,om=call('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':'005930','FID_INPUT_DATE_1':'20260911','FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''},'FHPTJ04160001')
result={'schema':1,'asof':'20261014','history_end':'20261013','unit':'KRW million; multiply frgn_ntby_tr_pbmn by 1000000','exchange':'KRX','run_id':os.environ['GITHUB_RUN_ID'],'records':records,'old_date_probe':{'metric':om,'dates':[r.get('stck_bsop_date') for r in old.get('output2',[])]},'metrics':metrics,'elapsed':round(time.monotonic()-started,4)}
raw=json.dumps(result,ensure_ascii=False,separators=(',',':'))
assert not any(value in raw for value in secrets)
Path('kis-history-probe.json').write_text(raw)
packed=base64.b64encode(gzip.compress(raw.encode(),mtime=0)).decode()
chunks=[packed[i:i+2800] for i in range(0,len(packed),2800)]
assert len(chunks)<45
for i,chunk in enumerate(chunks):print('::notice title=History sample data::'+json.dumps({'part':i,'parts':len(chunks),'data':chunk}),flush=True)
summary={'symbols':len(records),'rows':sum(len(r['rows']) for r in records),'invalid_rows':sum(r['invalid_rows'] for r in records),'duplicates':sum(r['duplicate_dates'] for r in records),'limited_requests':sum(m['http']==429 or m['code']=='EGW00201' for m in metrics),'elapsed':result['elapsed'],'requests':len(metrics),'flow_mean_seconds':statistics.mean(r['history_metric']['seconds'] for r in records),'successes':sum(len(r['rows'])>=19 and r['history_metric']['http']==200 for r in records)}
print('::notice title=History sample summary::'+json.dumps(summary),flush=True)
Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('```json\n'+json.dumps(summary,indent=2)+'\n```\n')
assert summary['successes']==10 and summary['invalid_rows']==0 and summary['duplicates']==0
