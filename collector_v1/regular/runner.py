"""Scheduled collection vs deployment validation are deliberately distinct commands."""
import argparse
import base64
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import re
import sys
from ..core import Failure, Stop, Store, encoded, now, sha
from . import policy
from .github import GitHub, APIError, check_runs
from .ledger import Ledger, TERMINAL

ROOT=Path('data/kis-regular')
CONFIG=Path('.github/config/kis-regular-access.json')


def output(**values):
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:
            for k,v in values.items():f.write(k+'='+str(v).lower()+'\n' if isinstance(v,bool) else k+'='+str(v)+'\n')


def scan(root,apis=()):
    values=[os.environ[k] for k in ('GH_TOKEN','GITHUB_TOKEN','KIS_APP_KEY','KIS_APP_SECRET','KIS_REGULAR_APP_KEY','KIS_REGULAR_APP_SECRET') if os.getenv(k)]
    values += [a.token for a in apis if a and a.token]
    for p in Path(root).rglob('*'):
        if not p.is_file():continue
        raw=p.read_bytes()
        if p.suffix=='.gz':raw=gzip.decompress(raw)
        if any(x.encode() in raw for x in values):raise Failure('credential_in_regular_output')


def inventory(root):return {str(p.relative_to(root)):sha(p.read_bytes()) for p in sorted(Path(root).rglob('*')) if p.is_file() and p.name not in ('manifest.json','writer.lock')}


def verify_manifest(root):
    m=json.loads((Path(root)/'manifest.json').read_bytes())
    if m['files']!=inventory(root):raise Failure('regular_artifact_manifest_mismatch')
    return m


def context():
    return Store(ROOT).read('context.json')


def assert_runtime_actor(c):
    expected={'GITHUB_REPOSITORY':policy.REPO,'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'schedule',
              'GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':c['run_id'],'GITHUB_SHA':c['code_commit']}
    if any(os.getenv(k)!=v for k,v in expected.items()):raise Failure('regular_runtime_actor_binding_mismatch')


def freeze():
    client=GitHub();repo=client.request('repos/'+policy.REPO)
    if repo.get('default_branch')!=policy.BRANCH or os.getenv('GITHUB_REPOSITORY')!=policy.REPO or os.getenv('GITHUB_REF')!='refs/heads/main':raise Failure('regular_requires_actual_default_main_branch')
    if os.getenv('GITHUB_RUN_ATTEMPT')!='1':raise Failure('regular_rerun_forbidden')
    run=client.run(os.environ['GITHUB_RUN_ID'])
    if run.get('path')!=policy.WORKFLOW or run.get('head_sha')!=os.getenv('GITHUB_SHA'):raise Failure('regular_workflow_or_code_mismatch')
    event=os.environ['GITHUB_EVENT_NAME'];store=Store(ROOT)
    if event not in ('schedule','push','workflow_dispatch'):raise Failure('unsupported_regular_event')
    if event!='schedule':
        c=dict(mode='VALIDATION_ONLY',run_id=str(run['id']),code_commit=run['head_sha'],artifact_name='kis-regular-deployment-'+str(run['id']))
        store.write('context.json',c);output(collect=False,artifact_name=c['artifact_name']);return 0
    c=policy.freeze(run,os.environ.get('REQUESTED_CRON',''));c['mode']='SCHEDULED_COLLECTION';c['artifact_name']=c['generation_id']+'-run-'+c['run_id']
    store.write('context.json',c)
    ledger=Ledger(client,c)
    previous=ledger.current()
    if previous:
        store.write('report.json',dict(status='DUPLICATE_NOT_COLLECTED',prior_status=previous['status'],prior_run=previous['context']['run_id'],market_http=0))
        output(collect=False,artifact_name=c['artifact_name']);return 0
    # This job already holds kis-validation-auth; inventory is read again now.
    check_runs(client,c)
    claimed,record=ledger.claim()
    store.write('claim.json',dict(claimed=claimed,owner=record['context']['run_id'],prior_status=record['status']))
    if claimed:
        try:policy.window(c)
        except Failure as e:
            ledger.transition('BLOCKED',reason=e.code)
            store.write('report.json',dict(status='BLOCKED',reason=e.code,asof=c['asof'],market_http=0))
            claimed=False
    output(collect=claimed,artifact_name=c['artifact_name']);return 0


class RuntimeGuard:
    def __init__(self,client,c,config):self.client,self.c,self.config=client,c,config;self.checked=False;self.apis=[]
    def register_api(self,api):self.apis.append(api)
    def __call__(self):
        assert_runtime_actor(self.c)
        policy.window(self.c);policy.access_proof(self.config,os.environ)
        if not self.checked:
            Ledger(self.client,self.c).assert_owner();check_runs(self.client,self.c)
            self.checked=True


