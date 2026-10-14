"""Add real adjusted closes/indices to a candidate, keeping all old raw observations intact."""
import argparse,datetime as dt,gzip,hashlib,json,math,shutil,sys
from pathlib import Path
import duckdb
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.store import load
from screener.scoring import evaluate

SCORE_COLUMNS=['symbol','net_amount','close','market_cap','revenue_growth','op_growth','roe','debt_ratio','per','pbr','score_foreign_flow','score_growth','score_profitability_health','score_valuation','score_activity','total_score','evaluation_rank']
def fingerprint(data,asof):
    x,s,_=evaluate(data,asof)
    raw=x[SCORE_COLUMNS].sort_values('symbol').to_json(orient='records',double_precision=15)
    return hashlib.sha256(raw.encode()).hexdigest(),x,s

def build(source,baseline,destination):
    response=json.loads(gzip.decompress(Path(source).read_bytes()));asof=response['asof'];start=response['start']
    if response['exchange']!='KRX' or response['adjustment']!='FID_ORG_ADJ_PRC=0':raise ValueError('Unsupported scope/adjustment')
    before=load(baseline);score_before,x,_=fingerprint(before,asof);targets=set(x.symbol)
    if set(response['records'])!=targets or response['failures']:raise ValueError('Incomplete adjusted universe')
    if Path(baseline).resolve()==Path(destination).resolve():raise ValueError('Use a candidate')
    old={(r.symbol,str(r.trade_date.date())):r for r in before['prices'].itertuples()}
    updates=[];missing_price_keys=[];end_checks=0
    for code,record in response['records'].items():
        dates=[r['stck_bsop_date'] for r in record['rows']]
        if len(dates)!=len(set(dates)):raise ValueError('Duplicate adjusted dates')
        if asof.replace('-','') not in dates:raise ValueError('Missing analysis-date adjusted close '+code)
        for r in record['rows']:
            day=str(dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date());close=float(r['stck_clpr'])
            if not start<=day<=asof or not math.isfinite(close) or close<=0:raise ValueError('Invalid adjusted observation')
            if (code,day) not in old:missing_price_keys.append({'symbol':code,'date':day});continue
            if day==asof:
                if close!=old[code,day].close:raise ValueError('Latest adjusted close mismatch '+code)
                end_checks+=1
            updates.append((close,code,day))
    benchmark=[]
    for market,d in response['benchmarks'].items():
        if market not in ['KOSPI','KOSDAQ'] or d.get('rt_cd')!='0':raise ValueError('Invalid benchmark source')
        dates=[r['stck_bsop_date'] for r in d['output2']]
        if len(dates)!=len(set(dates)) or asof.replace('-','') not in dates:raise ValueError('Benchmark duplicate/missing analysis date')
        for r in d['output2']:
            day=str(dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date());v=float(r['bstp_nmix_prpr'])
            if not start<=day<=asof or not math.isfinite(v) or v<=0:raise ValueError('Invalid benchmark date/value')
            benchmark.append((market,day,v))
    dest=Path(destination);dest.parent.mkdir(exist_ok=True);temp=dest.with_suffix('.building.duckdb');shutil.copy2(baseline,temp)
    try:
        with duckdb.connect(str(temp)) as c:
            c.execute('BEGIN')
            c.executemany('update prices set adjusted_close=? where symbol=? and trade_date=?',updates)
            c.executemany('insert or ignore into benchmarks values (?,?,?)',benchmark)
            c.executemany('insert or ignore into calendar values (?,?)',[(m,d) for m,d,v in benchmark])
            c.execute('insert or replace into metadata values (?,?)',['sector_support',json.dumps({'source_run':response['run_id'],'adjusted_price_source':'KIS J FHKST03010100 FID_ORG_ADJ_PRC=0','benchmark_source':'KIS U FHKUP03500100 0001/1001','start':start,'asof':asof,'historical_membership':'NOT_OBTAINED; never backfilled','adjustment_vintage':'response at collection time, not archived historical vintage'})])
            c.execute('COMMIT');c.execute('CHECKPOINT')
        after=load(temp);score_after,x,s=fingerprint(after,asof)
        if score_before!=score_after:raise ValueError('Existing stock scores changed')
        for table in ['flows','financials','memberships','instruments','exclusions']:
            keys={'flows':['symbol','trade_date'],'financials':['symbol','period_end','receipt'],'memberships':['symbol','valid_from'],'instruments':['symbol'],'exclusions':['symbol','trade_date']}[table]
            if before[table].sort_values(keys).reset_index(drop=True).to_json()!=after[table].sort_values(keys).reset_index(drop=True).to_json():raise ValueError('Existing data changed '+table)
        pcols=[c for c in before['prices'] if c!='adjusted_close']
        if before['prices'][pcols].sort_values(['symbol','trade_date']).reset_index(drop=True).to_json()!=after['prices'][pcols].sort_values(['symbol','trade_date']).reset_index(drop=True).to_json():raise ValueError('Raw prices changed')
        # A missing historic industry must remain missing even with all price data available.
        if before['memberships'].valid_from.min().strftime('%Y-%m-%d')==asof and s.total_score.notna().any():raise ValueError('Historical membership was assumed')
        audit={'asof':asof,'source_run':response['run_id'],'adjusted_symbols':len(targets),'adjusted_rows':len(updates),'benchmark_rows':len(benchmark),'benchmark_markets':sorted(response['benchmarks']),
               'latest_close_checks':end_checks,'missing_raw_price_keys':missing_price_keys,'elapsed_seconds':response['elapsed_seconds'],'requests':len(response['metrics']),
               'rate_limit_responses':sum(m['http']==429 or m['code']=='EGW00201' for m in response['metrics']),
               'stock_score_sha256_before':score_before,'stock_score_sha256_after':score_after,'complete_stock_scores':int(x.total_score.notna().sum()),'complete_sector_scores':int(s.total_score.notna().sum()),
               'raw_observations_preserved':True,'historical_membership_obtained':False,'sector_components':{k:int(s['score_'+k].notna().sum()) for k in ['foreign_intensity','foreign_persistence','relative_strength','activity']}}
        temp.replace(dest);return audit
    except Exception:temp.unlink(missing_ok=True);raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('staging/sector_support.json.gz'));p.add_argument('--baseline',type=Path,default=Path('snapshot/dashboard.duckdb'));p.add_argument('--output',type=Path,default=Path('data/sector-candidate.duckdb'));a=p.parse_args()
    audit=build(a.source,a.baseline,a.output);Path('reports/sector/support-validation.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n');print(json.dumps(audit,ensure_ascii=False))
