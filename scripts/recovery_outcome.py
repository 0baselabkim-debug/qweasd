"""Data outcomes are independent of GitHub's process exit status."""
import collections,datetime as dt,json,os
from pathlib import Path

def outcome(report,published=False,publication_error=False):
    counts=collections.Counter()
    for r in report.get('results',[]):
        key='RECOVERED' if r.get('promoted') and published else 'VALIDATED_PENDING_PUBLICATION' if r.get('promoted') else 'BUDGET_PAUSED' if r.get('error_type')=='RecoveryBudget' else 'VALIDATION_FAILED'
        counts[key]+=1
    blocked=sum(v not in ['RECEIVED','REFERENCE_RECEIVED','CACHED_SAME_DATE'] for states in report.get('reference_status',{}).values() for v in states.values())
    if not blocked and report.get('queue',{}).get('reference_deferred',0):blocked=1
    if report.get('budget_exhausted') and report.get('queue',{}).get('carried_forward',1)>0 and not counts['BUDGET_PAUSED']:counts['BUDGET_PAUSED']=1
    if blocked:counts['PROVIDER_BLOCKED']=blocked
    if publication_error:status='PUBLICATION_FAILED';counts[status]+=1
    elif counts['RECOVERED']:status='RECOVERED'
    elif counts['BUDGET_PAUSED']:status='BUDGET_PAUSED'
    elif blocked:status='PROVIDER_BLOCKED'
    elif counts['VALIDATION_FAILED']:status='VALIDATION_FAILED'
    elif counts['VALIDATED_PENDING_PUBLICATION']:status='VALIDATED_PENDING_PUBLICATION'
    else:status='NO_ELIGIBLE_WORK';counts[status]=1
    return {'status':status,'counts':dict(counts),'published':published,'provider_count_unit':'market/date status, not symbols',
            'run_id':report.get('run_id'),'event':report.get('event'),'scheduled_cron':report.get('scheduled_cron'),
            'started_at':report.get('started_at'),'finished_at':report.get('finished_at'),'reported_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'queue':report.get('queue',{}),'kis_attempts':report.get('physical_request_attempts',0),'promoted_candidates':report.get('promoted',0)}

def emit(published=False,publication_error=False):
    path=Path('technical/recovery-report.json')
    if not path.exists():return
    r=json.loads(path.read_text())
    if r.get('run_id')!=os.environ.get('GITHUB_RUN_ID'):return
    value=outcome(r,published,publication_error)
    value['efficiency']=r.get('efficiency',{})
    prepared=Path('technical/recovery-publication-report.json')
    if prepared.exists():
        proof=json.loads(prepared.read_text())
        if proof.get('run_id')==r.get('run_id'):
            value['indicator_effect']={k:proof[k] for k in ['stock_activity_gain','sector_activity_gain']}
            value['indicator_effect']['publication_confirmed']=published and not publication_error
    value['published_at']=dt.datetime.now(dt.timezone.utc).isoformat() if published else None
    dest=Path('reports/recovery-execution');dest.mkdir(parents=True,exist_ok=True)
    (dest/'outcome.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    summary=os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a') as f:f.write('## Data recovery: '+value['status']+'\n\n```json\n'+json.dumps(value,ensure_ascii=False,indent=2)+'\n```\n')
    print(json.dumps(value,ensure_ascii=False))
    return value
