"""GitHub-only operational admission evidence. Never loads KIS credentials.

KIS presence is passed as booleans by Actions expressions. GitHub tokens are used
by gh's normal credential path, never read, printed, copied or persisted here.
Only --mode probe may write, and only to an isolated kis-shared-probe-v3-* ref.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from kis_shared.git_backend import GitBackend,BRANCH
from kis_shared.lease import Lease,Blocked,REPOSITORIES,validate
from kis_shared.runtime import review_local_evidence
CENTRAL='0baselabkim-debug/qweasd'
CONTROL='.github/lock-v3/readiness.json'

def deny_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.sendto','http.client.connect'):
        raise Blocked('operational_readiness_forbids_all_Python_network')

def gh(path,method='GET',body=None):
    if not path.startswith('repos/'+CENTRAL+'/') and path!='repos/'+CENTRAL:raise Blocked('readiness_GitHub_route_rejected')
    if method not in ('GET','POST'):raise Blocked('readiness_GitHub_method_rejected')
    if method=='POST' and (path!='repos/'+CENTRAL+'/git/refs' or not re.fullmatch(r'refs/heads/kis-shared-probe-v3-[a-z0-9-]+',str((body or {}).get('ref')))):
        raise Blocked('readiness_only_isolated_probe_ref_creation_allowed')
    args=['gh','api',path,'--method',method]
    if body is not None:args+=['--input','-']
    p=subprocess.run(args,input=json.dumps(body).encode() if body is not None else None,capture_output=True,timeout=45)
    if p.returncode:raise Blocked('readiness_GitHub_access_failed')
    return json.loads(p.stdout) if p.stdout.strip() else {}

def presence(env):
    return {name:env.get(name+'_PRESENT')=='true' for name in ('KIS_APP_KEY','KIS_APP_SECRET')} | {'central_GitHub_token':bool(env.get('GH_TOKEN'))}

def inspect(root,env,api=gh,backend_type=GitBackend):
    report=dict(status='BLOCKED',actual_KIS_HTTP=0,actual_OAuth_HTTP=0,production_lease_mutations=0,
        repository=env.get('GITHUB_REPOSITORY'),run_id=env.get('GITHUB_RUN_ID'),code_commit=env.get('GITHUB_SHA'),central_repository=CENTRAL,state_branch=BRANCH,credential_presence=presence(env),blockers=[])
    if report['repository'] not in REPOSITORIES:raise Blocked('readiness_actor_rejected')
    for name,available in report['credential_presence'].items():
        if not available:report['blockers'].append('MISSING_'+name)
    if not report['credential_presence']['central_GitHub_token']:return report
    info=api('repos/'+CENTRAL)
    report['central_repo_read']=info.get('full_name')==CENTRAL
    if not report['central_repo_read']:raise Blocked('readiness_central_identity_rejected')
    api('repos/'+CENTRAL+'/actions/runs?per_page=1')
    report['central_actions_read']=True
    backend=backend_type(Path(env['RUNNER_TEMP'])/'v3-production-read.git')
    revision,state=backend.read();validate(state)
    report.update(production_lease_revision=revision,production_lease_sha256=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest(),production_lease_schema=state['schema'],production_owner=state['owner'])
    if state['owner'] is not None:report['blockers'].append('PRODUCTION_ACCOUNT_BUSY_NO_TAKEOVER')
    cfg=json.loads((root/'.github/config/kis-shared-account-v2.proposed.json').read_bytes())
    review_local_evidence(cfg,report['repository']);report['candidate_source_hashes_valid']=True
    report['schema2_candidate_participants']=cfg['participants'];report['live_validation_enabled']=cfg['ENABLE_LIVE_KIS_VALIDATION']
    report['candidate_activated']=cfg['enabled']
    report['status']='PASS_READONLY' if not report['blockers'] else 'BLOCKED'
    return report

def probe(root,env,control,report,api=gh,backend_type=GitBackend,lease_type=Lease):
    if report['status']!='PASS_READONLY':raise Blocked('readiness_blockers_prevent_probe_write')
    branch=control['probe_branch']
    if not re.fullmatch(r'kis-shared-probe-v3-[a-z0-9-]+',branch):raise Blocked('readiness_probe_destination_rejected')
    if control.get('allow_isolated_GitHub_probe') is not True:raise Blocked('readiness_probe_not_approved')
    # Initial ref creation is performed once by the deployment controller, from
    # the recorded idle production revision. No production ref is ever updated.
    backend=backend_type(Path(env['RUNNER_TEMP'])/'v3-probe.git',branch=branch)
    _,before=backend.read()
    if before['owner'] is not None:
        # An occupied isolated test is a valid collision; do not steal or reset.
        report.update(status='BLOCKED_PROBE_BUSY',probe_branch=branch,contender_HTTP=0)
        return report
    lease=lease_type(backend,env['GITHUB_REPOSITORY'],env['GITHUB_RUN_ID'],env['GITHUB_SHA'])
    lease.acquire(seconds=120)
    report.update(probe_branch=branch,probe_owner_confirmed=lease.check()['owner']['repository']==env['GITHUB_REPOSITORY'])
    time.sleep(2)
    lease.release();_,after=backend.read()
    if after['owner'] is not None:raise Blocked('readiness_probe_release_not_verified')
    report.update(status='PASS_PROBE',probe_final_owner=None,confirmed_transitions=lease.confirmed_transitions,probe_sequence_before=before['sequence'],probe_sequence_after=after['sequence'],oauth_reservations=0,market_reservations=0)
    return report

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['inspect','probe'],required=True);p.add_argument('--output',required=True);a=p.parse_args(argv)
    sys.addaudithook(deny_network)
    result=dict(status='BLOCKED',actual_KIS_HTTP=0,actual_OAuth_HTTP=0,production_lease_mutations=0)
    target=Path(a.output)
    try:
        if target.exists():raise Blocked('readiness_output_cannot_overwrite')
        root=Path(__file__).resolve().parents[2];control=json.loads((root/CONTROL).read_bytes())
        result=inspect(root,os.environ)
        if a.mode=='probe':result=probe(root,os.environ,control,result)
    except Exception as error:
        result.update(status='BLOCKED',reason=str(error) if isinstance(error,Blocked) else type(error).__name__)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x') as file:json.dump(result,file,indent=2);file.write('\n')
    print(json.dumps(result));return 0 if result['status'].startswith('PASS') else 2
if __name__=='__main__':raise SystemExit(main())
