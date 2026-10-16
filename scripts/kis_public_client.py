"""Market-data-only KIS transport; credentials and tokens never enter results."""
import json, os, threading, time, urllib.error, urllib.parse, urllib.request
from contextvars import ContextVar

REQUEST_BUDGET = ContextVar("kis_physical_request_budget", default=None)

CORPORATE_PATHS={f'/uapi/domestic-stock/v1/ksdinfo/{name}' for name in ['rev-split','merger-split','bonus-issue','cap-dcrs']}

class KIS:
    physical_budget_supported = True
    def __init__(self, interval=1.05):
        self.key=os.environ['KIS_APP_KEY']; self.secret=os.environ['KIS_APP_SECRET']
        self.token=None; self.interval=interval; self.last=0.; self.lock=threading.Lock(); self.metrics=[]
        d=self.request('/oauth2/tokenP',body={'grant_type':'client_credentials','appkey':self.key,'appsecret':self.secret})
        self.token=d.get('access_token')
        if not self.token: raise RuntimeError('KIS authentication failed')

    def request(self,path,params=None,tr=None,body=None):
        if not (path=='/oauth2/tokenP' or path.startswith('/uapi/domestic-stock/v1/quotations/') or (body is None and path in CORPORATE_PATHS)):
            raise ValueError('Market-data endpoint only')
        for attempt in range(3):
            with self.lock:
                time.sleep(max(0,self.interval-(time.monotonic()-self.last))); self.last=time.monotonic()
            headers={'Content-Type':'application/json'}
            if tr: headers.update(authorization='Bearer '+self.token,appkey=self.key,appsecret=self.secret,custtype='P',tr_id=tr)
            url='https://openapi.koreainvestment.com:9443'+path+('?' + urllib.parse.urlencode(params) if params else '')
            status=None; d={}; started=time.monotonic()
            budget=REQUEST_BUDGET.get()
            if budget is not None:budget.before_http(path, attempt)
            try:
                req=urllib.request.Request(url,headers=headers,data=json.dumps(body).encode() if body else None)
                with urllib.request.urlopen(req,timeout=30) as r:
                    status=r.status; d=json.load(r); self.last_continuation=r.headers.get('tr_cont','')
            except urllib.error.HTTPError as e:
                status=e.code
                try:d=json.load(e)
                except (ValueError,UnicodeError):pass
            except (OSError,ValueError):pass
            code=str(d.get('msg_cd',d.get('error_code','')))[:40]
            self.metrics.append({'http':status,'code':code,'seconds':round(time.monotonic()-started,4),
                                 'kind':'authentication' if path=='/oauth2/tokenP' else 'market', 'attempt':attempt+1,
                                 'endpoint':path,'symbol':(params or {}).get('FID_INPUT_ISCD'),'started_at_unix':time.time()-(time.monotonic()-started)})
            if status==200 and (d.get('rt_cd')=='0' or (path=='/oauth2/tokenP' and d.get('access_token'))):return d
            if status==429 or code=='EGW00201' or status is None or (status and status>=500):
                time.sleep(3*(attempt+1)); continue
            break
        raise RuntimeError('KIS response rejected: http='+str(status)+' code='+code)

    def prices(self,symbol,start,end):
        p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':start,'FID_INPUT_DATE_2':end,'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0'}
        return self.request('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',p,'FHKST03010100')

    def benchmark(self,code,start,end):
        p={'FID_COND_MRKT_DIV_CODE':'U','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':start,'FID_INPUT_DATE_2':end,'FID_PERIOD_DIV_CODE':'D'}
        return self.request('/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice',p,'FHKUP03500100')
