"""Bounded after-close, read-only comparison; never publishes market data."""
import datetime as dt,json,sys,time,urllib.request,urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo
from kis_public_client import KIS
from collect_daily_snapshot import master,fetch_stock
import argparse

OUT=Path('reports/daily-optimization/probe');OUT.mkdir(parents=True,exist_ok=True)
def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def main(optimized=False):
    specs=[]
    for name in ([] if optimized else ['inquire-daily-itemchartprice','investor-trade-by-stock-daily','inquire-price']):
        url='https://apiportal.koreainvestment.com/api/apis/public/detail?'+urllib.parse.urlencode({'accessUrl':'/uapi/domestic-stock/v1/quotations/'+name})
        try:
            d=json.load(urllib.request.urlopen(url,timeout=20))
            properties=json.load(urllib.request.urlopen('https://apiportal.koreainvestment.com/api/apis/guide/property/'+d['id'],timeout=20))
            specs.append({'endpoint':name,'url':url,'detail':d,'properties':properties})
        except Exception as e:specs.append({'endpoint':name,'url':url,'error':type(e).__name__,'http':getattr(e,'code',None)})
    save('specs.json',specs)
    now=dt.datetime.now(ZoneInfo('Asia/Seoul'))
    if now.weekday()>4 or not dt.time(15,30)<=now.time()<dt.time(23,59):raise ValueError('After-close probe only')
    day=now.strftime('%Y%m%d');since=(now-dt.timedelta(days=60)).strftime('%Y%m%d')
    api=KIS();holiday=api.request('/uapi/domestic-stock/v1/quotations/chk-holiday',{'BASS_DT':day,'CTX_AREA_FK':'','CTX_AREA_NK':''},'CTCA0903R')
    if len([r for r in holiday.get('output',[]) if r.get('bass_dt')==day and r.get('opnd_yn')=='Y'])!=1:raise ValueError('Not verified trading date')
    universe,sources=master(day);by_code={r['symbol']:r for r in universe}
    symbols=['005930','000660','005380','035420','105560','000250','000440','247540','086520','042510']
    result={'day':day,'observed_at':now.isoformat(),'master_sources':sources,'records':{},'errors':{}}
    context={'schema':1,'trade_date':day,'exchange':'KRX','status':'complete','finality':'UNVERIFIED','master_sources':sources,'kind_industries':{}}
    if optimized:context['benchmarks']={m:api.benchmark(c,since,day) for m,c in [('KOSPI','0001'),('KOSDAQ','1001')]}
    start=time.monotonic()
    for code in symbols:
        t=time.monotonic();n=len(api.metrics)
        try:
            if optimized:
                cutoff=dt.datetime.combine(now.date()+dt.timedelta(days=1),dt.time(9),ZoneInfo('Asia/Seoul'))
                _,record,excluded,error=fetch_stock(by_code[code],api,day,since,context,set(),cutoff,start+300)
                result['records'][code]={'record':record,'excluded':excluded,'error':error,'seconds':time.monotonic()-t,'physical_requests':len(api.metrics)-n}
                if error:result['errors'][code]=error
                result.update(metrics=api.metrics,seconds=time.monotonic()-start)
                save('optimized.json',result)
                continue
            flow=api.request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':day,'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''},'FHPTJ04160001')
            series=api.prices(code,since,day)
            quote=api.request('/uapi/domestic-stock/v1/quotations/inquire-price',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code},'FHKST01010100')
            result['records'][code]={'master':by_code[code],'flow':flow,'series':series,'quote':quote,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'seconds':time.monotonic()-t,'physical_requests':len(api.metrics)-n}
        except Exception as e:result['errors'][code]=str(e)
        result.update(metrics=api.metrics,seconds=time.monotonic()-start)
        raw=json.dumps(result)
        if any(s and s in raw for s in [api.key,api.secret,api.token]):raise ValueError('Secret in evidence')
        save('responses.json',result)
    print(json.dumps({'records':len(result['records']),'errors':len(result['errors']),'physical_requests':len(api.metrics)}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--optimized',action='store_true');a=p.parse_args();main(a.optimized)
