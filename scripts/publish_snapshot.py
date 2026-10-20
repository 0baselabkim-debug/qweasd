"""Package an independently verified candidate; no API calls or key access."""
import argparse,collections,gzip,hashlib,json,shutil,sys
from pathlib import Path
import duckdb
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.store import load
from screener.scoring import evaluate

ROOT=Path(__file__).resolve().parents[1]
def read(p):return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+'\n')
ap=argparse.ArgumentParser();ap.add_argument('--candidate',type=Path,default=ROOT/'data/dashboard.duckdb');ap.add_argument('--raw',type=Path,required=True);args=ap.parse_args()
if (ROOT/'snapshot/publication-ledger.json').exists():
 raise SystemExit('Legacy bootstrap publisher cannot overwrite a ledger-backed publication. Use stage_daily_publication or publish_liquidity for coherent DB and briefing publication.')
audit=read(ROOT/'data/enrichment-audit.json');checks=read(ROOT/'reports/enrichment/independent_checks.json')
assert audit['original_2430_flow_rows_unchanged'] and not audit['financial_errors'] and not audit['financial_changes']
data=load(args.candidate);stocks,sectors,meta=evaluate(data,audit['asof'])
asof=meta['asof'];dates=sorted(data['calendar'].trade_date.unique())[-20:]
listing=dict(zip(data['instruments'].symbol,data['instruments'].listing_date))
flow_keys=set(zip(data['flows'].symbol,data['flows'].trade_date))
price_lookup={(r.symbol,r.trade_date):r for r in data['prices'].itertuples()}
missing=[];full_history=(ROOT/'staging/kis_history_20261014.json.gz').exists()
for code in stocks.symbol:
 for day in dates:
  if (code,day) in flow_keys:continue
  reason='pre_listing' if pd.notna(listing.get(code)) and day<listing[code] else 'pending_full_collection' if not full_history else 'no_trade_observed' if (code,day) in price_lookup and price_lookup[(code,day)].volume==0 else 'collection_or_provider_gap'
  missing.append({'symbol':code,'date':pd.Timestamp(day).strftime('%Y-%m-%d'),'reason':reason})
quality={'asof':audit['asof'],'expected_stock_dates':len(stocks)*len(dates),'missing_count':len(missing),'missing_types':dict(collections.Counter(r['reason'] for r in missing)),
 'missing_detail_file':'history-missing.json.gz','collection_status':'full_response_available' if full_history else 'in_progress_after_user_approval','coverage_by_date':data['flows'].groupby('trade_date').agg(rows=('symbol','size'),symbols=('symbol','nunique')).reset_index().to_dict('records'),
 'duplicate_keys':int(data['flows'][['symbol','trade_date']].duplicated().sum()),'missing_amount':int(data['flows'].net_amount.isna().sum()),
 'flow_rows':len(data['flows']),'full_20d_symbols':audit['complete_20d_symbols'],'start':str(min(dates).date()),'end':str(max(dates).date()),
 'flow_finality':'UNVERIFIED','collected_modes':{}}
for mode in ['quotes','history']:
 p=ROOT/f'staging/kis_{mode}_20261014.json.gz'
 if p.exists():
  v=read(p);metrics=v['metrics'];quality['collected_modes'][mode]={'run_id':v['run_id'],'success':len(v['records']),'failed':len(v['failures']),'failures':v['failures'],'elapsed_seconds':v['elapsed_seconds'],
   'requests':len(metrics),'rate_limit_responses':sum(m['http']==429 or m['code']=='EGW00201' for m in metrics),'ending_interval_seconds':v['ending_interval']}
