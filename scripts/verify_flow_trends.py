"""Offline independent reconciliation against saved original KIS responses. No network calls."""
import gzip,hashlib,json,math,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.store import load
from screener.scoring import evaluate
from screener.flow_trends import stock_trends,group_trends
from scripts.import_sector_support import fingerprint

OUT=Path('reports/flow-trends');OUT.mkdir(exist_ok=True)
def read(p):
    p=Path(p);return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_text())
def equal(a,b):return (pd.isna(a) and b is None) or (b is not None and a==b)
def clean(v):return json.loads(v.to_json(orient='records',date_format='iso'))

d=load('snapshot/dashboard.duckdb');asof=str(d['flows'].trade_date.max().date());sha,x,s=fingerprint(d,asof)
before=read(OUT/'before.json');dbhash=hashlib.sha256(Path('snapshot/dashboard.duckdb').read_bytes()).hexdigest()
assert dbhash==before['database_sha256'] and sha==before['stock_score_sha256']
original={}
for code,r in read('staging/kis_history_20261014.json.gz')['records'].items():
    for row in r['rows']:original[code,pd.Timestamp(row['stck_bsop_date'])]=int(row['frgn_ntby_tr_pbmn'])*1000000
for code,r in read('research/daily-replay.json.gz')['records'].items():
    f=r['flow'];original[code,pd.Timestamp(f['stck_bsop_date'])]=int(f['frgn_ntby_tr_pbmn'])*1000000
for r in d['flows'].itertuples():assert original[r.symbol,r.trade_date]==r.net_amount
m,matrix=stock_trends(d,x,asof);g,series=group_trends(d,x,matrix,asof)
dates=list(matrix.index);daily=[];checks=0
for day in dates:
    values=[original.get((code,day)) for code in x.symbol]
    observed=sum(v is not None for v in values);total=sum(v for v in values if v is not None)
    assert total==d['flows'].loc[(d['flows'].trade_date==day)&d['flows'].symbol.isin(x.symbol),'net_amount'].sum()
    daily.append({'trade_date':str(day.date()),'original_observed_total_krw':total,'received_symbols':observed,'expected_symbols':len(x),'missing_symbols':len(x)-observed})
for (market,sector),group in x.groupby(['market','sector']):
    frame=series[market,sector];complete=[]
    for day in dates:
        values=[original.get((code,day)) for code in group.symbol]
        amount=sum(values) if all(v is not None for v in values) else None
        assert equal(frame.loc[frame.trade_date==day,'net_amount'].item(),amount);checks+=1;complete.append(amount)
    r=g[(g.market==market)&(g.sector==sector)].iloc[0]
    for n in [5,20]:
        values=complete[-n:];expected=sum(values) if len(values)==n and all(v is not None for v in values) else None
        assert equal(r[f'net_{n}d'],expected)
for code in x.symbol:
    for n in [5,20]:
        values=[original.get((code,day)) for day in dates[-n:]]
        expected=sum(values) if len(values)==n and all(v is not None for v in values) else None
        assert equal(m.loc[code,f'net_{n}d'],expected)
for table in [m,g]:
    for n in [5,20]:
        vals=table[f'net_{n}d'];expected=vals.map(lambda v:1+sum(w>v for w in vals.dropna()) if pd.notna(v) else float('nan'))
        assert ((expected==table[f'net_{n}d_rank'])|(expected.isna()&table[f'net_{n}d_rank'].isna())).all()
report={'asof':asof,'api_recollection_calls':0,'database_sha256_unchanged':dbhash,'stock_score_sha256_unchanged':sha,'original_flow_rows_verified':len(d['flows']),
 'start':str(dates[0].date()),'end':str(dates[-1].date()),'days':len(dates),'stock_count':len(x),'sector_count':len(g),'sector_day_checks':checks,
 'complete_stock_scores_preserved':int(x.total_score.notna().sum()),'full_sector_scores':int(s.total_score.notna().sum()),
 'stocks_5d':int(m.net_5d.notna().sum()),'stocks_20d':int(m.net_20d.notna().sum()),'sectors_5d':int(g.net_5d.notna().sum()),'sectors_20d':int(g.net_20d.notna().sum()),'sectors_partial_flow_points':int(g.flow_points.notna().sum()),
 'missing_stock_days':sum(r['missing_symbols'] for r in daily),'basis':'현재 구성 기준 수급 추이; composition date '+asof,
 'sector_top_5d':clean(g.sort_values('net_5d',ascending=False).head(5)[['market','sector','net_5d','net_5d_rank']]),
 'sector_top_20d':clean(g.sort_values('net_20d',ascending=False).head(5)[['market','sector','net_20d','net_20d_rank']]),
 'stock_top_5d':clean(x[['symbol','name']].join(m,on='symbol').sort_values('net_5d',ascending=False).head(5)[['symbol','name','net_5d','net_5d_rank']]),
 'stock_top_20d':clean(x[['symbol','name']].join(m,on='symbol').sort_values('net_20d',ascending=False).head(5)[['symbol','name','net_20d','net_20d_rank']])}
(OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
pd.DataFrame(daily).to_csv(OUT/'daily-original-reconciliation.csv',index=False)
g.to_csv(OUT/'sector-flows.csv',index=False);x[['symbol','name','market','sector']].join(m,on='symbol').to_csv(OUT/'stock-flows.csv',index=False)
probe=read('reports/sector/adjustment-consistency-probe.json');examples=[]
for code,r in probe['records'].items():
    new=r['flow'][0];old=x[x.symbol==code].iloc[0]
    examples.append({'symbol':code,'preserved_close':old.close,'requeried_raw_close':int(r['chart_1']['output2'][0]['stck_clpr']),
      'requeried_adjusted_close':int(r['chart_0']['output2'][0]['stck_clpr']),'requeried_flow_close':int(new['stck_clpr']),
      'foreign_amount_unchanged':bool(int(new['frgn_ntby_tr_pbmn'])*1000000==old.net_amount),'foreign_quantity_unchanged':bool(int(new['frgn_ntby_qty'])==old.net_quantity)})
review={'api_recollection_calls':0,'review_sources':['reports/enrichment/official-market-specs.json','.github/scripts/kis_collect_once.py','.github/scripts/kis_enrich_collect.py','scripts/kis_public_client.py','scripts/probe_adjusted_consistency.py'],
 'documented_market_codes':{'J':'KRX','NX':'NXT','UN':'통합'},'all_saved_request_paths_use':'J','mismatch_symbols':2042,
 'conclusion':'KRX/NXT request mixing is not evidenced. Actual server aggregation or revision cause cannot be established from saved data. Preserve originals; no NXT/UN comparative responses collected.',
 'samples':examples}
(OUT/'price-scope-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if not isinstance(v,list)},ensure_ascii=False))
