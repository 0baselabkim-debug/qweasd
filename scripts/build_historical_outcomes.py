"""Write immutable retrospective price paths from a verified price pool.

No trade, strategy success, API request or operational publication is inferred.
"""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screener.decision_support.evidence import encoded, sha
from screener.historical_v2.price_pool import load
from screener.historical_v2.outcomes import path
from screener.investment_v2.contracts import independent


def build(pool, pool_manifest_sha256, calendar, calendar_sha256, cutoff, destination):
    pool, calendar = Path(pool), Path(calendar)
    destination = independent(destination, [pool, calendar])
    if destination.exists():
        raise ValueError('outcome_destination_must_be_new')
    raw = calendar.read_bytes()
    if sha(raw) != calendar_sha256:
        raise ValueError('outcome_calendar_anchor_mismatch')
    cal = json.loads(raw)
    book, source_hashes = load(pool, pool_manifest_sha256, cutoff)
    grouped = {}
    for bar in book.values():
        grouped.setdefault((bar['symbol'], bar['market']), {})[bar['trade_date']] = bar
    rows, counts = [], Counter()
    for (symbol, market), bars in sorted(grouped.items()):
        for day in sorted(bars):
            row = path(symbol, market, day, cal, bars, cutoff)
            rows.append(row)
            counts.update(n + ':' + v['status'] for n, v in row['outcomes'].items())
    if not rows:
        raise ValueError('verified_price_paths_required')
    import duckdb
    import pandas as pd
    destination.mkdir(parents=True)
    (destination / 'outcomes.json.gz').write_bytes(gzip.compress(encoded(dict(
        analysis_mode='RETROSPECTIVE_PRICE_PATH_ONLY', actual_user_trades_generated=False,
        strategy_performance='NOT_RUN', rows=rows)), mtime=0))
    with duckdb.connect(str(destination / 'outcomes.duckdb')) as db:
        frame = pd.DataFrame([(i, r['symbol'], r['market'], r['asof'], encoded(r).decode())
                              for i, r in enumerate(rows)],
                             columns=['ordinal', 'symbol', 'market', 'trade_date', 'payload'])
        db.register('incoming', frame)
        db.execute('create table outcomes as select ordinal,symbol,market,cast(trade_date as date) trade_date,cast(payload as json) payload from incoming')
        db.execute('copy outcomes to ? (format parquet, compression zstd)', [str(destination / 'outcomes.parquet')])
        actual = db.execute('select * from outcomes order by ordinal').fetchall()
        parquet = db.execute('select * from read_parquet(?) order by ordinal', [str(destination / 'outcomes.parquet')]).fetchall()
        if actual != parquet or [json.loads(r[-1]) for r in actual] != rows:
            raise ValueError('outcome_JSON_DB_Parquet_mismatch')
    manifest = dict(analysis_mode='RETROSPECTIVE_PRICE_PATH_ONLY', point_in_time_backtest=False,
                    actual_user_trades_generated=False, strategy_performance='NOT_RUN', rows=len(rows),
                    counts=dict(counts), pool_manifest_sha256=pool_manifest_sha256,
                    calendar_sha256=calendar_sha256, source_hashes=source_hashes, cutoff=cutoff,
                    code_sha256={str(p.relative_to(Path(__file__).resolve().parents[1])):sha(p.read_bytes())
                                 for p in (Path(__file__), Path(__file__).resolve().parents[1] / 'screener/historical_v2/outcomes.py')},
                    files={p.name:sha(p.read_bytes()) for p in destination.iterdir() if p.is_file()},
                    JSON_DuckDB_Parquet_match=True, market_HTTP=0, OpenAI_HTTP=0)
    (destination / 'manifest.json').write_bytes(encoded(manifest))
    return dict(directory=str(destination), manifest_sha256=sha(encoded(manifest)), **manifest)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('pool', 'pool-manifest-sha256', 'calendar', 'calendar-sha256', 'cutoff', 'destination'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.pool, a.pool_manifest_sha256, a.calendar, a.calendar_sha256, a.cutoff, a.destination), indent=2))


if __name__ == '__main__':
    main()
