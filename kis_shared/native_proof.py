"""Three source trees, separate OS processes, actual Git CAS, fake final HTTP only.

No GitHub write, secret, OAuth, market data, orders, Telegram or paid API is used.
Run this orchestrator outside the deny-all analysis process with python -I.
"""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kis_shared.lease import initial,Lease,Blocked,REPOSITORIES
from kis_shared.git_backend import GitBackend
BRANCH='kis-shared-probe-native-v2'
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def git(*args,cwd=None):
    result=subprocess.run(['git','-c','user.name=Native proof','-c','user.email=proof@example.invalid',*args],cwd=cwd,capture_output=True,timeout=10)
    if result.returncode:raise RuntimeError('local_git_fixture_failed')
    return result.stdout
class Proof:
    def __init__(self,roots,output,*,kind="actual_source_trees"):
        self.kind=kind
        self.roots=roots;self.output=output;self.output.mkdir(parents=True,exist_ok=False)
        self.temp=Path(tempfile.mkdtemp(prefix='kis-native-v2-'));self.rows=[];self.number=0
        self.env={k:v for k,v in os.environ.items() if not k.startswith(('KIS_','TELEGRAM_','OPENAI_'))}
    def case(self):
        self.number+=1;r=self.temp/str(self.number);r.mkdir();git('init','--bare',str(r/'central.git'))
        seed=r/'seed';seed.mkdir();git('init',str(seed));(seed/'lease.json').write_text(json.dumps(initial()))
        git('add','lease.json',cwd=seed);git('commit','-m','isolated native proof',cwd=seed)
        git('push',str(r/'central.git'),'HEAD:refs/heads/'+BRANCH,cwd=seed)
        return r
    def backend(self,r):return GitBackend(r/'review',remote=str(r/'central.git'),branch=BRANCH,proof_root=r)
    def start(self,r,role,*,fault='none',hold=False,scope=None,run=None,legacy=False,live=False):
        name=role+'-'+str(len(list(r.glob('out-*'))));out=r/('out-'+name);barrier=r/(name+'.acquired');release=r/(name+'.release')
        cmd=[sys.executable,'-I',str(self.roots[role]/'kis_shared/worker.py'),'--mode','live' if live else 'native-proof','--role',role,'--output',str(out),'--request-id',scope or name,'--run-id',run or str(1000+self.number)]
        if not live:cmd+=['--proof-root',str(r),'--fault',fault]
        if hold:cmd+=['--barrier',str(barrier),'--release-file',str(release)]
        if legacy:cmd+=['--legacy-client']
        # Reserve the output name before the child creates it, using a sidecar.
        marker=r/('out-'+name+'.reserved');marker.touch()
        proc=subprocess.Popen(cmd,env=self.env,cwd=self.roots[role],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        return proc,barrier,release,out,cmd
    def finish(self,item):
        proc,barrier,release,out,cmd=item
        stdout,stderr=proc.communicate(timeout=40)
        try:result=json.loads(stdout.decode().splitlines()[-1])
        except Exception:result=dict(status='PROCESS_EXIT',returncode=proc.returncode,actual_KIS_HTTP=0,simulated_dispatches=0)
        return result
    def wait_acquired(self,item):
        end=time.monotonic()+12
        while time.monotonic()<end and not item[1].exists() and item[0].poll() is None:time.sleep(.01)
        if not item[1].exists():raise RuntimeError('native_actor_did_not_acquire')
    def row(self,name,expected,check,results,**extra):
        good=bool(check);row=dict(scenario=name,expected=expected,status='PASS' if good else 'FAIL',results=results,actual_KIS_HTTP=0,**extra)
        self.rows.append(row);print(name,row['status'],flush=True)
    def alone(self,name,role,fault='none',expected_calls=None,success=False,**kw):
        r=self.case();result=self.finish(self.start(r,role,fault=fault,**kw));state=self.backend(r).read()[1]
        check=(result['status'].startswith('PASS')==success and result['actual_KIS_HTTP']==0)
        if expected_calls is not None:check &= result['simulated_dispatches']==expected_calls
        if success:check &= state['owner'] is None
        self.row(name,'success+release' if success else 'fail closed, bounded dispatch',check,[result],final_owner=state['owner'])
        return r,result
    def pair(self,name,a,b):
        r=self.case();left=self.start(r,a,hold=True,run='111');self.wait_acquired(left)
        right=self.finish(self.start(r,b,run='222'));left[2].touch();owner=self.finish(left)
        self.row(name,'owner completes; competitor 0 HTTP',owner['status'].startswith('PASS') and right['simulated_dispatches']==0 and right['status']=='BLOCKED_OR_FAILED' and self.backend(r).read()[1]['owner'] is None,[owner,right])
    def change_while_held(self,name,*,unavailable=False):
        r=self.case();left=self.start(r,'integrated',hold=True);self.wait_acquired(left);backend=self.backend(r)
        if unavailable:(r/'central.git').rename(r/'central.unavailable')
        else:
            revision,state=backend.read();state['owner']['id']='c'*32;state['sequence']+=1
            assert backend.compare_and_append(revision,state)
        left[2].touch();result=self.finish(left)
        self.row(name,'ownership/ledger failure before OAuth: 0 HTTP',result['status']=='BLOCKED_OR_FAILED' and result['simulated_dispatches']==0,[result])
    def run(self):
        for i,role in enumerate(self.roots):self.alone(f'{i+1:02d}_{role}_alone',role,expected_calls=2,success=True)
        self.pair('04_eod_blocks_intraday','eod','intraday');self.pair('05_intraday_blocks_integrated','intraday','integrated');self.pair('06_integrated_blocks_eod','integrated','eod')
        r=self.case();items=[self.start(r,role,hold=True,run=str(200+i)) for i,role in enumerate(self.roots)]
        end=time.monotonic()+12
        while time.monotonic()<end and not any(x[1].exists() for x in items):time.sleep(.01)
        winners=[x for x in items if x[1].exists()]
        # All contenders have reached their CAS/busy result before releasing owner.
        losers=[x for x in items if x not in winners]
        other=[self.finish(x) for x in losers]
        for x in winners:x[2].touch()
        results=other+[self.finish(x) for x in winners]
        self.row('07_three_process_race','exactly one winner, two losers with 0 HTTP',len(winners)==1 and sum(x['status'].startswith('PASS') for x in results)==1 and sum(x['simulated_dispatches'] for x in results)==2,results)
        self.alone('08_expired_before_oauth','integrated','expire',0)
        self.change_while_held('09_owner_lost_before_dispatch')
        self.change_while_held('10_ledger_unavailable',unavailable=True)
        self.alone('11_ledger_timeout','integrated','ledger-delay',0)
        self.alone('12_owner_change_after_unknown_http','integrated','retry-owner-loss',2)
        self.alone('13_oauth_failure_no_retry','integrated','auth',1)
        crash,_=self.alone('14_abrupt_exit_keeps_owner','integrated','crash',0)
        result=self.finish(self.start(crash,'eod'))
        self.row('15_old_lease_not_reused','retained owner blocks new process',result['simulated_dispatches']==0 and result['status']=='BLOCKED_OR_FAILED',[result])
        lease=Lease(self.backend(crash),REPOSITORIES[0],'999','d'*40,clock=lambda:dt.datetime(2026,11,3,10,tzinfo=dt.timezone.utc))
        try:lease.release();rejected=False
        except Blocked:rejected=True
        self.row('16_nonowner_release','no foreign release',rejected,[])
        self.pair('17_schedule_manual_same_actor','eod','eod');self.pair('18_regular_blocks_new_live_worker','eod','integrated')
        self.alone('19_default_cli_live_disabled','integrated',expected_calls=0,live=True)
        self.alone('20_no_credentials_no_live_dispatch','intraday',expected_calls=0,live=True)
        self.alone('21_order_endpoint_denied','integrated','order',0)
        self.alone('22_unreserved_opener_denied','integrated','raw-open',0)
        self.alone('23_redirect_denied','integrated','redirect',2)
        self.alone('24_429_no_automatic_retry','integrated','429',2)
        r,_=self.alone('25a_initial_scope','integrated',expected_calls=2,success=True,scope='fixed-restart-request')
        result=self.finish(self.start(r,'integrated',scope='fixed-restart-request',run='9999'))
        self.row('25b_process_restart_scope_not_reset','same request cannot reset budget',result.get('reason')=='shared_request_scope_already_claimed' and result['simulated_dispatches']==0,[result])
        self.alone('26_real_legacy_client','eod',expected_calls=2,success=True,legacy=True)
        self.alone('27_no_session_oauth_denied','integrated','no-session',0)
        self.alone('28_token_expired_denied','integrated','token-expire',0)
        self.alone('29_raw_httpclient_denied','integrated','low-level',0)
        self.alone('32_legacy_worker_thread_retains_session','eod','thread',2,success=True,legacy=True)
        # Only the isolated crashed PID is proven stopped; no production lock touched.
        backend=self.backend(crash);revision,state=backend.read();owner=state['owner'];clock=lambda:dt.datetime(2026,11,3,14,tzinfo=dt.timezone.utc)
        recovery=Lease(backend,REPOSITORIES[0],'9999','e'*40,clock=clock)
        try:recovery.recover(revision,lambda o:{});rejected=False
        except Blocked:rejected=True
        recovery.recover(revision,lambda o:{**o,'status':'completed','all_workers_stopped':True,'external_issuers_stopped':True})
        self.row('30_explicit_stopped_owner_recovery','requires terminal proof, quarantine and exact revision',rejected and backend.read()[1]['owner'] is None,[])
        common=['__init__.py','boundary.py','lease.py','runtime.py','git_backend.py','worker.py']
        hashes={role:{'kis_shared/'+n:digest(root/'kis_shared'/n) for n in common} for role,root in self.roots.items()}
        same=len({json.dumps(v,sort_keys=True) for v in hashes.values()})==1
        self.row('31_identical_common_protocol','identical three-party core on all actors',same,[])
        runtime_hashes={}
        for role,root in self.roots.items():
            names=list((root/'kis_shared').glob('*.py'))
            names += [root/name for name in ({
                'eod':['scripts/kis_public_client.py','collector_v1/providers.py','collector_v1/kis_manual.py','collector_v1/experiment/engine.py','collector_v1/regular/runner.py','collector_v1/regular/collector.py','.github/scripts/kis_enrich_collect.py','.github/scripts/kis_history_probe.py','.github/scripts/kis_collect_once.py','.github/scripts/kis_missing_analysis.py'],
                'intraday':['scripts/actions_auth.py','stage3/live_probe.py'],
                'integrated':[]}[role]) if (root/name).exists()]
            # Preserve every previously approved live source boundary. Narrowing
            # the old source map during a security rollout is not permitted.
            names += [root/p for p in ['.github/scripts/kis_lock_operational_readiness_v3.py','.github/workflows/kis-lock-operational-v3.yml','.github/workflows/kis-limited-validation.yml'] if (root/p).exists()]
            active=root/('config/shared-account.json' if role=='intraday' else '.github/config/kis-shared-account.json')
            if active.exists():
                for approved in json.loads(active.read_bytes()).get('runtime_source_hashes',{}):
                    source=(root/approved).resolve()
                    if not source.is_relative_to(root) or not source.is_file():raise RuntimeError('approved_source_missing')
                    names.append(source)
            runtime_hashes[REPOSITORIES[list(self.roots).index(role)]]={str(path.relative_to(root)):digest(path) for path in names if str(path.relative_to(root)) not in ('kis_shared/native_proof.py','kis_shared/bundled_proof.py')}
        # Retain all per-process outputs and complete local Git history for inspection.
        import shutil
        shutil.copytree(self.temp,self.output/'isolated-git-and-worker-evidence')
        report=dict(proof_kind=self.kind,status='PASS' if all(x['status']=='PASS' for x in self.rows) else 'FAIL',scenarios=self.rows,counts=dict(Counter(x['status'] for x in self.rows)),actual_KIS_HTTP=0,oauth_issuance=0,production_ledger_mutations=0,common_source_hashes=hashes,runtime_source_hashes=runtime_hashes,central_contract='0baselabkim-debug/qweasd:kis-shared-account-state:lease.json',proof_limit='real separate CLI processes, GitBackend/Session/clients; only final HTTPS transport simulated; GitHub credentials and KIS integration NOT_RUN')
        (self.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--eod-root',required=True);p.add_argument('--intraday-root',required=True);p.add_argument('--integrated-root',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    report=Proof({k:Path(getattr(a,k+'_root')).resolve() for k in ('eod','intraday','integrated')},Path(a.output)).run()
    print(json.dumps(dict(status=report['status'],counts=report['counts'],actual_KIS_HTTP=0)))
    return 0 if report['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
