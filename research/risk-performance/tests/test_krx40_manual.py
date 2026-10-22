"""All clocks, responses and environments here are isolated offline fixtures."""
import datetime as dt
import json
from pathlib import Path

import pytest
import krx40_manual as m
import krx40_collect as c
import krx40_checkpoint as checkpoint


@pytest.mark.parametrize('created,current,allowed', [
    ('2026-10-22T14:01:00Z', '2026-10-22T15:00:00Z', True),
    ('2026-10-22T14:01:00Z', '2026-10-22T23:59:59Z', True),
    ('2026-10-22T02:00:00Z', '2026-10-22T15:00:00Z', False),
    ('2026-10-21T14:01:00Z', '2026-10-22T15:00:00Z', False),
    ('2026-10-22T14:01:00Z', '2026-10-23T00:00:00Z', False),
    ('2026-10-22T16:01:00Z', '2026-10-22T15:00:00Z', False),
])
def test_dispatch_must_be_same_approved_night(created, current, allowed):
    if allowed:
        c.require_dispatch_window(created, current)
    else:
        with pytest.raises(ValueError):
            c.require_dispatch_window(created, current)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'OUT', tmp_path)
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    monkeypatch.setattr(m, 'git', lambda *args: c.BRANCH if args[0] == 'branch' else 'code-sha')
    monkeypatch.setattr(m, 'utcnow', lambda: dt.datetime.fromisoformat('2026-10-22T15:00:00+00:00'))
    events = []
    execution = {'plan': 'offline-plan.json', 'run_id': 'fixture-run', 'directory': 'fixture'}
    stats = {'asof': '2026-10-21', 'source_commit': 'fixture-commit', 'plan_sha256': 'fixture-plan',
             'minimum_missing_requests': 70, 'groups': {g: 0 for g in 'ABCD'}, 'unclassifiable': 2430}
    def prepare():
        events.append('prepare'); return execution, stats
    def post(commit):
        events.append('reclassify'); return execution, stats
    def collect(*args):
        events.append('collect'); return {'batch_requests': 1, 'requests': [{'status': 'validated'}]}
    monkeypatch.setattr(m.study, 'prepare_latest', prepare)
    monkeypatch.setattr(m.study, 'prepare', post)
    monkeypatch.setattr(m.study, 'execute_batch', collect)
    monkeypatch.setattr(m, 'persist_research', lambda: events.append('persist'))
    return tmp_path, events


def result(root):
    return json.loads((root/'LATEST_MANUAL_RESULT.json').read_text())


def test_prepare_never_calls_market_or_registration(harness):
    root, events = harness
    assert m.execute('prepare') == 0
    assert events == ['prepare']
    r = result(root)
    assert r['api_execution']['charged_requests'] == r['registered_signals'] == 0
    assert r['status'] == 'offline_preparation_complete_no_market_requests'


def test_daytime_collect_preserves_prior_view_and_no_calls(harness, monkeypatch):
    root, events = harness
    prior = b'{"previous_good_run":"untouched"}'
    (root/'CURRENT_RUN.json').write_bytes(prior)
    monkeypatch.setattr(m, 'utcnow', lambda: dt.datetime.fromisoformat('2026-10-22T02:00:00+00:00'))
    assert m.execute('collect-batch') == 1
    assert events == []
    assert (root/'CURRENT_RUN.json').read_bytes() == prior
    assert result(root)['reason'] == 'outside_approved_2300_0900_KST_window'


def test_collect_one_batch_only_then_reclassifies(harness):
    root, events = harness
    assert m.execute('collect-batch') == 0
    assert events == ['prepare', 'persist', 'collect', 'reclassify']
    assert result(root)['status'] == 'batch_completed_remaining'
    assert result(root)['registered_signals'] == 0


