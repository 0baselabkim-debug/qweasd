"""Offline verification of descriptive change analysis; no provider calls or DB writes."""
import hashlib,io,json,subprocess,sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.store import load
from screener.flow_trends import stock_trends,group_trends
from scripts.import_sector_support import fingerprint

BASE='8e76290';out=Path('reports/flow-change');out.mkdir(exist_ok=True)
d=load('snapshot/dashboard.duckdb');asof=str(d['flows'].trade_date.max().date())
sha,stocks,sectors=fingerprint(d,asof)
expected=json.loads(subprocess.check_output(['git','show',f'{BASE}:reports/flow-trends/validation.json']))
assert sha==expected['stock_score_sha256_unchanged']
dbhash=hashlib.sha256(Path('snapshot/dashboard.duckdb').read_bytes()).hexdigest()
assert dbhash==expected['database_sha256_unchanged'] and len(d['flows'])==48567
m,matrix=stock_trends(d,stocks,asof);g,_=group_trends(d,stocks,matrix,asof)
x=stocks[['symbol','name','market','sector']].join(m,on='symbol')
for name,new,keys in [('sector',g,['market','sector']),('stock',x,['symbol'])]:
 old=pd.read_csv(io.BytesIO(subprocess.check_output(['git','show',f'{BASE}:reports/flow-trends/{name}-flows.csv'])),dtype={'symbol':str}).set_index(keys).sort_index()
 current=new.set_index(keys).sort_index()
 pd.testing.assert_frame_equal(old,current[old.columns],check_dtype=False,check_exact=False,rtol=1e-12,atol=0)
# Independent window sums from raw DuckDB-loaded observations, without summarize/group_trends.
days=list(matrix.index);recent=days[-5:];previous=days[-10:-5]
assert len(recent)==len(previous)==5 and not set(recent)&set(previous)
raw=d['flows'].set_index(['symbol','trade_date']).net_amount.to_dict()
def window(codes,dates):
 vals=[raw.get((code,date)) for code in codes for date in dates]
 return sum(vals) if all(v is not None and pd.notna(v) for v in vals) else np.nan
checks=0
for frame,groups in [(x,[(r.symbol,[r.symbol]) for r in x.itertuples()]),(g,[((market,sector),f.symbol.tolist()) for (market,sector),f in stocks.groupby(['market','sector'])])]:
 for identifier,codes in groups:
  row=frame[frame.symbol==identifier].iloc[0] if isinstance(identifier,str) else frame[(frame.market==identifier[0])&(frame.sector==identifier[1])].iloc[0]
  a,b=window(codes,recent),window(codes,previous)
  for field,value in [('net_5d',a),('previous_5d',b),('change_5d',a-b)]:
   assert (pd.isna(value) and pd.isna(row[field])) or value==row[field]
  if pd.notna(a-b):assert row.change_direction==('강화' if a>b else '약화' if a<b else '유지')
  else:assert row.change_direction=='자료 부족'
  checks+=1
cols=['net_5d','previous_5d','change_5d','change_direction','change_state','buying_pattern','positive_days_20d']
def records(frame,identity):return json.loads(frame[frame.change_5d>0].sort_values('change_5d',ascending=False).head(5)[identity+cols].to_json(orient='records'))
report={'asof':asof,'baseline_commit':BASE,'provider_calls':0,'rows_preserved':len(d['flows']),'database_sha256':dbhash,'stock_score_sha256':sha,
 'complete_stock_scores':int(stocks.total_score.notna().sum()),'existing_flow_metrics_unchanged':True,'raw_window_checks':checks,
 'recent_window':[str(x.date()) for x in recent],'previous_window':[str(x.date()) for x in previous],
 'sector_top5_improvement':records(g,['market','sector']),'stock_top5_improvement':records(x,['symbol','name']),
 'sector_states':g.change_state.value_counts().to_dict(),'stock_states':x.change_state.value_counts().to_dict(),
 'steady_stocks':int((x.buying_pattern=='꾸준한 순매수').sum()),'full_sector_scores':int(sectors.total_score.notna().sum())}
(out/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
g.to_csv(out/'sectors.csv',index=False);x.to_csv(out/'stocks.csv',index=False)
print(json.dumps(report,ensure_ascii=False,indent=2))
