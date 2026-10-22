"""Explicit bounded auxiliary collection. Never writes an operating snapshot.

The persisted journal counts physical requests BEFORE HTTP, including auth and
failures. Retries require another manual run; successes are content-addressed.
"""
import argparse
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screener.decision_support.evidence import encoded, sha, read, date
from scripts.market_reference import classify_rows
from scripts.kis_public_client import KIS, REQUEST_BUDGET

ROOT = Path('auxiliary-prices')
SYMBOLS = ('178320', '000720', '064400')
TOTAL_LIMIT = 300


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(encoded(obj)); os.replace(tmp, path)


def guard():
    t = dt.datetime.now(ZoneInfo('Asia/Seoul'))
    if 18 <= t.hour < 23:
        raise ValueError('operating_collection_window')


class Budget:
    def __init__(self, root, limit=20, seconds=600):
        self.root = root; self.path = root/'requests.json'
        self.state = read(self.path) if self.path.exists() else {'schema':1, 'requests':[]}
        self.start = time.monotonic(); self.initial = len(self.state['requests'])
        self.limit = min(20, limit); self.seconds = min(600, seconds); self.last = 0

    def before_http(self, path, attempt=0):
        guard()
        if attempt:
            raise ValueError('automatic_retry_disabled')
        n = len(self.state['requests'])
        if n >= TOTAL_LIMIT or n-self.initial >= self.limit or time.monotonic()-self.start >= self.seconds:
            raise ValueError('physical_or_time_budget_exhausted')
        time.sleep(max(0, 1.05-(time.monotonic()-self.last)))
        self.last = time.monotonic()
        self.state['requests'].append({'sequence':n+1, 'endpoint':path, 'started_at':now(),
            'run_id':os.getenv('GITHUB_RUN_ID'), 'status':'attempted',
            'kind':'authentication' if path=='/oauth2/tokenP' else 'market'})
        save(self.path, self.state)

    def finish(self, status, **metadata):
        self.state['requests'][-1].update(status=status, completed_at=now(), **metadata)
        save(self.path, self.state)


def cache(root, key, obj):
    raw = encoded(obj)
    for name in ('KRX_API_KEY','KIS_APP_KEY','KIS_APP_SECRET'):
        secret = os.environ.get(name)
        if secret and secret.encode() in raw:
            raise ValueError('credential_echo')
    h = sha(raw); path = root/'objects'/(h+'.json.gz')
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(gzip.compress(raw,mtime=0))
    index_path = root/'index.json'
    index = read(index_path) if index_path.exists() else {'schema':1,'objects':{}}
    # Multiple versions survive. Selection never follows arbitrary file order.
    index['objects'].setdefault(key, [])
    record = {'file':str(path.relative_to(root)), 'sha256':sha(path.read_bytes()), 'payload_sha256':h}
    if record not in index['objects'][key]:index['objects'][key].append(record)
    save(index_path,index)


def cached(root,key):
    p=root/'index.json'
    if not p.exists():return []
    result=[]
    for r in read(p)['objects'].get(key,[]):
        path=root/r['file']
        if sha(path.read_bytes())!=r['sha256']:raise ValueError('auxiliary_evidence_hash_mismatch')
        obj=read(path)
        if sha(encoded(obj))!=r['payload_sha256']:raise ValueError('auxiliary_payload_hash_mismatch')
        result.append(obj)
    return result


def krx(day,market,root,budget):
    key=f'krx:{market}:{day}'
    if cached(root,key):return
    # Already preserved official references need no new API request.
    from screener.decision_support.evidence import official_rows
    paths=list(Path('liquidity/reference').rglob('*.json.gz'))+list(Path('liquidity/reference-observations').rglob('*.json.gz'))
    for p in paths:
        obj=read(p)
        if obj.get('asof')==day and ('/stk_' if market=='KOSPI' else '/ksq_') in obj.get('url',''):
            status,_=classify_rows({'OutBlock_1':obj.get('rows')},day)
            if status=='RECEIVED':cache(root,key,obj);return
    credential=os.environ.get('KRX_API_KEY')
    if not credential:raise ValueError('KRX_credential_not_bound')
    url=f'https://data-dbg.krx.co.kr/svc/apis/sto/{"stk" if market=="KOSPI" else "ksq"}_bydd_trd?basDd='+day.replace('-','')
    budget.before_http(url)
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'AUTH_KEY':credential}),timeout=25) as response:
            raw=response.read(16000000);http=response.status;ctype=response.headers.get('Content-Type')
        if credential.encode() in raw:raise ValueError('credential_echo')
        payload=json.loads(raw);status,rows=classify_rows(payload,day)
        budget.finish(status,http=http,response_sha256=sha(raw),content_type=ctype)
        if status!='RECEIVED':raise ValueError(status)
        obj={'asof':day,'url':url,'observed_at':now(),'http':http,'rows':rows,
             'response_sha256':sha(raw),'raw_response':payload,'market':market}
        cache(root,key,obj)
    except urllib.error.HTTPError as e:
        raw=e.read(8192)
        budget.finish('auth_or_access_error' if e.code in (401,403) else 'http_error',http=e.code,error_sha256=sha(raw))
        raise ValueError('KRX_HTTP_'+str(e.code)) from None
    except (OSError,ValueError):
        if budget.state['requests'][-1]['status']=='attempted':budget.finish('transport_or_schema_error')
        raise