assert quality['duplicate_keys']==0 and quality['missing_amount']==0
assert checks['original_rows_preserved']==2430
if full_history:
    raw_history=read(ROOT/'staging/kis_history_20261014.json.gz')
    observed=data['flows'].set_index(['symbol','trade_date'])
    reviewed=0;price_differences=[];duplicate_raw_dates=0
    for code,record in raw_history['records'].items():
        raw_dates=[r['stck_bsop_date'] for r in record['rows']]
        duplicate_raw_dates+=len(raw_dates)-len(set(raw_dates))
        for r in record['rows']:
            day=pd.Timestamp(r['stck_bsop_date']);key=(code,day)
            if key not in observed.index or day==asof:continue
            row=observed.loc[key]
            assert row.net_amount==int(r['frgn_ntby_tr_pbmn'])*1000000 and row.net_quantity==int(r['frgn_ntby_qty'])
            reviewed+=1
            if key in price_lookup:
                p=price_lookup[key]
                differences={k:{'kis':int(r[field]),'krx':getattr(p,k)} for k,field in [('close','stck_clpr'),('volume','acml_vol'),('turnover','acml_tr_pbmn')] if int(r[field])!=getattr(p,k)}
                if differences:price_differences.append({'symbol':code,'date':str(day.date()),'differences':differences,'retained_source':'KRX official daily'})
    assert duplicate_raw_dates==0
    assert reviewed==len(data['flows'])-2430
    quality['all_imported_historical_amounts_and_quantities_checked']=reviewed
    quality['duplicate_raw_dates']=duplicate_raw_dates
    quality['kis_krx_price_difference_rows']=len(price_differences)
    quality['kis_krx_price_differences']=price_differences
quality['completeness_excluding_prelisting_percent']=round(100*(quality['expected_stock_dates']-quality['missing_count'])/(quality['expected_stock_dates']-quality['missing_types'].get('pre_listing',0)),4)
summary={k:v for k,v in audit.items() if k not in ['accounts']}
write(ROOT/'reports/enrichment/coverage.json',summary);write(ROOT/'reports/enrichment/history-quality.json',quality)
(ROOT/'reports/enrichment/history-missing.json.gz').write_bytes(gzip.compress(json.dumps(missing,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
(ROOT/'reports/enrichment/account-provenance.json.gz').write_bytes(gzip.compress(json.dumps(audit['accounts'],ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
for name in ['financial_missing_diagnosis.json','financial_additions.json']:
 if (args.raw/name).exists():shutil.copy2(args.raw/name,ROOT/'reports/enrichment'/name)
verification={'asof':audit['asof'],'actual_flow_rows':len(stocks),'flow_history_rows':len(data['flows']),'formal_sectors':int((sectors.sector!='업종 미분류').sum()),'unmapped_symbols':audit['unmapped_after'],
 'financial_rows':len(data['financials']),'ocf_rows':audit['metrics']['ocf']['count'],'complete_scores':audit['complete_stock_scores'],'complete_sector_scores':audit['complete_sector_scores'],
 'three_stock_checks':[dict(row,net_amount_krw=int(stocks.set_index('symbol').loc[row['symbol'],'net_amount'])) for row in checks['stocks']],
 'two_sector_checks':checks['sectors'],'finality':'UNVERIFIED'}
write(ROOT/'snapshot/verification.json',verification)
shutil.copy2(args.candidate,ROOT/'snapshot/dashboard.duckdb')
manifest={'asof':audit['asof'],'flow_rows':len(data['flows']),'preserved_asof_flow_rows':2430,'financial_rows':len(data['financials']),'price_rows':len(data['prices']),'exchange':'KRX',
 'source_run':'https://github.com/0baselabkim-debug/qweasd/actions/runs/38560757360','enrichment_runs':quality['collected_modes'],
 'sha256':hashlib.sha256((ROOT/'snapshot/dashboard.duckdb').read_bytes()).hexdigest(),'finality':'UNVERIFIED','automatic_snapshot_sync':False,'secrets_required':False}
write(ROOT/'snapshot/manifest.json',manifest)
print(json.dumps({'manifest':manifest,'missing_types':quality['missing_types'],'complete_20d_symbols':audit['complete_20d_symbols']},ensure_ascii=False,indent=2))
