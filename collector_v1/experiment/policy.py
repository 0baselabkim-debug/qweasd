import datetime as dt
from zoneinfo import ZoneInfo
from ..core import Failure
ASOF='2026-10-26'
STARTS=[dt.datetime(2026,10,26,h,tzinfo=ZoneInfo('UTC')) for h in (10,13,16,19)]
ROUND_LIMIT=5600
PROBE_LIMIT=20
INTERVAL=1.05
MAX_ATTEMPTS=3
TOTAL_LIMIT=4*ROUND_LIMIT
EXPERIMENT_ID='full-market-20261026'


def utcnow():return dt.datetime.now(dt.timezone.utc)

def window(round_id,now=None):
    if type(round_id) is not int or not 1<=round_id<=4:raise Failure('invalid_experiment_round')
    start=STARTS[round_id-1];deadline=start+dt.timedelta(hours=3,minutes=-10)
    current=now or utcnow()
    if not start<=current<deadline:raise Failure('outside_one_time_round_window')
    return start,deadline


def budget_estimate(n=2349):
    base=2*n+5
    return dict(symbols=n,minimum_http=base,per_round_limit=ROUND_LIMIT,recovery_allowance=ROUND_LIMIT-base,
                four_round_baseline=4*base,four_round_limit=TOTAL_LIMIT,
                interval_seconds=INTERVAL,minimum_start_spacing_seconds=(base-1)*INTERVAL,
                maximum_spacing_seconds=(ROUND_LIMIT-1)*INTERVAL,
                collection_window_seconds=10200,github_job_timeout_minutes=175)


def retry_policy(code,attempt):
    if attempt>=MAX_ATTEMPTS:return None
    if code in ('network_failure','http_408') or code.startswith('http_5'):return 5*2**(attempt-1)
    if code in ('http_429','provider_rate_limit'):return 30*2**(attempt-1)
    if code in ('requested_date_missing','empty_source','REFERENCE_NOT_AVAILABLE'):return 60*attempt
    # Authorization and data conflicts never receive blind retry.
    return None
