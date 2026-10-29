"""Frozen regular-slot identities; every physical request rechecks the window."""
import datetime as dt
import hashlib
import hmac
import re
from collections import Counter
from zoneinfo import ZoneInfo
from ..core import Failure, encoded, sha

REPO='0baselabkim-debug/qweasd'
BRANCH='main'
WORKFLOW='.github/workflows/kis-market-2030.yml'
CRON='30 11 * * 1-5'
STATE_BRANCH='kis-regular-state'
FORMAT={'schema':1,'repository':REPO,'slot':'2030','policy':'atomic-slot-ownership-append-only-history'}
KST=ZoneInfo('Asia/Seoul')
ACTIVE=('in_progress','queued','requested','waiting','pending')
PREP_HTTP=4
FULL_HTTP=5600
INTERVAL=1.05
WINDOW_MINUTES=170


def utcnow():return dt.datetime.now(dt.timezone.utc)


def timestamp(value):
    try:
        value=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if value.tzinfo is None:raise ValueError()
        return value
    except (TypeError,ValueError):raise Failure('timezone_aware_time_required') from None


def freeze(run,cron):
    if cron!=CRON or run.get('event')!='schedule':raise Failure('unexpected_regular_schedule_event')
    created=timestamp(run['created_at']).astimezone(dt.timezone.utc)
    # The UTC event-creation date is bound before queueing; never use job date.
    day=created.date();start=dt.datetime.combine(day,dt.time(20,30),KST)
    g='kis-regular-'+day.strftime('%Y%m%d')+'-2030'
    result=dict(schema=1,asof=day.isoformat(),slot='2030',generation_id=g,
        scheduled_at=start.isoformat(),expires_at=(start+dt.timedelta(minutes=WINDOW_MINUTES)).isoformat(),
        run_created_at=created.isoformat(),run_id=str(run['id']),code_commit=run['head_sha'],
        date_binding='GitHub schedule Run created_at UTC date; never recomputed from job start')
    if not start<=created<start+dt.timedelta(minutes=WINDOW_MINUTES):raise Failure('schedule_creation_outside_admissible_window')
    return result


def window(context,current=None):
    current=current or utcnow();start=timestamp(context['scheduled_at']);end=timestamp(context['expires_at'])
    if context['asof']!=start.astimezone(KST).date().isoformat() or end!=start+dt.timedelta(minutes=WINDOW_MINUTES):
        raise Failure('frozen_target_or_window_mismatch')
    if not start<=current<end:raise Failure('regular_execution_window_closed')
    if current>=dt.datetime.combine(dt.date.fromisoformat(context['asof'])+dt.timedelta(days=1),dt.time(9),KST):
        raise Failure('prior_day_new_start_forbidden')
    if context['generation_id']!='kis-regular-'+context['asof'].replace('-','')+'-2030':raise Failure('generation_binding_mismatch')


def eligible(rows):
    if not isinstance(rows,list) or any(r.get('market') not in ('KOSPI','KOSDAQ') for r in rows):raise Failure('master_market_scope_mismatch')
    counts=Counter(r['market'] for r in rows)
    if len(rows)<2000 or counts['KOSPI']<500 or counts['KOSDAQ']<1000:raise Failure('master_population_too_small')
    if len({r['symbol'] for r in rows})!=len(rows):raise Failure('duplicate_master_symbol')
    if any(type(r.get('eligible')) is not bool or r['eligible']!=(not r.get('exclusion_reasons',[])) for r in rows):raise Failure('master_eligibility_inconsistent')
    selected=[r for r in rows if r['eligible']]
    if not selected or 2*len(selected)+3>FULL_HTTP:raise Failure('population_exceeds_existing_http_budget')
    return selected


def control(context):
    return dict(schema=1,mode='full',asof=context['asof'],trial_id=context['generation_id'],
        generation_id=context['generation_id'],request_id=context['generation_id'],previous_run=None,
        max_total_http=FULL_HTTP,not_before=context['scheduled_at'],expires_at=context['expires_at'],
        approval='USER_APPROVED_REGULAR_KRX_TRADING_DAY_2030')


