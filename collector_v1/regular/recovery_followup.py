"""User approved follow-up of the verified Nov3 partial collection. Preserve failed source and total budget.

No expiry takeover, altered clock, schedule impersonation or main cron change.
The old owner's190minute TTL and60second grace are checked by Lease.recover.
"""
import argparse
import copy
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import zipfile
from ..core import Failure, Store, encoded, sha, now
from .. import kis_manual
from ..experiment import engine
from ..experiment.storage import Evidence, Metrics, append, events
from . import collector, policy
from .github import GitHub, APIError
from .runner import scan, inventory
from kis_shared.lease import Lease, Blocked, stamp, REPOSITORIES
from kis_shared.git_backend import GitBackend
from kis_shared.runtime import configured, current, review_local_evidence

REPO=policy.REPO
BRANCH='recovery/regular-20261103'
WORKFLOW='.github/workflows/kis-regular-recovery-followup-20261103.yml'
CONTROL=Path('.github/trials/regular-recovery-followup-20261103.json')
ROOT=Path('data/kis-regular-recovery-followup-20261103')
SOURCE_RUN='41649661831'
SOURCE_SHA='38264b377649ac139c9e1ef89332302fe27ff5b6'
ARTIFACT=13255235841
ZIP_SHA='121a57a01d4b10a22f5358425537b7bc94a6cf34e58db03ed2b2cc19e7a9351a'
GENERATION='kis-regular-20261103-2030'
ASOF='2026-11-03'
POPULATION='29468db28029f0bd73c03e8443374273a282dd0e5e03b2aa31bf5b4eb0d0c0e7'
EXPECTED_OWNER='badae830e5224d198d003e3e554575b5'
SOURCE_BRANCH='kis-shared-account-state'
UTC=dt.timezone.utc


def require(test,reason):
    if not test:raise Failure('regular_recovery_'+reason)


def read_control(path=CONTROL):return json.loads(Path(path).read_bytes())


def check_control(c,at=None,*,allow_wait=False):
    at=at or dt.datetime.now(UTC)
    require(set(c)=={'schema','enabled','approval','source_run','artifact_id','artifact_sha256','asof','generation_id','max_total_http','request_id','expected_revision','reviewed_code_commit','offline_ci_run','not_before','expires_at'},'control_schema')
    require(all(isinstance(c.get(k),str) and re.fullmatch('[0-9a-f]{40}',c[k]) for k in ('expected_revision','reviewed_code_commit')) and str(c.get('offline_ci_run','')).isdigit(),'code_review_binding_missing')
    require(type(c.get('schema')) is int and type(c.get('artifact_id')) is int and type(c.get('max_total_http')) is int,'control_numeric_types')
    require(c.get('schema')==1 and c.get('enabled') is True and
        c.get('approval')=='USER_APPROVED_CONTINUE_VERIFIED_NOV3_REMAINDER','explicit_approval_missing')
    require((c.get('source_run'),c.get('artifact_id'),c.get('artifact_sha256'),c.get('asof'),c.get('generation_id'),c.get('max_total_http'))==
        (SOURCE_RUN,ARTIFACT,ZIP_SHA,ASOF,GENERATION,5600),'scope_changed')
    require(re.fullmatch('[A-Za-z0-9_-]{8,80}',c.get('request_id','')) is not None,'request_id_invalid')
    start,end=stamp(c['not_before']),stamp(c['expires_at'])
    require(start==stamp('2026-11-03T21:34:30+00:00') and end==stamp('2026-11-04T00:00:00+00:00') and 0<(end-start).total_seconds()<=10200,'window_not_reviewed')
    require(at<end and (allow_wait or at>=start),'outside_manual_window')
    require(at<dt.datetime(2026,11,4,9,tzinfo=policy.KST),'prior_day_cutoff')
    return c


