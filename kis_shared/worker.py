"""Default-off one-symbol account worker and the same CLI's native proof mode.

Run with python -I kis_shared/worker.py. Analysis remains in its denied-network
process; this worker has a separate reviewed, explicit account admission gate.
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kis_shared.lease import Blocked,REPOSITORIES
from kis_shared.runtime import Session,current
from kis_shared.git_backend import GitBackend

ROLES=dict(eod=REPOSITORIES[0],intraday=REPOSITORIES[1],integrated=REPOSITORIES[2])
QUOTE='/uapi/domestic-stock/v1/quotations/inquire-price'
AUTH='/oauth2/tokenP'
def encoded(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def sha(b):return hashlib.sha256(b).hexdigest()
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class ProofClock:
    def __init__(self,at):self.at=dt.datetime.fromisoformat(at)
    def __call__(self):return self.at
    def sleep(self,seconds):self.at+=dt.timedelta(seconds=seconds)

class ProofReply(io.BytesIO):
    status=200;code=200;msg='OK';headers={};url='https://openapi.koreainvestment.com:9443'
    def info(self):return self.headers
    def geturl(self):return self.url
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def native_network_fence():
    def deny(event,args):
        if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.sendto','http.client.connect'):
            raise Blocked('native_proof_real_network_forbidden')
    sys.addaudithook(deny)

def raw_request(path,params=None,body=None):
    session=current();session.before_http(auth=path==AUTH,interval=2.0);session.check()
    url='https://openapi.koreainvestment.com:9443'+path
    if params:
        from urllib.parse import urlencode
        url+='?'+urlencode(params)
    headers={'Content-Type':'application/json'}
    if path!=AUTH:headers.update(appkey=session.env['KIS_APP_KEY'],appsecret=session.env['KIS_APP_SECRET'],authorization='Bearer '+session._worker_token,tr_id='FHKST01010100',custtype='P')
    request=urllib.request.Request(url,headers=headers,data=encoded(body) if body is not None else None)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=25) as response:
            raw=response.read(2_000_001);status=response.status
        session.http_response_closed()
    except urllib.error.HTTPError as error:
        raw=error.read(2_000_001);status=error.code;error.close();session.http_response_closed()
    except Exception:
        session.uncertain=True;raise Blocked('worker_HTTP_outcome_unknown_no_retry') from None
    if len(raw)>2_000_000:raise Blocked('worker_response_size_rejected')
    session.check()
    if status!=200:raise Blocked('worker_HTTP_failed_no_retry')
    return raw

def collect(role,root):
    session=current()
    if getattr(session,'legacy_client',False):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from kis_public_client import KIS
        api=KIS()
        def quote():return api.request(QUOTE,{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':'000720'},'FHKST01010100')
        if getattr(session,'proof_fault',None)=='thread':
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:value=pool.submit(quote).result()
        else:value=quote()
        session._worker_sensitive=(api.token,);api.token=None;raw=encoded(value)
    elif role=='eod':
        # This is the actual class used by regular.collector.prepare().
        from collector_v1.core import Store,Budget
        from collector_v1.providers import Providers
        store=Store(root/'provider');budget=Budget(store,calls=2,cumulative=2)
        api=Providers(store,budget)
        raw=api.kis(QUOTE,{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':'000720'},'FHKST01010100')
        if api.token:session._worker_sensitive=(api.token,)
        api.token=None
    else:
        if role=='intraday':
            # Native proof executes the same OAuth Broker as real monitor jobs.
            # Synthetic policy describes only the mock server, never KIS policy.
            from scripts.actions_auth import Broker
            config=dict(token_mode='reuse_or_issue_once',auth_call_cap=1,
                token_issuance_policy_verified=True,token_reissue_effect_verified=True,
                token_expiry_timezone_verified=True,account_auth_limit_verified=True,
                token_reissue_effect='PREVIOUS_TOKENS_REMAIN_VALID',
                issuance_policy_reference='SYNTHETIC_ONLY',reissue_effect_record_id='SYNTHETIC_ONLY',
                expiry_timezone_reference='SYNTHETIC_ONLY',token_expiry_timezone='UTC')
            if not getattr(session.backend,'native_proof',False):
                raise Blocked('monitor_live_probe_requires_existing_reviewed_entrypoint')
            broker=Broker(now=session.clock);memory=broker.acquire(config,session.env,root=root,guard=session.check)
            session._worker_token=memory.value;session._worker_sensitive=(memory.value,)
        else:
            raw=raw_request(AUTH,body=dict(grant_type='client_credentials',appkey=session.env['KIS_APP_KEY'],appsecret=session.env['KIS_APP_SECRET']))
            payload=json.loads(raw);session.token_response(payload);session._worker_token=payload['access_token'];session._worker_sensitive=(payload['access_token'],)
        if role=='intraday':
            # The real monitor adapter, with its durable before_open callback.
            from stage3.live_probe import HttpAdapter
            from kis_probe import Credentials,Request
            credentials=Credentials(session.env['KIS_APP_KEY'],session.env['KIS_APP_SECRET'],session._worker_token)
            adapter=HttpAdapter(credentials,guard=session.check)
            task=Request('quote','000720',session.clock().date().isoformat(),{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':'000720'})
            receipt=adapter.send(task,lambda:None);raw=receipt.raw
        else:
            raw=raw_request(QUOTE,params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':'000720'})
        session._worker_token=None
    session.check()
    # No provider date/scope/finality or trading approval is inferred here.
    for value in (session.env['KIS_APP_KEY'],session.env['KIS_APP_SECRET'],*getattr(session,'_worker_sensitive',())):
        if value.encode() in raw:raise Blocked('worker_credential_echo_not_saved')
    (root/'response.bin').write_bytes(raw)
    return dict(source_sha256=sha(raw),validation='STRUCTURE_ONLY_UNVERIFIED',official_price_status='PENDING_KRX')

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['native-proof','live'],default='live')
    p.add_argument('--role',choices=ROLES,required=True);p.add_argument('--output',required=True)
    p.add_argument('--config');p.add_argument('--proof-root');p.add_argument('--proof-at',default='2026-11-03T10:00:00+00:00')
    p.add_argument('--request-id',required=True);p.add_argument('--run-id',default='100');p.add_argument('--barrier');p.add_argument('--release-file')
    p.add_argument('--legacy-client',action='store_true');p.add_argument('--fault',choices=['none','auth','network','429','owner-loss','expire','crash','redirect','order','restart','raw-open','no-session','token-expire','low-level','retry-owner-loss','ledger-delay','thread'],default='none')
    args=p.parse_args(argv);output=Path(args.output);result=dict(status='BLOCKED',role=args.role,actual_KIS_HTTP=0,oauth_issuance=0)
    calls=[];session=None;created=False
    try:
        if args.mode=='live':
            if args.fault!='none' or args.proof_root or args.barrier or args.release_file or args.legacy_client:raise Blocked('native_controls_forbidden_in_live_mode')
            # Gate before credential access, local Git initialization or claim.
            cfg=json.loads(Path(args.config).read_bytes()) if args.config else {}
            if cfg.get('operator_live_validation_approved') is not True or cfg.get('schema')!=2 or cfg.get('actual_market_calls_authorized') is not True or os.environ.get('ENABLE_LIVE_KIS_VALIDATION','false')!='true' or cfg.get('enabled') is not True or cfg.get('ENABLE_LIVE_KIS_VALIDATION') is not True:
                raise Blocked('live_validation_disabled_before_credentials')
            if os.environ.get('GITHUB_REPOSITORY')!=ROLES[args.role] or cfg.get('request_id')!=args.request_id or cfg.get('max_http')!=2 or cfg.get('symbols')!=['000720']:
                raise Blocked('live_actor_scope_or_budget_unapproved')
            if not cfg.get('not_before') or not cfg.get('expires_at'):raise Blocked('live_window_missing')
            start=dt.datetime.fromisoformat(cfg['not_before']);end=dt.datetime.fromisoformat(cfg['expires_at']);now=dt.datetime.now(dt.timezone.utc)
            if not start<=now<end or not 0<(end-start).total_seconds()<=900:raise Blocked('live_window_invalid')
            env=dict(os.environ);env['KIS_VALIDATION_REQUEST_ID']=args.request_id;clock=lambda:dt.datetime.now(dt.timezone.utc);backend=None
        else:
            native_network_fence()
            if any(os.environ.get(k) for k in ('KIS_APP_KEY','KIS_APP_SECRET','KIS_ACCESS_TOKEN')):raise Blocked('native_mode_refuses_real_credential_environment')
            if not args.proof_root:raise Blocked('native_proof_root_required')
            root=Path(args.proof_root).resolve();clock=ProofClock(args.proof_at)
            env=dict(GITHUB_REPOSITORY=ROLES[args.role],GITHUB_RUN_ID=args.run_id,GITHUB_SHA='a'*40,
                KIS_APP_KEY='SYNTHETIC_ONLY',KIS_APP_SECRET='SYNTHETIC_ONLY',EXECUTION_MODE='NATIVE_PROOF',KIS_VALIDATION_REQUEST_ID=args.request_id)
            os.environ['KIS_APP_KEY']=os.environ['KIS_APP_SECRET']='SYNTHETIC_ONLY'
            backend=GitBackend(root/('client-'+args.role+'-'+str(os.getpid())),remote=str(root/'central.git'),branch='kis-shared-probe-native-v2',proof_root=root)
            cfg=dict(mode='native-proof',token_expiry_timezone='UTC')
            def wire(opener,request,**kwargs):
                path=request.full_url.split('?')[0].replace('https://openapi.koreainvestment.com:9443','');calls.append(path)
                if len(calls)>2:raise Blocked('worker_two_request_cap')
                if args.fault=='network':raise TimeoutError()
                if args.fault=='retry-owner-loss' and path!=AUTH:
                    revision,state=backend.read();state['owner']['id']='b'*32;state['sequence']+=1
                    if not backend.compare_and_append(revision,state):raise Blocked('native_fault_write_failed')
                    raise TimeoutError()
                if args.fault=='auth' and path==AUTH:raise urllib.error.HTTPError(request.full_url,401,'mock',{},io.BytesIO(b'{}'))
                if args.fault=='429' and path!=AUTH:raise urllib.error.HTTPError(request.full_url,429,'mock',{},io.BytesIO(b'{}'))
                if args.fault=='redirect' and path!=AUTH:
                    response=ProofReply(b'{}');response.status=response.code=302;response.headers={'Location':'https://example.invalid/leak'};return response
                if path==AUTH:return ProofReply(encoded(dict(access_token='SYNTHETIC_TOKEN',token_type='Bearer',expires_in=86400,access_token_token_expired=(clock()+dt.timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'))))
                return ProofReply(encoded(dict(rt_cd='0',output=dict(stck_shrn_iscd='000720',stck_prpr='100',stck_oprc='99',stck_hgpr='101',stck_lwpr='98',acml_vol='1000',acml_tr_pbmn='100000'))))
            urllib.request.HTTPSHandler.https_open=wire
        if output.exists():raise Blocked('validation_output_cannot_reset')
        output.mkdir(parents=True);created=True
        if args.mode=='live':
            def dispatch_audit(event,values):
                if event!='urllib.Request':return
                from urllib.parse import urlsplit
                url,data,headers,method=values;parts=urlsplit(url)
                if parts.hostname!='openapi.koreainvestment.com':return
                if len(calls)>=2:raise Blocked('worker_two_request_cap_no_retry')
                current().check()
                row=encoded(dict(sequence=len(calls)+1,endpoint=parts.path,at=clock().isoformat()))+b'\n'
                with (output/'http-reservations.jsonl').open('ab' if calls else 'xb') as handle:
                    handle.write(row);handle.flush();os.fsync(handle.fileno())
                calls.append(parts.path)
            sys.addaudithook(dispatch_audit)
        if args.fault=='no-session':urllib.request.urlopen('https://openapi.koreainvestment.com:9443'+AUTH,data=b'{}')
        session=Session(cfg,backend=backend,env=env,clock=clock);session.legacy_client=args.legacy_client;session.proof_fault=args.fault
        if args.fault=='ledger-delay':
            # Actual subprocess deadline: a stalled Git executable, local proof only.
            import subprocess
            shim=output/'git';shim.write_text('#!/bin/sh\nexec /bin/sleep 2\n');shim.chmod(0o700)
            original=backend._git
            def stalled(*a,**kw):
                try:subprocess.run([str(shim)],timeout=.05,check=True,capture_output=True)
                except subprocess.TimeoutExpired:raise Blocked('shared_git_transport_unavailable_outcome_unknown') from None
                return original(*a,**kw)
            backend._git=stalled
        with session:
            if args.barrier:Path(args.barrier).write_text('ACQUIRED')
            if args.release_file:
                deadline=time.monotonic()+30
                while not Path(args.release_file).exists():
                    if time.monotonic()>deadline:raise Blocked('native_parent_barrier_timeout')
                    time.sleep(.02)
            if args.fault=='crash':os._exit(71)
            if args.fault=='expire':clock.sleep(12000)
            if args.fault=='token-expire':session.token_expiry((clock()-dt.timedelta(seconds=1)).isoformat())
            if args.fault=='low-level':
                import http.client
                connection=http.client.HTTPSConnection('openapi.koreainvestment.com',9443)
                connection.send(b'GET '+QUOTE.encode()+b' HTTP/1.1\r\nHost: openapi.koreainvestment.com:9443\r\n\r\n')
            if args.fault=='order':raw_request('/uapi/domestic-stock/v1/trading/order-cash')
            if args.fault=='raw-open':urllib.request.build_opener().open('https://openapi.koreainvestment.com:9443'+QUOTE)
            result.update(collect(args.role,output));result['status']='PASS_NATIVE_PROOF' if args.mode=='native-proof' else 'PASS_LIMITED_UNVERIFIED_QUERY'
        result['lease_released']=not session.lease.held
    except Exception as error:
        # Never persist exception repr, request headers, credential values or tokens.
        reason=str(error) if isinstance(error,Blocked) else type(error).__name__
        result.update(status='BLOCKED_OR_FAILED',reason=reason,lease_released=False if session is None else not bool(session.lease and session.lease.held))
    result.update(mode=args.mode,simulated_dispatches=len(calls),simulated_endpoints=calls,actual_KIS_HTTP=0 if args.mode=='native-proof' else len(calls),
        account_repository=ROLES[args.role],central_contract='0baselabkim-debug/qweasd:kis-shared-account-state:lease.json')
    # A successful token endpoint may reuse an existing provider token. Report
    # physical OAuth attempts; do not infer actual token issuance from HTTP 200.
    result['actual_OAuth_HTTP']=0 if args.mode=='native-proof' else calls.count(AUTH)
    result['oauth_issuance']=0 if args.mode=='native-proof' else 'NOT_INFERRED'
    if session is not None:
        session._worker_token=None;session._worker_sensitive=()
    if created:(output/'result.json').write_bytes(encoded(result))
    print(json.dumps(result));return 0 if result['status'].startswith('PASS') else 2

if __name__=='__main__':raise SystemExit(main())
