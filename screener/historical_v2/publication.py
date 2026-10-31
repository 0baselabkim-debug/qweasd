"""Retrospective DB/official-only adapter; unknown historic cohorts stay unknown."""
import datetime as dt
import json
from pathlib import Path
from collections import Counter
import duckdb
from screener.decision_support.evidence import encoded,sha,read,timestamp
from screener.decision_evidence.collector_source import references
from .price_pool import load as load_pool


def load(root,cutoff,reference_directory,*,proof):
    root=Path(root).resolve();asof=proof['asof'];dt.date.fromisoformat(asof)
    m=read(root/'manifest.json');raw=(root/'dashboard.duckdb').read_bytes()
    if sha(raw)!=m['sha256'] or sha((root/'manifest.json').read_bytes())!=proof['manifest_sha256']:raise ValueError('publication_source_anchor_mismatch')
    validation=read(root/'daily-validation.json');ledger=read(root/'publication-ledger.json')
    if validation['sha256']!=m['sha256'] or ledger[m['asof']]['sha256']!=m['sha256'] or not m['daily_validation'].get('preservation_verified'):
        raise ValueError('publication_validation_or_ledger_mismatch')
    calendar_path=Path(proof['calendar']['file']);cr=calendar_path.read_bytes()
    if sha(cr)!=proof['calendar']['sha256']:raise ValueError('calendar_anchor_mismatch')
    cal=json.loads(cr)
    if cal['status']!='OFFICIAL_POSITIVE_SESSIONS_INDEPENDENTLY_CROSSCHECKED' or asof not in cal['days']:raise ValueError('unverified_or_unavailable_historical_session')
    days=[d for d in cal['days'] if d<=asof]
    pool,hashes=load_pool(proof['price_pool']['directory'],proof['price_pool']['manifest_sha256'],cutoff)
    hashes[str(calendar_path.resolve())]=sha(cr)
    official,official_sources,errors,ref_hashes=references(reference_directory,timestamp(cutoff),asof=asof);hashes.update(ref_hashes)
    with duckdb.connect(str(root/'dashboard.duckdb'),read_only=True) as db:
        instruments={code:dict(name=name,market=market,listing_date=str(listed)) for code,name,market,listed in db.execute('select symbol,name,market,listing_date from instruments').fetchall()}
        members={r[0] for r in db.execute('select distinct symbol from memberships where valid_from<=? and (valid_to is null or valid_to>=?)',[asof,asof]).fetchall()}
        flow={};benchmarks={m:{} for m in ('KOSPI','KOSDAQ')}
        for code,d,a,q in db.execute('select symbol,trade_date,net_amount,net_quantity from flows where trade_date<=?',[asof]).fetchall():
            flow.setdefault(code,[]).append([str(d),a,q,None,None,None])
        for market,d,v in db.execute('select market,trade_date,close from benchmarks where trade_date<=?',[asof]).fetchall():benchmarks[market][str(d)]=v
    # Only contemporaneous stored membership proves an eligible cohort. Official
    # security lists and subsequently observed DB rows remain a labelled partial
    # research population, not a reconstructed survivor-free universe.
    known=bool(members);selected={(code,instruments[code]['market']) for code in members if code in instruments}
    if not known:
        selected={(code,market) for code,d,market in official if d==asof}
        selected.update((code,instruments[code]['market']) for code,values in flow.items() if any(v[0]==asof for v in values))
    pcols=['trade_date','open','high','low','close','volume','turnover','validation_status','reasons']
    by_stock={}
    for value in pool.values():
        if value['trade_date']>asof:continue
        by_stock.setdefault((value['symbol'],value['market']),[]).append(value)
    rows=[];latest=[]
    for code,market in sorted(selected):
        instrument=instruments.get(code,{})
        history=sorted(by_stock.get((code,market),[]),key=lambda v:v['trade_date'])
        pseries=[];origins={}
        for v in history:
            d=v['trade_date']
            if d not in days:continue
            refs=official.get((code,d,market),[])
            if not refs or any(r.get('value')!=v['value'] for r in refs):raise ValueError('publication_pool_reference_conflict')
            pseries.append([d,*[v['value'][k] for k in pcols[1:-2]],'operational_crosscheck_pass',[]]);origins[d]=v['sources']
        times=[o['observed_at'] for values in origins.values() for o in values];latest.extend(times)
        observed=max(times,key=timestamp) if times else None
        rows.append(dict(symbol=code,name=instrument.get('name',code),market=market,exchange='KRX',asof=asof,provider_finality='UNVERIFIED',
            validity=dict(historical_backtest_eligible=False),population_eligibility='VERIFIED_STORED_MEMBERSHIP' if known else 'UNKNOWN_HISTORICAL',
            flow_series=dict(columns=['trade_date','net_amount_krw','net_quantity_shares','institution_net_quantity_shares','institution_net_amount_krw','institution_net_amount_provider_units'],rows=sorted(flow.get(code,[]))),
            price_series=dict(columns=pcols,rows=pseries,basis='WHOLE_KIS_ORIGINAL_BARS_EXACT_KRX_MATCH_RETROSPECTIVE',observed_at=observed),
            metric_provenance=dict(price_source_ids=[],foreign_observed_at=None,retrospective_price_observations=origins),
            _closed_price_days=days if observed else [],_price_calendar=days,_benchmarks=benchmarks[market]))
    # A missing current-day collection is never relabelled as current data.
    if not rows:raise ValueError('no_observed_research_population')
    files={str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    idx=dict(asof=asof,dataset_id='historical-publication-'+m['sha256']+'-'+asof,source_snapshot_sha256=m['sha256'],
        source_collected_at=None,provider_finality='UNVERIFIED',validation_status='RETROSPECTIVE_PARTIAL_SOURCE_PUBLICATION_ONLY',
        units=dict(price='KRW',foreign_amount='KRW',quantity='shares',institution_amount='UNVERIFIED'),
        coverage=dict(stocks=len(rows),historical_eligible_population_verified=known,universe_basis='stored_date_membership' if known else 'official_reference_or_observed_rows_not_verified_eligible_master'))
    return dict(index=idx,rows=rows,calendars={market:days for market in ('KOSPI','KOSDAQ')},benchmarks=benchmarks,official=official,
        official_sources=official_sources,official_errors=errors,priority_symbols=[],macro=[],macro_source_file=None,input_file_hashes=files,
        reference_file_hashes=hashes,source_manifest_sha256=proof['manifest_sha256'],input_index_sha256=sha(encoded(proof)),
        known_source_observation_latest=max([*latest,*[s['observed_at'] for s in official_sources.values()]],key=timestamp) if latest or official_sources else None,source_publication_time=None,historical_backtest_eligible=False,
        source_verification=dict(status='RETROSPECTIVE_PUBLICATION_AND_VERIFIED_PRICE_POOL',collection_complete=False,original_publication_asof=m['asof'],
            historical_membership_verified=known,source_flow_validation='PUBLICATION_ONLY; exact row observation time unavailable',
            analysis_mode='RETROSPECTIVE_REANALYSIS',official_calendar_status=cal['status']),
        calendar_provenance=cal['official_source']+'; '+cal['completeness_source']+'; not a statutory holiday notice')