def kis_series(root,budget,asof):
    """Two bounded pages per basis; 4 index pages provide calendar + benchmarks."""
    pending=False
    for code in SYMBOLS+('0001','1001'):
        for option in (('0','1') if len(code)==6 else ('index',)):
            if not cached(root,f'kis:{asof}:{code}:{option}:1'):pending=True
    if not pending:return
    if not all(os.environ.get(k) for k in ('KIS_APP_KEY','KIS_APP_SECRET')):raise ValueError('KIS_credentials_not_bound')
    token=REQUEST_BUDGET.set(budget)
    try:
        api=KIS(interval=1.05);budget.finish('authenticated',http=200)
        for code in SYMBOLS+('0001','1001'):
            for option in (('0','1') if len(code)==6 else ('index',)):
                end=asof.replace('-','')
                for page in range(2):
                    key=f'kis:{asof}:{code}:{option}:{page}'
                    existing=cached(root,key)
                    if existing:obj=existing[-1]
                    else:
                        start=(dt.date.fromisoformat(asof)-dt.timedelta(days=400)).strftime('%Y%m%d')
                        params={'FID_COND_MRKT_DIV_CODE':'J' if len(code)==6 else 'U','FID_INPUT_ISCD':code,
                                'FID_INPUT_DATE_1':start,'FID_INPUT_DATE_2':end,'FID_PERIOD_DIV_CODE':'D'}
                        if len(code)==6:params['FID_ORG_ADJ_PRC']=option
                        endpoint='/uapi/domestic-stock/v1/quotations/'+('inquire-daily-itemchartprice' if len(code)==6 else 'inquire-daily-indexchartprice')
                        response=api.request(endpoint,params,'FHKST03010100' if len(code)==6 else 'FHKUP03500100')
                        budget.finish('received',http=200,symbol=code,option=option,params=params)
                        if api.token and api.token in json.dumps(response):raise ValueError('token_echo')
                        obj={'endpoint':endpoint,'params':params,'observed_at':now(),'response':response,'asof':asof,'symbol':code}
                        rows=response.get('output2',[])
                        if not rows:raise ValueError('empty_KIS_history')
                        dates=[r['stck_bsop_date'] for r in rows]
                        if len(set(dates))!=len(dates) or any(not start<=d<=end for d in dates):raise ValueError('KIS_history_date_mismatch')
                        if len(code)==6 and response.get('output1',{}).get('stck_shrn_iscd')!=code:raise ValueError('KIS_history_symbol_mismatch')
                        cache(root,key,obj)
                    oldest=min(r['stck_bsop_date'] for r in obj['response']['output2'])
                    end=(dt.datetime.strptime(oldest,'%Y%m%d').date()-dt.timedelta(days=1)).strftime('%Y%m%d')
    finally:
        if budget.state['requests'] and budget.state['requests'][-1]['status']=='attempted':budget.finish('KIS_failed_or_interrupted')
        REQUEST_BUDGET.reset(token)


def index_days(root,asof,code):
    rows={}
    for page in range(2):
        parts=cached(root,f'kis:{asof}:{code}:index:{page}')
        if len(parts)!=1:raise ValueError('index_history_missing_or_ambiguous')
        for r in parts[0]['response']['output2']:
            d=date(r['stck_bsop_date'])
            if d in rows or d>asof:raise ValueError('index_calendar_duplicate_or_future')
            rows[d]=r
    days=sorted(rows)
    if len(days)<121 or days[-1]!=asof:raise ValueError('official_market_calendar_short')
    return days[-121:]


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['reference','pilot','history-references']);a=p.parse_args()
    guard();asof=read(Path('snapshot/manifest.json'))['asof']
    if asof>=dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat():raise ValueError('previous_published_day_only')
    budget=Budget(ROOT)
    try:
        if a.mode=='pilot':kis_series(ROOT,budget,asof)
        else:
            for market,code in [('KOSPI','0001'),('KOSDAQ','1001')]:
                days=[asof] if a.mode=='reference' else list(reversed(index_days(ROOT,asof,code)))
                for day in days:krx(day,market,ROOT,budget)
        status='complete'
    except Exception as e:
        status=str(e) if isinstance(e,ValueError) else type(e).__name__
    save(ROOT/'last-run.json',{'asof':asof,'mode':a.mode,'status':status,'run_id':os.getenv('GITHUB_RUN_ID'),
         'physical_requests':len(budget.state['requests'])-budget.initial,'total_physical_requests':len(budget.state['requests']),
         'seconds':round(time.monotonic()-budget.start,3),'completed_at':now()})
    print(json.dumps(read(ROOT/'last-run.json')))
    if status not in ('complete','physical_or_time_budget_exhausted'):raise SystemExit(1)


if __name__=='__main__':main()
