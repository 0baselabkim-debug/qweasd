"""Approved credential capability probe, with no KIS endpoint or credentials.

Only a fresh isolated central probe branch is written. Production lease and both
main branches are never changed. Missing/insufficient credentials are evidence,
not a reason to infer that the shared-account rollout is verified.
"""
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
from kis_shared.git_backend import GitBackend
from kis_shared.lease import Blocked, Lease, initial, stamp, utcnow

REPO = '0baselabkim-debug/qweasd'


@contextmanager
def authority(value):
    previous=os.environ.get('GH_TOKEN')
    os.environ['GH_TOKEN']=value
    try:yield
    finally:
        if previous is None:os.environ.pop('GH_TOKEN',None)
        else:os.environ['GH_TOKEN']=previous


def initialize(backend, payload, message, member='lease.json'):
    raw=json.dumps(payload,sort_keys=True).encode()+b'\n'
    blob=backend._git('hash-object','-w','--stdin',data=raw).stdout.strip()
    tree=backend._git('mktree',data=b'100644 blob '+blob+b'\t'+member.encode()+b'\n').stdout.strip().decode()
    parent=backend._git('ls-remote',backend.remote,'refs/heads/'+backend.branch).stdout.decode().split()
    args=['commit-tree',tree]
    if parent:args+=['-p',parent[0]]
    commit=backend._git(*args,data=message.encode()+b'\n').stdout.strip().decode()
    pushed=backend._git('push','--porcelain',backend.remote,commit+':refs/heads/'+backend.branch,reject=True)
    if pushed.returncode:raise Blocked('central_or_report_write_rejected_or_unconfirmed')
    return commit


def main():
    control=json.loads(Path('.github/trials/shared-readiness-20261029.json').read_bytes())
    if (os.getenv('GITHUB_REPOSITORY')!=REPO or os.getenv('GITHUB_REF')!='refs/heads/fix/shared-readiness-20261029'
            or os.getenv('GITHUB_EVENT_NAME')!='push' or os.getenv('GITHUB_RUN_ATTEMPT')!='1'
            or control.get('approval')!='USER_APPROVED_CENTRAL_LEDGER_ACCESS_AND_COLLECTION'
            or control.get('market_http')!=0 or utcnow()>=stamp(control['expires_at'])):
        raise Blocked('readiness_scope_or_window_invalid')
    run=os.environ['GITHUB_RUN_ID'];commit=os.environ['GITHUB_SHA']
    report=dict(schema=1,status='BLOCKED_NO_CENTRAL_WRITE_PROOF',run_id=run,code_commit=commit,
        repository=REPO,checked_at=utcnow().isoformat(),request_id=control['request_id'],
        market_http=0,oauth_issuance=0,candidates=[],existing_secrets_changed=False,permissions_expanded=False)
    root=Path(tempfile.mkdtemp(prefix='kis-readiness-'))
    seen=set()
    for name in ('KIS_COORDINATOR_TOKEN','OPERATIONS_READ_TOKEN','GITHUB_TOKEN'):
        value=os.getenv(name)
        if not value:
            report['candidates'].append(dict(name=name,present=False));continue
        binding=hashlib.sha256(value.encode()).digest()
        if binding in seen:
            report['candidates'].append(dict(name=name,present=True,status='SAME_AUTHORITY_ALREADY_TESTED'));continue
        seen.add(binding)
        branch='kis-shared-probe-actions-'+run+'-'+str(len(seen))
        result=dict(name=name,present=True,status='NOT_VERIFIED',central_branch=branch)
        try:
            with authority(value):
                backend=GitBackend(root/name,branch=branch)
                if backend._git('ls-remote',backend.remote,'refs/heads/'+branch).stdout.strip():
                    raise Blocked('probe_branch_already_exists_no_replay')
                initialize(backend,initial(),'Approved Actions-only credential capability probe; zero KIS HTTP')
                lease=Lease(backend,REPO,run,commit)
                lease.acquire(seconds=180);lease.check();lease.release()
                final,state=backend.read()
                if state['owner'] is not None or state['sequence']!=2:raise Blocked('probe_handover_not_verified')
                result.update(status='CENTRAL_GIT_WRITE_ACQUIRE_RELEASE_PASS',central_commit=final)
                report.update(status='CENTRAL_ACCESS_READY_KIS_NOT_TESTED',selected_credential_name=name)
        except Exception as error:
            result.update(status='BLOCKED',reason=str(error) if isinstance(error,Blocked) else 'probe_'+type(error).__name__)
        report['candidates'].append(result)
        if report['status']=='CENTRAL_ACCESS_READY_KIS_NOT_TESTED':break
    # Publish with the existing own-repository GITHUB_TOKEN, including failures.
    with authority(os.environ['GITHUB_TOKEN']):
        publisher=GitBackend(root/'publisher',branch='kis-shared-probe-publisher')
        publisher.remote='https://github.com/'+REPO+'.git'
        publisher.branch='kis-shared-readiness-results'
        report['publication_commit']=initialize(publisher,report,'Preserve approved shared-access result '+run, member='readiness.json')
    Path('readiness-result.json').write_text(json.dumps(report,indent=2)+'\n')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('```json\n'+json.dumps(report,indent=2)+'\n```\n')
    print(json.dumps(report))
    return 0 if report['status']=='CENTRAL_ACCESS_READY_KIS_NOT_TESTED' else 2


if __name__=='__main__':raise SystemExit(main())
