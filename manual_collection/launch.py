"""One user-approved manual start; frozen reviewed runtime, same durable day claim."""
import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
import tempfile

LAUNCHER=Path(__file__).resolve().parents[1]
RUNTIME=Path(os.environ.get('VERIFIED_RUNTIME_PATH',os.getcwd())).resolve()
sys.path.insert(0,str(RUNTIME))
REPO='0baselabkim-debug/qweasd'
BRANCH='refs/heads/fix/manual-start-20261030'
SOURCE='f36ab970428564e7569ae37d61f2cfa3f644e119'
DAY='2026-10-30'
ROOT=Path('data/kis-user-manual-20261030')
CONTROL=LAUNCHER/'.github/trials/manual-start-20261030.json'


def api(path,method='GET',value=None):
    args=['gh','api','repos/'+REPO+'/'+path,'--method',method]
    if value is not None:args+=['--input','-']
    p=subprocess.run(args,input=json.dumps(value).encode() if value is not None else None,capture_output=True,timeout=60)
    if p.returncode:raise ValueError('official_GitHub_request_failed')
    return json.loads(p.stdout) if p.stdout.strip() else {}


def actor():
    c=json.loads(CONTROL.read_bytes())
    if (os.getenv('GITHUB_REPOSITORY'),os.getenv('GITHUB_REF'),os.getenv('GITHUB_EVENT_NAME'),os.getenv('GITHUB_RUN_ATTEMPT'))!=(REPO,BRANCH,'push','1'):
        raise ValueError('manual_actor_scope_mismatch')
    if (c.get('asof'),c.get('approval'),c.get('max_total_http'),c.get('runtime_commit'))!=(DAY,'USER_START_NOW_CANCEL_AUTOMATIC_SCHEDULE',5600,SOURCE):
        raise ValueError('manual_explicit_scope_mismatch')
    at=dt.datetime.now(dt.timezone.utc)
    if not dt.datetime.fromisoformat(c['not_before'])<=at<dt.datetime.fromisoformat(c['expires_at']):raise ValueError('manual_control_window_closed')
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=RUNTIME,text=True).strip()!=SOURCE:raise ValueError('reviewed_runtime_commit_mismatch')
    return c


def disable():
    actor();workflow=api('actions/workflows/kis-market-2030.yml')
    if workflow['path']!='.github/workflows/kis-market-2030.yml':raise ValueError('wrong_disable_target')
    api('actions/workflows/'+str(workflow['id'])+'/disable','PUT')
    after=api('actions/workflows/'+str(workflow['id']))
    if after['state']!='disabled_manually':raise ValueError('automatic_schedule_not_disabled')
    from collector_v1.regular.github import GitHub
    from collector_v1.core import now,Store
    result=dict(status='AUTOMATIC_2030_DISABLED_BY_USER',workflow_id=workflow['id'],workflow_state=after['state'],
        run_id=os.environ['GITHUB_RUN_ID'],asof=DAY,checked_at=now(),market_http=0,
        source_files_unchanged=True,reason='User requested start now and remove scheduled collection')
    GitHub().write('manual-start/'+DAY+'-schedule-disabled.json',result)
    Store(ROOT).write('schedule-disabled.json',result)
    print(json.dumps(result))
    return 0


def context(run=None):
    from collector_v1.regular import policy
    control=json.loads(CONTROL.read_bytes());start=policy.timestamp(control['not_before'])
    if start.astimezone(policy.KST).date().isoformat()!=DAY:raise ValueError('manual_target_date_changed')
    run=run or api('actions/runs/'+os.environ['GITHUB_RUN_ID'])
    c=dict(schema=1,asof=DAY,slot='2030',generation_id='kis-regular-20261030-2030',
        scheduled_at=start.isoformat(),expires_at=(start+dt.timedelta(minutes=170)).isoformat(),
        run_created_at=run['created_at'],run_id=os.environ['GITHUB_RUN_ID'],code_commit=os.environ['GITHUB_SHA'],
        date_binding='USER_FIXED_20261030; original regular slot reused only for atomic daily deduplication',
        mode='USER_APPROVED_MANUAL_START',trigger='USER_REQUEST_START_NOW',runtime_commit=SOURCE,
        original_planned_slot='2030',observation_policy='ACTUAL_HTTP_TIMESTAMPS_NOT_2030_OBSERVATION',
        artifact_name='kis-user-manual-20261030-'+os.environ['GITHUB_RUN_ID'])
    return c


