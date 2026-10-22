"""Synthetic HTTP and clocks ONLY; no market API and no production mutations."""
import datetime as dt
import gzip
import json
from pathlib import Path

import pytest
import krx40_collect as c
import krx40_study as s
from krx_validation import canonical, hash_bytes, OfficialIndex
from test_krx_validation import reference


class Clock:
    def __init__(self, instant='2026-10-22T14:00:00+00:00'):
        self.value = dt.datetime.fromisoformat(instant); self.elapsed = 0.0
    def now(self): return self.value+dt.timedelta(seconds=self.elapsed)
    def monotonic(self): return self.elapsed
    def sleep(self, n): self.elapsed += n


def plan(n=40):
    days = [str(dt.date(2026, 8, 1)+dt.timedelta(days=i)) for i in range(n)]  # TEST ONLY
    return {'asof': days[-1], 'sessions': {'KOSPI': days},
            'missing_market_dates': [{'market': 'KOSPI', 'date': d} for d in days],
            'source_commit': 'a'*40}


def sender(calls, clock):
    def send(url, params, key):
        calls.append((url, params.copy(), clock.monotonic()))
        day = dt.datetime.strptime(params['basDd'], '%Y%m%d').date().isoformat()
        return 200, 'application/json', canonical({'OutBlock_1': reference(day)['rows']})
    return send


def batch(root, p, clock=None, calls=None, **kwargs):
    clock = clock or Clock(); calls = calls if calls is not None else []
    return c.collect_batch(p, root, 'unit-test-secret', kwargs.pop('preflight', lambda: None),
                           kwargs.pop('durable', lambda: None), send=kwargs.pop('send', sender(calls, clock)),
                           clock=clock.now, monotonic=clock.monotonic, sleep=clock.sleep, **kwargs)


@pytest.mark.parametrize(('time', 'allowed'), [
    ('2026-10-22T13:59:59+00:00', False), ('2026-10-22T14:00:00+00:00', True),
    ('2026-10-22T23:59:59+00:00', True), ('2026-10-23T00:00:00+00:00', False),
    ('2026-10-22T02:30:00+00:00', False)])
def test_actual_approved_window(time, allowed):
    if allowed: c.require_window(time)
    else:
        with pytest.raises(ValueError, match='outside_approved'): c.require_window(time)


def test_daytime_sends_nothing_and_does_not_charge(tmp_path):
    calls = []
    with pytest.raises(ValueError, match='outside_approved'):
        batch(tmp_path, plan(1), Clock('2026-10-22T02:00:00+00:00'), calls)
    assert calls == [] and not (tmp_path/'events').exists()


def test_batch_20_spacing_resume_and_preserved_raw(tmp_path):
    calls = []; clock = Clock(); p = plan()
    first = batch(tmp_path, p, clock, calls)
    assert first['batch_requests'] == 20 and first['remaining_budget'] == 60
    before = {x: x.read_bytes() for x in (tmp_path/'responses').glob('*')}
    second = batch(tmp_path, p, clock, calls)
    assert second['batch_requests'] == 20 and second['remaining_budget'] == 40
    assert len(calls) == len({r[1]['basDd'] for r in calls}) == 40
    assert all(b[2]-a[2] >= 1.05-1e-9 for a, b in zip(calls, calls[1:]))
    assert all(x.read_bytes() == data for x, data in before.items())
    assert batch(tmp_path, p, clock, calls)['batch_requests'] == 0
    assert 'unit-test-secret' not in ''.join(x.read_text() for x in (tmp_path/'events').glob('*'))


def test_total_80_does_not_reset_after_restart(tmp_path):
    clock = Clock(); calls = []
    for n in range(4):
        p = plan(40)
        p['sessions']['KOSPI'] = [str(dt.date(2026, 1, 1)+dt.timedelta(days=i+n*20)) for i in range(40)]
        p['asof'] = p['sessions']['KOSPI'][-1]
        p['missing_market_dates'] = [{'market': 'KOSPI', 'date': d} for d in p['sessions']['KOSPI'][:20]]
        batch(tmp_path, p, clock, calls)
    assert c.Journal(tmp_path).remaining() == 0 and len(calls) == 80
    with pytest.raises(ValueError, match='remaining_plan'):
        batch(tmp_path, plan(1), clock, calls)
    assert len(calls) == 80


def test_uncertain_request_blocks_restart(tmp_path):
    journal = c.Journal(tmp_path)
    journal.append({'kind': 'attempt', 'attempt': 1, 'market': 'KOSPI', 'date': '2026-08-01'})
    assert journal.remaining() == 79
    calls = []
    with pytest.raises(ValueError, match='uncertain_prior'):
        batch(tmp_path, plan(1), calls=calls)
    assert calls == []


def test_remote_checkpoint_failure_prevents_transport(tmp_path):
    calls = []
    def fail(): raise RuntimeError('remote checkpoint failed')
    with pytest.raises(RuntimeError): batch(tmp_path, plan(1), calls=calls, durable=fail)
    assert calls == [] and c.Journal(tmp_path).remaining() == 79


@pytest.mark.parametrize('http', [401, 403, 429])
def test_access_errors_stop_no_retry(tmp_path, http):
    calls = []
    def fail(*args): calls.append(1); return http, 'text/plain', b'access denied'
    result = batch(tmp_path, plan(2), send=fail)
    assert result['batch_requests'] == 1 and len(calls) == 1
    with pytest.raises(ValueError, match='prior_access'):
        batch(tmp_path, plan(2), send=fail, retry_failed=True)
    assert len(calls) == 1