def summarize(r,c):
    keys=('status','internal_validation','target','received_symbols','kis_validated','physical_http','preparation_http','total_physical_http',
        'failed_http_requests','physical_retries','actual_started_at','first_http_at','last_http_at','completed_at','elapsed_seconds',
        'reason','collection_complete')
    result={k:r.get(k) for k in keys};result.update(asof=c['asof'],generation_id=c['generation_id'],run_id=c['run_id'],code_commit=c['code_commit'],
        scheduled_at=c['scheduled_at'],provider_finality='UNVERIFIED',official_price_status='PENDING_KRX',
        artifact_storage='NOT_CONFIRMED',dashboard_publication='NOT_PUBLISHED',application_reflection='NOT_UPDATED')
    result['uncollected_count']=len(r['uncollected_symbols']) if 'uncollected_symbols' in r else None
    result['validation_failed_count']=len(r['validation_failed_symbols']) if 'validation_failed_symbols' in r else None
    return result


def collect():
    from . import collector
    store=Store(ROOT);c=context();assert_runtime_actor(c);client=GitHub();ledger=Ledger(client,c);ledger.assert_owner()
    config=json.loads(CONFIG.read_bytes());guard=RuntimeGuard(client,c,config);started=now();report={}
    try:
        policy.window(c)
        if dt.date.fromisoformat(c['asof']).weekday()>=5:
            report=dict(status='SKIPPED_WEEKEND',reason='Asia_Seoul_civil_weekend',market_http=0)
        else:
            guard() # zero auth or market requests without reviewed isolated identity
            r,api=collector.run(ROOT/c['generation_id'],c,guard,phase=lambda state:ledger.transition(state))
            report=r
            report['status']='INTERNAL_PASS' if r['status']=='INTERNAL_PASS' else r['status']
    except (Failure,Stop) as e:
        report=dict(status='BLOCKED' if isinstance(e,Failure) and any(x in e.code for x in ('unverified','isolation','credential','github_','active_regular')) else 'FAILED',
            reason=e.code if isinstance(e,Failure) else str(e),internal_validation='NOT_PASS')
    except Exception as e:report=dict(status='FAILED',reason='program_'+type(e).__name__,internal_validation='NOT_PASS')
    finally:
        from ..experiment.storage import events
        prep=Store(ROOT/c['generation_id']/'preparation');calls=prep.read('requests.json',[])
        full_events=events(ROOT/c['generation_id']/'collection'/'http.jsonl')
        starts=[e for e in full_events if e['event']=='started'];ends=[e for e in full_events if e['event']=='finished']
        report.update(preparation_http=len(calls),physical_http=len(starts),total_physical_http=len(calls)+len(starts),
            pipeline_started_at=started,completed_at=now(),scheduled_at=c['scheduled_at'],context=c)
        times=[e['started_at'] for e in calls+starts];last=[e['completed_at'] for e in calls+ends if e.get('completed_at')]
        report['first_http_at']=min(times) if times else None;report['last_http_at']=max(last) if last else None
        store.write('report.json',report);scan(ROOT,guard.apis)
        summary=summarize(report,c)
        state=report['status'] if report['status'] in TERMINAL or report['status']=='INTERNAL_PASS' else 'FAILED'
        ledger.transition(state,summary=summary,reason=report.get('reason'))
        store.write('summary.json',summary)
    return 0 if report['status'] in ('INTERNAL_PASS','SKIPPED_CLOSED','SKIPPED_WEEKEND') else 1


def validate():
    """Deployment verification never reads market credentials or claims a slot."""
    client=GitHub();c=context();marker,_=client.contents('FORMAT.json')
    if marker!=policy.FORMAT:raise Failure('regular_state_branch_format_mismatch')
    workflow=client.request('repos/'+policy.REPO+'/actions/workflows/kis-market-2030.yml')
    if workflow.get('state')!='active' or workflow.get('path')!=policy.WORKFLOW:raise Failure('regular_workflow_not_registered_active')
    published=client.request('repos/'+policy.REPO+'/contents/'+policy.WORKFLOW+'?ref=main')
    workflow_raw=base64.b64decode(published['content']);workflow_text=workflow_raw.decode()
    if workflow_text.count('cron:')!=1 or "cron: '"+policy.CRON+"'" not in workflow_text:raise Failure('default_branch_regular_cron_mismatch')
    cfg=json.loads(CONFIG.read_bytes());external={}
    try:
        runs=client.active(cfg['other_repository']);external=dict(status='READ',active_runs=[dict(id=r['id'],status=r['status'],path=r['path']) for r in runs])
    except Failure:external=dict(status='UNREADABLE',reason='cross_repository_Actions_read_not_verified')
    report=dict(status='DEPLOYMENT_VALIDATED_NO_MARKET_HTTP',run_id=c['run_id'],code_commit=c['code_commit'],
        actual_default_branch='main',workflow_id=workflow['id'],workflow_state=workflow['state'],registered_cron=policy.CRON,
        default_branch_workflow_blob_sha=published['sha'],default_branch_workflow_sha256=sha(workflow_raw),
        operational_readiness='BLOCKED_ACCOUNT_ISOLATION_REVIEW',access_profile_mode=cfg['mode'],external_run_inventory=external,
        slot_claimed=False,market_http=0,first_scheduled_collection='NOT_YET_VERIFIED',
        dashboard_publication='NOT_PUBLISHED',application_reflection='NOT_UPDATED',checked_at=now())
    scan(ROOT);client.write('deployments/'+c['run_id']+'.json',report)
    Store(ROOT).write('report.json',report);return 0


