"""One-off Actions clock independent of cron delivery. Administrative HTTP only."""
import argparse,datetime as dt,json,os,subprocess,time
from pathlib import Path
from ..core import Failure,encoded
from . import runtime
from .policy import ASOF,EXPERIMENT_ID,STARTS,ROUND_LIMIT,utcnow,window
CONTROL='.github/experiments/full-market-clock.json'


def authorize():
    if os.getenv('GITHUB_REPOSITORY')!=runtime.REPO or os.getenv('GITHUB_REF')!='refs/heads/feat/market-data-collector-v1' or os.getenv('GITHUB_EVENT_NAME')!='push' or os.getenv('GITHUB_RUN_ATTEMPT')!='1':raise Failure('clock_context_rejected')
    c=json.loads(Path(CONTROL).read_bytes())
    if set(c)!={'schema','experiment','asof','action','request_id'} or (c['schema'],c['experiment'],c['asof'],c['action'])!=(1,EXPERIMENT_ID,ASOF,'arm_once'):raise Failure('clock_control_rejected')
    if utcnow().date()!=dt.date(2026,10,26):raise Failure('clock_expired')
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes());parent=subprocess.check_output(['git','rev-parse','HEAD^'],text=True).strip()
    if event.get('before')!=parent or event.get('forced') or event.get('created') or event.get('deleted'):raise Failure('clock_control_commit_required')
    if subprocess.check_output(['git','diff','--name-only',parent,'HEAD'],text=True).splitlines()!=[CONTROL]:raise Failure('clock_control_only_commit_required')


def metadata():
    m=runtime.api(f'repos/{runtime.REPO}/actions/workflows/{runtime.WORKFLOW}')
    data=runtime.api(f'repos/{runtime.REPO}/actions/workflows/{m["id"]}/runs?per_page=100')['workflow_runs']
    if len(data)>=100:raise Failure('clock_history_pagination_required')
    return m,data


def recovery_decision(run,report,now,round_id):
    start,deadline=window(round_id,now)
    if run is None:return 'dispatch_missing'
    if run['status']!='completed':return 'wait'
    if report is None:return 'blocked_unknown_usage'
    if report['status']=='COMPLETE':return 'complete'
    if report.get('recovery_possible') and report['physical_http']<ROUND_LIMIT:return 'dispatch_recovery'
    return 'unresolved'


def phase(round_id):
    authorize();runtime.ROOT.mkdir(parents=True,exist_ok=True)
    start=STARTS[round_id-1];deadline=start+dt.timedelta(hours=3,minutes=-10)
    while utcnow()<start:time.sleep(min(30,(start-utcnow()).total_seconds()))
    last_dispatched=None;reports={}
    while utcnow()<deadline:
        m,runs=metadata();matches=[r for r in runs if runtime.run_round_id(r)==round_id]
        run=max(matches,key=lambda r:int(r['id'])) if matches else None;report=None
        if run and run['status']=='completed':
            key=str(run['id'])
            if key not in reports:
                try:reports[key]=runtime.restore_or_zero(run,runtime.ROOT/('clock-R'+str(round_id)+'-'+key))
                except Failure:reports[key]=None
            report=reports[key]
        decision=recovery_decision(run,report,utcnow(),round_id)
        if decision in ('complete','unresolved','blocked_unknown_usage'):
            runtime.issue_report(dict(round=round_id,asof=ASOF,status=decision.upper(),reason='Clock observer result; provider API calls here are zero'),'independent clock')
            return
        token=str(run['id']) if run else 'missing'
        if decision.startswith('dispatch') and token!=last_dispatched:
            if m['state']!='active':raise Failure('main_experiment_not_active_no_override')
            runtime.api(f'repos/{runtime.REPO}/actions/workflows/{m["id"]}/dispatches','POST',{'ref':'main','inputs':{'round':str(round_id)}})
            last_dispatched=token
            runtime.issue_report(dict(round=round_id,asof=ASOF,status='CLOCK_DISPATCH_REQUESTED',reason=decision+' at '+utcnow().isoformat()+'; actual start recorded by collector'),'independent clock')
        time.sleep(60)
    runtime.issue_report(dict(round=round_id,asof=ASOF,status='UNRESOLVED',reason='Absolute round window closed; clock issued no provider requests'),'independent clock deadline')


def cleanup():
    authorize();runtime.ROOT.mkdir(parents=True,exist_ok=True)
    until=STARTS[-1]+dt.timedelta(hours=3,minutes=30)
    while utcnow()<until:
        m,runs=metadata()
        if not any(runtime.run_round_id(r) and r['status']!='completed' for r in runs):break
        time.sleep(60)
    m,runs=metadata();reports=[]
    from .analysis import aggregate
    for n in range(1,5):
        candidates=[r for r in runs if runtime.run_round_id(r)==n and r['status']=='completed']
        run=max(candidates,key=lambda r:int(r['id'])) if candidates else None
        if run:
            try:reports.append(runtime.restore_round(run,runtime.ROOT/('clock-final-'+str(n))));continue
            except Failure:pass
        reports.append(dict(round=n,asof=ASOF,status='UNRESOLVED',reason='No verified round artifact'))
    analysis=aggregate(reports)
    result=dict(asof=ASOF,status='COMPLETE' if all(r['status']=='COMPLETE' for r in reports) else 'UNRESOLVED',analysis=analysis,rounds=reports,provider_finality='UNVERIFIED')
    (runtime.ROOT/'clock-final-report.json').write_bytes(encoded(result))
    try:runtime.issue_report(dict(asof=ASOF,status=result['status'],reason=json.dumps(analysis,ensure_ascii=False)), 'clock final report')
    finally:runtime.api(f'repos/{runtime.REPO}/actions/workflows/{m["id"]}/disable','PUT')
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(json.dumps(analysis,ensure_ascii=False,indent=2))


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['authorize','phase','cleanup']);p.add_argument('--round',type=int);a=p.parse_args()
    if a.mode=='authorize':authorize()
    elif a.mode=='phase':phase(a.round)
    else:cleanup()


if __name__=='__main__':main()
