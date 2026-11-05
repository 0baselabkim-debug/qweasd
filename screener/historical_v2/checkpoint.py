"""Revalidate preserved legacy daily checkpoints without assuming modern schemas."""
import json
from pathlib import Path
from screener.decision_support.evidence import read,sha,encoded,timestamp,date
from screener.decision_evidence.collector_source import flow_observation,references
from .price_pool import load as load_pool


def load(root,cutoff,reference_directory,*,proof):
    root=Path(root);p=root/'bundle.json.gz'
    if sha(p.read_bytes())!=proof['bundle_sha256']:raise ValueError('checkpoint_hash_anchor_mismatch')
    b=read(p);asof=date(b['trade_date'])
    if b['exchange']!='KRX' or b['status']!='complete' or b.get('failures') or asof!=proof['asof']:raise ValueError('checkpoint_not_complete_or_wrong_date')
    if timestamp(b['completed_at'])>timestamp(cutoff):raise ValueError('checkpoint_after_cutoff')
    universe={r['symbol']:r for r in b['universe']};records=b['records'];excluded=b['excluded']
    if len(universe)!=len(b['universe']) or set(records)&set(excluded) or set(records)|set(excluded)!=set(universe):raise ValueError('checkpoint_population_or_exclusion_mismatch')
    pool,ref_hashes=load_pool(proof['price_pool']['directory'],proof['price_pool']['manifest_sha256'],cutoff)
    cp=Path(proof['calendar']['file']);cal=read(cp)
    if sha(cp.read_bytes())!=proof['calendar']['sha256'] or cal['status']!='OFFICIAL_POSITIVE_SESSIONS_INDEPENDENTLY_CROSSCHECKED' or asof not in cal['days']:raise ValueError('checkpoint_calendar_mismatch')
    ref_hashes[str(cp.resolve())]=sha(cp.read_bytes());days=[d for d in cal['days'] if d<=asof]
    official,sources,errors,rh=references(reference_directory,timestamp(cutoff),asof=asof);ref_hashes.update(rh)
    price_by_stock={}
    for v in pool.values():
        if v['trade_date']<=asof:price_by_stock.setdefault((v['symbol'],v['market']),[]).append(v)
    benchmarks={market:{date(r['stck_bsop_date']):float(r['bstp_nmix_prpr']) for r in payload['output2'] if date(r['stck_bsop_date'])<=asof} for market,payload in b['benchmarks'].items()}
    fcols=['trade_date','net_amount_krw','net_quantity_shares','institution_net_quantity_shares','institution_net_amount_krw','institution_net_amount_provider_units']
    pcols=['trade_date','open','high','low','close','volume','turnover','validation_status','reasons'];rows=[];unknown=0
    for code,record in sorted(records.items()):
        instrument=universe[code];market=instrument['market'];analysis=record.get('analysis_source',{})
        request=analysis.get('flow_request');raws=analysis.get('flow_rows') or [record['flow']]
        if request is not None and request!=dict(FID_COND_MRKT_DIV_CODE='J',FID_INPUT_ISCD=code,FID_INPUT_DATE_1=b['trade_date'],FID_ORG_ADJ_PRC='',FID_ETC_CLS_CODE=''):raise ValueError('legacy_flow_request_binding_mismatch')
        if request is None:unknown+=1
        price={date(r['stck_bsop_date']):r for r in record.get('adjusted_prices',[])}
        if len(price)!=len(record.get('adjusted_prices',[])):raise ValueError('checkpoint_duplicate_price_date')
        flow=[];seen=set()
        for r in raws:
            d=date(r['stck_bsop_date'])
            if d in seen or d>asof:raise ValueError('checkpoint_duplicate_or_future_flow')
            seen.add(d);q=flow_observation(r,price.get(d));flow.append([d,*[q[k] for k in fcols[1:]]])
        history=sorted(price_by_stock.get((code,market),[]),key=lambda v:v['trade_date']);series=[];times=[]
        for v in history:
            d=v['trade_date']
            if d not in days:continue
            refs=official.get((code,d,market),[])
            if not refs or any(r.get('value')!=v['value'] for r in refs):raise ValueError('checkpoint_pool_reference_binding_mismatch')
            series.append([d,*[v['value'][k] for k in pcols[1:-2]],'operational_crosscheck_pass',[]]);times.extend(s['observed_at'] for s in v['sources'])
        rows.append(dict(symbol=code,name=instrument['name'],market=market,exchange='KRX',asof=asof,provider_finality='UNVERIFIED',
            validity=dict(historical_backtest_eligible=False),flow_series=dict(columns=fcols,rows=sorted(flow)),
            price_series=dict(columns=pcols,rows=series,basis='VERIFIED_WHOLE_ORIGINAL_BARS_RETROSPECTIVE',observed_at=max(times,key=timestamp) if times else None),
            metric_provenance=dict(price_source_ids=[],foreign_observed_at=analysis.get('flow_observed_at')),
            _closed_price_days=days if times else [],_price_calendar=days,_benchmarks=benchmarks.get(market,{})))
    files={str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    idx=dict(asof=asof,dataset_id='checkpoint-'+proof['bundle_sha256'],source_snapshot_sha256=proof['bundle_sha256'],source_collected_at=b['completed_at'],provider_finality='UNVERIFIED',validation_status='REPLAYED_LEGACY_CHECKPOINT_WITH_SCHEMA_LIMITATIONS',
        coverage=dict(stocks=len(rows),source_master=len(universe),excluded=len(excluded),KIS_http_reported_not_independently_ledger_verified=b.get('call_summary',{}).get('physical_requests')),units=dict(price='KRW',foreign_amount='KRW; million x 1000000',quantity='shares',institution_amount='UNVERIFIED'))
    return dict(index=idx,rows=rows,calendars={market:days for market in benchmarks},benchmarks=benchmarks,official=official,official_sources=sources,official_errors=errors,
        priority_symbols=[],macro=[],macro_source_file=None,input_file_hashes=files,reference_file_hashes=ref_hashes,source_manifest_sha256=proof['bundle_sha256'],input_index_sha256=sha(encoded(proof)),
        known_source_observation_latest=max([b['completed_at'],*[v['price_series']['observed_at'] for v in rows if v['price_series']['observed_at']],*[s['observed_at'] for s in sources.values()]],key=timestamp),
        source_publication_time=None,historical_backtest_eligible=False,calendar_provenance=cal['official_source']+'; '+cal['completeness_source'],
        source_verification=dict(status='LEGACY_CHECKPOINT_REPLAY_WITH_EXPLICIT_SCHEMA_LIMITATIONS',collection_complete=True,source_run=b['source_run'],source_commit=proof['commit'],
            missing_original_flow_request_metadata=unknown,per_request_HTTP_ledger='NOT_PRESENT_IN_LEGACY_CHECKPOINT',analysis_mode='RETROSPECTIVE_REANALYSIS'))