def seal():
    store=Store(ROOT);c=context() or {}
    marker=ROOT/'UPLOAD_ALLOWED';marker.unlink(missing_ok=True);(ROOT/'manifest.json').unlink(missing_ok=True)
    if store.read('failure.json',{}).get('reason')=='credential_in_regular_output':
        raise Failure('credential_in_regular_output')
    if not store.read('report.json'):
        store.write('report.json',dict(status='FAILED',reason='workflow_step_failed_before_report',physical_http=None,physical_http_unknown=True,context=c))
    scan(ROOT);marker.touch()
    store.write('manifest.json',dict(schema=1,generation_id=c.get('generation_id'),run_id=os.environ.get('GITHUB_RUN_ID'),
        code_commit=os.environ.get('GITHUB_SHA'),asof=c.get('asof'),files=inventory(ROOT)))
    return 0


def finalize():
    store=Store(ROOT);c=context();r=store.read('report.json',{})
    if not c or c.get('mode')!='SCHEDULED_COLLECTION':return 0
    assert_runtime_actor(c)
    client=GitHub();ledger=Ledger(client,c);record=ledger.current()
    if not record or record['context']!=c:return 0 # duplicate did not acquire ownership
    if record['status'] in TERMINAL:return 0
    if store.read('failure.json'):
        ledger.transition('FAILED',reason=store.read('failure.json')['reason']);return 1
    if r.get('status')!='INTERNAL_PASS':
        ledger.transition('FAILED',reason=r.get('reason','workflow_step_failed_before_validated_report'));return 1
    try:
        from .collector import verify
        m=verify_manifest(ROOT)
        if (m.get('generation_id'),m.get('run_id'),m.get('asof'),m.get('code_commit'))!=(c['generation_id'],c['run_id'],c['asof'],c['code_commit']):raise Failure('artifact_manifest_identity_changed')
        verify(ROOT/c['generation_id'],c,r)
        artifact_id=os.environ.get('ARTIFACT_ID','');digest=os.environ.get('ARTIFACT_DIGEST','').removeprefix('sha256:')
        if not artifact_id.isdigit() or not re.fullmatch('[0-9a-f]{64}',digest):raise Failure('artifact_upload_not_verified')
        a=client.request('repos/'+policy.REPO+'/actions/artifacts/'+artifact_id)
        if (a.get('name'),a.get('digest'),a.get('expired'),str(a.get('workflow_run',{}).get('id')))!=(c['artifact_name'],'sha256:'+digest,False,c['run_id']):raise Failure('stored_artifact_identity_mismatch')
        if policy.timestamp(a['expires_at'])<=policy.utcnow():raise Failure('stored_artifact_expiration_inconsistent')
        artifact=dict(id=int(artifact_id),name=a['name'],digest=a['digest'],expires_at=a['expires_at'],
            run_url='https://github.com/'+policy.REPO+'/actions/runs/'+c['run_id'],
            download_API='repos/'+policy.REPO+'/actions/artifacts/'+artifact_id+'/zip',manifest_sha256=sha(encoded(m)),retention_days=90)
        summary=summarize(r,c);summary['artifact_storage']='VERIFIED'
        receipt=ledger.transition('COMPLETE_PROVISIONAL',summary=summary,artifact=artifact)
        Store(ROOT.parent).write('kis-regular-final-receipt.json',receipt)
        return 0
    except Exception as e:
        reason=e.code if isinstance(e,Failure) else 'artifact_verification_'+type(e).__name__
        ledger.transition('ARTIFACT_UNCONFIRMED',reason=reason);return 1


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('freeze','collect','validate','seal','finalize'));args=p.parse_args()
    try:code=globals()[args.command]()
    except Exception as e:
        reason=e.code if isinstance(e,Failure) else 'program_'+type(e).__name__
        Store(ROOT).write('failure.json',dict(status='FAILED',reason=reason,market_http_unknown=args.command=='collect'))
        print(json.dumps(dict(status='FAILED',reason=reason)));code=1
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            r=Store(ROOT).read('failure.json') or Store(ROOT.parent).read('kis-regular-final-receipt.json') or Store(ROOT).read('summary.json') or Store(ROOT).read('report.json')
            if r:f.write('```json\n'+json.dumps({k:v for k,v in r.items() if k not in ('rows','indices','master','context')},ensure_ascii=False,indent=2)+'\n```\n')
    return code


if __name__=='__main__':raise SystemExit(main())
