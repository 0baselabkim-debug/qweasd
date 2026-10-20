"""Bounded settlement retries; clocks/fixtures never enter production data."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from daily_retry import collect_pending

CONFLICT = 'ValueError: Dated chart/flow mismatch: stck_clpr'

class Clock:
    def __init__(self): self.now = 1000.; self.sleeps = []
    def time(self): return self.now
    def sleep(self, seconds): self.sleeps.append(seconds); self.now += seconds

def run(result, outcomes, clock=None, deadline=10000, cutoff=10000):
    clock = clock or Clock()
    calls, saves = [], []
    def fetch(row):
        code = row['symbol']; calls.append((code, clock.now))
        values = outcomes[code]
        value = values.pop(0) if len(values) > 1 else values[0]
        return code, None if value else {'validated': code}, None, value
    def accept(code, record, excluded, error):
        if error: result['failures'][code] = error
        else:
            result['failures'].pop(code, None)
            result['records'][code] = record
    collect_pending(result, fetch, accept, lambda: saves.append(copy.deepcopy(result)),
                    deadline, cutoff, clock=clock.time, monotonic=clock.time, sleep=clock.sleep)
    return calls, saves, clock

def state(*codes):
    return {'universe': [{'symbol': c} for c in codes], 'records': {}, 'excluded': {}, 'failures': {}}

def test_conflict_recovers_only_after_spaced_rechecks_and_keeps_success():
    b = state('a', 'b', 'cached'); b['records']['cached'] = {'original': True}
    calls, saves, clock = run(b, {'a': [CONFLICT, CONFLICT, None], 'b': [None]})
    assert [t for c, t in calls if c == 'a'] == [1000, 1300, 1900]
    assert [c for c, _ in calls].count('b') == 1
    assert b['records']['cached'] == {'original': True} and not b['failures']
    assert not b['retry_state'] and b['retry_summary']['resolved_conflicts'] == 1
    assert b['retry_summary']['wait_seconds'] == 900
    assert max(clock.sleeps) <= 30
    assert any(s['retry_state'].get('a', {}).get('next_eligible_at') == 1300 for s in saves)

def test_persistent_conflict_stays_failed_and_is_bounded():
    b = state('a'); calls, _, _ = run(b, {'a': [CONFLICT]})
    assert len(calls) == 3 and b['failures']['a'] == CONFLICT and not b['records']
    assert b['retry_state']['a']['total_attempts'] == 3
    assert b['retry_state']['a']['next_eligible_at'] == 2500

def test_resume_honors_persisted_backoff_but_processes_other_stocks():
    b = state('a', 'b')
    b['retry_state'] = {'a': {'category': 'dated_market_conflict', 'total_attempts': 3,
                             'next_eligible_at': 1200, 'first_failed_at': 900}}
    calls, _, _ = run(b, {'a': [None], 'b': [None]})
    assert calls == [('b', 1000), ('a', 1200)] and not b['retry_state']

def test_normal_errors_get_two_attempts_without_settlement_wait():
    b = state('a'); calls, _, clock = run(b, {'a': ['ValueError: Target date missing or duplicate']})
    assert len(calls) == 2 and not clock.sleeps and b['failures']

import pytest
@pytest.mark.parametrize('deadline,cutoff', [(1290, 10000), (10000, 1290)])
def test_no_wait_or_request_when_backoff_exceeds_existing_budget(deadline, cutoff):
    b = state('a'); calls, _, clock = run(b, {'a': [CONFLICT]}, deadline=deadline, cutoff=cutoff)
    assert len(calls) == 1 and not clock.sleeps and b['failures']
    assert b['retry_summary']['stop_reason'] == 'existing_time_limit'

def test_expired_budget_never_submits_a_request():
    b = state('a'); calls, _, _ = run(b, {'a': [None]}, deadline=999)
    assert not calls and not b['records']

@pytest.mark.parametrize('field', ['stck_clpr', 'acml_vol', 'acml_tr_pbmn'])
def test_only_exact_dated_market_conflicts_use_backoff(field):
    b = state('a'); calls, _, _ = run(b, {'a': ['ValueError: Dated chart/flow mismatch: ' + field, None]})
    assert calls == [('a', 1000), ('a', 1300)]

def test_error_changing_to_invalid_units_does_not_become_success():
    b = state('a'); calls, _, _ = run(b, {'a': [CONFLICT, 'ValueError: Flow unit mismatch']})
    assert len(calls) == 2 and not b['records'] and b['failures']
    assert b['retry_state']['a']['category'] == 'validation_or_api_error'

def test_real_failed_then_converged_responses_pass_unchanged_validation(tmp_path, monkeypatch):
    import datetime as dt
    import json
    import collect_daily_snapshot as collector
    from screener.daily import validate_bundle
    root = Path('reports/taewon-20261020')
    probe = json.loads((root / 'probe.json').read_text())
    obs = {r['kind']: json.loads((root / r['file']).read_text())['response'] for r in probe['observations']}
    success = json.loads((root / 'successful-retry-record.json').read_text())['record']
    b = json.loads(Path('reports/daily-settlement-retry/real-replay-metadata.json').read_text())
    original_success = copy.deepcopy(success)
    clock = Clock(); counts = {'flow': 0, 'chart': 0}
    class Replay:
        def request(self, endpoint, params, tr):
            assert endpoint.endswith('investor-trade-by-stock-daily')
            counts['flow'] += 1
            return obs['flow'] if counts['flow'] < 3 else {'output2': [success['flow']]}
        def prices(self, *args):
            counts['chart'] += 1
            return obs['adjusted'] if counts['flow'] < 3 else {'output1': success['chart_header'], 'output2': success['adjusted_prices']}
    monkeypatch.setattr(collector, 'utcnow', lambda: success['observed_at'])
    def accept(code, record, excluded, error):
        if error: b['failures'][code] = error
        else: b['failures'].pop(code, None); b['records'][code] = record
    collect_pending(b, lambda row: collector.fetch_stock(row, Replay(), '20261020', '20260821', b, set(),
                    dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc), float('inf'), tmp_path),
                    accept, lambda: None, 10000, 10000, clock=clock.time, monotonic=clock.time, sleep=clock.sleep)
    b['status'] = 'complete'
    validate_bundle(b)
    assert b['finality'] == 'UNVERIFIED' and not b['failures']
    assert counts == {'flow': 3, 'chart': 3} and b['retry_summary']['resolved_conflicts'] == 1
    assert b['records']['001420']['flow'] == original_success['flow']
    assert b['records']['001420']['quote'] == original_success['quote']
    assert b['records']['001420']['adjusted_prices'] == original_success['adjusted_prices']
    diagnostics = list(tmp_path.rglob('*.json')); assert len(diagnostics) == 2
    for p in diagnostics:
        x = json.loads(p.read_text())
        assert x['observations'][0]['response']['output2'][0]['stck_clpr'] == '3020'
        assert x['observations'][1]['response']['output2'][0]['stck_clpr'] == '2995'

def test_retry_metrics_are_available_in_market_operation_record():
    from record_market_run import make_record
    b = state('a'); run(b, {'a': [CONFLICT]})
    record = make_record({}, b, {})
    assert record['retry_summary'] == b['retry_summary']
    assert record['retry_state'] == b['retry_state']
    assert not record['new_trading_day_published']

def test_first_conflict_after_other_error_still_waits_five_minutes():
    b = state('a')
    calls, _, _ = run(b, {'a': ['ConnectionError: provider temporarily unavailable', CONFLICT, None]})
    assert calls == [('a', 1000), ('a', 1000), ('a', 1300)] and not b['failures']

def test_saved_wait_cannot_hold_shared_job_beyond_fifteen_minutes():
    b = state('a'); b['failures']['a'] = CONFLICT
    b['retry_state'] = {'a': {'category': 'dated_market_conflict', 'next_eligible_at': 2000}}
    calls, _, clock = run(b, {'a': [None]})
    assert calls == [] and clock.sleeps == [] and b['failures']
    assert b['retry_summary']['stop_reason'] == 'settlement_wait_limit'