def test_bad_response_is_failed_after_reclassification(harness, monkeypatch):
    root, events = harness
    monkeypatch.setattr(m.study, 'execute_batch', lambda *a: {'batch_requests': 1, 'requests': [{'status': 'response_rejected'}]})
    assert m.execute('collect-batch') == 1
    assert events == ['prepare', 'persist', 'reclassify']
    assert result(root)['status'] == 'batch_failed_evidence_preserved'
    assert not (root/'CURRENT_RUN.json').exists()


def test_no_progress_is_not_complete(harness, monkeypatch):
    root, _ = harness
    monkeypatch.setattr(m.study, 'execute_batch', lambda *a: {'batch_requests': 0, 'requests': []})
    assert m.execute('collect-batch') == 1
    assert result(root)['status'] == 'no_progress_pending_evidence_review'


@pytest.mark.parametrize('reason', ['other_operation_active_or_waiting', 'dispatch_not_in_current_approved_night'])
def test_preflight_blocks_batch_without_ignoring_error(harness, monkeypatch, reason):
    root, events = harness
    def blocked(*args): raise ValueError(reason)
    monkeypatch.setattr(m.study, 'execute_batch', blocked)
    assert m.execute('collect-batch') == 1
    assert result(root)['reason'] == reason
    assert result(root)['api_execution']['charged_requests'] == 0


def test_exceptions_do_not_leak_secrets(harness, monkeypatch):
    root, _ = harness
    def fail(): raise RuntimeError('private credential example DO_NOT_WRITE')
    monkeypatch.setattr(m.study, 'prepare_latest', fail)
    assert m.execute('prepare') == 1
    assert 'DO_NOT_WRITE' not in (root/'LATEST_MANUAL_RESULT.json').read_text()
    assert result(root)['failed_phase'] == 'fresh_publication_checkpoint_calendar_plan'


def checkpoint_fixture():
    m = {'asof': '2026-10-21', 'daily_run': '42'}
    r = {'flow': {'stck_bsop_date': '20261021'}, 'request_scope': {
        'market': 'J', 'symbol': '005930', 'end': '20261021', 'adjustment': '0'},
        'chart_header': {'stck_shrn_iscd': '005930'}}
    obj = {'trade_date': '20261021', 'exchange': 'KRX', 'status': 'complete', 'failures': {},
           'source_run': '42', 'records': {'005930': r}}
    return obj, m


def test_checkpoint_matches_publication_and_scope():
    obj, manifest = checkpoint_fixture()
    checkpoint.validate_checkpoint(obj, manifest, {'005930'})


@pytest.mark.parametrize('field,value', [('trade_date', '20261020'), ('exchange', 'NXT'),
    ('status', 'pending'), ('failures', {'symbol': 'failed'}), ('source_run', '43'), ('records', {})])
def test_wrong_checkpoint_is_rejected(field, value):
    obj, manifest = checkpoint_fixture(); obj[field] = value
    with pytest.raises(ValueError, match='identity_or_completion'):
        checkpoint.validate_checkpoint(obj, manifest, {'005930'})


def test_wrong_record_scope_is_rejected():
    obj, manifest = checkpoint_fixture()
    obj['records']['005930']['request_scope']['market'] = 'NX'
    with pytest.raises(ValueError, match='record_identity'):
        checkpoint.validate_checkpoint(obj, manifest, {'005930'})


def test_existing_workflow_rewired_without_schedules_or_kis_credentials():
    text = (c.REPO/'.github/workflows/kis-history-probe.yml').read_text()
    assert 'schedule:' not in text and 'cron:' not in text
    assert 'default: prepare' in text and 'options: [prepare, collect-batch]' in text
    assert f"if: github.ref_name == '{c.BRANCH}'" in text
    assert 'kis_version_probe.py' not in text and 'KIS_APP_' not in text
    assert 'krx40_manual.py' in text and 'secrets.KRX_API_KEY' in text
    c.validate_shared_runner({'head_branch': c.BRANCH, 'status': 'in_progress', 'event': 'workflow_dispatch'}, text)
    prepare = text.split('- name: Prepare latest')[1].split('- name: Collect')[0]
    assert 'secrets.' not in prepare
