"""Read one existing published generation; never fetch, collect, merge or install."""
import datetime as dt
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
import duckdb

from screener.decision_support.evidence import read, sha, timestamp, official_rows
from screener.decision_support.history import pages
from .metrics import table, FIELDS


def load(snapshot, knowledge_cutoff):
    from scripts.build_decision_support import verify
    snapshot = Path(snapshot).resolve(); directory = snapshot/'decision-support'
    cutoff = timestamp(knowledge_cutoff)
    # Reuse the existing complete hash/DB/JSON/calendar/flow-sum validator.
    verified = verify(directory, snapshot)
    index = read(directory/'index.json'); inventory = read(directory/'source-manifest.json')
    if dt.date.fromisoformat(index['asof']) > cutoff.astimezone(ZoneInfo('Asia/Seoul')).date():
        raise ValueError('asof_after_knowledge_cutoff')
    known_times = [timestamp(index['generated_at'])]
    for group in inventory.values():
        for source in group.values():
            if source.get('observed_at'):known_times.append(timestamp(source['observed_at']))
    if max(known_times) > cutoff:
        raise ValueError('source_observation_after_knowledge_cutoff')
    rows = []
    for shard in index['sectors']:rows.extend(read(directory/shard['file'])['stocks'])
    if any(r['asof'] != index['asof'] or r['market'] not in ('KOSPI','KOSDAQ') or r.get('exchange') != 'KRX' for r in rows):
        raise ValueError('stock_date_market_or_exchange_mismatch')
    with duckdb.connect(str(snapshot/'dashboard.duckdb'), read_only=True) as db:
        calendars = {m:[str(d) for d, in db.execute('select trade_date from calendar where market=? and trade_date<=? order by trade_date',[m,index['asof']]).fetchall()] for m in ('KOSPI','KOSDAQ')}
        benchmarks = {m:{str(d):v for d,v in db.execute('select trade_date,close from benchmarks where market=? and trade_date<=? order by trade_date',[m,index['asof']]).fetchall()} for m in calendars}
    auxiliary = inventory.get('auxiliary', {})
    index_objects = {sid:(source['key'],read(directory/source['file'])) for sid,source in auxiliary.items() if ':index:' in source.get('key','')}
    official_paths = [directory/s['file'] for section in ('references','auxiliary') for s in inventory.get(section,{}).values() if s.get('asof') == index['asof']]
    official, official_sources, official_errors = official_rows(official_paths,index['asof'])
    cache = {}
    for row in rows:
        market = row['market']; dates = calendars[market]; bmarks = benchmarks[market]
        source_ids = row.get('metric_provenance',{}).get('price_source_ids',[])
        own={}
        for sid in source_ids:
            if sid in index_objects:
                key,obj=index_objects[sid];own.setdefault(key,[]).append((sid,obj))
        vintages = {key.split(':')[1] for key in own}
        if own:
            if len(vintages) != 1:raise ValueError('mixed_price_calendar_vintages')
            vintage = next(iter(vintages)); key = (market, vintage, tuple(sorted((k,tuple(sid for sid,_ in v)) for k,v in own.items())))
            if key not in cache:
                idx, _, _ = pages(own,vintage,'0001' if market=='KOSPI' else '1001','index')
                old_dates = sorted(d for d in idx if d <= index['asof'])
                overlap = [d for d in dates if old_dates[0] <= d <= old_dates[-1]]
                if overlap != [d for d in old_dates if dates[0] <= d <= dates[-1]]:raise ValueError('price_calendar_overlap_conflict')
                values = {d:float(idx[d]['bstp_nmix_prpr']) for d in old_dates}
                if any(d in bmarks and Decimal(str(v)) != Decimal(str(bmarks[d])) for d,v in values.items()):
                    raise ValueError('benchmark_vintage_conflict')
                cache[key] = (old_dates+[d for d in dates if d > old_dates[-1]], {**values,**bmarks})
            dates, bmarks = cache[key]
        observed = row['price_series'].get('observed_at')
        try:observed = timestamp(observed).astimezone(ZoneInfo('Asia/Seoul'))
        except (ValueError,TypeError,AttributeError):observed = None
        if observed and observed > cutoff:raise ValueError('price_vintage_after_cutoff')
        row['_price_calendar'] = dates
        row['_benchmarks'] = bmarks
        row['_closed_price_days'] = [d for d in dates if observed and
            (d < observed.date().isoformat() or d == observed.date().isoformat() and observed.time() >= dt.time(15,30))]
    priority = []
    briefing = read(snapshot/'daily_briefing.json')
    if briefing.get('asof') != index['asof'] or briefing.get('source_snapshot_sha256') != index['source_snapshot_sha256']:
        raise ValueError('briefing_generation_mismatch')
    for sector in briefing.get('top_sectors',[]):
        for stock in sector.get('top_stocks',[]):
            if stock.get('symbol') not in priority:priority.append(stock['symbol'])
    macro = read(snapshot/'macro.json') if (snapshot/'macro.json').exists() else {}
    macro_refs = []
    for key,value in macro.get('indicators',{}).items():
        if value.get('date','9999') > index['asof']:continue
        try:known = timestamp(value['observed_at']) <= cutoff
        except (KeyError,ValueError,AttributeError):known = False
        if known:macro_refs.append(dict(id=key,date=value['date'],source=value.get('provider'),series=value.get('series'),unit=value.get('unit'),value=value.get('value'),observed_at=value['observed_at'],provider_finality=value.get('finality')))
    files = {str(p.relative_to(snapshot)):sha(p.read_bytes()) for p in sorted(snapshot.rglob('*')) if p.is_file()}
    return dict(index=index,rows=rows,calendars=calendars,benchmarks=benchmarks,official=official,
        official_sources=official_sources,official_errors=official_errors,priority_symbols=priority,macro=macro_refs,
        input_file_hashes=files,source_verification=verified,source_manifest_sha256=sha((directory/'source-manifest.json').read_bytes()),
        input_index_sha256=sha((directory/'index.json').read_bytes()),known_source_observation_latest=max(known_times).isoformat(),
        source_publication_time=index.get('published_at'),historical_backtest_eligible=False)


def differences(stock, official):
    row = table(stock['price_series']).get(stock['asof'])
    refs = official.get((stock['symbol'],stock['asof'],stock['market']),[])
    if not row or not refs:return []
    variants = {tuple(r['value'][k] for k in FIELDS) for r in refs if r.get('value')}
    if len(variants) != 1 or any(r.get('value') is None for r in refs):
        return [dict(stock_code=stock['symbol'],trade_date=stock['asof'],status='conflict',reason='official_reference_versions_conflict_or_invalid')]
    reference = dict(zip(FIELDS,next(iter(variants))))
    return [dict(stock_code=stock['symbol'],trade_date=stock['asof'],status='conflict',field=k,
        kis_value=row.get(k),krx_value=reference[k],delta=row[k]-reference[k] if row.get(k) is not None else None,
        cause='UNRESOLVED_NOT_ATTRIBUTED_TO_NXT_OR_ADJUSTMENTS',official_source_ids=sorted({r['source_id'] for r in refs})) for k in FIELDS if row.get(k) != reference[k]]
