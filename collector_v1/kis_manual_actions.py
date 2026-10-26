"""New feature-branch manual executor; never enables old workflows or calls KRX."""
import argparse,base64,datetime as dt,json,os,subprocess,time
from pathlib import Path
from .core import Failure,Store,encoded,now,sha
from .trial import REPO,BRANCH,secret_scan
from .experiment import runtime
from .experiment.storage import events
from . import kis_manual as manual
ROOT=manual.ROOT
WORKFLOW='kis-manual-validation.yml'
ARTIFACT='kis-manual-state-20261026'


def context():
    if os.getenv('GITHUB_REPOSITORY')!=REPO or os.getenv('GITHUB_REF')!=BRANCH or os.getenv('GITHUB_EVENT_NAME')!='push' or os.getenv('GITHUB_RUN_ATTEMPT')!='1':raise Failure('manual_workflow_context_rejected')
    c=json.loads(Path(manual.CONTROL).read_bytes());manual.validate_control(c)
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
    parent=subprocess.check_output(['git','rev-parse','HEAD^'],text=True).strip()
    if event.get('before')!=parent or event.get('forced') or event.get('created') or event.get('deleted'):raise Failure('manual_control_commit_required')
    if subprocess.check_output(['git','diff','--name-only',parent,'HEAD'],text=True).splitlines()!=[manual.CONTROL]:raise Failure('only_manual_control_may_change')
    return c


def verify_master():
    from .trial import inventory
    from .validation import parse
    from .market_trial import MASTER_RUN
    s=Store(ROOT/'seed');m=s.read('state-manifest.json');r=s.read('receipt.json')
    if len(m['files'])!=11 or inventory(s.root)!=m['files'] or (r['github_run_id'],r['asof'],r['status'],r['total_http'])!=(MASTER_RUN,manual.ASOF,'PASS',4):raise Failure('manual_restored_master_integrity')
    rows=[]
    for entry in s.read('state.json')['tasks'].values():
        e=entry['evidence'];values=parse(s.raw(e),e)
        if e['task']['kind']=='master':rows.extend(values)
    manual.eligible(rows);return rows


def prepare(c):
    current=runtime.api(f'repos/{REPO}/actions/runs/{os.environ["GITHUB_RUN_ID"]}')
    if current['path']!='.github/workflows/'+WORKFLOW:raise Failure('manual_wrong_workflow')
    runs=runtime.api(f'repos/{REPO}/actions/workflows/{current["workflow_id"]}/runs?per_page=100')['workflow_runs']
    if len(runs)>=100:raise Failure('manual_history_pagination_required')
    prior=[r for r in runs if int(r['id'])<int(current['id'])]
    previous=max(prior,key=lambda r:int(r['id'])) if prior else None
    if previous:
        if previous['status']!='completed' or str(previous['id'])!=str(c['previous_run']):raise Failure('manual_latest_previous_run_required')
        data=runtime.api(f'repos/{REPO}/actions/runs/{previous["id"]}/artifacts?per_page=100')['artifacts']
        matches=[a for a in data if a['name']==ARTIFACT and not a['expired']]
        if len(matches)!=1:raise Failure('manual_previous_state_missing_unknown_calls_reserved_no_reset')
        meta=matches[0]
        if not meta.get('digest','').startswith('sha256:'):raise Failure('manual_artifact_digest_missing')
        archive=ROOT.parent/(str(meta['id'])+'.zip');archive.parent.mkdir(parents=True,exist_ok=True)
        runtime.api(f'repos/{REPO}/actions/artifacts/{meta["id"]}/zip',binary=archive)
        runtime.safe_extract(archive,ROOT,meta['digest'][7:]);manifest=runtime.verify(ROOT)
        if str(manifest.get('run_id'))!=str(previous['id']) or manifest.get('asof')!=manual.ASOF:raise Failure('manual_previous_artifact_identity_mismatch')
        Store(ROOT).write('restoration.json',dict(previous_run=previous['id'],artifact_id=meta['id'],zip_sha256=meta['digest'][7:]))
    elif c['previous_run'] is not None:raise Failure('manual_unexpected_previous_run')
    ROOT.mkdir(parents=True,exist_ok=True)
    if (ROOT/'seed').exists():universe=verify_master()
    else:
        runtime.ROOT=ROOT
        universe=runtime.seed()
    Store(ROOT).write('universe.json',universe)
    if (ROOT/'UNKNOWN_PREVIOUS_USAGE').exists():raise Failure('manual_previous_usage_unknown_no_new_HTTP')
    mode=ROOT/'modes'/c['mode']
    if c['mode']=='full':
        report=Store(ROOT/'modes/sample').read('report.json',{})
        verify_mode(ROOT/'modes/sample',report)
        manual.full_plan(report,universe)
    return universe,mode


