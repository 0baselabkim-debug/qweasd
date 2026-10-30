"""Actions metadata audit only. Does not claim a slot, acquire a lease or use KIS."""
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re

from collector_v1.core import Failure, now, sha
from collector_v1.regular import policy
from collector_v1.regular.github import GitHub, APIError


def main():
    control = json.loads(Path('.github/trials/regular-preflight-20261030.json').read_bytes())
    if (os.getenv('GITHUB_REPOSITORY') != policy.REPO
            or os.getenv('GITHUB_REF') != 'refs/heads/fix/regular-readiness-20261030'
            or os.getenv('GITHUB_EVENT_NAME') != 'push'
            or os.getenv('GITHUB_RUN_ATTEMPT') != '1'
            or control.get('asof') != '2026-10-30'
            or policy.utcnow() >= policy.timestamp(control['expires_at'])):
        raise Failure('preflight_actor_or_window_invalid')
    client = GitHub()
    report = dict(schema=1, inspected_at=now(), run_id=os.environ['GITHUB_RUN_ID'],
                  audit_code_commit=os.environ['GITHUB_SHA'], market_http=0,
                  oauth_issuance=0, slot_claims=0, production_changes=False)
    repo = client.request('repos/' + policy.REPO)
    head = client.request('repos/' + policy.REPO + '/git/ref/heads/main')
    workflow = client.request('repos/' + policy.REPO + '/actions/workflows/kis-market-2030.yml')
    published = client.request('repos/' + policy.REPO + '/contents/' + policy.WORKFLOW + '?ref=main')
    raw = base64.b64decode(published['content'])
    access, _ = client.contents('.github/config/kis-regular-access.json', ref='main')
    shared, _ = client.contents('.github/config/kis-shared-account.json', ref='main')
    lease, _ = client.contents('lease.json', ref='kis-shared-account-state')
    ledger, _ = client.contents('ledger/2026.json')
    cron = re.findall(r"cron:\s*['\"]([^'\"]+)['\"]", raw.decode())
    registered = (repo['default_branch'] == 'main' and workflow['state'] == 'active'
                  and workflow['path'] == policy.WORKFLOW and cron == [policy.CRON])
    today = ledger.get('slots', {}).get('2026-10-30/2030')
    yesterday = ledger.get('slots', {}).get('2026-10-29/2030')
    fields = ('enabled', 'both_repositories_deployed', 'all_account_actors_enrolled',
              'github_access_tested', 'rate_policy_reviewed', 'token_policy_reviewed',
              'real_kis_validation_approved_and_passed')
    report.update(default_branch=repo['default_branch'], main_commit=head['object']['sha'],
                  workflow_id=workflow['id'], workflow_state=workflow['state'], cron=cron,
                  workflow_sha256=sha(raw), schedule_registered=registered,
                  next_configured_slot_kst='2026-10-30T20:30:00+09:00',
                  access_profile=access, shared_policy={k: shared.get(k) for k in fields},
                  central_lease_owner=lease.get('owner'),
                  today_slot=None if today is None else dict(status=today['status'], run_id=today['context']['run_id']),
                  yesterday_scheduled_slot=None if yesterday is None else dict(status=yesterday['status'],
                    run_id=yesterday['context']['run_id'], reason=yesterday.get('reason'), summary=yesterday.get('summary')),
                  kis_app_key_present=os.getenv('KIS_APP_KEY_PRESENT') == 'true',
                  kis_app_secret_present=os.getenv('KIS_APP_SECRET_PRESENT') == 'true')
    try:
        runs = client.active('0baselabkim-debug/kis-intraday-monitor')
        report['other_repository_visibility'] = dict(status='READABLE', active_runs=[
            dict(id=str(r['id']), path=r.get('path'), status=r['status']) for r in runs])
    except APIError as exc:
        report['other_repository_visibility'] = dict(status='UNREADABLE', http_status=exc.status)
    report['operational_readiness'] = (
        'NOT_PROVEN_BY_METADATA_ONLY' if registered and access.get('verified') is True
        and all(shared.get(k) is True for k in fields) else 'BLOCKED_SHARED_ACCOUNT_ROLLOUT')
    client.write('preflights/2026-10-30-' + report['run_id'] + '.json', report)
    Path('regular-readiness-result.json').write_text(json.dumps(report, indent=2) + '\n')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write('```json\n' + json.dumps(report, indent=2) + '\n```\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
