"""One-off 001420 / 20261020 observation. Never alter a collection checkpoint."""
import datetime as dt,hashlib,json,sys,time
from pathlib import Path
from kis_public_client import KIS,REQUEST_BUDGET
class Budget:
    def __init__(self):self.calls=0;self.start=time.monotonic()
    def before_http(self,path,attempt):
        if self.calls>=10 or time.monotonic()-self.start>=180:raise RuntimeError('Probe budget exhausted')
        self.calls+=1

def main():
    dest=Path('reports/taewon-20261020');dest.mkdir(parents=True,exist_ok=True)
    budget=Budget();REQUEST_BUDGET.set(budget);api=KIS();out=[]
    stock='001420';day='20261020'
    requests=[('flow','/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily','FHPTJ04160001',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':stock,'FID_INPUT_DATE_1':day,'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''})]
    for kind,adj in [('adjusted','0'),('raw','1')]:
        requests.append((kind,'/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice','FHKST03010100',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':stock,'FID_INPUT_DATE_1':'20260821','FID_INPUT_DATE_2':day,'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':adj}))
    for kind,path,tr,params in requests:
        started=dt.datetime.now(dt.timezone.utc).isoformat();data=api.request(path,params,tr)
        observed=dt.datetime.now(dt.timezone.utc).isoformat()
        record={'kind':kind,'endpoint':path,'tr_id':tr,'params':params,'started_at':started,'observed_at':observed,'response':data,'not_original_failure_response':True}
        raw=json.dumps(record,ensure_ascii=False,indent=2)
        if any(s and s in raw for s in [api.key,api.secret,api.token]):raise ValueError('Secret found; abort persistence')
        sha=hashlib.sha256(raw.encode()).hexdigest();(dest/(kind+'-'+sha+'.json')).write_text(raw+'\n')
        rows=[r for r in data.get('output2',[]) if r.get('stck_bsop_date')==day]
        out.append({'kind':kind,'file':kind+'-'+sha+'.json','observed_at':observed,'target_rows':rows})
    report={'symbol':stock,'day':day,'physical_requests':budget.calls,'metrics':api.metrics,'observations':out}
    (dest/'probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'physical_requests':budget.calls,'rows':[{ 'kind':r['kind'],'rows':r['target_rows']} for r in out]},ensure_ascii=False))
if __name__=='__main__':main()