def actor(c,client):
    require(os.environ.get('GITHUB_REPOSITORY')==REPO and os.environ.get('GITHUB_REF')=='refs/heads/'+BRANCH
        and os.environ.get('GITHUB_EVENT_NAME')=='push' and os.environ.get('GITHUB_RUN_ATTEMPT')=='1','actor_or_attempt_invalid')
    run=client.run(os.environ['GITHUB_RUN_ID'])
    require(run.get('path')==WORKFLOW and run.get('head_sha')==os.environ.get('GITHUB_SHA')
        and run.get('event')=='push' and run.get('status')=='in_progress' and run.get('run_attempt')==1,'actual_run_mismatch')
    review_local_evidence(json.loads(Path('.github/config/kis-shared-account.json').read_bytes()),REPO)
    ci=client.run(c['offline_ci_run'])
    require(ci.get('status')=='completed' and ci.get('conclusion')=='success' and ci.get('head_sha')==c['reviewed_code_commit']
        and ci.get('path')=='.github/workflows/kis-regular-recovery-offline.yml','offline_CI_not_verified')
    compare=client.request(f"repos/{REPO}/compare/{c['reviewed_code_commit']}...{run['head_sha']}")
    require(compare.get('ahead_by')==1 and compare.get('behind_by')==0
        and [f['filename'] for f in compare.get('files',[])]==[str(CONTROL)],'arming_must_change_control_only')
    return run


def restore_zip(path,destination):
    path=Path(path);destination=Path(destination)
    require(sha(path.read_bytes())==ZIP_SHA,'zip_digest_mismatch')
    require(not destination.exists(),'source_root_exists')
    z=zipfile.ZipFile(path);require(z.testzip() is None,'zip_CRC_failed')
    names=z.namelist();require(len(names)==len(set(names)),'duplicate_zip_entry')
    for name in names:
        target=(destination/name).resolve()
        require(target.is_relative_to(destination.resolve()) and not name.startswith('/'),'zip_path_unsafe')
        require(not ((z.getinfo(name).external_attr>>16)&0o170000)==0o120000,'zip_symlink')
    z.extractall(destination)
    m=json.loads((destination/'manifest.json').read_bytes())
    require((m['recovery_run'],m['asof'],m['generation_id'])==(SOURCE_RUN,ASOF,GENERATION) and m['source_run']=='41633101454' and m['source_artifact']==13230751197,'source_identity_mismatch')
    require(m['files']==inventory(destination),'source_manifest_mismatch')
    folder=destination/GENERATION;report=json.loads((destination/'report.json').read_bytes())
    collector.verify(folder,json.loads((destination/'source/context.json').read_bytes()),report)
    require(report['target']==2348 and report['kis_validated']==1217 and report['physical_http']==2440
        and report['failed_http_requests']==2 and report['population_sha256']==POPULATION,'source_counts_not_reviewed')
    original=Store(destination/'source').read('manifest.json')
    require(sha(encoded(original))=='bf312b7f74dfcf1de8c7a5b5c9add84ab9a853b5281ef6dcb73dc5c461e17d99' and original['files']==inventory(destination/'source'),'original_chain_corrupt')
    prep=json.loads((folder/'preparation/requests.json').read_bytes())
    require(len(prep)==4 and all(e.get('status')=='received' and e.get('http')==200 for e in prep),'preparation_or_auth_unknown')
    auth=[e for e in prep if e['endpoint']=='/oauth2/tokenP']
    require(len(auth)==1,'auth_count_not_reviewed')
    journal=events(folder/'collection/http.jsonl');starts=[x for x in journal if x['event']=='started'];ends=[x for x in journal if x['event']=='finished']
    require(len(starts)==len(ends)==2440 and [e['sequence'] for e in starts]==list(range(1,2441))
        and {e['sequence'] for e in ends}==set(range(1,2441)),'physical_ledger_incomplete')
    auth_starts=[e for e in starts if e['endpoint']=='/oauth2/tokenP']
    require(len(auth_starts)==1 and auth_starts[0]['sequence']==565 and next(e for e in ends if e['sequence']==565).get('http')==200,'prior_collection_auth_unresolved')
    failed=[e for e in ends if e['status']!='received']
    require(len(failed)==2 and all(e['status']=='network_failure' for e in failed) and {e['sequence'] for e in failed}=={564,2440},'unknown_request_not_reviewed')
    last=starts[-1]
    require(last['endpoint']==kis_manual.Q+'inquire-daily-itemchartprice' and last['symbol']=='060900'
        and last['generation_id']==GENERATION,'unknown_auth_or_write_forbidden')
    failures=events(folder/'collection/failures.jsonl')
    require(sum(e['reason']=='network_failure' for e in failures)==2 and all(e['reason'] in
        ('network_failure','shared_prior_HTTP_outcome_unresolved') for e in failures),'other_permanent_failure')
    cache=engine.replay(Evidence(folder/'collection',Metrics()),1,asof=ASOF,generation_id=GENERATION)
    require(len(cache)==2437,'cache_count_not_reviewed')
    return dict(artifact_sha256=ZIP_SHA,source_run=SOURCE_RUN,source_commit=SOURCE_SHA,source_generation=GENERATION,
        source_manifest_sha256=sha(encoded(m)),population_sha256=POPULATION,preparation_http=4,collection_http=2440,
        total_http=2444,successful_observations=2437,validated_symbols=1217,target=2348,remaining_tasks=2261,
        allowed_failure_hashes=[sha(encoded(e)) for e in failures if e['reason']=='shared_prior_HTTP_outcome_unresolved'])


