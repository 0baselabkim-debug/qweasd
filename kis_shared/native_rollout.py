"""Two native repositories exercise one isolated account lease. Zero KIS HTTP."""
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .git_backend import GitBackend
from .lease import Blocked, Lease, initial, utcnow, stamp

Q='0baselabkim-debug/qweasd'
M='0baselabkim-debug/kis-intraday-monitor'
REQUEST='shared-rollout-20261030-v2'
BRANCH='kis-shared-probe-rollout-20261030-v2'
STATE='kis-regular-state'
ROOT=Path(__file__).resolve().parents[1]
COMMON=('kis_shared/runtime.py','kis_shared/lease.py','kis_shared/git_backend.py')
SOURCES={Q:COMMON+('collector_v1/providers.py','collector_v1/regular/runner.py',
    'collector_v1/regular/policy.py','collector_v1/regular/collector.py','collector_v1/kis_manual.py',
    'collector_v1/experiment/engine.py','collector_v1/validation.py','collector_v1/core.py',
    '.github/workflows/kis-market-2030.yml'),
 M:COMMON+('kis_shared/admission.py','kis_shared/bootstrap.py','kis_shared/flow.py',
    'kis_shared/remaining.py','kis_shared/remaining_four.py','kis_shared/run.py',
    'scripts/actions_auth.py','stage3/live_probe.py','kis_probe.py','scripts/bootstrap_trial.py',
    'scripts/intraday_flow_trial.py','scripts/intraday_remaining_trial.py','scripts/intraday_remaining_four.py',
    'config/bootstrap-initial-one.json',
    'config/intraday-flow-six.json','config/intraday-remaining-five.json','config/intraday-remaining-four.json',
    '.github/workflows/kis-browser-validation.yml','.github/workflows/kis-validation.yml')}


def api(repo,path,method='GET',body=None):
    if repo not in (Q,M):raise Blocked('native_metadata_destination_invalid')
    actor=os.environ['GITHUB_REPOSITORY']
    token=os.getenv('KIS_COORDINATOR_TOKEN') if repo==Q and actor==M else os.getenv('GITHUB_TOKEN')
    if not token:raise Blocked('native_metadata_credential_missing')
    args=['gh','api','repos/'+repo+'/'+path,'--method',method]
    if body is not None:args+=['--input','-']
    p=subprocess.run(args,input=json.dumps(body).encode() if body is not None else None,
        capture_output=True,timeout=35,env={**os.environ,'GH_TOKEN':token})
    if p.returncode:
        if b'HTTP 404' in p.stderr:raise FileNotFoundError(path)
        raise Blocked('native_GitHub_metadata_request_failed')
    return json.loads(p.stdout)


def get_note(role):
    path='coordination/native/'+REQUEST+'-'+role+'.json'
    try:
        value=api(Q,'contents/'+path+'?ref='+STATE)
        return json.loads(base64.b64decode(value['content'])),value['sha']
    except FileNotFoundError:return None,None


def put_note(role,phase,**fields):
    old,version=get_note(role)
    owner=os.environ['GITHUB_RUN_ID']
    if old and old['run_id']!=owner:raise Blocked('native_proof_request_already_owned')
    value=dict(schema=1,request_id=REQUEST,role=role,repository=os.environ['GITHUB_REPOSITORY'],
        run_id=owner,code_commit=os.environ['GITHUB_SHA'],phase=phase,checked_at=utcnow().isoformat(),
        market_http=0,oauth_issuance=0,history=(old or {}).get('history',[])+[dict(phase=phase,at=utcnow().isoformat())],**fields)
    raw=json.dumps(value,sort_keys=True).encode()
    for key in ('GH_TOKEN','GITHUB_TOKEN','KIS_COORDINATOR_TOKEN'):
        if os.getenv(key) and os.environ[key].encode() in raw:raise Blocked('credential_in_native_proof')
    body=dict(branch=STATE,message='Native shared lease proof '+role+' '+phase,
              content=base64.b64encode(raw+b'\n').decode())
    if version:body['sha']=version
    api(Q,'contents/coordination/native/'+REQUEST+'-'+role+'.json','PUT',body)
    return value


def wait_for(role,phases,deadline):
    while time.monotonic()<deadline:
        note,_=get_note(role)
        if note:
            if note.get('request_id')!=REQUEST or note.get('market_http')!=0 or note.get('oauth_issuance')!=0:
                raise Blocked('native_peer_proof_scope_invalid')
            if note['phase']=='FAILED':raise Blocked('native_peer_failed')
            if note['phase'] in phases:return note
        time.sleep(5)
    raise Blocked('native_peer_timeout_no_market_calls')


def verify_deployed(actor):
    head=api(actor,'git/ref/heads/main')['object']['sha']
    hashes={}
    for path in SOURCES[actor]:
        value=api(actor,'contents/'+path+'?ref='+head)
        raw=base64.b64decode(value['content'])
        if raw!=(ROOT/path).read_bytes():raise Blocked('native_main_source_not_deployed_'+path)
        hashes[path]=hashlib.sha256(raw).hexdigest()
    return dict(main_commit=head,runtime_source_hashes=hashes)


