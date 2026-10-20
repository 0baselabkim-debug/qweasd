"""Read-only KIS pilot: hard budget at every physical HTTP boundary, no publish."""
import datetime as dt,hashlib,json,os,time,urllib.request,urllib.parse,urllib.error
from pathlib import Path

class BudgetExceeded(RuntimeError):pass

class Budget:
    def __init__(self,maximum=40,seconds=600,interval=1.05,clock=time.monotonic,sleep=time.sleep):
        self.maximum=maximum;self.seconds=seconds;self.interval=interval;self.clock=clock;self.sleep=sleep
        self.started=clock();self.last=None;self.count=0;self.metrics=[]
    def acquire(self,path,attempt):
        if self.count>=self.maximum:raise BudgetExceeded('physical_request_limit')
        remaining=self.seconds-(self.clock()-self.started)
        delay=max(0,self.interval-(self.clock()-self.last)) if self.last is not None else 0
        if remaining<=delay+0.1:raise BudgetExceeded('elapsed_time_limit')
        self.sleep(delay)
        if self.clock()-self.started>=self.seconds:raise BudgetExceeded('elapsed_time_limit')
        self.count+=1;self.last=self.clock()
        metric={'number':self.count,'endpoint':path,'attempt':attempt+1,
                'kind':'authentication' if path=='/oauth2/tokenP' else 'market','started_at':utcnow()}
        self.metrics.append(metric)
        return metric,min(25,max(0.1,self.seconds-(self.clock()-self.started)))
    def backoff(self,seconds):
        if self.clock()-self.started+seconds>=self.seconds:raise BudgetExceeded('elapsed_time_limit')
        self.sleep(seconds)

def utcnow():return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(raw):return hashlib.sha256(raw).hexdigest()

class Client:
    def __init__(self,budget):
        self.budget=budget;self.key=os.environ.get('KIS_APP_KEY');self.secret=os.environ.get('KIS_APP_SECRET');self.token=None
        if not self.key or not self.secret:raise RuntimeError('repository_secrets_unavailable')
    def request(self,path,params=None,tr=None,body=None):
        for attempt in range(3):
            metric,timeout=self.budget.acquire(path,attempt)
            headers={'Content-Type':'application/json'}
            if tr:headers.update(authorization='Bearer '+self.token,appkey=self.key,appsecret=self.secret,custtype='P',tr_id=tr)
            url='https://openapi.koreainvestment.com:9443'+path+('?' + urllib.parse.urlencode(params) if params else '')
            start=time.monotonic();response={};status=None
            try:
                req=urllib.request.Request(url,headers=headers,data=json.dumps(body).encode() if body else None)
                with urllib.request.urlopen(req,timeout=timeout) as r:status=r.status;response=json.load(r)
            except urllib.error.HTTPError as e:
                status=e.code
                try:response=json.load(e)
                except (ValueError,UnicodeError):pass
            except (OSError,ValueError):pass
            code=str(response.get('msg_cd',response.get('error_code','')))[:40]
            metric.update(http=status,code=code,seconds=round(time.monotonic()-start,4))
            if status==200 and (response.get('rt_cd')=='0' or (path=='/oauth2/tokenP' and response.get('access_token'))):return response
            if status in [429,500,502,503,504] or status is None or code=='EGW00201':
                self.budget.backoff(3*(attempt+1));continue
            break
        # Never log request headers, token, credentials, or arbitrary error response text.
        raise RuntimeError('response_rejected:http='+str(status)+';code='+code)
    def auth(self):
        data=self.request('/oauth2/tokenP',body={'grant_type':'client_credentials','appkey':self.key,'appsecret':self.secret})
        self.token=data.get('access_token')
        if not self.token:raise RuntimeError('authentication_failed')
    def chart(self,symbol,start,end,adjusted):
        p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':start.replace('-',''),
           'FID_INPUT_DATE_2':end.replace('-',''),'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0' if adjusted else '1'}
        r=self.request('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',p,'FHKST03010100')
        return {'observed_at':utcnow(),'params':p,'output1':r.get('output1',{}),'output2':r.get('output2',[]),
                'rt_cd':r.get('rt_cd'),'msg_cd':r.get('msg_cd')}

def main(config_path='research/pilot-config.json',out_path='research-output'):
    config=json.loads(Path(config_path).read_text());out=Path(out_path);out.mkdir(parents=True,exist_ok=True)
    # Hard-coded ceilings cannot be raised by configuration.
    assert len(config['targets'])==10 and len({x['symbol'] for x in config['targets']})==10
    budget=Budget(min(40,config['max_physical_requests']),min(600,config['max_elapsed_seconds']),max(1.05,config['interval_seconds']))
    report={'started_at':utcnow(),'source_asof':config['asof'],'run_id':os.environ.get('GITHUB_RUN_ID'),'records':{},'errors':{},'complete':False}
    try:
        client=Client(budget);client.auth()
        for target in config['targets']:
            code=target['symbol'];report['records'][code]={}
            for kind,adjusted in [('adjusted',True),('raw',False)]:
                start=budget.count
                try:
                    record=client.chart(code,config['start'],config['end'],adjusted)
                    raw=json.dumps(record,ensure_ascii=False,sort_keys=True).encode()
                    # Only public market response fields are persisted.
                    name=f'{code}-{kind}-{digest(raw)}.json';(out/name).write_bytes(raw)
                    report['records'][code][kind]={'file':name,'sha256':digest(raw),'rows':len(record['output2']),'requests':budget.count-start}
                except BudgetExceeded:raise
                except RuntimeError as e:report['errors'][code+':'+kind]=str(e)
            save(out,report,budget)
        report['complete']=len(report['records'])==10 and all(len(v)==2 for v in report['records'].values())
    except (RuntimeError,BudgetExceeded) as e:report['stopped_reason']=str(e)
    finally:
        report['completed_at']=utcnow();save(out,report,budget)
    print(json.dumps({'complete':report['complete'],'physical_requests':budget.count,'elapsed_seconds':report['elapsed_seconds'],'errors':len(report['errors'])}))
    if not report['complete']:raise SystemExit(1)

def save(out,report,budget):
    report.update(physical_requests=budget.count,elapsed_seconds=round(budget.clock()-budget.started,3),metrics=budget.metrics)
    (out/'run.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
