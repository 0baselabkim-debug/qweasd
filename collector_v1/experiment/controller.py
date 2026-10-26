"""Feature-branch metadata/recovery bootstrap. Never imports or calls provider code."""
import datetime as dt,json,os,subprocess,re,tempfile,zipfile
from pathlib import Path
from ..core import Failure,encoded
from . import runtime
from .policy import ASOF,EXPERIMENT_ID,STARTS,window,utcnow
CONTROL='.github/experiments/full-market-control.json'


def validate(c,now):
    if set(c)!= {'schema','experiment','asof','action','round','request_id'} or c['schema']!=1 or c['experiment']!=EXPERIMENT_ID or c['asof']!=ASOF or c['action'] not in ('inspect','recover_missing') or type(c['round']) is not int or c['round'] not in (1,2,3,4):raise Failure('bad_admin_control')
    if now.date()!=dt.date(2026,10,26):raise Failure('admin_bootstrap_expired')
    if c['action']=='recover_missing':
        start,_=window(c['round'],now)
        if now<start+dt.timedelta(minutes=15):raise Failure('scheduled_run_grace_period')


def main():
    if os.getenv('GITHUB_REPOSITORY')!=runtime.REPO or os.getenv('GITHUB_REF')!='refs/heads/feat/market-data-collector-v1' or os.getenv('GITHUB_EVENT_NAME')!='push' or os.getenv('GITHUB_RUN_ATTEMPT')!='1':raise Failure('admin_context_rejected')
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
    parent=subprocess.check_output(['git','rev-parse','HEAD^'],text=True).strip()
    if event.get('before')!=parent or event.get('forced') or event.get('created') or event.get('deleted'):raise Failure('admin_control_commit_required')
    changed=subprocess.check_output(['git','diff','--name-only',parent,'HEAD'],text=True).splitlines()
    if changed!=[CONTROL]:raise Failure('only_admin_control_may_change')
    c=json.loads(Path(CONTROL).read_bytes());current=utcnow();validate(c,current)
    meta=runtime.api(f'repos/{runtime.REPO}/actions/workflows/{runtime.WORKFLOW}')
    if meta['path']!='.github/workflows/'+runtime.WORKFLOW:raise Failure('unexpected_workflow')
    data=runtime.api(f'repos/{runtime.REPO}/actions/workflows/{meta["id"]}/runs?per_page=100')
    if len(data['workflow_runs'])>=100:raise Failure('history_requires_pagination')
    matching=[r for r in data['workflow_runs'] if runtime.run_round_id(r)==c['round']]
    step_states={}
    for existing in matching[:3]:
        jobs=runtime.api(f'repos/{runtime.REPO}/actions/runs/{existing["id"]}/jobs?per_page=20')['jobs']
        step_states[str(existing['id'])]=[{'job':j['name'],'status':j['status'],'conclusion':j['conclusion'],'steps':[{'name':s['name'],'status':s['status'],'conclusion':s['conclusion']} for s in j.get('steps',[])]} for j in jobs]
    diagnostics={}
    for existing in matching[:2]:
        if existing['status']!='completed':continue
        with tempfile.TemporaryDirectory() as tmp:
            archive=Path(tmp)/'logs.zip'
            runtime.api(f'repos/{runtime.REPO}/actions/runs/{existing["id"]}/logs',binary=archive)
            found=[]
            with zipfile.ZipFile(archive) as z:
                for name in z.namelist():
                    if 'Offline integration' not in name:continue
                    content=z.read(name).decode('utf-8',errors='replace')
                    found+=re.findall(r'FAILED (tests/[A-Za-z0-9_./]+::[A-Za-z0-9_]+)',content)
                    found+=re.findall(r"fatal: (?:invalid object name|bad revision) '[a-f0-9]+'",content)
            diagnostics[str(existing['id'])]=sorted(set(found))
    result=dict(diagnostics=diagnostics,step_states=step_states,status='INSPECTED',asof=ASOF,round=c['round'],workflow_state=meta['state'],matching_runs=[{'id':r['id'],'status':r['status'],'conclusion':r['conclusion']} for r in matching],provider_http=0,observed_at=current.isoformat())
    if c['action']=='recover_missing' and not matching:
        if meta['state']!='active':raise Failure('workflow_not_active_no_override')
        runtime.api(f'repos/{runtime.REPO}/actions/workflows/{meta["id"]}/dispatches','POST',{'ref':'main','inputs':{'round':str(c['round'])}})
        result.update(status='LATE_RECOVERY_REQUESTED',reason='No scheduled round exists after 15-minute grace; actual collection time will be recorded')
    Path('data/experiment-admin').mkdir(parents=True,exist_ok=True)
    Path('data/experiment-admin/result.json').write_bytes(encoded(result))
    issue=runtime.issue_report(dict(result,reason=json.dumps(result,ensure_ascii=False)),'administrative inspection/recovery')
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n'+issue)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
