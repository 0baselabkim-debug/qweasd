"""Start a missing daily attempt; never collect market data in this watchdog."""
import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Seoul')
REPO = '0baselabkim-debug/qweasd'
WORKFLOW = 'daily-dashboard.yml'


def decision(now, ledger, runs):
    """Pure policy. Failure retries belong to the existing 21:00 workflow."""
    now = now.astimezone(ZONE)
    day = now.date().isoformat()
    result = {'trade_date': day, 'checked_at': now.isoformat(), 'dispatch': False}

    def stop(reason):
        return {**result, 'reason': reason}

    if now.weekday() >= 5 or not dt.time(18, 45) <= now.time().replace(tzinfo=None) < dt.time(21):
        return stop('outside_watch_window')
    if day in ledger:
        return stop('already_published')
    relevant = [r for r in runs if r['head_branch'] == 'main']
    active = [r for r in relevant if r['status'] != 'completed']
    if active:
        return {**stop('daily_attempt_active'), 'run_ids': [r['id'] for r in active]}
    # Any attempt today suppresses the backup, including official closed-day
    # checks and failures. Do not turn this into a second failure-retry loop.
    start = dt.datetime.combine(now.date(), dt.time(18, 30), ZONE)
    attempts = [r for r in relevant
                if start <= dt.datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) <= now]
    if attempts:
        return {**stop('daily_attempt_already_exists'), 'run_ids': [r['id'] for r in attempts]}
    return {**result, 'dispatch': True, 'reason': 'scheduled_attempt_missing'}


def api(path, payload=None):
    cmd = ['gh', 'api', path]
    if payload is None:
        cmd += ['--paginate']
    else:
        cmd += ['--method', 'POST', '--input', '-']
    result = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=120)
    if result.returncode:
        # Do not dispatch when GitHub status cannot be established. Do not log
        # environment/authentication material or raw error response bodies.
        raise RuntimeError('GitHub API status check/dispatch failed; no automatic retry')
    if payload is not None:
        return json.loads(result.stdout) if result.stdout.strip() else None
    # Older gh versions lack --slurp: --paginate emits consecutive JSON objects.
    pages, remaining = [], result.stdout.strip()
    decoder = json.JSONDecoder()
    while remaining:
        page, end = decoder.raw_decode(remaining)
        pages.append(page)
        remaining = remaining[end:].lstrip()
    return pages


def inspect(now):
    content = api(f'repos/{REPO}/contents/snapshot/publication-ledger.json?ref=main')[0]
    ledger = json.loads(base64.b64decode(content['content']))
    if not isinstance(ledger, dict):
        raise ValueError('Publication ledger must be an object')
    pages = api(f'repos/{REPO}/actions/workflows/{WORKFLOW}/runs?branch=main&per_page=100')
    runs = [r for page in pages for r in page['workflow_runs']]
    result = decision(now, ledger, runs)
    result['ledger_git_blob_sha'] = content['sha']
    return result


def execute(dry_run=False, clock=lambda: dt.datetime.now(ZONE), inspect_fn=inspect, api_fn=api):
    now = clock()
    # Fast time gate: delayed watchdogs never dispatch the next morning.
    gate = decision(now, {}, [])
    if gate['reason'] == 'outside_watch_window':
        return {**gate, 'action': 'skipped', 'dry_run': dry_run}
    result = inspect_fn(now)
    result.update(action='skipped', dry_run=dry_run)
    if not result['dispatch']:
        return result
    if dry_run:
        return {**result, 'action': 'would_dispatch'}
    # Re-read both current publication and run state immediately before POST.
    # The daily workflow's shared concurrency + ledger guard closes the
    # remaining scheduler-vs-dispatch race without concurrent collection.
    result = inspect_fn(clock())
    result.update(action='skipped', dry_run=False)
    if result['dispatch']:
        api_fn(f'repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches',
               {'ref': 'main', 'inputs': {'mode': 'collect'}})
        result['action'] = 'dispatch_requested'
        # HTTP acceptance is not collection success; record that distinction.
        result['collection_success_verified'] = False
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    result = {'action': 'error', 'checked_at': dt.datetime.now(ZONE).isoformat()}
    try:
        result = execute(args.dry_run)
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        raise
    finally:
        result.update(watchdog_run_id=os.environ.get('GITHUB_RUN_ID'),
                      event=os.environ.get('GITHUB_EVENT_NAME'),
                      code_sha=os.environ.get('GITHUB_SHA'), market_api_calls=0)
        path = Path('watchdog-results/check.json')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False))
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
                f.write('### Daily start watchdog\n```json\n' + json.dumps(result, indent=2) + '\n```\n')


if __name__ == '__main__':
    main()
