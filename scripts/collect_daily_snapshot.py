"""Once-per-trading-date KIS collection. Reuse successes; never publish partial results."""
import argparse,concurrent.futures,datetime as dt,gzip,hashlib,io,json,os,re,sys,time,urllib.request,zipfile
from pathlib import Path
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from kis_public_client import KIS
from daily_checkpoint import Journal
from daily_metrics import summarize
from daily_retry import collect_pending
from daily_diagnostics import preserve_failure,redact
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.daily import validate_bundle
from screener.collection_schedule import window
from screener.daily_quote import chart_quote,FIELDS
import duckdb
from collections import Counter

ZONE=ZoneInfo('Asia/Seoul')
def utcnow():return dt.datetime.now(dt.timezone.utc).isoformat()
def readzip(url,member):
    with urllib.request.urlopen(url,timeout=40) as r:raw=r.read(25000000)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:text=z.read(member).decode('cp949')
    return text,hashlib.sha256(raw).hexdigest()

def master(day):
    text,sha=readzip('https://new.real.download.dws.co.kr/common/master/idxcode.mst.zip','idxcode.mst')
    # Fixed widths in this file are bytes, not Unicode character widths.
    index={}
    for s in text.splitlines():
        line=s.encode('cp949')
        if len(line)>=45:index[line[1:5].decode('ascii')]=line[5:45].decode('cp949').strip()
    sources=[{'name':'industry index names','sha256':sha,'observed_at':utcnow()}];rows=[]
    layouts=json.loads(Path('.github/scripts/kis-master-layouts.json').read_text())
    for market,l in layouts.items():
        text,sha=readzip(l['url'],l['member']);sources.append({'market':market,'url':l['url'],'sha256':sha,'observed_at':utcnow()})
        width=sum(l['widths'])
        for line in text.splitlines():
            code=line[:9].strip()
            if not re.fullmatch(r'\d{6}',code):continue
            tail=line[-width:];values=[];offset=0
            for n in l['widths']:values.append(tail[offset:offset+n].strip());offset+=n
            fields=dict(zip(l['columns'],values));listing=values[l['listing_date_index']]
            if values[l['group_index']]!='ST' or values[l['preferred_index']]!='0' or listing>day:continue
            industry=values[3] if values[3]!='0000' else values[2]
            halt=fields.get('거래정지',fields.get('거래정지 여부'))
            if halt not in ['Y','N']:raise ValueError('Unknown halt flag')
            rows.append({'symbol':code,'name':line[21:-width].strip(),'market':market,'listing_date':listing,
                         'halt':halt,'sector':index.get(industry),'industry_code':industry,
                         'shares_thousands':values[50 if market=='KOSPI' else 45],
                         'spac':fields.get('SPAC',fields.get('기업인수목적회사여부'))=='Y'})
    if len(rows)<2000 or len(rows)!=len({r['symbol'] for r in rows}):raise ValueError('Master universe rejected')
    return rows,sources

def kind_industries():
    class Table(HTMLParser):
        def __init__(self):super().__init__();self.rows=[];self.row=[];self.cell=False;self.text=[]
        def handle_starttag(self,tag,attrs):
            if tag=='tr':self.row=[]
            if tag in ('td','th'):self.cell=True;self.text=[]
        def handle_data(self,data):
            if self.cell:self.text.append(data)
        def handle_endtag(self,tag):
            if tag in ('td','th'):self.row.append(''.join(self.text).strip());self.cell=False
            if tag=='tr' and self.row:self.rows.append(self.row)
    url='https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
    with urllib.request.urlopen(url,timeout=40) as r:raw=r.read(10000000)
    for encoding in ['utf-8','euc-kr','cp949']:
        try:text=raw.decode(encoding);break
        except UnicodeDecodeError:continue
    t=Table();t.feed(text)
    return {r[2]:'표준산업 · '+r[3] for r in t.rows if len(r)>5 and re.fullmatch(r'\d{6}',r[2])}

def delisted(row,info,day):
    field='kosdaq_mket_lstg_abol_dt' if row['market']=='KOSDAQ' else 'scts_mket_lstg_abol_dt'
    date=info.get('lstg_abol_dt','')
    return bool(re.fullmatch(r'\d{8}',date) and row['listing_date']<=date<=day and info.get(field)==date)