def drain_old_monitor(actor,deadline):
    if actor!=M:return []
    observed=[]
    while time.monotonic()<deadline:
        unsafe=[]
        for state in ('in_progress','queued','requested','waiting','pending'):
            runs=api(M,'actions/runs?status='+state+'&per_page=100')
            if runs['total_count']>100:raise Blocked('native_active_inventory_too_large')
            for run in runs['workflow_runs']:
                if str(run['id'])==os.environ['GITHUB_RUN_ID']:continue
                path=run.get('path','').split('@')[0]
                if not path.startswith('.github/workflows/'):raise Blocked('native_unknown_active_workflow')
                try:text=base64.b64decode(api(M,'contents/'+path+'?ref='+run['head_sha'])['content']).decode()
                except FileNotFoundError:raise Blocked('native_active_source_unreadable') from None
                if 'KIS_APP_KEY' in text or 'KIS_APP_SECRET' in text:
                    unsafe.append(dict(run_id=str(run['id']),status=run['status'],code_commit=run['head_sha']))
        observed.append(dict(at=utcnow().isoformat(),potential_KIS_runs=unsafe))
        if not unsafe:return observed
        time.sleep(10)
    raise Blocked('native_older_monitor_runs_not_drained')


def initialize(backend):
    if backend._git('ls-remote',backend.remote,'refs/heads/'+BRANCH).stdout.strip():
        raise Blocked('native_probe_branch_already_exists_no_reset')
    raw=json.dumps(initial(),sort_keys=True).encode()+b'\n'
    blob=backend._git('hash-object','-w','--stdin',data=raw).stdout.strip()
    tree=backend._git('mktree',data=b'100644 blob '+blob+b'\tlease.json\n').stdout.strip().decode()
    commit=backend._git('commit-tree',tree,data=b'Isolated two-repository lease test; no KIS requests\n').stdout.strip().decode()
    result=backend._git('push','--porcelain',backend.remote,commit+':refs/heads/'+BRANCH,reject=True)
    if result.returncode:raise Blocked('native_probe_initialization_unconfirmed')


def denied(backend,peer):
    contender=Lease(backend,os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_SHA'])
    try:contender.acquire(seconds=300)
    except Blocked as error:
        if str(error)!='shared_lock_busy':raise
    else:raise Blocked('native_mutual_exclusion_failed')
    _,state=backend.read()
    owner=state['owner']
    if owner is None or owner['run_id']!=peer['run_id'] or owner['repository']!=peer['repository']:
        raise Blocked('native_peer_owner_not_verified')
    try:contender.reserve(auth=True)
    except Blocked:pass
    else:raise Blocked('native_loser_authorized_authentication')
    if state['last_auth'] is not None:raise Blocked('native_probe_must_never_reserve_OAuth')
    return dict(owner=owner,authentication_authorized=False,market_http=0)


def main():
    actor=os.environ['GITHUB_REPOSITORY'];role='M' if actor==M else 'Q'
    c=json.loads((ROOT/'.github/trials/shared-native-20261030.json').read_bytes())
    if (actor not in (Q,M) or os.getenv('GITHUB_REF')!='refs/heads/fix/shared-operating-20261030'
            or os.getenv('GITHUB_EVENT_NAME')!='push' or os.getenv('GITHUB_RUN_ATTEMPT')!='1'
            or c.get('request_id')!=REQUEST or utcnow()>=stamp(c['expires_at'])
            or any(os.getenv(k) for k in ('KIS_APP_KEY','KIS_APP_SECRET','KIS_ACCESS_TOKEN'))):
        raise Blocked('native_actor_window_or_no_KIS_scope_invalid')
    os.environ['GH_TOKEN']=os.environ['KIS_COORDINATOR_TOKEN'] if actor==M else os.environ['GITHUB_TOKEN']
    deadline=time.monotonic()+480
    source=verify_deployed(actor)
    drain=drain_old_monitor(actor,deadline)
    backend=GitBackend(Path(tempfile.mkdtemp(prefix='native-lease-'))/'state.git',branch=BRANCH)
    lease=None
    try:
        if role=='M':
            initialize(backend)
            lease=Lease(backend,actor,os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_SHA'])
            lease.acquire(seconds=300)
            put_note(role,'M_HOLDING',source=source,drain=drain)
            wait_for('Q',{'Q_DENIED'},deadline)
            lease.release()
            put_note(role,'M_RELEASED',source=source,drain=drain)
            peer=wait_for('Q',{'Q_HOLDING'},deadline)
            proof=denied(backend,peer)
            put_note(role,'M_DENIED',source=source,drain=drain,denial=proof)
            wait_for('Q',{'PASS'},deadline)
        else:
            peer=wait_for('M',{'M_HOLDING'},deadline)
            proof=denied(backend,peer)
            put_note(role,'Q_DENIED',source=source,denial=proof)
            wait_for('M',{'M_RELEASED'},deadline)
            lease=Lease(backend,actor,os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_SHA'])
            lease.acquire(seconds=300)
            put_note(role,'Q_HOLDING',source=source)
            wait_for('M',{'M_DENIED'},deadline)
            lease.release()
        _,state=backend.read()
        if state['owner'] is not None or state['last_auth'] is not None or state['sequence']!=4:
            raise Blocked('native_final_shared_state_not_clean')
        report=put_note(role,'PASS',source=source,drain=drain,isolated_branch=BRANCH,
            mutual_exclusion_both_directions=True,final_owner=None,final_sequence=4)
    except Exception as error:
        # An isolated test failure never resets the production lease or its history.
        reason=str(error) if isinstance(error,Blocked) else 'native_'+type(error).__name__
        put_note(role,'FAILED',source=source,reason=reason)
        raise
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as out:out.write('```json\n'+json.dumps(report,indent=2)+'\n```\n')
    print(json.dumps(report))


if __name__=='__main__':main()