class Guard:
    def __init__(self,c,client,ledger,config):self.c,self.client,self.ledger,self.config=c,client,ledger,config;self.apis=[];self.checked=False
    def register_api(self,api):self.apis.append(api)
    def __call__(self):
        from collector_v1.regular import policy
        from collector_v1.regular.github import check_runs
        from collector_v1.core import Failure
        from kis_shared.runtime import current
        actor();policy.window(self.c);policy.access_proof(self.config,os.environ)
        if sum(a.budget.used for a in self.apis)>=5600:raise Failure('combined_physical_HTTP_budget_exhausted')
        deadline=current().token_deadline
        if deadline is not None and deadline<=policy.timestamp(self.c['expires_at'])+dt.timedelta(seconds=35):raise Failure('shared_token_cannot_cover_frozen_execution_window')
        if not self.checked:
            self.ledger.assert_owner();check_runs(self.client,self.c);self.checked=True


def physical(c):
    from collector_v1.core import Store
    from collector_v1.experiment.storage import events
    calls=Store(ROOT/c['generation_id']/'preparation').read('requests.json',[])
    events_=events(ROOT/c['generation_id']/'collection'/'http.jsonl')
    starts=[e for e in events_ if e['event']=='started'];ends=[e for e in events_ if e['event']=='finished']
    resolved=(len(starts)==len(ends) and all(e.get('status') in ('received','http_failure') for e in calls+ends))
    return calls,starts,ends,resolved


def collect():
    actor()
    from collector_v1.regular import collector,policy,runner
    from collector_v1.regular.github import GitHub,check_runs
    from collector_v1.regular.ledger import Ledger,TERMINAL
    from collector_v1.core import Store,now,Failure,encoded
    from kis_shared.runtime import configured
    c=context();policy.window(c);client=GitHub();ledger=Ledger(client,c);store=Store(ROOT)
    if client.request('repos/'+REPO+'/actions/workflows/kis-market-2030.yml')['state']!='disabled_manually':raise ValueError('schedule_must_be_disabled_before_manual_auth')
    current_run=client.run(c['run_id'])
    if current_run['path']!='.github/workflows/kis-manual-now-20261030.yml' or current_run['head_sha']!=c['code_commit']:raise ValueError('manual_workflow_identity_mismatch')
    previous=ledger.current()
    if previous:
        store.write('report.json',dict(status='DUPLICATE_NOT_COLLECTED',prior_status=previous['status'],prior_run=previous['context']['run_id'],total_physical_http=0))
        print('Existing date owner preserved; no new market requests');return 0
    check_runs(client,c)
    claimed,record=ledger.claim()
    if not claimed:raise ValueError('another_date_owner_won_no_market_HTTP')
    store.write('context.json',c)
    config=json.loads(Path('.github/config/kis-regular-access.json').read_bytes());guard=Guard(c,client,ledger,config)
    started=now();report={};session=None
    try:
        with configured(config['shared_policy']) as session:
            guard()
            report,_=collector.run(ROOT/c['generation_id'],c,guard,phase=lambda phase:ledger.transition(phase))
            if not physical(c)[3]:session.uncertain=True
    except Exception as exc:
        report=dict(status='FAILED',reason=getattr(exc,'code',str(exc) if type(exc).__name__=='Blocked' else 'program_'+type(exc).__name__),internal_validation='NOT_PASS')
        # Only this live synchronous owner can release after positively resolved
        # HTTP. Killed workers and unknown outcomes never reach this release.
        if session is not None and session.lease is not None and session.lease.held and physical(c)[3]:
            session.lease.release()
    finally:
        calls,starts,ends,resolved=physical(c)
        report.update(context=c,actual_trigger='USER_MANUAL_REQUEST',preparation_http=len(calls),physical_http=len(starts),
            total_physical_http=len(calls)+len(starts),pipeline_started_at=started,pipeline_completed_at=now(),all_http_resolved=resolved,
            provider_finality='UNVERIFIED',official_price_status='PENDING_KRX',dashboard_publication='NOT_PUBLISHED',
            account_lease_released=bool(session is not None and session.lease is not None and not session.lease.held))
        store.write('report.json',report);runner.scan(ROOT,guard.apis)
        summary=runner.summarize(report,c);store.write('summary.json',summary)
        record=ledger.current()
        if record and record['status'] not in TERMINAL:
            ledger.transition('INTERNAL_PASS' if report['status']=='INTERNAL_PASS' else 'SKIPPED_CLOSED' if report['status']=='SKIPPED_CLOSED' else 'FAILED',summary=summary,reason=report.get('reason'))
        store.write('SCAN_COMPLETE.json',dict(files=runner.inventory(ROOT),live_in_memory_tokens_scanned=True))
    return 0 if report['status'] in ('INTERNAL_PASS','SKIPPED_CLOSED') else 1


