"""Explicit main-only manual resume. No scheduler, implicit takeover or budget reset."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import zipfile
from ..core import Failure,Store,encoded,sha,now
from . import policy
from .github import GitHub,APIError
from .session_cleanup import controlled_session,discard_for_normal_exit
from kis_shared.runtime import current,review_local_evidence
from kis_shared.git_backend import GitBackend
from kis_shared.lease import Lease,stamp

PROTOCOL='REGULAR_OPERATIONS_V1'
CONTRACT_FILES=('collector_v1/regular/runner.py','collector_v1/regular/operator.py',
 'collector_v1/regular/session_cleanup.py','collector_v1/regular/collector.py','collector_v1/regular/policy.py',
 'collector_v1/regular/github.py','collector_v1/regular/ledger.py','collector_v1/kis_manual.py',
 'collector_v1/providers.py','collector_v1/experiment/engine.py','collector_v1/core.py','collector_v1/validation.py','collector_v1/experiment/storage.py',
 'kis_shared/boundary.py','kis_shared/lease.py','kis_shared/git_backend.py','kis_shared/runtime.py',
 'kis_shared/worker.py','.github/workflows/kis-market-2030.yml')


def require(ok,reason):
    if not ok:raise Failure('regular_resume_'+reason)


def root():
    from .runner import ROOT
    return ROOT


def contract_hashes():
    base=Path(__file__).resolve().parents[2]
    return {p:sha((base/p).read_bytes()) for p in CONTRACT_FILES}


def attest(c,session):
    """Receipt is written BEFORE authentication; contains no credential values."""
    s=Store(root());s.write('source-code.json',dict(protocol=PROTOCOL,run_id=c['run_id'],code_commit=c['code_commit'],
        synchronous_hosted_worker=True,contract_hashes=contract_hashes()))
    if session is not None:
        revision,state=session.lease.backend.read();owner=state['owner']
        require(owner and owner['run_id']==c['run_id'] and owner['commit']==c['code_commit'],'acquired_owner_mismatch')
        s.write('lease-acquisition.json',dict(revision=revision,owner=owner))


def check_window(c,at=None,*,early=False):
    at=at or policy.utcnow();start=stamp(c['not_before']);end=stamp(c['expires_at'])
    day=dt.date.fromisoformat(c['asof']);scheduled=dt.datetime.combine(day,dt.time(20,30),policy.KST)
    cutoff=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(9),policy.KST)
    require(c['generation_id']=='kis-regular-'+day.strftime('%Y%m%d')+'-2030','generation_changed')
    require(scheduled<=start<end<=cutoff and (end-start).total_seconds()<=10200,'invalid_manual_window')
    require(at<end and (early or start<=at),'outside_manual_window')
    require(at<cutoff,'prior_day_cutoff')


def freeze_resume(run,inputs):
    from .runner import output
    require(inputs.get('approval')=='USER_APPROVED_REGULAR_RESUME','explicit_approval_required')
    require(re.fullmatch('[A-Za-z0-9_-]{8,80}',inputs.get('request_id','')) is not None,'request_id_invalid')
    require(str(inputs.get('source_run','')).isdigit() and str(inputs['source_run'])!=str(run['id']),'source_run_invalid')
    day=dt.date.fromisoformat(inputs.get('asof',''));scheduled=dt.datetime.combine(day,dt.time(20,30),policy.KST)
    start=inputs.get('not_before') or scheduled.isoformat();end=inputs.get('expires_at') or (scheduled+dt.timedelta(minutes=170)).isoformat()
    c=dict(schema=1,mode='MANUAL_RESUME',trigger='workflow_dispatch',execution_operation='resume',asof=day.isoformat(),slot='2030',
        generation_id='kis-regular-'+day.strftime('%Y%m%d')+'-2030',scheduled_at=scheduled.isoformat(),
        not_before=start,expires_at=end,source_run=str(inputs['source_run']),request_id=inputs['request_id'],
        run_id=str(run['id']),code_commit=run['head_sha'],run_created_at=run['created_at'],
        artifact_name='kis-regular-'+day.strftime('%Y%m%d')+'-2030-recovery-'+str(run['id']),
        max_total_http=5600,approval=inputs['approval'])
    check_window(c,early=True);Store(root()).write('context.json',c)
    output(collect=False,operation='resume',artifact_name=c['artifact_name']);return 0


def actor(c,client):
    from .runner import assert_runtime_actor
    assert_runtime_actor(c)
    require(c['mode']=='MANUAL_RESUME' and c.get('approval')=='USER_APPROVED_REGULAR_RESUME','wrong_operation')
    run=client.run(c['run_id'])
    require(run.get('path')==policy.WORKFLOW and run.get('head_sha')==c['code_commit'] and run.get('status')=='in_progress'
        and run.get('event')=='workflow_dispatch' and run.get('run_attempt')==1,'actual_actor_mismatch')
    review_local_evidence(json.loads(Path('.github/config/kis-shared-account.json').read_bytes()),policy.REPO)


def record_path(c):return 'resumptions/'+c['source_run']+'.json'


def terminal_proof(client,c,source_owner):
    run=client.run(c['source_run']);source_sha=source_owner['commit']
    require(source_owner['repository']==policy.REPO and source_owner['run_id']==c['source_run'],'source_owner_not_target')
    require(stamp(source_owner['expires_at'])-stamp(source_owner['acquired_at'])==dt.timedelta(seconds=11400),'source_TTL_changed')
    require(run.get('status')=='completed' and run.get('conclusion') in ('failure','cancelled','timed_out')
        and run.get('run_attempt')==1 and run.get('head_sha')==source_sha and run.get('path')==policy.WORKFLOW
        and run.get('head_branch')=='main','source_not_stopped_main_attempt1')
    attempt=client.request(f"repos/{policy.REPO}/actions/runs/{c['source_run']}/attempts/1")
    fields=('id','status','conclusion','head_sha','run_attempt','path','workflow_id','check_suite_id','created_at','run_started_at')
    require(all(run.get(k) is not None and run[k]==attempt.get(k) for k in fields),'attempt_conflict')
    jobs=client.request(f"repos/{policy.REPO}/actions/runs/{c['source_run']}/attempts/1/jobs?per_page=100")
    require(jobs.get('total_count')==len(jobs.get('jobs',[]))==1,'source_jobs_unverified')
    j=jobs['jobs'][0]
    require(str(j.get('run_id'))==c['source_run'] and j.get('head_sha')==source_sha and j.get('run_attempt')==1
        and j.get('status')=='completed' and j.get('runner_group_name')=='GitHub Actions' and 'ubuntu-latest' in j.get('labels',[])
        and j.get('started_at') and j.get('completed_at') and j.get('steps') and all(s.get('status')=='completed' for s in j['steps']),'source_worker_unverified')
    require(stamp(j['started_at'])<=stamp(source_owner['acquired_at'])<=stamp(j['completed_at']),'lease_job_time_conflict')
    require(policy.utcnow()>=stamp(j['completed_at'])+dt.timedelta(seconds=120),'worker_quiet_period')
    require(run.get('updated_at') and attempt.get('updated_at') and stamp(j['completed_at'])<=stamp(run['updated_at'])<=policy.utcnow() and stamp(j['completed_at'])<=stamp(attempt['updated_at'])<=policy.utcnow(),'metadata_times_unverified')
    again=client.run(c['source_run']);a2=client.request(f"repos/{policy.REPO}/actions/runs/{c['source_run']}/attempts/1")
    require(all(again.get(k)==run.get(k) and a2.get(k)==attempt.get(k) for k in fields+('updated_at',)),'source_metadata_changed')
    return dict(repository=policy.REPO,run_id=c['source_run'],commit=source_sha,status='completed',all_workers_stopped=True,
        external_issuers_stopped=True,job_id=str(j['id']),run_attempt=1,checked_at=now())


def restore_archive(path,out,c,source_sha):
    from .. import kis_manual
    from ..experiment import engine
    from ..experiment.storage import Evidence,Metrics,events
    from . import collector
    from .runner import inventory
    require(not out.exists(),'source_root_exists')
    with zipfile.ZipFile(path) as z:
        names=z.namelist();require(len(names)==len(set(names)) and len(names)<=50000,'archive_population')
        require(sum(i.file_size for i in z.infolist())<=2*1024**3 and z.testzip() is None,'archive_size_or_CRC')
        for n in names:
            require((out/n).resolve().is_relative_to(out.resolve()) and not n.startswith('/')
                and ((z.getinfo(n).external_attr>>16)&0o170000)!=0o120000,'archive_path')
        m=json.loads(z.read('manifest.json'))
        require((m.get('asof'),str(m.get('run_id')),m.get('code_commit'),m.get('generation_id'))==
            (c['asof'],c['source_run'],source_sha,c['generation_id']),'source_manifest_identity')
        require(set(names)==set(m['files'])|{'manifest.json'},'archive_extra_or_missing_files')
        require(all(sha(z.read(n))==h for n,h in m['files'].items()),'source_hash_mismatch')
        z.extractall(out)
    require(inventory(out)==m['files'],'source_tree_mismatch')
    s=Store(out);att=s.read('source-code.json');owner=s.read('lease-acquisition.json',{}).get('owner')
    require(att and att.get('protocol')==PROTOCOL and att.get('run_id')==c['source_run'] and att.get('code_commit')==source_sha
        and att.get('synchronous_hosted_worker') is True and att.get('contract_hashes')==contract_hashes(),'source_contract_not_reviewed')
    require(owner and owner['run_id']==c['source_run'] and owner['commit']==source_sha,'source_lease_receipt_missing')
    context=s.read('origin-context.json') or s.read('context.json');report=s.read('report.json')
    require(context and report and context['asof']==c['asof'] and context['generation_id']==c['generation_id'],'source_context_missing')
    require(report.get('collection_complete') is not True and report.get('status') not in ('PASS','INTERNAL_PASS'),'already_complete_no_recollection')
    collector.verify(out/c['generation_id'],context,report)
    folder=out/c['generation_id']/'collection';http=events(folder/'http.jsonl')
    starts=[e for e in http if e['event']=='started'];ends=[e for e in http if e['event']=='finished']
    require(len(starts)==len(ends) and {e['sequence'] for e in starts}=={e['sequence'] for e in ends},'dangling_HTTP_requires_independent_incident_review')
    finish={e['sequence']:e for e in ends}
    for e in starts:
        end=finish[e['sequence']]
        if e['endpoint']=='/oauth2/tokenP':
            require(end.get('status')=='received' and end.get('http')==200 and any(
                later['sequence']>e['sequence'] and later['endpoint']!='/oauth2/tokenP' for later in starts),
                'unknown_or_unvalidated_OAuth')
        elif end.get('status')!='received':require(e['endpoint'] in kis_manual.ENDPOINTS-{'/oauth2/tokenP'},'unknown_non_readonly_request')
    failures=events(folder/'failures.jsonl')
    require(all(e.get('retryable',True) or e['reason']=='shared_prior_HTTP_outcome_unresolved' for e in failures),'permanent_failure_needs_program_review')
    prep=s.read(c['generation_id']+'/preparation/requests.json',[])
    require(len(prep)==4 and all(e.get('status')=='received' and e.get('http')==200 for e in prep),'incomplete_preparation_or_auth')
    total=len(starts)+len(prep);require(total<5600,'budget_exhausted')
    cache=engine.replay(Evidence(folder,Metrics()),1,asof=c['asof'],generation_id=c['generation_id'])
    missing=2*report['target']+2-len(cache);require(missing>0 and total+missing+1<=5600,'missing_requests_exceed_remaining_budget')
    return dict(source_sha=source_sha,source_owner=owner,origin_context=context,source_manifest_sha256=sha(encoded(m)),
        initial_collection_http=len(starts),preparation_http=4,total_http=total,target=report['target'],missing_requests=missing,
        population_sha256=report['population_sha256'],validated=report['kis_validated'],
        unknown_transport=any(e.get('status')=='network_failure' for e in ends),
        allowed_failure_hashes=[sha(encoded(e)) for e in failures if e['reason']=='shared_prior_HTTP_outcome_unresolved'])


def prepare():
    from .runner import context,scan
    c=context();client=GitHub();check_window(c,early=True);actor(c,client)
    try:client.contents(record_path(c))
    except APIError as e:
        if e.status!=404:raise
    else:raise Failure('regular_resume_source_already_consumed_use_latest_attempt')
    source=client.run(c['source_run']);require(source.get('status')=='completed' and source.get('head_branch')=='main','source_not_terminal_main')
    items=client.request(f"repos/{policy.REPO}/actions/runs/{c['source_run']}/artifacts?per_page=100")
    candidates=[a for a in items.get('artifacts',[]) if a.get('name','').startswith('kis-regular-'+c['asof'].replace('-','')+'-2030')]
    require(items.get('total_count')==len(items.get('artifacts',[])) and len(candidates)==1,'source_artifact_ambiguous')
    a=candidates[0];digest=a.get('digest','');require(not a.get('expired') and re.fullmatch('sha256:[0-9a-f]{64}',digest) and str(a.get('workflow_run',{}).get('id'))==c['source_run'],'source_artifact_unverified')
    archive=Path(os.environ['RUNNER_TEMP'])/'regular-resume-source.zip'
    with archive.open('xb') as f:subprocess.run(['gh','api',f"repos/{policy.REPO}/actions/artifacts/{a['id']}/zip"],stdout=f,check=True,timeout=180)
    require('sha256:'+sha(archive.read_bytes())==digest,'download_digest_mismatch')
    proof=restore_archive(archive,root()/'source',c,source['head_sha']);proof['artifact']={k:a[k] for k in ('id','name','digest','expires_at')}
    terminal=terminal_proof(client,c,proof['source_owner'])
    shutil.copytree(root()/'source'/c['generation_id'],root()/c['generation_id'])
    Store(root()).write('origin-context.json',proof['origin_context'])
    record=dict(status='PREPARED',run_id=c['run_id'],code_commit=c['code_commit'],context=c,proof=proof,terminal=terminal,
        total_physical_http=proof['total_http'],new_physical_http=0)
    client.write(record_path(c),record);Store(root()).write('resume-receipt.json',record)
    for p in (root()/'source').rglob('*'):
        if p.is_file():p.chmod(0o444)
    scan(root());print(json.dumps(dict(status='PREPARED',source_run=c['source_run'],total_http=proof['total_http'],missing=proof['missing_requests'])))


def wait():
    from .runner import context
    c=context();client=GitHub();receipt=Store(root()).read('resume-receipt.json');actor(c,client)
    require(receipt and receipt['context']==c,'receipt_missing')
    backend=GitBackend(Path(os.environ['RUNNER_TEMP'])/'regular-recovery.git')
    while True:
        check_window(c,early=True);revision,state=backend.read();owner=state['owner']
        if owner is not None:require(owner==receipt['proof']['source_owner'],'another_actor_owns_account')
        safe=stamp(c['not_before'])
        if owner is not None or receipt['proof'].get('unknown_transport'):
            safe=max(safe,stamp(receipt['proof']['source_owner']['expires_at'])+dt.timedelta(seconds=60))
        require(safe<stamp(c['expires_at']),'lease_cannot_fit_manual_window')
        if policy.utcnow()>=safe:break
        print(json.dumps(dict(status='WAITING_FOR_APPROVED_WINDOW_AND_SOURCE_LEASE',safe_after=safe.isoformat())),flush=True)
        time.sleep(min(30,max(0,(safe-policy.utcnow()).total_seconds())))
    terminal=terminal_proof(client,c,receipt['proof']['source_owner'])
    if owner is not None:
        Lease(backend,policy.REPO,c['run_id'],c['code_commit']).recover(revision,lambda o:terminal)
    _,state=backend.read();require(state['owner'] is None,'account_not_idle_after_recovery')
    receipt.update(status='RECOVERED_READY',terminal=terminal);Store(root()).write('resume-receipt.json',receipt)
    old,version=client.contents(record_path(c));require(old['context']==c,'resume_owner_changed');client.write(record_path(c),receipt,version)


class Review:
    def __init__(self,receipt):self.allowed=set(receipt['proof']['allowed_failure_hashes'])
    def permits(self,event):return event.get('reason')=='shared_prior_HTTP_outcome_unresolved' and sha(encoded(event)) in self.allowed


class ResumePolicy:
    stop_on_unknown=True
    bounded_resume=True
    initial_progress_http_interval=2
    eligible=staticmethod(policy.eligible)
    def __init__(self,c,control,proof):self.c,self.control,self.proof,self.apis=c,control,proof,[]
    def transfer(self,api):api.stop_on_unknown=True;self.apis.append(api)
    def validate_control(self,value):
        check_window(self.c);require(value==self.control,'control_changed');session=current();session.review()
        require(session.lease and session.lease.held,'session_missing')
        if session.token_deadline is not None:require(session.token_deadline>stamp(self.c['expires_at'])+dt.timedelta(seconds=35),'token_cannot_cover_window')
        used=self.apis[0].budget.used if self.apis else self.proof['initial_collection_http']
        require(used+self.proof['preparation_http']<5600,'cumulative_budget_exhausted')


def collect():
    from .. import kis_manual
    from ..experiment.storage import events
    from . import collector
    from .runner import context,scan
    c=context();client=GitHub();actor(c,client);check_window(c);s=Store(root());receipt=s.read('resume-receipt.json')
    require(receipt and receipt['status']=='RECOVERED_READY' and receipt['context']==c,'resume_not_prepared')
    proof=receipt['proof'];folder=root()/c['generation_id'];universe=Store(folder/'preparation').read('universe.json')
    require(sha(encoded(universe))==proof['population_sha256'],'population_changed')
    control=dict(schema=1,mode='full',trial_id=c['generation_id'],generation_id=c['generation_id'],asof=c['asof'],request_id=c['request_id'],
        previous_run=c['source_run'],max_total_http=5600,not_before=c['not_before'],expires_at=c['expires_at'],approval=c['approval'])
    guard=ResumePolicy(c,control,proof);report={};os.environ['KIS_VALIDATION_REQUEST_ID']=c['request_id']
    def progress(payload):
        print(json.dumps(payload),flush=True)
        r,version=client.contents(record_path(c));require(r['context']==c,'progress_owner_changed')
        r.update(status='COLLECTING',live_progress=payload,total_physical_http=payload['physical_http']+4,
            new_physical_http=payload['physical_http']-proof['initial_collection_http']);client.write(record_path(c),r,version)
    try:
        with controlled_session('.github/config/kis-shared-account.json',guard.apis) as session:
            attest(c,session)
            report=kis_manual.run(folder/'collection',control,universe,c['run_id'],policy=guard,memory_session=guard,resume_review=Review(receipt),progress=progress)
            report['lock_disposition']=discard_for_normal_exit(session,guard.apis)
        report.update(master=Store(folder/'preparation').read('master.json'),preparation_http=4,
            collection_complete=report['status']=='PASS',internal_validation='PASS' if report['status']=='PASS' else 'FAIL',
            source_run=c['source_run'],source_artifact=proof['artifact'],recovery_request_id=c['request_id'],
            collection_time_semantics='CONTINUATION; original per-request observation times preserved; not a fresh independent sample')
        collector.verify(folder,proof['origin_context'],report)
    except Exception as error:
        report.update(status='FAILED',collection_complete=False,reason=error.code if isinstance(error,Failure) else type(error).__name__)
        raise
    finally:
        report.update(asof=c['asof'],generation_id=c['generation_id'],context=c,preparation_http=4,
            physical_http=sum(e['event']=='started' for e in events(folder/'collection/http.jsonl')))
        report['total_physical_http']=4+report['physical_http'];s.write('report.json',report);scan(root(),guard.apis)
    require(report['status']=='PASS','collection_incomplete')


def finalize():
    from . import collector
    from .runner import context,verify_manifest,summarize
    c=context();client=GitHub();r=Store(root()).read('report.json',{});record,version=client.contents(record_path(c));require(record['context']==c,'finalize_owner_changed')
    aid=os.environ.get('ARTIFACT_ID','');digest=os.environ.get('ARTIFACT_DIGEST','').removeprefix('sha256:')
    require(aid.isdigit() and re.fullmatch('[0-9a-f]{64}',digest),'artifact_not_uploaded')
    meta=client.request(f'repos/{policy.REPO}/actions/artifacts/{aid}')
    require(meta.get('digest')=='sha256:'+digest and str(meta.get('workflow_run',{}).get('id'))==c['run_id'] and meta.get('expired') is False and meta.get('name')==c['artifact_name'],'stored_artifact_mismatch')
    archive=Path(os.environ['RUNNER_TEMP'])/'regular-resume-stored.zip'
    with archive.open('wb') as f:subprocess.run(['gh','api',f'repos/{policy.REPO}/actions/artifacts/{aid}/zip'],stdout=f,check=True,timeout=180)
    require(sha(archive.read_bytes())==digest,'stored_bytes_mismatch')
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None,'stored_CRC')
        manifest=json.loads(z.read('manifest.json'));require(all(sha(z.read(n))==h for n,h in manifest['files'].items()),'stored_manifest_hash')
    good=r.get('status')=='PASS' and r.get('collection_complete') is True and r.get('internal_validation')=='PASS' and r.get('db_json_match') is True and r.get('target')==r.get('kis_validated') and r.get('total_physical_http',5601)<=5600
    if good:
        verify_manifest(root());collector.verify(root()/c['generation_id'],Store(root()).read('origin-context.json'),r)
    artifact={k:meta[k] for k in ('id','name','digest','expires_at')};artifact.update(retention_days=90,run_url='https://github.com/'+policy.REPO+'/actions/runs/'+c['run_id'])
    summary=summarize(r,c);summary['artifact_storage']='VERIFIED'
    record.update(status='COMPLETE_PROVISIONAL' if good else 'FAILED_PRESERVED',summary=summary,artifact=artifact,
        total_physical_http=r.get('total_physical_http'),completed_at=now(),provider_finality='UNVERIFIED',official_price_status='PENDING_KRX')
    client.write(record_path(c),record,version)
    if good:
        # New recovery pointer; the failed original slot and its history are immutable.
        path='recovered/'+c['asof']+'.json'
        try:_,old=client.contents(path)
        except APIError as e:
            if e.status!=404:raise
            old=None
        client.write(path,record,old)
    print(json.dumps(dict(status=record['status'],run_id=c['run_id'],summary=summary)))
    require(good,'not_complete')


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','wait','collect','finalize']);a=p.parse_args()
    try:globals()[a.command]();return 0
    except Exception as e:
        reason=e.code if isinstance(e,Failure) else str(e) if isinstance(e, __import__('kis_shared.lease',fromlist=['Blocked']).Blocked) else type(e).__name__
        print(json.dumps(dict(status='BLOCKED_OR_FAILED',phase=a.command,reason=reason)))
        Store(root()).write('operator-failure.json',dict(status='FAILED',phase=a.command,reason=reason));return 1

if __name__=='__main__':raise SystemExit(main())
