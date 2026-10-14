"""Acquire adjusted daily prices and market indices, without inventing membership history."""
import concurrent.futures,datetime as dt,gzip,json,os,time
from pathlib import Path
import duckdb
from kis_public_client import KIS

def main():
    started=time.monotonic();out=Path('staging/sector_support.json.gz')
    with duckdb.connect('snapshot/dashboard.duckdb',read_only=True) as c:
        end=c.execute('select max(trade_date) from flows').fetchone()[0]
        start=c.execute('select min(trade_date) from prices').fetchone()[0]
        targets=[r[0] for r in c.execute('select distinct symbol from flows where trade_date=? order by symbol',[end]).fetchall()]
    result={'asof':str(end),'start':str(start),'exchange':'KRX','adjustment':'FID_ORG_ADJ_PRC=0','records':{},'failures':{},'benchmarks':{}}
    if out.exists():
        previous=json.loads(gzip.decompress(out.read_bytes()))
        if previous['asof']==str(end) and previous['start']==str(start):result=previous
    api=KIS();start_s=start.strftime('%Y%m%d');end_s=end.strftime('%Y%m%d')
    for market,code in [('KOSPI','0001'),('KOSDAQ','1001')]:
        if market not in result['benchmarks']:
            result['benchmarks'][market]=api.benchmark(code,start_s,end_s)
    def fetch(code):
        try:
            d=api.prices(code,start_s,end_s);rows=d.get('output2',[])
            if not rows or any(not r.get('stck_bsop_date') or float(r['stck_clpr'])<=0 for r in rows):raise ValueError('invalid adjusted prices')
            return code,{'rows':rows,'received_at':dt.datetime.now(dt.timezone.utc).isoformat()},None
        except Exception as e:return code,None,type(e).__name__+': '+str(e)
    def save():
        result.update(run_id=os.environ.get('GITHUB_RUN_ID'),elapsed_seconds=round(time.monotonic()-started,3),metrics=api.metrics)
        raw=json.dumps(result,ensure_ascii=False,separators=(',',':'))
        if any(s in raw for s in [api.key,api.secret,api.token]):raise ValueError('credential in result')
        out.parent.mkdir(exist_ok=True);temp=out.with_suffix('.tmp');temp.write_bytes(gzip.compress(raw.encode(),mtime=0));temp.replace(out)
    # Validate ten controls before expanding to the already authorized full universe.
    controls=['005930','000660','035420','010120','267260','034020','277810','454910','012450','079550']
    for code in controls:
        if code not in result['records']:
            code,record,error=fetch(code)
            if error:raise RuntimeError('Control rejected '+code+' '+error)
            result['records'][code]=record
    save();print(json.dumps({'controls':10,'benchmarks':{m:len(d.get('output2',[])) for m,d in result['benchmarks'].items()}}),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for i,(code,record,error) in enumerate(pool.map(fetch,[s for s in targets if s not in result['records']]),1):
            if error:result['failures'][code]=error
            else:result['records'][code]=record;result['failures'].pop(code,None)
            if i%50==0:save();print(json.dumps({'success':len(result['records']),'failed':len(result['failures'])}),flush=True)
    save();print(json.dumps({'success':len(result['records']),'failed':len(result['failures']),'elapsed_seconds':result['elapsed_seconds']}),flush=True)
    if result['failures']:raise SystemExit(1)

if __name__=='__main__':main()
