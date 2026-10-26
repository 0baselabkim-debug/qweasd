"""Dated authorization, immutable population and generation boundaries."""
import datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo
from ..core import Failure, encoded, sha

ASOF = '2026-10-27'
EXPERIMENT = 'kis-stability-20261027-v1'
CONTROL = '.github/experiments/market-20261027.json'
WORKFLOW = 'market-20261027.yml'
REPO = '0baselabkim-debug/qweasd'
BRANCH = 'refs/heads/feat/market-data-collector-v1'
APPROVAL = 'USER_APPROVED_FOUR_INDEPENDENT_FULL_KIS_OBSERVATIONS'
BASELINE = '503d94135b03c0cb3f2365d6d13356ac726be587'
KST = ZoneInfo('Asia/Seoul')
LABELS = ('1800', '2100', '0000', '0300')
STARTS = tuple(dt.datetime.fromisoformat(x) for x in (
    '2026-10-27T18:00:00+09:00', '2026-10-27T21:00:00+09:00',
    '2026-10-28T00:00:00+09:00', '2026-10-28T03:00:00+09:00'))
WAIT_TARGETS = tuple(STARTS[0] - dt.timedelta(hours=h) for h in (8, 4)) + (STARTS[0] - dt.timedelta(minutes=30),)


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def generation(n):
    if type(n) is not int or not 1 <= n <= 4:
        raise Failure('only_four_authorized_generations')
    return EXPERIMENT + '-' + LABELS[n-1]


def configuration(request_id):
    return dict(schema=1, experiment_id=EXPERIMENT, asof=ASOF,
                request_id=request_id, baseline_commit=BASELINE, approval=APPROVAL,
                max_total_http_per_generation=5600, max_master_http=4,
                interval_seconds=1.05, rounds=[dict(round=n, generation_id=generation(n),
                scheduled_at=STARTS[n-1].isoformat(), artifact='market-20261027-'+LABELS[n-1])
                for n in range(1, 5)])


def validate_configuration(c):
    import re
    if not isinstance(c, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(c.get('request_id', ''))):
        raise Failure('invalid_experiment_request_id')
    if c != configuration(c['request_id']):
        raise Failure('experiment_configuration_not_exactly_approved')


def control(n):
    start = STARTS[n-1] if 1 <= n <= 4 else None
    return dict(schema=1, mode='full', asof=ASOF, trial_id=generation(n),
                generation_id=generation(n), request_id=generation(n), previous_run=None,
                max_total_http=5600, not_before=start.isoformat(),
                expires_at=(start+dt.timedelta(minutes=170)).isoformat(), approval=APPROVAL)


def validate_control(c, current=None):
    n = next((i for i in range(1, 5) if c.get('generation_id') == generation(i)), None)
    if n is None or c != control(n):
        raise Failure('generation_control_scope_mismatch')
    current = current or utcnow()
    if not STARTS[n-1] <= current < STARTS[n-1]+dt.timedelta(minutes=170):
        raise Failure('generation_execution_window_closed')


def eligible(rows):
    if not isinstance(rows, list) or any(r.get('market') not in ('KOSPI', 'KOSDAQ') for r in rows):
        raise Failure('master_market_scope_mismatch')
    counts = Counter(r['market'] for r in rows)
    if len(rows) < 2000 or counts['KOSPI'] < 500 or counts['KOSDAQ'] < 1000:
        raise Failure('master_population_too_small')
    if len({r['symbol'] for r in rows}) != len(rows):
        raise Failure('duplicate_master_symbol')
    if any(type(r.get('eligible')) is not bool or r['eligible'] != (not r.get('exclusion_reasons', [])) for r in rows):
        raise Failure('master_eligibility_inconsistent')
    selected = [r for r in rows if r['eligible']]
    if not selected or 2*len(selected)+3 > 5600:
        raise Failure('population_exceeds_approved_request_budget')
    return selected


def verify_population(rows, receipt):
    selected = eligible(rows)
    if (receipt.get('asof'), receipt.get('status'), receipt.get('population_sha256'),
        receipt.get('master_rows'), receipt.get('eligible')) != (ASOF, 'PASS', sha(encoded(rows)), len(rows), len(selected)):
        raise Failure('fixed_population_receipt_mismatch')
    return selected
