"""Once-per-trading-date KIS collection. Reuse successes; never publish partial results."""
import argparse,concurrent.futures,datetime as dt,gzip,hashlib,io,json,os,re,sys,time,urllib.request,zipfile
from pathlib import Path
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from kis_public_client import KIS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.daily import validate_bundle

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

def collect(day,out):
    started=time.monotonic();today=dt.datetime.now(ZONE)
    target=dt.datetime.strptime(day,'%Y%m%d').date()
    planned=dt.datetime.combine(target,dt.time(20,30),ZONE)
    cutoff=dt.datetime.combine(target+dt.timedelta(days=1),dt.time(9),ZONE)
    if not planned<=today<cutoff:raise ValueError('Outside verified after-close quote window')
    path=out/'bundle.json.gz';out.mkdir(parents=True,exist_ok=True)
    result=json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {
        'schema':1,'trade_date':day,'exchange':'KRX','records':{},'excluded':{},'failures':{},'finality':'UNVERIFIED',
        'planned_start_at':planned.isoformat(),'started_at':utcnow(),'source_run':os.environ.get('GITHUB_RUN_ID')}
    if result['trade_date']!=day or result['exchange']!='KRX':raise ValueError('Checkpoint date mismatch')
    if result.get('status') in ['complete','closed']:print('Already completed; no API calls');return
    if 'universe' not in result and today.date()!=target:
        raise ValueError('No same-day observed industry master; preserve published snapshot')
    api=KIS()
    def save():
        result.update(updated_at=utcnow(),elapsed_seconds=round(time.monotonic()-started,3),metrics=api.metrics)
        raw=json.dumps(result,ensure_ascii=False,separators=(',',':'))
        if any(s in raw for s in [api.key,api.secret,api.token]):raise ValueError('Credential in result')
        temp=path.with_suffix('.tmp');temp.write_bytes(gzip.compress(raw.encode(),mtime=0));temp.replace(path)
    if 'holiday' not in result:
        d=api.request('/uapi/domestic-stock/v1/quotations/chk-holiday',{'BASS_DT':day,'CTX_AREA_FK':'','CTX_AREA_NK':''},'CTCA0903R')
        matched=[r for r in d.get('output',[]) if r.get('bass_dt')==day]
        if len(matched)!=1:raise ValueError('Holiday calendar missing target date')
        result['holiday']=matched[0];save()
    if result['holiday'].get('opnd_yn')=='N':result['status']='closed';save();return
    if result['holiday'].get('opnd_yn')!='Y':raise ValueError('Unverified trading day')
    if 'universe' not in result:
        result['universe'],result['master_sources']=master(day)
        try:result['kind_industries']=kind_industries()
        except Exception:result['kind_industries']={} # missing mapping stays unknown, never carried forward
        save()
    since=(target-dt.timedelta(days=60)).strftime('%Y%m%d')
    if 'benchmarks' not in result:
        result['benchmarks']={m:api.benchmark(code,since,day) for m,code in [('KOSPI','0001'),('KOSDAQ','1001')]};save()
    for row in result['universe']:
        if row['halt']=='Y':result['excluded'][row['symbol']]={'reason':'official_master_halt','name':row['name']}
    def fetch(row):
        code=row['symbol']
        try:
            if dt.datetime.now(ZONE)>=cutoff:raise ValueError('Quote date window expired')
            p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':day,'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''}
            flow=api.request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',p,'FHPTJ04160001')
            series=api.prices(code,since,day)
            price=[r for r in series.get('output2',[]) if r.get('stck_bsop_date')==day]
            matched=[r for r in flow.get('output2',[]) if r.get('stck_bsop_date')==day]
            if len(price)==1 and int(price[0]['acml_vol'])==0:
                return code,None,{'reason':'dated_zero_volume','name':row['name'],'evidence':price[0]},None
            if len(matched)!=1 or len(price)!=1:raise ValueError('Target date missing or duplicate')
            quote=api.request('/uapi/domestic-stock/v1/quotations/inquire-price',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code},'FHKST01010100')['output']
            observed=utcnow()
            if dt.datetime.now(ZONE)>=cutoff:raise ValueError('Quote date window expired')
            keep=['stck_shrn_iscd','stck_prpr','acml_vol','lstn_stcn','hts_avls','bstp_kor_isnm','iscd_stat_cls_code']
            record={'flow':matched[0],'adjusted_prices':series['output2'],'quote':{k:quote.get(k) for k in keep},'observed_at':observed}
            # Invalid units/alignment remain retryable failures, never durable successes.
            validate_bundle({**result,'records':{code:record},'universe':[row],'excluded':{},'failures':{},'status':'complete'})
            return code,record,None,None
        except Exception as e:return code,None,None,type(e).__name__+': '+str(e)
    for attempt in range(2):
        pending=[r for r in result['universe'] if r['symbol'] not in result['records'] and r['symbol'] not in result['excluded']]
        if not pending:break
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for i,(code,record,excluded,error) in enumerate(pool.map(fetch,pending),1):
                if error:result['failures'][code]=error
                else:
                    result['failures'].pop(code,None)
                    if record:result['records'][code]=record
                    if excluded:result['excluded'][code]=excluded
                if i%25==0:save();print(json.dumps({'successful':len(result['records']),'excluded':len(result['excluded']),'failed':len(result['failures'])}),flush=True)
        save()
    result['status']='complete' if not result['failures'] and len(result['records'])+len(result['excluded'])==len(result['universe']) else 'partial'
    result['completed_at']=utcnow();save()
    if result['status']!='complete':raise RuntimeError('Partial collection; keep published snapshot')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--date',required=True);p.add_argument('--output',type=Path,default=Path('daily-results'));a=p.parse_args();collect(a.date,a.output)