def fetch_stock(row,api,day,since,result,known_industry,cutoff,deadline,diagnostic_dir=None):
    code=row['symbol'];observations=[]
    try:
        if dt.datetime.now(ZONE)>=cutoff:raise ValueError('Quote date window expired')
        if time.monotonic()>=deadline:raise ValueError('Run time budget exhausted; retry pending only')
        p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':day,'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''}
        flow=api.request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',p,'FHPTJ04160001')
        observations.append({'endpoint':'investor-trade-by-stock-daily','tr_id':'FHPTJ04160001','params':p,'observed_at':utcnow(),'response':flow})
        series=api.prices(code,since,day)
        observations.append({'endpoint':'inquire-daily-itemchartprice','tr_id':'FHKST03010100','params':{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':since,'FID_INPUT_DATE_2':day,'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0'},'observed_at':utcnow(),'response':series})
        price=[r for r in series.get('output2',[]) if r.get('stck_bsop_date')==day]
        matched=[r for r in flow.get('output2',[]) if r.get('stck_bsop_date')==day]
        if len(price)==1 and int(price[0]['acml_vol'])==0:
            return code,None,{'reason':'dated_zero_volume','name':row['name'],'evidence':price[0],'adjusted_prices':series['output2']},None
        if len(matched)!=1 or len(price)!=1:
            info=api.request('/uapi/domestic-stock/v1/quotations/search-stock-info',{'PRDT_TYPE_CD':'300','PDNO':code},'CTPF1002R')['output']
            if delisted(row,info,day):
                fields={k:info.get(k,'') for k in ['lstg_abol_dt','kosdaq_mket_lstg_abol_dt','scts_mket_lstg_abol_dt']}
                return code,None,{'reason':'official_delisting','name':row['name'],'market':row['market'],'fields':fields,'observed_at':utcnow(),'source_run':os.environ.get('GITHUB_RUN_ID')},None
            raise ValueError('Target date missing or duplicate')
        observed=utcnow()
        quote,reasons=chart_quote(series,matched[0],row,day,observed,bool(row.get('sector')))
        source='FHKST03010100.output1'
        if reasons:
            quote=api.request('/uapi/domestic-stock/v1/quotations/inquire-price',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code},'FHKST01010100')['output']
            source='FHKST01010100.output';observed=utcnow()
        if dt.datetime.now(ZONE)>=cutoff:raise ValueError('Quote date window expired')
        record={'flow':matched[0],'adjusted_prices':series['output2'],'chart_header':series.get('output1',{}),
                'quote':{k:quote.get(k) for k in FIELDS},'observed_at':observed,
                'quote_source':source,'quote_fallback_reasons':reasons,'validation_profile':'dated_close_volume_turnover_v2',
                'request_scope':{'market':'J','symbol':code,'end':day,'adjustment':'0'}}
        # Invalid units/alignment remain retryable failures, never durable successes.
        _,checked=validate_bundle({**result,'records':{code:record},'universe':[row],'excluded':{},'failures':{},'status':'complete'})
        if code in known_industry and checked[0]['sector']=='업종 미분류':raise ValueError('Current official industry missing')
        return code,record,None,None
    except Exception as e:
        secrets=[getattr(api,k,None) for k in ['key','secret','token']]
        error=redact(type(e).__name__+': '+str(e),secrets)
        try:preserve_failure(diagnostic_dir,day,code,error,observations,secrets)
        except Exception as diagnostic_error:error+='; diagnostic persistence failed: '+type(diagnostic_error).__name__
        return code,None,None,error

def benchmark_ready(response,day):
    import math
    try:
        rows=response['output2'];dates=[r['stck_bsop_date'] for r in rows]
        return day in dates and len(dates)==len(set(dates)) and all(d<=day and math.isfinite(float(r['bstp_nmix_prpr'])) and float(r['bstp_nmix_prpr'])>0 for d,r in zip(dates,rows))
    except (KeyError,TypeError,ValueError):return False

def collect(day,out):
    started=time.monotonic();today=dt.datetime.now(ZONE)
    target=dt.datetime.strptime(day,'%Y%m%d').date()
    planned,cutoff=window(target,today)
    path=out/'bundle.json.gz';out.mkdir(parents=True,exist_ok=True)
    result=json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {
        'schema':1,'trade_date':day,'exchange':'KRX','records':{},'excluded':{},'failures':{},'finality':'UNVERIFIED',
        'planned_start_at':planned.isoformat(),'started_at':utcnow(),'source_run':os.environ.get('GITHUB_RUN_ID')}
    if result['trade_date']!=day or result['exchange']!='KRX':raise ValueError('Checkpoint date mismatch')
    journal=Journal(out/'successes.jsonl',day)
    restored=journal.restore(result)
    result.setdefault('attempts',[]).append({'started_at':utcnow(),'run_id':os.environ.get('GITHUB_RUN_ID'),'restored':restored,'cached_successes':len(result['records'])})
    result.pop('retry_summary',None)  # The previous run's metrics stay in attempts, not this run.
    phases={};checkpoint_stats={'writes':0,'seconds':0.,'bytes':0}
    deadline=started+140*60
    if result.get('status') in ['complete','closed']:print('Already completed; no API calls');return
    if 'universe' not in result and today.date()!=target:
        raise ValueError('No same-day observed industry master; preserve published snapshot')
    api=KIS()
    with duckdb.connect('snapshot/dashboard.duckdb',read_only=True) as c:
        known_industry={r[0] for r in c.execute("select distinct symbol from memberships where sector!='업종 미분류'").fetchall()}
        entry=c.execute("select value from metadata where key='verified_delistings'").fetchone()
    registry=json.loads(Path('research/verified-delistings.json').read_text())
    if entry:registry.update(json.loads(entry[0]))
    def save():
        t=time.monotonic()
        result.update(updated_at=utcnow(),elapsed_seconds=round(time.monotonic()-started,3),metrics=api.metrics,phase_seconds=phases,checkpoint_cost=checkpoint_stats,call_summary=summarize(api.metrics,result['records']))
        result['attempts'][-1].update(physical_requests=len(api.metrics),retries=sum(m.get('attempt',1)>1 for m in api.metrics),endpoints=dict(Counter(m.get('endpoint','legacy') for m in api.metrics)),elapsed_seconds=round(time.monotonic()-started,3),successful=len(result['records']),failed=len(result['failures']),retry_summary=result.get('retry_summary',{}))
        raw=json.dumps(result,ensure_ascii=False,separators=(',',':'))
        if any(s in raw for s in [api.key,api.secret,api.token]):raise ValueError('Credential in result')
        temp=path.with_suffix('.tmp');temp.write_bytes(gzip.compress(raw.encode(),compresslevel=1,mtime=0));temp.replace(path)
        checkpoint_stats['writes']+=1;checkpoint_stats['seconds']+=time.monotonic()-t;checkpoint_stats['bytes']+=temp.stat().st_size if temp.exists() else path.stat().st_size
    preparation_start=time.monotonic()
    if 'holiday' not in result:
        d=api.request('/uapi/domestic-stock/v1/quotations/chk-holiday',{'BASS_DT':day,'CTX_AREA_FK':'','CTX_AREA_NK':''},'CTCA0903R')
        matched=[r for r in d.get('output',[]) if r.get('bass_dt')==day]
        if len(matched)!=1:raise ValueError('Holiday calendar missing target date')
        result['holiday']=matched[0];save()
    if result['holiday'].get('opnd_yn')=='N':result['status']='closed';save();return
    if result['holiday'].get('opnd_yn')!='Y':raise ValueError('Unverified trading day')
    if 'universe' not in result:
        result['universe'],result['master_sources']=master(day)
        save()
    if 'kind_industries' not in result or result.get('kind_error'):
        try:result['kind_industries']=kind_industries();result.pop('kind_error',None)
        except Exception:result['kind_industries']={};result['kind_error']=True
        save()
    since=(target-dt.timedelta(days=60)).strftime('%Y%m%d')
    result.setdefault('benchmarks',{})
    for market,code in [('KOSPI','0001'),('KOSDAQ','1001')]:
        old=result['benchmarks'].get(market,{})
        if not benchmark_ready(old,day):
            response=api.benchmark(code,since,day)
            if not benchmark_ready(response,day):
                result.setdefault('preparation_errors',{})[market]='Benchmark target date missing/invalid; retry required'
                save();raise ValueError('Benchmark not ready: '+market)
            result['benchmarks'][market]=response
            result.setdefault('preparation_errors',{}).pop(market,None)
            save()
    for row in result['universe']:
        previous=registry.get(row['symbol'])
        if previous and previous['market']==row['market'] and delisted(row,previous['fields'],day):result['excluded'][row['symbol']]=previous
    # Halted companies are not scored, but an explicit dated close can be needed
    # to value yesterday's sector holdings. Missing dates remain missing.
    def halted_prices(row):
        e={'reason':'official_master_halt','name':row['name']}
        try:e['adjusted_prices']=api.prices(row['symbol'],since,day)['output2']
        except Exception as error:e['price_error']=type(error).__name__
        return row['symbol'],e
    halted=[r for r in result['universe'] if r['halt']=='Y' and r['symbol'] not in result['excluded']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for code,e in pool.map(halted_prices,halted):result['excluded'][code]=e
    save()
    phases['preparation']=time.monotonic()-preparation_start
    collection_start=time.monotonic()
    def accept(code,record,excluded,error):
        if error:result['failures'][code]=error
        else:
            result['failures'].pop(code,None)
            if record or excluded:journal.append(code,record,excluded)
            if record:result['records'][code]=record
            if excluded:result['excluded'][code]=excluded
    collect_pending(result,
        lambda row:fetch_stock(row,api,day,since,result,known_industry,cutoff,deadline,out/'diagnostics'),
        accept,save,deadline,cutoff.timestamp())
    phases['stocks']=time.monotonic()-collection_start
    result['status']='complete' if not result['failures'] and len(result['records'])+len(result['excluded'])==len(result['universe']) else 'partial'
    result['completed_at']=utcnow();save()
    if result['status']!='complete':raise RuntimeError('Partial collection; keep published snapshot')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--date',required=True);p.add_argument('--output',type=Path,default=Path('daily-results'));a=p.parse_args();collect(a.date,a.output)
