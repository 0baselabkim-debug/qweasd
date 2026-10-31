import sys,json,gzip,datetime,duckdb
from pathlib import Path
from collections import Counter
repo=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(repo))
from collector_v1.core import Store
from collector_v1.experiment.engine import replay,identity
from collector_v1.experiment.storage import events
from collector_v1.providers import task
root=Path(sys.argv[1]).resolve();config=json.load(open(root/'all-jobs.json'));jobs={j['id']:j for j in config['jobs']};calendar=json.load(open(root/'session-calendar.json'))['days'];book=json.loads(gzip.decompress((root/'verified-price-pool-v2/bars.json.gz').read_bytes()));checks=[]
cachefile=root/'independent-checks.json'
if cachefile.exists():checks=json.load(open(cachefile))
seen={r['job'] for r in checks}
with duckdb.connect(str(root/'published-source/dashboard.duckdb'),read_only=True) as db:
 dbflow={}
 for code,d,a,q in db.execute('select symbol,trade_date,net_amount,net_quantity from flows').fetchall():dbflow.setdefault(code,{})[str(d)]=(a,q,None)
def normalized(raw,price):
 a=q=inst=None
 try:
  quantity=int(raw['frgn_ntby_qty']);amount=int(raw['frgn_ntby_tr_pbmn']);buy=int(raw['frgn_shnu_vol']);sell=int(raw['frgn_seln_vol']);ba=int(raw['frgn_shnu_tr_pbmn']);sa=int(raw['frgn_seln_tr_pbmn'])
  valid=(min(buy,sell,ba,sa)>=0 and buy-sell==quantity and abs(ba-sa-amount)<=1 and price is not None and all(int(raw[k])==int(price[k]) for k in ['stck_clpr','acml_vol','acml_tr_pbmn']) and min(int(raw[k]) for k in ['stck_clpr','acml_vol','acml_tr_pbmn'])>0)
  if valid:a,q=amount*1000000,quantity
 except (KeyError,ValueError):pass
 try:
  n=int(raw['orgn_ntby_qty']);b=int(raw['orgn_shnu_vol']);s=int(raw['orgn_seln_vol'])
  if min(b,s)>=0 and b-s==n:inst=n
 except (KeyError,ValueError):pass
 return a,q,inst
