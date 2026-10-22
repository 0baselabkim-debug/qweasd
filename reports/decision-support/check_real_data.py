"""Offline real-source reconciliation; no credentials, HTTP, or writes to source DB."""
import collections
import datetime as dt
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import duckdb
from scripts.build_decision_support import verify
from screener.decision_support.evidence import read,sha,KIS_FIELDS

root=Path(__file__).resolve().parents[2];d=root/'snapshot/decision-support'
index=read(d/'index.json');inventory=read(d/'source-manifest.json')
latest=next(x for x in inventory['checkpoints'].values() if x['asof']==index['asof'])
bundle=read(d/latest['file']);stocks={}
for shard in index['sectors']:
    stocks.update({s['symbol']:s for s in read(d/shard['file'])['stocks']})
samples=[]
for market in ('KOSPI','KOSDAQ'):
    codes=[s for s,v in sorted(stocks.items()) if v['market']==market][:5]
    for code in codes:
        stock=stocks[code];record=bundle['records'][code];r=record['flow']
        p=next(x for x in record['adjusted_prices'] if x['stck_bsop_date']==index['asof'].replace('-',''))
        checks={
            'foreign_amount':stock['foreign']['net_1d']['value']==int(r['frgn_ntby_tr_pbmn'])*1000000,
            'institution_quantity':stock['institution']['net_quantity_1d']['value']==int(r['orgn_ntby_qty']),
            'institution_buy_minus_sell':int(r['orgn_shnu_vol'])-int(r['orgn_seln_vol'])==int(r['orgn_ntby_qty']),
            'institution_amount_held':stock['institution']['net_1d']['value'] is None,
            'saved_ohlcv_six_fields':all(stock['ohlcv'][k]==int(p[v]) for k,v in KIS_FIELDS.items()),
            'annual_disclosure_cutoff':all(x['disclosed_on'][:10]<=index['asof'] for x in stock['financial']['annual']),
            'period_disclosure_cutoff':all(x['disclosed_on'][:10]<=index['asof'] for x in stock['financial']['verified_period_reports']),
            'no_orders':stock['observations']['order_signal'] is None}
        assert all(checks.values()),code
        samples.append({'symbol':code,'name':stock['name'],'market':market,'checks':checks,
            'foreign_net_1d_krw':stock['foreign']['net_1d']['value'],
            'institution_net_1d_shares':int(r['orgn_ntby_qty']),
            'institution_amount_raw_provider_units':r['orgn_ntby_tr_pbmn'],
            'institution_amount_multiplier':None,
            'ohlcv':{k:stock['ohlcv'][k] for k in KIS_FIELDS},
            'source_observed_at':stock['ohlcv']['observed_at'],'source_sha256':latest['sha256']})
with duckdb.connect(str(root/'snapshot/dashboard.duckdb'),read_only=True) as c:
    lo,hi,n=c.execute('select min(trade_date),max(trade_date),count(*) from prices').fetchone()
    counts=c.execute('select symbol,count(*) from prices group by symbol').fetchall()
    records={t:c.execute('select count(*) from '+t).fetchone()[0] for t in ('flows','prices','financials')}
counts40=collections.Counter(s['validity']['verified_contiguous_price_days'] for s in stocks.values())
phases=read(root/'snapshot/manifest.json')['daily_validation']
seconds=(dt.datetime.fromisoformat(phases['collection_completed_at'])-dt.datetime.fromisoformat(phases['collection_started_at'])).total_seconds()
protected=['dashboard.duckdb','manifest.json','daily-validation.json','publication-ledger.json','daily_briefing.json','briefing/index.json','corporate-state.json.gz']
result={'asof':index['asof'],'dataset_id':index['dataset_id'],'source_sha256':index['source_snapshot_sha256'],
    'all_stock_independent_checks':verify(d,root/'snapshot'),'real_samples':samples,'coverage':index['coverage'],
    'operating_price_history':{'min':str(lo),'max':str(hi),'rows':n,'max_rows_per_symbol':max(n for _,n in counts)},
    'verified_contiguous_price_days_distribution':dict(counts40),'original_record_counts':records,
    'annual_financial_field_coverage':{k:sum(bool(s['financial']['annual']) and s['financial']['annual'][0].get(k) is not None for s in stocks.values()) for k in ('revenue','operating_profit','net_income','equity','liabilities','ocf')},
    'source_collection_seconds_historical':seconds,'original_publishing_candidate_seconds':phases['candidate_total_seconds'],
    'sidecar_build_seconds':read(d/'validation.json')['total_build_and_validation_seconds'],
    'market_api_calls_added':0,
    'normal_source_hashes':{p:sha((root/'snapshot'/p).read_bytes()) for p in protected},
    'new_scheduled_trading_day_verified':False}
(root/'reports/decision-support/REAL_DATA_CHECKS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'asof':result['asof'],'samples_passed':len(samples),'all_stock_checks':result['all_stock_independent_checks'],'build_seconds':result['sidecar_build_seconds']},ensure_ascii=False))
