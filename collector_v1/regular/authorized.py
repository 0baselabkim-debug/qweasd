"""One explicitly approved recovery, after native monitor drain evidence.

The ordinary schedule/profile is untouched. No fabricated rollout PASS or
cross-repository privilege is needed: the operator first fences monitor starts,
verifies older runners ended, then this repository holds its real central lease.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import zipfile
from ..core import Failure, Store, sha, encoded, now
from ..experiment.storage import events
from . import policy, collector, runner
from .github import GitHub, APIError
from kis_shared.runtime import Session
from kis_shared.lease import Blocked

ROOT=Path('data/kis-authorized')
CONTROL=Path('.github/trials/approved-market-20261029.json')
WORKFLOW='.github/workflows/kis-approved-market-20261029.yml'
REF='refs/heads/fix/approved-market-20261029'
REQUEST='full-20261029-user-approved-exclusive'
DATE='2026-10-29'


def validate_window(c,current):
    start=policy.timestamp(c['execution_not_before']);end=policy.timestamp(c['expires_at'])
    if (c.get('asof')!=DATE or c.get('request_id')!=REQUEST
            or c.get('generation_id')!='kis-authorized-20261029-'+c['run_id']
            or c.get('slot')!='manual-approved-recovery'
            or not 0<(end-start).total_seconds()<=170*60):raise Failure('authorized_scope_or_window_invalid')
    if not start<=current<end:raise Failure('authorized_execution_window_closed')
    if current>=dt.datetime(2026,10,30,9,tzinfo=policy.KST):raise Failure('prior_day_new_collection_after_0900_forbidden')
    if policy.timestamp(c['scheduled_at'])!=dt.datetime(2026,10,29,20,30,tzinfo=policy.KST):raise Failure('original_slot_metadata_changed')


def verify_proof(control,c):
    path=Path(control['monitor_proof_file'])
    raw=path.read_bytes()
    if sha(raw)!=control['monitor_proof_sha256']:raise Failure('monitor_drain_proof_hash_mismatch')
    p=json.loads(raw)
    if (p.get('status'),p.get('request_id'),p.get('asof'),p.get('repository'),p.get('unprotected_active'),p.get('automatic_release'))!=(
            'QUIESCENT_WITH_NEW_REQUESTS_FENCED',REQUEST,DATE,'0baselabkim-debug/kis-intraday-monitor',0,False):
        raise Failure('monitor_not_proven_quiescent_and_fenced')
    if p.get('fence_commit')!=control['monitor_fence_commit'] or p['observations'][-1]['unprotected']:
        raise Failure('monitor_fence_identity_or_active_run_mismatch')
    for name in ('kis_shared/admission.py','config/kis-account-exclusive.json','scripts/actions_auth.py','stage3/live_probe.py'):
        if not re.fullmatch('[0-9a-f]{64}',p.get('source_hashes',{}).get(name,'')):
            raise Failure('monitor_transport_fence_proof_incomplete')
    gap=(policy.timestamp(c['admitted_at'])-policy.timestamp(p['quiescent_at'])).total_seconds()
    if not 65<=gap<=1800:raise Failure('monitor_drain_auth_quarantine_or_freshness_invalid')
    return p


def assert_actor(c):
    values={'GITHUB_REPOSITORY':policy.REPO,'GITHUB_REF':REF,'GITHUB_EVENT_NAME':'push',
            'GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':c['run_id'],'GITHUB_SHA':c['code_commit']}
    if any(os.getenv(k)!=v for k,v in values.items()):raise Failure('authorized_runtime_actor_mismatch')


def read_control():
    control=json.loads(CONTROL.read_bytes())
    if (control.get('schema'),control.get('request_id'),control.get('asof'),control.get('max_total_http'),control.get('approval'))!=(
            1,REQUEST,DATE,5600,'USER_APPROVED_EXCLUSIVE_FULL_MARKET_RECOVERY'):
        raise Failure('authorized_control_scope_mismatch')
    return control


def check_local_runs(client,c):
    run=client.run(c['run_id'])
    if (run.get('status'),run.get('head_sha'),run.get('path'))!=('in_progress',c['code_commit'],WORKFLOW):
        raise Failure('authorized_current_run_not_live')
    audited=[]
    for r in client.active():
        if str(r['id'])==c['run_id']:continue
        text=client.workflow(r)
        if 'KIS_APP_KEY' in text or 'KIS_REGULAR_APP_KEY' in text:
            if text.lstrip().startswith('{'):
                group=json.loads(text).get('concurrency',{}).get('group')
            else:
                match=re.search(r'^concurrency:\s*\n\s+group:\s*([^\n]+)',text,re.M)
                group=match[1].strip(' \"\'') if match else None
            if group!='kis-validation-auth':raise Failure('unserialized_existing_KIS_run_active')
        audited.append(dict(id=str(r['id']),status=r['status'],head_sha=r['head_sha'],path=r.get('path')))
    return audited


class Ticket:
    """Date claim is atomic. A failed request cannot be silently restarted."""
    def __init__(self,client,c):self.client,self.c=client,c;self.path='authorized/'+DATE+'.json'
    def read(self):
        try:return self.client.contents(self.path)
        except APIError as e:
            if e.status==404:return None,None
            raise
    def claim(self):
        old,_=self.read()
        if old:raise Failure('authorized_date_already_claimed_no_new_HTTP')
        normal,_=self.client.contents('ledger/2026.json')
        prior=normal['slots'].get(DATE+'/2030')
        if not prior or prior['status']!='BLOCKED' or prior.get('summary',{}).get('total_physical_http')!=0:
            raise Failure('normal_slot_not_proven_zero_HTTP_blocked')
        complete=normal.get('latest_complete')
        if complete and complete.get('asof')==DATE:raise Failure('date_already_has_complete_generation')
        value=dict(schema=1,context=self.c,status='CLAIMED',previous_run=prior['context']['run_id'],
                   previous_http=0,history=[dict(at=now(),status='CLAIMED')])
        self.client.write(self.path,value)
        actual,_=self.read()
        if actual!=value:raise Failure('authorized_claim_not_read_back')
    def update(self,status,**fields):
        old,version=self.read()
        if not old or old['context']!=self.c:raise Failure('authorized_ticket_ownership_lost')
        if old['status'] in ('COMPLETE_PROVISIONAL','FAILED','BLOCKED','SKIPPED_CLOSED'):
            raise Failure('authorized_ticket_terminal_preserved')
        old.update(status=status,**fields);old['history'].append(dict(at=now(),status=status))
        if len(old['history'])>128:raise Failure('authorized_history_limit')
        self.client.write(self.path,old,version)
        return old


class ExclusiveSession(Session):
    def __init__(self,c,control):
        self.c,self.control=c,control
        super().__init__({'token_expiry_timezone':None})
    def review(self):
        assert_actor(self.c);validate_window(self.c,policy.utcnow());verify_proof(self.control,self.c)
        if not os.getenv('KIS_APP_KEY') or not os.getenv('KIS_APP_SECRET'):raise Blocked('existing_KIS_credentials_missing')
        if self.binding is not None:
            import hashlib,hmac
            actual=hashlib.sha256(json.dumps([os.environ['KIS_APP_KEY'],os.environ['KIS_APP_SECRET']]).encode()).digest()
            if not hmac.compare_digest(self.binding,actual):raise Blocked('shared_credential_binding_changed')
    def token_response(self,value):
        """Use a conservative bound, without guessing the server timezone."""
        try:
            seconds=value['expires_in']
            if type(seconds) is not int or not 35<seconds<=86400:raise ValueError()
            naive=dt.datetime.strptime(value['access_token_token_expired'],'%Y-%m-%d %H:%M:%S')
            earliest=naive.replace(tzinfo=dt.timezone.utc)-dt.timedelta(hours=14)
            limit=min(earliest,policy.utcnow()+dt.timedelta(seconds=seconds-35))
            if limit<=policy.timestamp(self.c['expires_at'])+dt.timedelta(seconds=35):
                raise Blocked('token_cannot_safely_cover_bounded_collection_no_refresh')
            self.token_expiry(limit.isoformat())
        except (KeyError,ValueError,TypeError):raise Blocked('provider_token_expiry_not_safe_no_refresh') from None


class Guard:
    def __init__(self,c,control,ticket,client):self.c,self.control,self.ticket,self.client=c,control,ticket,client;self.apis=[];self.checked=False
    def register_api(self,api):
        if all(api is not other for other in self.apis):self.apis.append(api)
    def __call__(self):
        assert_actor(self.c);validate_window(self.c,policy.utcnow())
        if sum(api.budget.used for api in self.apis)>=5600:raise Failure('combined_physical_HTTP_budget_exhausted')
        if not self.checked:
            record,_=self.ticket.read()
            if not record or record['context']!=self.c:raise Failure('authorized_ticket_not_owned')
            check_local_runs(self.client,self.c);self.checked=True


def physical_state(c):
    calls=Store(ROOT/c['generation_id']/'preparation').read('requests.json',[])
    http=events(ROOT/c['generation_id']/'collection'/'http.jsonl')
    starts=[e for e in http if e['event']=='started'];ends=[e for e in http if e['event']=='finished']
    resolved=(len(starts)==len(ends) and all(e.get('status') in ('received','http_failure') for e in calls+ends))
    return calls,starts,ends,resolved


def make_context(control):
    return dict(schema=1,mode='AUTHORIZED_EXCLUSIVE_RECOVERY',asof=DATE,slot='manual-approved-recovery',request_id=REQUEST,
        run_id=os.environ['GITHUB_RUN_ID'],code_commit=os.environ['GITHUB_SHA'],
        generation_id='kis-authorized-20261029-'+os.environ['GITHUB_RUN_ID'],
        scheduled_at='2026-10-29T20:30:00+09:00',execution_not_before=control['not_before'],expires_at=control['expires_at'],
        admitted_at=now(),artifact_name='kis-authorized-20261029-'+os.environ['GITHUB_RUN_ID'],
        observation_policy='ACTUAL_HTTP_TIMES_ONLY_NOT_A_2030_OBSERVATION')


def collect():
    control=read_control();c=make_context(control);assert_actor(c);validate_window(c,policy.utcnow());verify_proof(control,c)
    client=GitHub();ticket=Ticket(client,c);check_local_runs(client,c);ticket.claim()
    store=Store(ROOT);store.write('context.json',c);store.write('admission-proof.json',verify_proof(control,c))
    guard=Guard(c,control,ticket,client);started=now();report={};session=None
    try:
        with ExclusiveSession(c,control) as session:
            report,_=collector.run(ROOT/c['generation_id'],c,guard,phase=lambda state:ticket.update(state),
                progress=lambda p:ticket.update('COLLECTING',progress=dict(p,total_physical_http=p['physical_http']+4)),progress_step=60)
            if not physical_state(c)[3]:session.uncertain=True
        report['account_lease_released']=not session.lease.held
    except Exception as e:
        report.update(status='BLOCKED' if isinstance(e,(Failure,Blocked)) else 'FAILED',
            reason=e.code if isinstance(e,Failure) else str(e) if isinstance(e,Blocked) else 'program_'+type(e).__name__)
        # Only this still-live owner may release a controlled failure after all
        # synchronous HTTP responses closed. Unknown transport/killed runners
        # retain the lease; the monitor fence also remains active for review.
        if session is not None and session.lease is not None and session.lease.held and physical_state(c)[3]:
            try:session.lease.release();report['account_lease_released']=True
            except Exception:report['account_lease_released']=False
    finally:
        calls,starts,ends,resolved=physical_state(c)
        report.update(context=c,pipeline_started_at=started,pipeline_completed_at=now(),
            total_physical_http=len(calls)+len(starts),preparation_http=len(calls),physical_http=len(starts),
            all_http_resolved=resolved,
            provider_finality='UNVERIFIED',official_price_status='PENDING_KRX',dashboard_publication='NOT_PUBLISHED')
        store.write('report.json',report)
        summary={k:v for k,v in report.items() if k not in ('rows','indices','master','context','target_symbols','uncollected_symbols','validation_failed_symbols','errors')}
        store.write('summary.json',summary)
        try:runner.scan(ROOT,guard.apis)
        except Failure:
            store.write('UNSAFE_OUTPUT.json',dict(reason='credential_echo_no_artifact'));raise
        store.write('SCAN_COMPLETE.json',dict(files=runner.inventory(ROOT),checked_with_live_token_in_memory=True))
        status=report.get('status','FAILED')
        ticket.update('INTERNAL_PASS' if status=='INTERNAL_PASS' else 'BLOCKED' if status=='BLOCKED' else 'SKIPPED_CLOSED' if status=='SKIPPED_CLOSED' else 'FAILED',summary=summary)
    return 0 if report['status'] in ('INTERNAL_PASS','SKIPPED_CLOSED') else 1


def seal():
    store=Store(ROOT)
    if store.read('UNSAFE_OUTPUT.json'):raise Failure('unsafe_output_must_not_upload')
    scanned=store.read('SCAN_COMPLETE.json')
    current={k:v for k,v in runner.inventory(ROOT).items() if k!='SCAN_COMPLETE.json'}
    if not scanned or scanned.get('files')!=current:raise Failure('live_token_scan_not_proven_for_current_files')
    runner.scan(ROOT)
    c=store.read('context.json',{})
    (ROOT/'UPLOAD_ALLOWED').touch()
    store.write('manifest.json',dict(schema=1,generation_id=c.get('generation_id'),asof=c.get('asof'),run_id=os.getenv('GITHUB_RUN_ID'),
        code_commit=os.getenv('GITHUB_SHA'),files=runner.inventory(ROOT)))
    return 0


def verify_zip(path,digest,manifest):
    path=Path(path)
    if path.stat().st_size>256*1024*1024 or sha(path.read_bytes())!=digest:
        raise Failure('stored_artifact_ZIP_digest_mismatch')
    with zipfile.ZipFile(path) as archive:
        members=[i for i in archive.infolist() if not i.is_dir()]
        names=[i.filename for i in members]
        if len(names)!=len(set(names)) or set(names)!=set(manifest['files'])|{'manifest.json'}:
            raise Failure('stored_artifact_file_population_mismatch')
        if json.loads(archive.read('manifest.json'))!=manifest:raise Failure('stored_artifact_manifest_mismatch')
        for info in members:
            if info.file_size>128*1024*1024:raise Failure('stored_artifact_member_too_large')
            if info.filename!='manifest.json' and sha(archive.read(info))!=manifest['files'][info.filename]:
                raise Failure('stored_artifact_file_hash_mismatch')
    return dict(zip_sha256=digest,files_verified=len(members),zip_bytes=path.stat().st_size)


def finalize():
    store=Store(ROOT);r=store.read('report.json',{});c=store.read('context.json',{})
    if not c or r.get('status')!='INTERNAL_PASS':return 1
    assert_actor(c);manifest=runner.verify_manifest(ROOT);collector.verify(ROOT/c['generation_id'],c,r)
    id=os.environ.get('ARTIFACT_ID','');digest=os.environ.get('ARTIFACT_DIGEST','').removeprefix('sha256:')
    if not id.isdigit() or not re.fullmatch('[0-9a-f]{64}',digest):raise Failure('authorized_artifact_upload_unconfirmed')
    client=GitHub();a=client.request('repos/'+policy.REPO+'/actions/artifacts/'+id)
    if (a.get('name'),a.get('digest'),a.get('expired'),str(a.get('workflow_run',{}).get('id')))!=(c['artifact_name'],'sha256:'+digest,False,c['run_id']):
        raise Failure('authorized_artifact_identity_mismatch')
    if policy.timestamp(a['expires_at'])<=policy.utcnow():raise Failure('authorized_artifact_expired')
    archive=Path(tempfile.mkdtemp(prefix='verified-authorized-artifact-'))/'artifact.zip'
    with archive.open('wb') as output:
        downloaded=subprocess.run(['gh','api','repos/'+policy.REPO+'/actions/artifacts/'+id+'/zip'],stdout=output,stderr=subprocess.PIPE,timeout=180)
    if downloaded.returncode:raise Failure('stored_artifact_official_download_failed')
    zip_proof=verify_zip(archive,digest,manifest)
    summary=store.read('summary.json');summary.update(artifact_storage='VERIFIED',internal_validation='PASS')
    summary['stored_zip_verification']=zip_proof
    artifact=dict(id=id,digest=a['digest'],expires_at=a['expires_at'],name=a['name'],manifest_sha256=sha(encoded(manifest)),retention_days=90,
        run_url='https://github.com/'+policy.REPO+'/actions/runs/'+c['run_id'])
    samples=[row for row in r['rows'] if row['symbol'] in ('000720','064400','300080')]
    receipt=Ticket(client,c).update('COMPLETE_PROVISIONAL',summary=summary,artifact=artifact,sample_rows=samples)
    Store(ROOT.parent).write('kis-authorized-final-receipt.json',receipt)
    return 0


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('collect','seal','finalize'));a=p.parse_args()
    try:code=globals()[a.command]()
    except Exception as e:
        reason=e.code if isinstance(e,Failure) else str(e) if isinstance(e,Blocked) else 'program_'+type(e).__name__
        if a.command=='collect':Store(ROOT.parent).write('authorized-workflow-error.json',dict(status='FAILED',reason=reason))
        print(json.dumps(dict(status='FAILED',reason=reason)));code=1
    summary=Store(ROOT).read('summary.json',{})
    scanned=Store(ROOT).read('SCAN_COMPLETE.json',{})
    if (Store(ROOT).read('UNSAFE_OUTPUT.json') or not scanned
            or scanned.get('files',{}).get('summary.json')!=sha(encoded(summary))):
        summary=dict(status='NOT_YET_SAFELY_SCANNED',raw_output_not_published=True)
    if os.getenv('GITHUB_STEP_SUMMARY') and summary:
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n')
    return code


if __name__=='__main__':raise SystemExit(main())