def verify_mode(folder,report):
    if report.get('status')!='PASS' or not report.get('db_json_match'):raise Failure('manual_mode_report_not_pass')
    from .experiment import engine
    store=Store(folder);cache=engine.replay(store,1)
    for row in report['rows']:
        if engine.qualify_kis(row,cache,store,1)!=row:raise Failure('manual_row_raw_mismatch')
    entries=events(folder/'http.jsonl')
    if sum(e['event']=='started' for e in entries)!=report['physical_http']:raise Failure('manual_ledger_report_mismatch')
    import duckdb
    dbpath=(folder/report['database_file']).resolve()
    if folder.resolve() not in dbpath.parents:raise Failure('manual_unsafe_DB_path')
    with duckdb.connect(str(dbpath),read_only=True) as db:
        saved={s:json.loads(p) for s,p in db.execute('SELECT symbol,payload FROM observations').fetchall()}
    if saved!={r['symbol']:r for r in report['rows']}:raise Failure('manual_DB_JSON_mismatch')


def seal():
    if not ROOT.exists():return
    from .core import atomic
    try:
        secret_scan(Store(ROOT),None)
        Store(ROOT).write('manifest.json',dict(asof=manual.ASOF,run_id=os.environ['GITHUB_RUN_ID'],files=runtime.inventory(ROOT)))
    except Exception:
        (ROOT/'manifest.json').unlink(missing_ok=True)
        (ROOT.parent/'kis-manual-DO_NOT_UPLOAD').touch();raise


def publish():
    # A machine-readable CI result in this feature branch, not a message/Issue.
    # Raw responses and credentials stay only in scanned Actions artifacts.
    c=json.loads(Path(manual.CONTROL).read_bytes());s=Store(ROOT)
    blocked=s.read('blocked.json',{})
    report=blocked if str(blocked.get('run_id'))==os.environ['GITHUB_RUN_ID'] else s.read('modes/'+c['mode']+'/report.json') or {'status':'FAIL','reason':'no_verified_report'}
    if c['mode']=='full':report={k:v for k,v in report.items() if k!='rows'} | {'rows_preserved_in_state_artifact':True}
    state=runtime.verify(ROOT)
    receipt=dict(report=report,run_id=os.environ['GITHUB_RUN_ID'],run_url=f'https://github.com/{REPO}/actions/runs/'+os.environ['GITHUB_RUN_ID'],source_commit=os.environ['GITHUB_SHA'],state_manifest_sha256=sha(encoded(state)),full_plan=s.read('full-plan.json'),analysis=s.read('full-analysis.json'),restoration=s.read('restoration.json'),published_at=now())
    artifacts=runtime.api(f'repos/{REPO}/actions/runs/{os.environ["GITHUB_RUN_ID"]}/artifacts?per_page=100')['artifacts']
    receipt['artifacts']=[{k:a.get(k) for k in ('id','name','digest','size_in_bytes')} for a in artifacts]
    raw=encoded(receipt)
    for name in ('KIS_APP_KEY','KIS_APP_SECRET'):
        value=os.getenv(name)
        if value and value.encode() in raw:raise Failure('manual_receipt_credential_echo')
    if len(raw)>3000000:raise Failure('manual_receipt_size_cap')
    path=f'reports/kis-manual-20261026/{os.environ["GITHUB_RUN_ID"]}.json'
    runtime.api(f'repos/{REPO}/contents/{path}','PUT',dict(message='report: preserve KIS manual execution result '+os.environ['GITHUB_RUN_ID'],content=base64.b64encode(raw).decode(),branch=BRANCH.removeprefix('refs/heads/')))
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
        if c['mode']=='full' and receipt.get('analysis'):
            from .kis_full_analysis import markdown_table
            f.write(markdown_table(report,receipt['analysis'])+'\n')
            concise={k:v for k,v in receipt.items() if k not in ('report','full_plan')}
            f.write('```json\n'+json.dumps(concise,ensure_ascii=False,indent=2)+'\n```\n')
        else:f.write('```json\n'+json.dumps(receipt,ensure_ascii=False,indent=2)+'\n```\n')