def seal():
    from collector_v1.regular import runner
    from collector_v1.core import Store
    store=Store(ROOT);scan=store.read('SCAN_COMPLETE.json')
    current={k:v for k,v in runner.inventory(ROOT).items() if k!='SCAN_COMPLETE.json'}
    if not scan or scan['files']!=current:return 0
    runner.scan(ROOT);(ROOT/'UPLOAD_ALLOWED').touch();c=store.read('context.json')
    store.write('manifest.json',dict(schema=1,generation_id=c['generation_id'],asof=DAY,run_id=c['run_id'],code_commit=c['code_commit'],files=runner.inventory(ROOT)))
    return 0


def finalize():
    actor()
    from collector_v1.regular import runner,collector,policy
    from collector_v1.regular.github import GitHub
    from collector_v1.regular.ledger import Ledger
    from collector_v1.core import Store,sha,encoded
    store=Store(ROOT);r=store.read('report.json',{});c=store.read('context.json')
    if r.get('status')!='INTERNAL_PASS':return 0
    manifest=runner.verify_manifest(ROOT);collector.verify(ROOT/c['generation_id'],c,r)
    aid=os.environ['ARTIFACT_ID'];digest=os.environ['ARTIFACT_DIGEST'].removeprefix('sha256:')
    meta=api('actions/artifacts/'+aid)
    if (str(meta['workflow_run']['id']),meta['digest'],meta['expired'])!=(c['run_id'],'sha256:'+digest,False):raise ValueError('uploaded_artifact_identity_mismatch')
    path=Path(tempfile.mkdtemp())/'verified.zip'
    with path.open('wb') as out:p=subprocess.run(['gh','api','repos/'+REPO+'/actions/artifacts/'+aid+'/zip'],stdout=out,stderr=subprocess.PIPE,timeout=180)
    if p.returncode or sha(path.read_bytes())!=digest:raise ValueError('uploaded_artifact_ZIP_hash_mismatch')
    with zipfile.ZipFile(path) as z:
        files=[i.filename for i in z.infolist() if not i.is_dir()]
        if len(files)!=len(set(files)) or set(files)!=set(manifest['files'])|{'manifest.json'}:raise ValueError('uploaded_artifact_file_population_mismatch')
        if json.loads(z.read('manifest.json'))!=manifest or any(sha(z.read(n))!=h for n,h in manifest['files'].items()):raise ValueError('uploaded_artifact_file_hash_mismatch')
    artifact=dict(id=int(aid),name=meta['name'],digest=meta['digest'],expires_at=meta['expires_at'],retention_days=90,
        manifest_sha256=sha(encoded(manifest)),run_url='https://github.com/'+REPO+'/actions/runs/'+c['run_id'],
        download_API='repos/'+REPO+'/actions/artifacts/'+aid+'/zip',ZIP_download_verified=True)
    summary=runner.summarize(r,c);summary['artifact_storage']='VERIFIED'
    Ledger(GitHub(),c).transition('COMPLETE_PROVISIONAL',summary=summary,artifact=artifact)
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('disable','collect','seal','finalize'));a=parser.parse_args()
    try:code=globals()[a.command]()
    except Exception as exc:
        print(json.dumps(dict(status='FAILED',reason=getattr(exc,'code',str(exc) if isinstance(exc,ValueError) else type(exc).__name__))));code=1
    raise SystemExit(code)