def terminal_proof(client,owner,at=None):
    at=at or dt.datetime.now(UTC)
    require(owner and owner['repository']==REPO and owner['run_id']==SOURCE_RUN and owner['commit']==SOURCE_SHA
        and owner['id']==EXPECTED_OWNER and stamp(owner['expires_at'])-stamp(owner['acquired_at'])==dt.timedelta(seconds=11400),'owner_not_reviewed')
    run=client.run(SOURCE_RUN)
    require(run.get('head_sha')==SOURCE_SHA and run.get('run_attempt')==1 and run.get('status')=='completed'
        and run.get('path')=='.github/workflows/kis-regular-recovery-20261103.yml' and run.get('conclusion')=='failure','source_run_not_terminal_attempt1')
    attempt=client.request(f'repos/{REPO}/actions/runs/{SOURCE_RUN}/attempts/1')
    bindings=('id','head_sha','run_attempt','status','conclusion','path','workflow_id','check_suite_id','created_at','run_started_at')
    require(all(run.get(k) is not None and attempt.get(k)==run[k] for k in bindings),'attempt_conflict')
    jobs=client.request(f'repos/{REPO}/actions/runs/{SOURCE_RUN}/attempts/1/jobs?per_page=100')
    require(jobs.get('total_count')==len(jobs.get('jobs',[]))==1,'source_jobs_incomplete')
    job=jobs['jobs'][0]
    require(str(job.get('id'))=='125964145863' and job.get('run_attempt')==1 and str(job.get('run_id'))==SOURCE_RUN
        and job.get('head_sha')==SOURCE_SHA and job.get('status')=='completed' and job.get('completed_at')
        and job.get('runner_group_name')=='GitHub Actions' and 'ubuntu-latest' in job.get('labels',[]),'source_worker_unverified')
    require(all(s.get('status')=='completed' for s in job.get('steps',[])) and job.get('steps'),'source_steps_not_terminal')
    require(job.get('started_at') and stamp(job['started_at'])<=stamp(owner['acquired_at'])<=stamp(job['completed_at']),'lease_outside_source_job')
    require(at>=stamp(job['completed_at'])+dt.timedelta(seconds=120),'worker_termination_grace')
    # GitHub's run and attempt views have distinct metadata update timestamps.
    # Bind execution identity/status exactly, then recheck EACH view unchanged.
    require(run.get('updated_at') and attempt.get('updated_at') and
        stamp(job['completed_at'])<=stamp(run['updated_at'])<=at and
        stamp(job['completed_at'])<=stamp(attempt['updated_at'])<=at,'terminal_metadata_time_invalid')
    again=client.run(SOURCE_RUN)
    require(all(again.get(k)==run.get(k) for k in ('id','head_sha','run_attempt','status','conclusion','updated_at')),'run_changed')
    attempt_again=client.request(f'repos/{REPO}/actions/runs/{SOURCE_RUN}/attempts/1')
    require(all(attempt_again.get(k)==attempt.get(k) for k in bindings+('updated_at',)),'attempt_changed')
    # The immutable source has a known complete OAuth, synchronous read-only GET
    # failure, no external request workers, all hosted jobs ended; retain its cost.
    return dict(repository=REPO,run_id=SOURCE_RUN,commit=SOURCE_SHA,status='completed',all_workers_stopped=True,
        external_issuers_stopped=True,attempt=1,job_id=str(job['id']),checked_at=at.isoformat(),
        job_started_at=job['started_at'],job_completed_at=job['completed_at'],run_updated_at=run['updated_at'],attempt_updated_at=attempt['updated_at'],
        evidence_basis='source ZIP manifest/raw/complete auth+GET-only failure; exact pinned synchronous source; terminal hosted job; expiry+grace')


def state_path(c):return 'recoveries/2026-11-03/'+c['request_id']+'.json'