def test_transport_failure_charged_retry_explicit(tmp_path):
    def fail(*args): raise TimeoutError('NEVER LOG SECRET')
    result = batch(tmp_path, plan(1), send=fail)
    assert result['charged_requests'] == 1
    assert batch(tmp_path, plan(1))['batch_requests'] == 0
    result = batch(tmp_path, plan(1), retry_failed=True)
    assert result['charged_requests'] == 2 and result['requests'][-1]['retry']
    assert 'NEVER LOG SECRET' not in str(result)


def test_operations_start_mid_batch_stops_other_calls(tmp_path):
    checks = []; calls = []
    def guard():
        checks.append(1)
        if len(checks) == 4: raise ValueError('operations running')
    with pytest.raises(ValueError, match='operations running'):
        batch(tmp_path, plan(3), calls=calls, preflight=guard)
    assert len(calls) == 1


def test_bad_dates_duplicates_empty_not_success(tmp_path):
    for i, rows in enumerate([[], reference()['rows']*2, reference()['rows']]):
        result = batch(tmp_path/str(i), plan(1), send=lambda *a: (200, 'application/json', canonical({'OutBlock_1': rows})))
        assert result['requests'][0]['status'] == 'response_rejected'
        assert result['charged_requests'] == 1


def test_secret_echo_never_saved(tmp_path):
    result = batch(tmp_path, plan(1), send=lambda *a: (200, 'text/plain', b'unit-test-secret'))
    assert result['requests'][0]['status'] == 'response_rejected'
    assert not list((tmp_path/'responses').glob('*'))
    assert 'unit-test-secret' not in str(result)


def test_plan_reuses_existing_market_date_even_if_one_symbol_invalid():
    index = OfficialIndex(); env = reference('2026-08-01'); env['rows'][0]['TDD_OPNPRC'] = '0'
    from screener.liquidity_quality import digest
    env['rows_sha256'] = digest(env['rows']); index.add(env, 'KOSPI', '2026-08-01', 'a'*64)
    days = plan()['sessions']['KOSPI']
    p = s.request_plan('a'*40, {'asof': days[-1]}, index, {'KOSPI': days}, {})
    assert len(p['missing_market_dates']) == 39
    assert index.resolve('KOSPI', '001234', '2026-08-01')[1] == 'official_invalid_observation'


def test_journal_tamper_and_response_tamper_detected(tmp_path):
    batch(tmp_path, plan(1))
    path = next((tmp_path/'responses').iterdir()); path.write_bytes(gzip.compress(b'changed'))
    with pytest.raises(ValueError, match='response_checkpoint_corrupt'): batch(tmp_path, plan(1))
    p = tmp_path/'events/0001.json'; obj = json.loads(p.read_text()); obj['body']['attempt'] = 9
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match='chain_corrupt'): c.Journal(tmp_path).events()


def test_closed_window_after_first_request_stops(tmp_path):
    calls = []; clock = Clock('2026-10-22T23:59:57+00:00')
    send = sender(calls, clock)
    def slow(*args):
        result = send(*args); clock.sleep(2); return result
    with pytest.raises(ValueError, match='outside_approved'):
        batch(tmp_path, plan(2), clock, calls, send=slow)
    assert len(calls) == 1


def test_missing_and_corporate_holds_not_D():
    context = {'reasons': ['official_40_bars_incomplete_or_conflicting'], 'bars': []}
    result = s.classify_official(context, {'status': 'replay_ready'}, {}, [])
    assert result['status'] == 'unclassifiable' and result['group'] is None
    context = {'reasons': [], 'bars': [None]*40}
    result = s.classify_official(context, {'status': 'replay_ready'}, {}, ['explicit_adjustment'])
    assert result['status'] == 'unclassifiable'
    result = s.classify_official(context, {'status': 'hold'}, {}, [])
    assert 'original_20_day' in result['reason']
    result = s.classify_official(context, {'status': 'replay_ready'}, {}, [], 94)
    assert 'earlier_swing_context' in result['reason']


def test_original_classifier_is_delegated_without_rule_changes(monkeypatch):
    context = {'reasons': [], 'bars': [None]*40}
    seen = []
    def classify(link, flows):
        seen.append((link, flows)); return {'status': 'condition_not_met', 'group': None}
    monkeypatch.setattr(s, 'classify_link', classify)
    result = s.classify_official(context, {'status': 'replay_ready'}, {'today': (0, 'KRX')}, [])
    assert result['status'] == 'condition_not_met'
    assert seen[0][0]['status'] == 'linked_history_validated'


def test_no_registration_or_workflow_writer():
    import ast
    module = ast.parse(Path(s.__file__).read_text())
    names = [n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
             for n in ast.walk(module) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Attribute, ast.Name))]
    assert 'generate' not in names and 'observe' not in names


def test_shared_operations_lock_required():
    run = {'head_branch': c.BRANCH, 'status': 'in_progress', 'event': 'workflow_dispatch'}
    good = 'concurrency:\n  group: kis-validation-auth\n  cancel-in-progress: false\n'
    c.validate_shared_runner(run, good)
    for wrong in [good.replace('kis-validation-auth', 'unrelated'), good.replace('false', 'true'), '{}']:
        with pytest.raises(ValueError, match='concurrency_required'): c.validate_shared_runner(run, wrong)
    with pytest.raises(ValueError): c.validate_shared_runner({**run, 'event': 'schedule'}, good)


def test_unknown_plan_date_never_sends(tmp_path):
    p = plan(1); p['missing_market_dates'][0]['date'] = '2026-12-31'
    calls = []
    with pytest.raises(ValueError, match='outside_official'): batch(tmp_path, p, calls=calls)
    assert calls == [] and c.Journal(tmp_path).remaining() == 80