def progress_report(payload):
    # Approved machine progress record only; no raw responses or credential values.
    path=f'reports/kis-manual-20261026/{os.environ["GITHUB_RUN_ID"]}-progress.json'
    endpoint=f'repos/{REPO}/contents/{path}'
    data=dict(message='progress: KIS full collection '+str(payload.get('physical_http')),content=base64.b64encode(encoded(payload)).decode(),branch=BRANCH.removeprefix('refs/heads/'))
    if getattr(progress_report,'file_sha',None):data['sha']=progress_report.file_sha
    result=runtime.api(endpoint,'PUT',data)
    progress_report.file_sha=result['content']['sha']


def execute():
    preparation_started=now();begin=time.monotonic();c=context()
    try:universe,folder=prepare(c)
    except Exception:
        ROOT.mkdir(parents=True,exist_ok=True);(ROOT/'UNKNOWN_PREVIOUS_USAGE').touch();raise
    (ROOT/'blocked.json').unlink(missing_ok=True)
    preparation_seconds=time.monotonic()-begin
    result=manual.run(folder,c,universe,os.environ['GITHUB_RUN_ID'],progress=progress_report if c['mode']=='full' else None)
    verification_start=time.monotonic()
    if result['status']=='PASS':
        verify_mode(folder,result)
        if c['mode']=='sample':Store(ROOT).write('full-plan.json',manual.full_plan(result,universe))
    result['preparation_started_at']=preparation_started;result['preparation_seconds']=preparation_seconds
    result['raw_DB_reverification_seconds']=time.monotonic()-verification_start
    result['metrics']['seconds']['raw_DB_reverification']=result['raw_DB_reverification_seconds']
    result['completed_at']=now()
    result['elapsed_seconds']=(dt.datetime.fromisoformat(result['completed_at'])-dt.datetime.fromisoformat(result['actual_started_at'])).total_seconds()
    result['symbols_per_minute']=result['kis_validated']/max(result['elapsed_seconds']/60,1e-9)
    result['end_to_end_seconds']=(dt.datetime.fromisoformat(result['completed_at'])-dt.datetime.fromisoformat(preparation_started)).total_seconds()
    Store(folder).write('report.json',result)
    if c['mode']=='full':
        from .kis_full_analysis import analyze
        Store(ROOT).write('full-analysis.json',analyze(result,events(folder/'http.jsonl')))
        try:progress_report(dict(run_id=os.environ['GITHUB_RUN_ID'],mode='full',asof=manual.ASOF,status=result['status'],target=result['target'],received=result['received_symbols'],validated=result['kis_validated'],physical_http=result['physical_http'],completed_at=result['completed_at']))
        except Exception:pass
    return 0 if result['status']=='PASS' else 1


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['run','seal','publish','plan-full']);a=p.parse_args()
    try:
        if a.mode=='run':return execute()
        if a.mode=='seal':return seal()
        if a.mode=='publish':return publish()
        c=json.loads(Path(manual.CONTROL).read_bytes())
        # Local planning is offline; no credentials or GitHub API are used.
        r=Store(ROOT).read('modes/sample/report.json',{});u=Store(ROOT).read('universe.json',[])
        verify_mode(ROOT/'modes/sample',r);print(json.dumps(manual.full_plan(r,u),ensure_ascii=False,indent=2))
    except Exception as e:
        if a.mode=='run':
            ROOT.mkdir(parents=True,exist_ok=True)
            if not (ROOT/'universe.json').exists():(ROOT/'UNKNOWN_PREVIOUS_USAGE').touch()
            reason=e.code if isinstance(e,Failure) else 'manual_program_failure'
            Store(ROOT).write('blocked.json',dict(status='FAIL',reason=reason,exception_type=type(e).__name__,run_id=os.environ.get('GITHUB_RUN_ID'),observed_at=now()))
        raise


if __name__=='__main__':raise SystemExit(main())
