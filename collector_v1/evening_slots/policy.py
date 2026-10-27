"""User-authorized 2026-10-28 20:30/23:30 KST experiment; never recurring."""
import datetime as dt
import re
from zoneinfo import ZoneInfo
from ..core import Failure, encoded, sha
from ..time_slots.policy import eligible

ASOF = '2026-10-28'
EXPERIMENT = 'kis-evening-20261028-v1'
CONTROL = '.github/experiments/market-20261028-evening.json'
WORKFLOW = 'market-20261028-evening.yml'
REPO = '0baselabkim-debug/qweasd'
BRANCH = 'refs/heads/feat/market-data-collector-v1'
APPROVAL = 'USER_APPROVED_TWO_FULL_KIS_OBSERVATIONS_2030_2330'
BASELINE = 'fb1c810bfc7b62610c8b61872224a27705677243'
KST = ZoneInfo('Asia/Seoul')
LABELS = ('2030', '2330')
STARTS = tuple(dt.datetime.fromisoformat(x) for x in (
    '2026-10-28T20:30:00+09:00', '2026-10-28T23:30:00+09:00'))
WAIT_TARGETS = tuple(STARTS[0]-dt.timedelta(hours=h) for h in (12, 8, 4))+(STARTS[0]-dt.timedelta(minutes=30),)


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def generation(n):
    if type(n) is not int or not 1 <= n <= 2:
        raise Failure('only_two_authorized_generations')
    return EXPERIMENT+'-'+LABELS[n-1]


def artifact(n):
    generation(n)
    return 'market-20261028-'+LABELS[n-1]


def configuration(request_id):
    return dict(schema=1, experiment_id=EXPERIMENT, asof=ASOF, request_id=request_id,
        baseline_commit=BASELINE, approval=APPROVAL, max_total_http_per_generation=5600,
        max_master_http=4, interval_seconds=1.05,
        rounds=[dict(round=n, generation_id=generation(n), scheduled_at=STARTS[n-1].isoformat(), artifact=artifact(n)) for n in (1,2)])


def validate_configuration(c):
    if not isinstance(c,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(c.get('request_id',''))):
        raise Failure('invalid_evening_request_id')
    if c != configuration(c['request_id']):
        raise Failure('evening_configuration_not_exactly_approved')


def control(n):
    g=generation(n);start=STARTS[n-1]
    return dict(schema=1,mode='full',asof=ASOF,trial_id=g,generation_id=g,request_id=g,
        previous_run=None,max_total_http=5600,not_before=start.isoformat(),
        expires_at=(start+dt.timedelta(minutes=170)).isoformat(),approval=APPROVAL)


def validate_control(c,current=None):
    n=next((i for i in (1,2) if c.get('generation_id')==generation(i)),None)
    if n is None or c!=control(n):raise Failure('evening_generation_control_scope_mismatch')
    current=current or utcnow()
    if not STARTS[n-1] <= current < STARTS[n-1]+dt.timedelta(minutes=170):
        raise Failure('evening_generation_execution_window_closed')


def verify_population(rows,receipt):
    selected=eligible(rows)
    if (receipt.get('asof'),receipt.get('status'),receipt.get('population_sha256'),receipt.get('master_rows'),receipt.get('eligible')) != (ASOF,'PASS',sha(encoded(rows)),len(rows),len(selected)):
        raise Failure('evening_fixed_population_receipt_mismatch')
    return selected
