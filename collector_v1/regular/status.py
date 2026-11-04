"""Read-only slot health: never collects, dispatches, cancels or unlocks."""
import datetime as dt
from . import policy
from .github import APIError


def inspect_slot(client,asof,at=None):
    day=dt.date.fromisoformat(asof);at=at or policy.utcnow()
    scheduled=dt.datetime.combine(day,dt.time(20,30),policy.KST)
    result=dict(asof=asof,scheduled_at=scheduled.isoformat(),checked_at=at.isoformat(),market_HTTP=0,
        scheduler_exact_time_guaranteed=False,actions_taken=[],execution_deadline=(scheduled+dt.timedelta(minutes=170)).isoformat())
    if day.weekday()>=5:return dict(result,status='WEEKEND_NO_REGULAR_SLOT')
    if at<scheduled:return dict(result,status='NOT_DUE')
    try:doc,_=client.contents('ledger/'+asof[:4]+'.json')
    except APIError as e:
        if e.status!=404:raise
        doc={}
    slot=doc.get('slots',{}).get(asof+'/2030')
    try:recovered,_=client.contents('recovered/'+asof+'.json')
    except APIError as e:
        if e.status!=404:raise
        recovered=None
    if recovered and recovered.get('status')=='COMPLETE_PROVISIONAL':
        summary=recovered.get('summary',{});artifact=recovered.get('artifact',{})
        if (recovered.get('context',{}).get('asof')==asof and summary.get('internal_validation')=='PASS'
                and summary.get('target',0)>0 and summary.get('target')==summary.get('kis_validated')
                and artifact.get('expires_at') and policy.timestamp(artifact['expires_at'])>at):
            return dict(result,status='RECOVERED_COMPLETE_PROVISIONAL',run_id=recovered['context']['run_id'])
        return dict(result,status='RECOVERY_RECEIPT_UNVERIFIED_OR_EXPIRED')
    if slot:return dict(result,status=slot['status'],run_id=slot['context']['run_id'],reason=slot.get('reason'))
    active=[]
    for run in client.active():
        if run.get('event')!='schedule' or run.get('path')!=policy.WORKFLOW:continue
        try:other=policy.freeze(run,policy.CRON)
        except Exception:continue
        if other['asof']==asof:active.append(dict(run_id=str(run['id']),status=run['status']))
    if active:return dict(result,status='SCHEDULE_RUN_EXISTS_SLOT_NOT_YET_CLAIMED',active_runs=active)
    return dict(result,status='NO_SLOT_AFTER_SCHEDULE',late_seconds=(at-scheduled).total_seconds(),
        required_action='Inspect actual Runs; explicitly approved start if safe. No automatic extra collection.')