def put_new(client,path,value):
    try:client.contents(path)
    except APIError as e:
        if e.status!=404:raise
    else:raise Failure('regular_recovery_request_already_recorded')
    client.write(path,value)


def prepare():
    c=check_control(read_control(),allow_wait=True);client=GitHub();run=actor(c,client)
    require(not ROOT.exists(),'execution_root_cannot_reset')
    ROOT.mkdir(parents=True)
    meta=client.request(f'repos/{REPO}/actions/artifacts/{ARTIFACT}')
    require(meta.get('digest')=='sha256:'+ZIP_SHA and not meta.get('expired')
        and str(meta.get('workflow_run',{}).get('id'))==SOURCE_RUN,'official_artifact_mismatch')
    path=Path(os.environ['RUNNER_TEMP'])/'regular-source-13255235841.zip'
    with path.open('xb') as f:
        subprocess.run(['gh','api',f'repos/{REPO}/actions/artifacts/{ARTIFACT}/zip'],stdout=f,check=True,timeout=120)
    proof=restore_zip(path,ROOT/'source')
    Store(ROOT).write('source-proof.json',proof)
    # Copy execution state, never delete or rewrite the failed source prefix.
    shutil.copytree(ROOT/'source'/GENERATION,ROOT/GENERATION)
    for p in (ROOT/'source').rglob('*'):
        if p.is_file():p.chmod(0o444)
    backend=GitBackend(Path(os.environ['RUNNER_TEMP'])/'old-owner-recovery.git')
    revision,state=backend.read();require(revision==c['expected_revision'],'owner_revision_changed')
    terminal=terminal_proof(client,state['owner'])
    receipt=dict(schema=1,status='WAITING_FOR_SAFE_RECOVERY',request_id=c['request_id'],new_run_id=str(run['id']),new_commit=run['head_sha'],
        owner_revision=revision,owner=state['owner'],source=proof,terminal=terminal,not_before=c['not_before'],expires_at=c['expires_at'],
        cumulative_http=2444,market_http_this_run=0,prepared_at=now(),
        safety_state={k:state.get(k) for k in ('last_auth','last_http','auth_not_before','request_scopes')})
    put_new(client,state_path(c),receipt);Store(ROOT).write('intent.json',receipt)
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('source','terminal')}))