for f in sorted(Path(config['output_root']).glob('*/result.json')):
 result=json.load(open(f));jid=result['job']
 if jid in seen or result['status']!='PASS_OFFLINE_REPROCESSING':continue
 job=jobs[jid];asof=job['proof']['asof'];data=json.loads(gzip.decompress(Path(result['evidence']['directory']).joinpath('market.json.gz').read_bytes()));days=[d for d in calendar if d<=asof];flows={};benchmarks={}
 if job['source_kind']=='historical-collector':
  folder=(Path(job['directory'])/job['proof']['report_path']).parent;store=Store(folder);ev=events(folder/'tasks.jsonl');t0=ev[0]['task'];g=t0.get('generation_id');rnd=t0['round'];cache=replay(store,rnd,asof=asof,generation_id=g)
  for m in ['KOSPI','KOSDAQ']:
   t=task('index',asof,round=rnd,market=m,**({'generation_id':g} if g else {}));e=cache[identity(t)];o=json.loads(store.raw(e))
   benchmarks[m]={datetime.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat():float(r['bstp_nmix_prpr']) for r in o['output2']}
  if asof>'2026-10-29':days=sorted(benchmarks['KOSPI'])
  for row in data['stocks']:
   code=row['stock_code'];market=row['market'];raws={}
   for kind in ['flow','price']:
    t=task(kind,asof,round=rnd,symbol=code,market=market,**({'generation_id':g} if g else {}));e=cache.get(identity(t));o=json.loads(store.raw(e)) if e else {}
    raws[kind]={datetime.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat():r for r in o.get('output2',[])}
   flows[code]={d:normalized(r,raws['price'].get(d)) for d,r in raws['flow'].items()}
 elif job['source_kind']=='historical-checkpoint':
  b=json.loads(gzip.decompress(Path(job['directory']).joinpath('bundle.json.gz').read_bytes()))
  for m,idx in b['benchmarks'].items():benchmarks[m]={datetime.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat():float(r['bstp_nmix_prpr']) for r in idx['output2']}
  for code,r in b['records'].items():
   price={v['stck_bsop_date']:v for v in r['adjusted_prices']};flows[code]={datetime.datetime.strptime(v['stck_bsop_date'],'%Y%m%d').date().isoformat():normalized(v,price.get(v['stck_bsop_date'])) for v in (r.get('analysis_source',{}).get('flow_rows') or [r['flow']])}
 else:
  flows=dbflow
  with duckdb.connect(str(root/'published-source/dashboard.duckdb'),read_only=True) as db:
   for m in ['KOSPI','KOSDAQ']:benchmarks[m]={str(d):v for d,v in db.execute('select trade_date,close from benchmarks where market=?',[m]).fetchall()}
 counts=Counter()
 for row in data['stocks']:
  code=row['stock_code'];market=row['market'];mapping=flows.get(code,{})
  for n in [1,3,5,20]:
   ds=days[-n:]
   for ix,group,key in [(0,'foreign_flow_metrics',f'net_{n}d'),(1,'foreign_flow_metrics',f'net_quantity_{n}d'),(2,'institution_flow_metrics',f'net_quantity_{n}d')]:
    values=[mapping.get(d,(None,None,None))[ix] for d in ds];expect=sum(values) if len(ds)==n and all(v is not None for v in values) else None
    actual=row[group][key]['value']
    assert actual==expect,(jid,code,key,actual,expect);counts['flow_windows']+=1
  assert row['institution_flow_metrics']['net_1d']['value'] is None
  assert row['data_quality']['historical_backtest_eligible'] is False
  assert row['analysis_mode']=='RETROSPECTIVE_REANALYSIS'
  prices={d:book.get('|'.join((code,d,market))) for d in days};prices={d:v['value'] for d,v in prices.items() if v}
  pm=row['price_trend_metrics']
  if pm['close']['value'] is not None and asof in prices:
   for field,outkey in [('close','close'),('open','current_open'),('high','current_high'),('low','current_low'),('volume','volume'),('turnover','current_turnover')]:
    assert pm[outkey]['value']==prices[asof][field],(jid,code,outkey);counts['current_price_fields']+=1
  for n in [5,20,60,120]:
   actual=pm[f'sma_{n}']['value'];span=days[-n:]
   if actual is not None and len(span)==n and all(d in prices for d in span):
    expected=sum(prices[d]['close'] for d in span)/n;assert actual==expected,(jid,code,n,actual,expected);counts['SMA']+=1
  suffix=[]
  for d in reversed(days):
   if d not in prices:break
   suffix.append(d)
  suffix.reverse()
  atr=pm['atr_14']['value']
  if atr is not None and len(suffix)==row['data_quality']['verified_contiguous_price_days'] and len(suffix)>=15:
   tr=[max(prices[b]['high']-prices[b]['low'],abs(prices[b]['high']-prices[a]['close']),abs(prices[b]['low']-prices[a]['close'])) for a,b in zip(suffix,suffix[1:])];expected=sum(tr[:14])/14
   for v in tr[14:]:expected=(expected*13+v)/14
   assert expected==atr,(jid,code,'ATR',atr,expected);counts['ATR']+=1
  for n in [5,20]:
   actual=row['relative_strength_metrics'][f'market_excess_{n}d_pp']['value'];span=days[-n-1:];bm=benchmarks.get(market,{})
   if actual is not None and len(span)==n+1 and all(d in prices and d in bm for d in span):
    expected=(prices[span[-1]]['close']/prices[span[0]]['close']-1)*100-(bm[span[-1]]/bm[span[0]]-1)*100
    assert expected==actual,(jid,code,'RS',n,actual,expected);counts['relative_strength']+=1
 checks.append(dict(job=jid,asof=asof,status='PASS_INDEPENDENT_RAW_SUMS_AND_PRICE_RECALCULATION',stocks=len(data['stocks']),counts=dict(counts)))
 cachefile.write_text(json.dumps(checks,indent=2));print(jid,dict(counts),flush=True)
print('checked jobs',len(checks),'checks',dict(sum((Counter(v['counts']) for v in checks),Counter())),flush=True)
