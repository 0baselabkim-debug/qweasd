"""Bounded KRX availability checks. Never replace a valid cache with empty data."""
import datetime as dt
import json
import math
import os
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo
from screener.technical_quality import FIELDS


def classify_rows(payload, day):
    if not isinstance(payload, dict) or not isinstance(payload.get('OutBlock_1'), list):
        return 'REFERENCE_SCHEMA_ERROR', []
    rows = payload['OutBlock_1']
    if not rows:
        return 'REFERENCE_NOT_AVAILABLE', []
    if any(not isinstance(r, dict) for r in rows):
        return 'REFERENCE_SCHEMA_ERROR', []
    if any(r.get('BAS_DD') != day.replace('-', '') for r in rows):
        return 'REFERENCE_DATE_MISMATCH', []
    codes = [r.get('ISU_CD') for r in rows]
    if any(not isinstance(c, str) or len(c) != 6 or not c.isascii() or not c.isalnum() for c in codes) or len(set(codes)) != len(codes):
        return 'REFERENCE_SCHEMA_ERROR', []
    try:
        if any(not math.isfinite(float(r[f])) for r in rows for f in FIELDS.values()):
            return 'REFERENCE_SCHEMA_ERROR', []
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'REFERENCE_SCHEMA_ERROR', []
    return 'RECEIVED', rows


def fetch_references(day, root):
    from scripts.technical_batch import read, write
    dt.date.fromisoformat(day)  # Only a real ISO date can enter a cache path.
    key = os.getenv('KRX_API_KEY')
    result, statuses = {}, {}
    ledger_path = root / 'reference-status.json'
    ledger = read(ledger_path, {'dates': {}})
    observations = ledger.setdefault('dates', {}).setdefault(day, {})
    for market, code in [('KOSPI', 'stk'), ('KOSDAQ', 'ksq')]:
        url = f'https://data-dbg.krx.co.kr/svc/apis/sto/{code}_bydd_trd?basDd={day.replace("-", "")}'
        path = root / 'reference' / day / (market + '.json.gz')
        cached = read(path) or read(root / 'reference' / (market + '.json.gz'), {})
        now = dt.datetime.now(dt.timezone.utc)
        observed = cached.get('observed_at', '')
        reusable = False
        try:
            fetched = dt.datetime.fromisoformat(observed)
            fetched_day = fetched.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
            status, rows = classify_rows({'OutBlock_1': cached.get('rows')}, day)
            reusable = (cached.get('asof') == day and cached.get('url') == url
                        and fetched.tzinfo is not None and status == 'RECEIVED'
                        and fetched_day > day)
        except (ValueError, TypeError):
            pass
        record = {'requested_date': day, 'checked_at': now.isoformat(), 'url': url,
                  'http': None, 'received_rows': 0, 'returned_dates': []}
        rows = []
        if reusable:
            rows = cached['rows']
            record.update(status='CACHED_SAME_DATE', received_rows=len(rows),
                          returned_dates=[day.replace('-', '')], reference_observed_at=observed)
        elif not key:
            record['status'] = 'CREDENTIAL_NOT_BOUND'
        else:
            try:
                request = urllib.request.Request(url, headers={'AUTH_KEY': key})
                with urllib.request.urlopen(request, timeout=25) as response:
                    record['http'] = response.status
                    payload = json.load(response)
                raw_rows = payload.get('OutBlock_1') if isinstance(payload, dict) else None
                if isinstance(raw_rows, list):
                    record['received_rows'] = len(raw_rows)
                    record['returned_dates'] = sorted({str(r.get('BAS_DD')) for r in raw_rows if isinstance(r, dict)})
                record['status'], rows = classify_rows(payload, day)
                if rows:
                    cached = {'asof': day, 'url': url, 'observed_at': now.isoformat(), 'rows': rows}
                    write(path, cached)
            except urllib.error.HTTPError as exc:
                record.update(http=exc.code, status='REFERENCE_AUTH_ERROR' if exc.code in (401, 403) else 'REFERENCE_HTTP_ERROR')
            except (json.JSONDecodeError, UnicodeError):
                record['status'] = 'REFERENCE_SCHEMA_ERROR'
            except (OSError, TimeoutError):
                record['status'] = 'REFERENCE_NETWORK_ERROR'
        statuses[market] = record['status']
        # Do not persist exception text, headers or credential-bearing response bodies.
        observations[market] = record
        history = ledger.setdefault('checks', [])
        history.append({'market': market, **record})
        ledger['checks'] = history[-200:]
        for row in rows:
            result[row['ISU_CD']] = {**row, '_retrieved_at': cached['observed_at'], '_source_url': url}
    write(ledger_path, ledger)
    return result, statuses
