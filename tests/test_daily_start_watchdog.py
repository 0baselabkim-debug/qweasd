import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from daily_start_watchdog import ZONE, api, decision, execute


def now(hour=18, minute=45, day=22):
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


def run(status='completed', day=22, branch='main', conclusion='success'):
    return {'id': 123, 'head_branch': branch, 'status': status, 'conclusion': conclusion,
            'created_at': now(18, 30, day).isoformat()}


@pytest.mark.parametrize('stamp', [now(18, 44), now(21), now(9), now(day=24), now(day=25)])
def test_no_early_late_weekend_dispatch(stamp):
    assert not decision(stamp, {}, [])['dispatch']


@pytest.mark.parametrize('status', ['queued', 'in_progress', 'waiting', 'pending', 'requested'])
def test_no_duplicate_or_prior_day_active_dispatch(status):
    r = decision(now(), {}, [run(status, day=21)])
    assert not r['dispatch'] and r['reason'] == 'daily_attempt_active'


@pytest.mark.parametrize('conclusion', ['success', 'failure', 'cancelled', 'timed_out'])
def test_existing_attempt_leaves_retry_to_21(conclusion):
    assert not decision(now(), {}, [run(conclusion=conclusion)])['dispatch']


def test_published_day_skipped_and_old_day_does_not_block():
    assert decision(now(), {'2026-10-22': {}}, [])['reason'] == 'already_published'
    assert decision(now(), {'2026-10-21': {}}, [run(day=21)])['dispatch']


def test_missing_run_and_timezone_and_nonmain():
    r = decision(now().astimezone(dt.timezone.utc), {}, [run(branch='research')])
    assert r['dispatch'] and r['trade_date'] == '2026-10-22'


def test_dispatch_exact_existing_workflow_once():
    calls = []
    result = execute(clock=now, inspect_fn=lambda t: decision(t, {}, []),
                     api_fn=lambda *args: calls.append(args))
    assert len(calls) == 1
    assert calls[0][0].endswith('/daily-dashboard.yml/dispatches')
    assert calls[0][1] == {'ref': 'main', 'inputs': {'mode': 'collect'}}
    assert result['action'] == 'dispatch_requested'
    assert result['collection_success_verified'] is False


def test_dry_run_cannot_dispatch():
    result = execute(True, now, lambda t: decision(t, {}, []),
                     lambda *args: pytest.fail('unexpected dispatch'))
    assert result['action'] == 'would_dispatch'


@pytest.mark.parametrize('second', ['active', 'published', 'late'])
def test_recheck_closes_changed_state(second):
    states = [decision(now(), {}, []),
              decision(now(21) if second == 'late' else now(),
                       {'2026-10-22': {}} if second == 'published' else {},
                       [run('queued')] if second == 'active' else [])]
    result = execute(clock=now, inspect_fn=lambda t: states.pop(0),
                     api_fn=lambda *args: pytest.fail('unexpected dispatch'))
    assert result['action'] == 'skipped'


def test_metadata_failure_never_dispatches():
    def fail(t):
        raise RuntimeError('metadata unavailable')
    with pytest.raises(RuntimeError):
        execute(clock=now, inspect_fn=fail, api_fn=lambda *a: pytest.fail('unexpected dispatch'))


def test_outside_window_needs_no_github_or_market_request():
    result = execute(clock=lambda: now(9), inspect_fn=lambda t: pytest.fail('unexpected query'))
    assert result['action'] == 'skipped'


def test_github_pagination_without_new_cli_flags(monkeypatch):
    from types import SimpleNamespace
    def command(args, **kwargs):
        assert '--paginate' in args and '--slurp' not in args
        return SimpleNamespace(returncode=0, stdout='{"workflow_runs":[1]}\n {"workflow_runs":[2]}\n')
    monkeypatch.setattr('daily_start_watchdog.subprocess.run', command)
    assert api('unused') == [{'workflow_runs': [1]}, {'workflow_runs': [2]}]


def test_workflow_preserves_regular_schedule_and_serialization():
    base = Path(__file__).resolve().parents[1]
    daily = (base / '.github/workflows/daily-dashboard.yml').read_text()
    guard = (base / '.github/workflows/daily-start-watchdog.yml').read_text()
    assert "- cron: '30 9 * * 1-5'" in daily and "- cron: '0 12 * * 1-5'" in daily
    assert daily.count('- cron:') == 2
    assert 'group: kis-validation-auth' in daily and 'cancel-in-progress: false' in daily
    assert "- cron: '45 9 * * 1-5'" in guard and "- cron: '15 10 * * 1-5'" in guard
    assert 'contents: read' in guard and 'actions: write' in guard
    assert 'secrets.' not in guard
