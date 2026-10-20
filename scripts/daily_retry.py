"""Retry dated provider conflicts without relaxing validation or replacing successes.

Times are persisted as UTC epoch seconds. Per-run limits reset on the existing
21:00 retry; next eligibility and lifetime attempt counts survive checkpoints.
Transport retry/rate limiting remains exclusively in the shared KIS client.
"""
import concurrent.futures
import time

CONFLICT_PREFIX = 'ValueError: Dated chart/flow mismatch: '
CONFLICT_FIELDS = {'stck_clpr', 'acml_vol', 'acml_tr_pbmn'}
MAX_SETTLEMENT_WAIT_SECONDS = 900


def category(error):
    field = error.removeprefix(CONFLICT_PREFIX).split(';', 1)[0]
    if error.startswith(CONFLICT_PREFIX) and field in CONFLICT_FIELDS:
        return 'dated_market_conflict'
    return 'validation_or_api_error'


def collect_pending(result, fetch, accept, save, deadline, cutoff, *,
                    clock=None, monotonic=None, sleep=None):
    clock = clock or time.time
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    states = result.setdefault('retry_state', {})
    for code in list(states):
        if code in result['records'] or code in result['excluded']:
            states.pop(code)  # Includes successes restored from the durable journal.
    attempts = {}
    conflict_attempts = {}
    summary = result['retry_summary'] = {
        'policy': 'dated-conflict-5m-10m-v1', 'attempts_by_symbol': attempts,
        'wait_seconds': 0., 'resolved_conflicts': 0,
        'max_conflict_attempts_per_run': 3, 'max_wait_seconds': MAX_SETTLEMENT_WAIT_SECONDS,
        'stop_reason': None,
    }
    while True:
        pending = [r for r in result['universe']
                   if r['symbol'] not in result['records'] and r['symbol'] not in result['excluded']]
        if not pending:
            summary['stop_reason'] = 'all_accounted'; break
        if monotonic() >= deadline or clock() >= cutoff:
            summary['stop_reason'] = 'existing_time_limit'; break
        candidates = []
        for row in pending:
            code = row['symbol']; old = states.get(code, {})
            limit = 3 if old.get('category') == 'dated_market_conflict' else 2
            if attempts.get(code, 0) < limit:
                candidates.append(row)
        if not candidates:
            summary['stop_reason'] = 'attempt_limit'; break
        ready = [r for r in candidates if states.get(r['symbol'], {}).get('next_eligible_at', 0) <= clock()]
        if not ready:
            wait = min(states[r['symbol']]['next_eligible_at'] for r in candidates) - clock()
            if wait >= min(deadline - monotonic(), cutoff - clock()):
                summary['stop_reason'] = 'existing_time_limit'; break
            if wait > MAX_SETTLEMENT_WAIT_SECONDS - summary['wait_seconds'] + .01:
                summary['stop_reason'] = 'settlement_wait_limit'; break
            # No long blocking sleeps; checkpoint already contains the next due time.
            print(f'Pending dated conflicts: {len(candidates)}; next check in {wait:.0f}s', flush=True)
            until = monotonic() + wait
            while monotonic() < until and clock() < cutoff and monotonic() < deadline:
                before = monotonic()
                sleep(max(0., min(30., until - before, deadline - before, cutoff - clock())))
                summary['wait_seconds'] += monotonic() - before
            continue
        # Keep the existing two workers and the KIS client's serialized 1.05s throttle.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            for i, (code, record, excluded, error) in enumerate(pool.map(fetch, ready), 1):
                attempts[code] = attempts.get(code, 0) + 1
                old = states.get(code, {})
                accept(code, record, excluded, error)
                if error:
                    kind = category(error)
                    delay = 0
                    if kind == 'dated_market_conflict':
                        conflict_attempts[code] = conflict_attempts.get(code, 0) + 1
                        delay = 300 if conflict_attempts[code] == 1 else 600
                    now = clock()
                    states[code] = {
                        'category': kind, 'total_attempts': old.get('total_attempts', 0) + 1,
                        'first_failed_at': old.get('first_failed_at', now),
                        'last_attempt_at': now, 'next_eligible_at': now + delay,
                        'last_error': error,
                    }
                else:
                    if old.get('category') == 'dated_market_conflict':
                        summary['resolved_conflicts'] += 1
                    states.pop(code, None)
                if i % 100 == 0:
                    save()
                    print(f"Collected {len(result['records'])}; excluded {len(result['excluded'])}; failed {len(result['failures'])}", flush=True)
        save()  # Persist backoff before waiting or returning to the scheduled retry.
    save()
