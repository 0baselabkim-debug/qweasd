"""Independent SQL/raw response checks, no HTTP or writes to source DBs."""
from pathlib import Path
import datetime as dt,gzip,hashlib,json,subprocess
import duckdb,pandas as pd
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def same(a,b):return (pd.isna(a) and pd.isna(b)) or a==b
def main():
 inp=ROOT/'inputs';out=ROOT/'results';f=pd.read_csv(out/'all-stocks.csv',dtype={'symbol':str});summary=json.loads((out/'summary.json').read_text())
 assert len(f)==2429 and f.symbol.is_unique and f.price_data_status.notna().all() and f.final_group.notna().all()
 initial=json.loads((ROOT/'input-hashes.json').read_text())
 assert all(sha(ROOT/p)==h for p,h in initial.items()),'frozen input changed'
 with duckdb.connect(str(inp/'asof19.duckdb'),read_only=True) as c:
  members=c.execute("select symbol,market,sector from memberships where valid_from<='2026-10-19' and (valid_to is null or valid_to>='2026-10-19')").fetchdf().set_index('symbol')
  assert members.index.is_unique
  for row in f.itertuples():assert row.market==members.loc[row.symbol,'market'] and row.sector==members.loc[row.symbol,'sector']
  sql=c.execute('''with dates as (select trade_date,row_number() over(order by trade_date desc) as n from (select distinct trade_date from calendar where trade_date<='2026-10-19')),
  x as (select f.*,d.n from flows f join dates d using(trade_date) where d.n<=20 and f.exchange='KRX')
  select symbol,
  case when count(net_amount) filter(where n=1)=1 then sum(net_amount) filter(where n=1) end as net_1d_krw,
  case when count(net_amount) filter(where n<=5)=5 then sum(net_amount) filter(where n<=5) end as net_5d_krw,
  case when count(net_amount) filter(where n between 6 and 10)=5 then sum(net_amount) filter(where n between 6 and 10) end as previous_5d_krw,
  case when count(net_amount)=20 then sum(net_amount) end as net_20d_krw,
  case when count(net_amount)=20 then count(*) filter(where net_amount>0) end as positive_days_20
  from x group by symbol''').fetchdf().set_index('symbol')
 fields=['net_1d_krw','net_5d_krw','previous_5d_krw','net_20d_krw','positive_days_20'];checks=0
 cap_checks=0;official_caps={}
 for o in json.loads((ROOT/'collection/official-reference-probe.json').read_text()).get('records',[]):
  if o.get('status')!='received_unreconciled':continue
  p=ROOT/'collection'/o['file'];assert sha(p)==o['sha256']
  for row in json.loads(gzip.decompress(p.read_bytes()))['response']['OutBlock_1']:
   assert row['BAS_DD']=='20261019'
   official_caps[row['ISU_CD']]=row
 for r in f.itertuples():
  if r.market_cap_quality=='KRX_OFFICIAL_ASOF_CLOSE_X_LISTED_SHARES_MATCH':
   v=official_caps[r.symbol]
   assert r.market_cap_krw==int(v['MKTCAP'])==int(v['TDD_CLSPRC'])*int(v['LIST_SHRS']);cap_checks+=1
  for name in fields:
   assert same(getattr(r,name),sql.loc[r.symbol,name]),(r.symbol,name);checks+=1
  assert same(r.change_5d_krw,sql.loc[r.symbol,'net_5d_krw']-sql.loc[r.symbol,'previous_5d_krw'])
  if pd.notna(r.intensity_5d_pct):assert abs(r.intensity_5d_pct-r.net_5d_krw/r.market_cap_krw*100)<1e-12
 cp=json.loads(gzip.decompress((inp/'checkpoint-20261019.json.gz').read_bytes()))
 for r in f.itertuples():assert r.net_1d_krw==int(cp['records'][r.symbol]['flow']['frgn_ntby_tr_pbmn'])*1_000_000
 baseline=pd.read_csv(out/'baseline-regression.csv');assert len(baseline)==701 and baseline.prices_and_events_identical.all() and baseline.net5_and_previous5_identical.all()
 for row in f.itertuples():
  if pd.notna(row.rise_pct):assert abs((row.close/row.L2_price-1)*100-row.rise_pct)<1e-9
 candidates=pd.read_csv(out/'verified-candidates.csv',dtype={'symbol':str})
 assert candidates.trusted_candidate_data.all() and candidates.reasons.isna().all() and candidates.flow_improving.all()
 middle=candidates[candidates.final_group.isin(['핵심 연구 후보','확장 연구 후보'])]
 assert (middle.close<=middle.H3_price).all() and middle.middle_before_breakout.all()
 events=pd.read_csv(out/'stage-events.csv',dtype={'symbol':str})
 assert (events.confirmed_date<='2026-10-19').all()
 for name in ['H1','L1','H2','L2','H3','L3']:
  g=events[events[name+'_confirmed_date'].notna()]
  assert (g[name+'_confirmed_date']<=g.confirmed_date).all()
 assert not events.duplicated(['symbol','stage','L2_date','L3_date']).any()
 prices=pd.read_parquet(out/'research-prices.parquet')
 assert prices.trade_date.max()<='2026-10-19' and not prices.duplicated(['symbol','trade_date']).any()
 price_map={(r.symbol,r.trade_date):r.close for r in prices.itertuples()}
 calendar=pd.read_csv(inp/'calendar.csv').trade_date.tolist();indexes={d:i for i,d in enumerate(calendar)}
 pivot_checks=0
 for e in events.itertuples():
  assert e.H2_price<e.H1_price and e.L2_price<e.L1_price
  for name in ['H1','L1','H2','L2','H3','L3']:
   day=getattr(e,name+'_date');p=getattr(e,name+'_price')
   if pd.isna(day):continue
   ix=indexes[day];neighbors=[price_map[e.symbol,calendar[ix+k]] for k in [-2,-1,1,2]]
   assert price_map[e.symbol,day]==p and getattr(e,name+'_confirmed_date')==calendar[ix+2]
   assert p>max(neighbors) if name.startswith('H') else p<min(neighbors)
   assert calendar[ix+2]<=e.confirmed_date and ix>=indexes[e.confirmed_date]-39
   pivot_checks+=1
  assert abs((e.confirmation_close/e.L2_price-1)*100-e.rise_pct)<1e-9
  if e.stage in ['중간','확정']:assert e.L2_price<e.L3_price<e.H3_price
  if e.stage=='조기':assert e.L2_confirmed_date==e.confirmed_date and e.confirmation_close>e.L2_price
  if e.stage=='중간':assert e.L3_confirmed_date==e.confirmed_date
  if e.stage=='확정':assert price_map[e.symbol,calendar[indexes[e.confirmed_date]-1]]<=e.H3_price<e.confirmation_close
 rules=json.loads((ROOT/'rule-hashes.json').read_text())
 for name,expected in rules.items():assert sha(ROOT/name)==expected

 for col in ['within10','within15','within20']:
  valid=f[f[col].notna()]
  limit={'within10':10,'within15':15,'within20':20}[col]
  truth=(valid.rise_pct>=-1e-10)&(valid.rise_pct<=limit+1e-10)
  assert (valid[col].astype(bool)==truth).all()
 # Original stage EVENTS on 19-Oct remain unchanged before applying new quality evidence.
 prior=pd.read_csv(inp/'baseline-stage-events.csv',dtype={'symbol':str});prior=prior[prior.date=='2026-10-19']
 originals=set(pd.read_csv(inp/'baseline-screening.csv',dtype={'symbol':str}).query('research_price_eligible').symbol)
 now=events[(events.confirmed_date=='2026-10-19')&events.symbol.isin(originals)]
 prior_events=set(zip(prior.symbol,prior.stage));new_events=set(zip(now.symbol,now.stage))
 event_differences={'prior_only':sorted(prior_events-new_events),'new_only':sorted(new_events-prior_events)}
 # New validated price observations may change events. Preserve the old inputs and
 # disclose differences; the original 701 replay is checked separately.
 ledger=json.loads((ROOT/'collection/ledger.json').read_text());requests=0;runs=[];pairs=set();retries=0;ratelimits=0;min_gaps=[]
 for b in ledger['batches']:
  assert b['status']=='complete' and b['charged']<=300
  r=json.loads((ROOT/'collection/runs'/f"{b['run']}.json").read_text());m=r['metrics'];assert r['physical_requests']==len(m)==b['charged'] and r['elapsed_seconds']<=900
  requests+=len(m);retries+=sum(x['attempt']>1 for x in m);ratelimits+=sum(x.get('http')==429 or x.get('code') in ['EGW00201','DART_020'] for x in m)
  stamps=[dt.datetime.fromisoformat(x['started_at']) for x in m]
  if len(stamps)>1:min_gaps.append(min((y-x).total_seconds() for x,y in zip(stamps,stamps[1:])))
  runs.append({'run':b['run'],'requests':len(m),'seconds':r['elapsed_seconds'],'auth':sum(x['kind']=='authentication' for x in m),'retry':sum(x['attempt']>1 for x in m),'url':'https://github.com/0baselabkim-debug/qweasd/actions/runs/'+b['run'].split('-')[0]})
 assert requests<=3800 and requests==sum(b['charged'] for b in ledger['batches'])
 assert not min_gaps or min(min_gaps)>=1.04
 state=json.loads((ROOT/'collection/state.json').read_text());rawfiles=0
 plan=json.loads((ROOT/'plan.json').read_text());planned={r['symbol'] for r in plan['targets']}
 pilot=json.loads((inp/'pilot/run.json').read_text())['records']
 assert set(state['records'])<=planned and not(set(state['records'])&set(pilot)) and not(set(state['records'])&originals)
 for code,entries in state['records'].items():
  for kind,e in entries.items():
   assert (code,kind) not in pairs;pairs.add((code,kind));assert sha(ROOT/'collection'/e['file'])==e['sha256'];rawfiles+=1
 protected=json.loads((ROOT/'operational-baseline.json').read_text());base=protected['main']
 # The only non-research difference allowed is the manual research branch probe.
 changed=subprocess.check_output(['git','diff',base,'HEAD','--name-only'],cwd=REPO).decode().splitlines()
 assert all(p.startswith('research/full-market/') or p=='.github/workflows/kis-history-probe.yml' for p in changed)
 assert not subprocess.check_output(['git','diff',base,'--','snapshot','app.py','screener','scripts','.github/workflows/daily-dashboard.yml','.github/workflows/corporate-refresh.yml','.github/workflows/macro-refresh.yml','.github/workflows/liquidity-recovery.yml'],cwd=REPO)
 calendar_status='not_collected'
 calpath=ROOT/'collection/calendar.json'
 assert calpath.exists(),'official index calendar validation not yet completed'
 if calpath.exists():
  cal=json.loads(calpath.read_text());calendar_status=cal['status']
  expected=[d for d in calendar if '2026-06-01'<=d<='2026-10-19']
  assert cal['status']=='passed' and cal['sessions']==expected and cal['market_sessions']['KOSPI']==cal['market_sessions']['KOSDAQ']
  for observation in cal['observations']:assert sha(ROOT/'collection'/observation['file'])==observation['sha256']
 result={'status':'passed','population':len(f),'unique_symbols':f.symbol.nunique(),'independent_SQL_flow_checks':checks,'dated_market_sector_checks':len(f),'official_market_cap_checks':cap_checks,'raw_asof_flow_unit_checks':len(f),'baseline_rule_flow_checks':len(baseline),'independent_pivot_checks':pivot_checks,'official_index_calendar':calendar_status,'baseline_new_event_differences':event_differences,'raw_response_hash_checks':rawfiles,'physical_requests':requests,'planned_price_symbols':len(planned),'complete_price_pairs':sum(set(v)=={'adjusted','raw'} for v in state['records'].values()),'missing_price_responses':sum(kind not in state['records'].get(code,{}) for code in planned for kind in ['adjusted','raw']),'retry_requests':retries,'rate_limit_responses':ratelimits,'minimum_request_start_gap_seconds':min(min_gaps) if min_gaps else None,'batch_collection_seconds':sum(x['seconds'] for x in runs),'batches':runs,'operational_files_changed':False,'asof':'2026-10-19','no_future_performance_claim':True,'completion_checks':{'unique_population':True,'all_stocks_classified':True,'baseline_701_preserved':True,'observations_preserved_separately':True,'no_future_bars_in_pivots':True,'same_L2_for_thresholds':True,'flow_independent_match':True,'unresolved_quality_excluded_from_verified_candidates':True,'API_and_batch_limits':True,'operational_assets_unchanged':True}}
 (out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