def wait_recover():
    c=check_control(read_control(),allow_wait=True);client=GitHub();actor(c,client)
    receipt=Store(ROOT).read('intent.json');require(receipt and receipt['new_run_id']==os.environ['GITHUB_RUN_ID'],'intent_missing')
    backend=GitBackend(Path(os.environ['RUNNER_TEMP'])/'old-owner-recovery.git')
    last_print=0
    while dt.datetime.now(UTC)<stamp(c['not_before']):
        check_control(c,allow_wait=True)
        revision,state=backend.read();require(revision==receipt['owner_revision'] and state['owner']==receipt['owner'],'owner_changed_during_wait')
        left=(stamp(c['not_before'])-dt.datetime.now(UTC)).total_seconds()
        if time.monotonic()-last_print>300:
            print(json.dumps(dict(status='WAITING_FOR_EXISTING_190_MINUTE_LEASE_AND_GRACE',safe_after=c['not_before'],seconds_remaining=int(left))),flush=True);last_print=time.monotonic()
        time.sleep(max(0,min(30,left)))
    check_control(c);actor(c,client)
    proof=terminal_proof(client,receipt['owner'])
    lease=Lease(backend,REPO,os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_SHA'])
    lease.recover(receipt['owner_revision'],lambda owner:proof)
    revision,state=backend.read();require(state['owner'] is None,'recovery_readback_not_idle')
    for key in ('last_auth','last_http','auth_not_before','request_scopes'):
        require(state.get(key)==receipt['safety_state'].get(key),'cooldown_changed')
    result=dict(receipt,status='RECOVERED_STOPPED_OWNER',recovered_at=now(),after_revision=revision,terminal=proof)
    Store(ROOT).write('recovery-receipt.json',result)
    old,version=client.contents(state_path(c));require(old==receipt,'intent_changed');client.write(state_path(c),result,sha=version)
    print(json.dumps(dict(status=result['status'],after_revision=revision,cumulative_http=2444,new_market_http=0)))


class ResumeReview:
    def __init__(self,receipt):
        require(receipt.get('status')=='RECOVERED_STOPPED_OWNER' and receipt['source']['artifact_sha256']==ZIP_SHA,'retry_review_unverified')
        self.allowed=set(receipt['source']['allowed_failure_hashes']);self.used=set()
    def permits(self,event):
        key=sha(encoded(event));ok=event.get('reason')=='shared_prior_HTTP_outcome_unresolved' and key in self.allowed
        if ok:self.used.add(key)
        return ok


class ResumePolicy:
    stop_on_unknown=True
    eligible=staticmethod(policy.eligible)
    def __init__(self,c,context):self.c,self.context,self.apis=c,context,[]
    def validate_control(self,value,current=None):
        check_control(self.c,current);require(value==self.context,'control_changed')
        session=globals()['current']();session.review()
        require(session.lease is not None and session.lease.held,'session_not_owned')
        if session.token_deadline is not None:
            require(session.token_deadline>stamp(self.c['expires_at'])+dt.timedelta(seconds=35),'token_cannot_cover_window')
        used=self.apis[0].budget.used if self.apis else 2440
        require(used+4<5600,'combined_physical_budget_exhausted')
    def transfer(self,api):
        api.stop_on_unknown=True
        self.apis.append(api) # One fresh OAuth, journaled in the restored physical ledger.


def collect():
    c=check_control(read_control());client=GitHub();run=actor(c,client);store=Store(ROOT);receipt=store.read('recovery-receipt.json')
    require(receipt and receipt['status']=='RECOVERED_STOPPED_OWNER' and receipt['new_run_id']==str(run['id'])
        and receipt['new_commit']==run['head_sha'] and receipt['request_id']==c['request_id'],'stopped_owner_not_recovered')
    # Immutable original window stays in source; this separately approved manual
    # continuation uses its own <=170minute window with the SAME target date.
    control=dict(schema=1,mode='full',asof=ASOF,trial_id=GENERATION,generation_id=GENERATION,request_id=c['request_id'],
        previous_run=SOURCE_RUN,max_total_http=5600,not_before=c['not_before'],expires_at=c['expires_at'],approval=c['approval'])
    guard=ResumePolicy(c,control);review=ResumeReview(receipt)
    folder=ROOT/GENERATION;universe=Store(folder/'preparation').read('universe.json')
    require(sha(encoded(universe))==POPULATION,'restored_population_changed')
    remaining=(stamp(c['expires_at'])-dt.datetime.now(UTC)).total_seconds()
    require(remaining>2262*1.05+60,'insufficient_window_before_auth')
    config=json.loads(Path('.github/config/kis-shared-account.json').read_bytes())
    os.environ['KIS_VALIDATION_REQUEST_ID']=c['request_id']
    result={};session=None
    try:
        with configured('.github/config/kis-shared-account.json') as session:
            result=kis_manual.run(folder/'collection',control,universe,str(run['id']),policy=guard,memory_session=guard,resume_review=review,
                progress=lambda p:print(json.dumps(dict(stage='RESUMED_COLLECTION',**p)),flush=True))
            if result['status']!='PASS':session.uncertain=True
            for api in guard.apis:api.token=None
        old=Store(ROOT/'source'/GENERATION/'preparation').read('master.json')
        result.update(master=old,preparation_http=4,total_physical_http=4+result['physical_http'],generation_id=GENERATION,
            collection_complete=result['status']=='PASS',internal_validation='PASS' if result['status']=='PASS' else 'FAIL',
            recovery_source_run=SOURCE_RUN,recovery_source_artifact=ARTIFACT,recovery_source_zip_sha256=ZIP_SHA,
            reused_validated_symbols=1217,reused_successful_observations=2437,recovery_request_id=c['request_id'],
            collection_time_semantics='same generation resumed; original observation times preserved; not an independent fresh observation')
        collector.verify(folder,Store(ROOT/'source/source').read('context.json'),result)
    except Exception as error:
        result.update(status='FAILED',reason=error.code if isinstance(error,Failure) else str(error) if isinstance(error,Blocked) else type(error).__name__,collection_complete=False)
        raise
    finally:
        result.update(asof=ASOF,generation_id=GENERATION,recovery_source_run=SOURCE_RUN,recovery_source_artifact=ARTIFACT,
            recovery_request_id=c['request_id'],preparation_http=4,physical_http=sum(e['event']=='started' for e in events(folder/'collection/http.jsonl')))
        result['total_physical_http']=4+result['physical_http']
        store.write('report.json',result)
        for api in guard.apis:api.token=None
    require(result['status']=='PASS','collection_incomplete')
    return result


def seal():
    if not ROOT.exists():return
    store=Store(ROOT);(ROOT/'manifest.json').unlink(missing_ok=True);(ROOT/'UPLOAD_ALLOWED').unlink(missing_ok=True)
    if not store.read('report.json'):store.write('report.json',dict(status='FAILED_OR_WAITING',market_http_unknown=False,source_run=SOURCE_RUN))
    # Original source prefix is immutable and cryptographically checked again.
    if (ROOT/'source/manifest.json').exists():
        m=Store(ROOT/'source').read('manifest.json');require(m['files']==inventory(ROOT/'source') and sha(encoded(m))==(store.read('intent.json',{}).get('source') or store.read('source-proof.json',{})).get('source_manifest_sha256'),'source_mutated')
    scan(ROOT)
    (ROOT/'UPLOAD_ALLOWED').touch()
    store.write('manifest.json',dict(schema=1,source_run=SOURCE_RUN,source_artifact=ARTIFACT,source_zip_sha256=ZIP_SHA,
        recovery_run=os.environ['GITHUB_RUN_ID'],asof=ASOF,generation_id=GENERATION,files=inventory(ROOT)))
    (ROOT/'UPLOAD_ALLOWED').touch()


def finalize():
    c=read_control();client=GitHub();report=Store(ROOT).read('report.json',{})
    aid=os.environ.get('ARTIFACT_ID','');digest=os.environ.get('ARTIFACT_DIGEST','')
    require(aid.isdigit() and re.fullmatch('[0-9a-f]{64}',digest.removeprefix('sha256:')) is not None,'artifact_not_stored')
    a=client.request(f'repos/{REPO}/actions/artifacts/{aid}')
    require(a.get('digest')==digest if digest.startswith('sha256:') else a.get('digest')=='sha256:'+digest,'artifact_digest_changed')
    require(str(a.get('workflow_run',{}).get('id'))==os.environ['GITHUB_RUN_ID'] and not a.get('expired')
        and a.get('name')=='kis-regular-recovery-followup-20261103-'+os.environ['GITHUB_RUN_ID'],'artifact_run_mismatch')
    raw_path=Path(os.environ['RUNNER_TEMP'])/'stored-recovery.zip'
    with raw_path.open('wb') as f:subprocess.run(['gh','api',f'repos/{REPO}/actions/artifacts/{aid}/zip'],stdout=f,check=True,timeout=180)
    require('sha256:'+sha(raw_path.read_bytes())==a['digest'],'stored_ZIP_digest_failed')
    with zipfile.ZipFile(raw_path) as z:
        require(z.testzip() is None and 'manifest.json' in z.namelist(),'stored_ZIP_invalid')
        m=json.loads(z.read('manifest.json'))
        for name,expected in m['files'].items():require(name in z.namelist() and sha(z.read(name))==expected,'stored_file_hash_failed')
    record,version=client.contents(state_path(c))
    complete=(report.get('status')=='PASS' and report.get('collection_complete') is True and report.get('db_json_match') is True
        and report.get('target')==report.get('kis_validated')==2348 and report.get('internal_validation')=='PASS'
        and report.get('total_physical_http',5601)<=5600)
    record.update(cumulative_http=report.get('total_physical_http',2444),market_http_this_run=report.get('physical_http_this_run',0),status='COMPLETE_PROVISIONAL' if complete else 'FAILED_PRESERVED',completed_at=now(),artifact={k:a.get(k) for k in ('id','name','digest','expires_at')},
        summary={k:report.get(k) for k in ['target','received_symbols','kis_validated','total_physical_http','physical_retries','reason','internal_validation','db_json_match']},official_price_status='PENDING_KRX',provider_finality='UNVERIFIED')
    client.write(state_path(c),record,sha=version)
    print(json.dumps({k:v for k,v in record.items() if k not in ('source','terminal','owner')}))
    require(complete,'not_complete')


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','wait','collect','seal','finalize']);a=p.parse_args()
    try:
        {'prepare':prepare,'wait':wait_recover,'collect':collect,'seal':seal,'finalize':finalize}[a.command]()
    except Exception as e:
        result=dict(status='BLOCKED_OR_FAILED',phase=a.command,reason=e.code if isinstance(e,Failure) else str(e) if isinstance(e,Blocked) else type(e).__name__,observed_at=now())
        print(json.dumps(result),flush=True)
        if ROOT.exists():Store(ROOT).write('failure-'+a.command+'.json',result)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