class RunPolicy:
    def __init__(self,context,guard,clock=None):self.context,self.guard,self.clock=context,guard,clock or utcnow
    eligible=staticmethod(eligible)
    def validate_control(self,value,current=None):
        if value!=control(self.context):raise Failure('regular_control_changed')
        window(self.context,current or self.clock());self.guard()


def access_proof(config,env,current=None):
    """No secret value or derived fingerprint is ever returned or logged."""
    current=current or utcnow()
    if config.get('mode')=='shared-account-exclusive':
        from kis_shared.runtime import current as shared_current
        from kis_shared.lease import Blocked
        try:
            session=shared_current();session.review()
            if not session.lease or not session.lease.held:raise Blocked('shared_session_not_acquired')
        except Blocked as exc:raise Failure('shared_account_safety_unverified_'+str(exc)) from None
        if config.get('verified') is not True or config.get('repository')!=REPO:
            raise Failure('shared_account_rollout_unverified')
        return dict(status='EXCLUSIVE_SHARED_SESSION_HELD',provider_finality='UNVERIFIED')
    if config.get('mode')!='dedicated-account-reviewed' or config.get('verified') is not True:
        raise Failure('shared_KIS_account_safety_unverified')
    if config.get('repository')!=REPO or config.get('separate_account_verified') is not True:
        raise Failure('dedicated_account_isolation_not_verified')
    if not all(isinstance(config.get(k),str) and config[k].strip() for k in ('reviewed_by','evidence_reference','account_rate_policy_reference','token_policy_reference')):
        raise Failure('credential_policy_evidence_missing')
    reviewed=timestamp(config.get('reviewed_at'));expires=timestamp(config.get('expires_at'))
    if not reviewed<=current<expires or expires-reviewed>dt.timedelta(days=90):raise Failure('credential_review_expired_or_invalid')
    expected=config.get('app_key_sha256')
    if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise Failure('credential_identity_binding_missing')
    key=env.get('KIS_APP_KEY');secret=env.get('KIS_APP_SECRET')
    if not key or not secret:raise Failure('KIS_credentials_missing')
    if not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(),expected):raise Failure('credential_identity_does_not_match_review')
    return dict(status='REVIEWED_ACCOUNT_ISOLATION',reviewed_at=reviewed.isoformat(),expires_at=expires.isoformat(),evidence_reference=config['evidence_reference'])


def calendar(raw,context,evidence,reference=None):
    import json
    value=json.loads(raw)
    if value.get('rt_cd')!='0':raise Failure('trading_calendar_provider_rejected')
    rows=value.get('output',[]);dates=[r.get('bass_dt') for r in rows]
    if len(dates)!=len(set(dates)):raise Failure('calendar_duplicate_dates')
    matches=[r for r in rows if r.get('bass_dt')==context['asof'].replace('-','')]
    if len(matches)!=1 or matches[0].get('opnd_yn') not in ('Y','N'):raise Failure('trading_day_unverified')
    opened=matches[0]['opnd_yn']=='Y'
    if reference is not None:
        if reference.get('source_kind')!='KRX_OFFICIAL' or reference.get('asof')!=context['asof'] or type(reference.get('open')) is not bool:
            raise Failure('official_calendar_reference_invalid')
        if not reference.get('source_url') or not re.fullmatch('[0-9a-f]{64}',str(reference.get('sha256',''))):raise Failure('official_calendar_reference_unbound')
        timestamp(reference.get('retrieved_at'))
        if opened!=reference['open']:raise Failure('trading_calendar_sources_conflict')
    if dt.date.fromisoformat(context['asof']).weekday()>=5 and opened:raise Failure('provider_weekend_calendar_conflict')
    return dict(open=opened,asof=context['asof'],source_kind='KIS_PROVIDER_CALENDAR_NOT_KRX_OFFICIAL',
        endpoint='CTCA0903R /uapi/domestic-stock/v1/quotations/chk-holiday',
        observed_at=evidence['collected_at'],source_hash=evidence['source_hash'],provider_row=matches[0],official_reference=reference)
