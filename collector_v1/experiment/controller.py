"""Feature-branch metadata/recovery bootstrap. Never imports or calls provider code."""
import datetime as dt,json,os,subprocess,re,tempfile,zipfile,gzip
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


def artifact_audit(run):
    runtime.ROOT.mkdir(parents=True,exist_ok=True)
    folder=runtime.ROOT/('audit-'+str(run['id']))
    report=runtime.restore_round(run,folder)
    manifest=runtime.verify(folder);raw_count=0
    for path in (folder/'raw').glob('*.gz'):
        if runtime.sha(gzip.decompress(path.read_bytes()))!=path.stem:raise Failure('audit_source_hash_mismatch')
        raw_count+=1
    ledger=[json.loads(x) for x in (folder/'http.jsonl').read_text().splitlines()]
    started=[e for e in ledger if e['event']=='started'];finished=[e for e in ledger if e['event']=='finished']
    if len(started)!=report['physical_http']:raise Failure('audit_ledger_report_mismatch')
    sources=[]
    for path in (folder/'observations').glob('*.json'):
        e=json.loads(path.read_bytes());body=json.loads(gzip.decompress((folder/'raw'/(e['source_hash']+'.gz')).read_bytes()))
        records=body.get('OutBlock_1',body.get('output2',[]))
        sources.append(dict(kind=e['task']['kind'],market=e['task'].get('market'),observed_at=e['collected_at'],rows=len(records),requested_date_present=any(r.get('BAS_DD',r.get('stck_bsop_date'))==ASOF.replace('-','') for r in records)))
    import duckdb
    dbpath=folder/report['database_file']
    if folder.resolve() not in dbpath.resolve().parents:raise Failure('unsafe_derived_database_path')
    with duckdb.connect(str(dbpath),read_only=True) as db:
        saved={k:json.loads(v) for k,v in db.execute('SELECT symbol,payload FROM observations').fetchall()}
    if saved!={r['symbol']:r for r in report['rows']}:raise Failure('audit_DB_JSON_mismatch')
    artifacts=runtime.api(f'repos/{runtime.REPO}/actions/runs/{run["id"]}/artifacts?per_page=100')['artifacts']
    return dict(run_id=run['id'],manifest_files=len(manifest['files']),raw_hashes_verified=raw_count,physical_http=len(started),http_statuses=[e.get('http') for e in finished],db_json_match=True,database_rows=len(saved),status=report['status'],sources=sources,artifacts=[dict(id=a['id'],name=a['name'],digest=a.get('digest')) for a in artifacts])


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
    audit=None
    completed=next((r for r in matching if r['status']=='completed'),None)
    if completed:
        try:audit=artifact_audit(completed)
        except Failure as e:audit={'status':'BLOCKED','reason':e.code}
    result=dict(artifact_audit=audit,diagnostics=diagnostics,step_states=step_states,status='INSPECTED',asof=ASOF,round=c['round'],workflow_state=meta['state'],matching_runs=[{'id':r['id'],'status':r['status'],'conclusion':r['conclusion']} for r in matching],provider_http=0,observed_at=current.isoformat())
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
