"""Explicit eight-reference recovery using the existing durable priority budget.

No KIS import, token issue, schedule, implicit retry, or new budget root. Every
remote append is a child of the last confirmed evidence tip; ambiguous writes
stop the run. A crash keeps its full reservation charged until reviewed.
"""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from scripts.collect_decision_prices import Budget, cache, cached, guard, save, now
from scripts.market_reference import classify_rows
from collector_v1.providers import NoRedirect
from screener.decision_support.evidence import sha, encoded

REF = 'refs/heads/evidence/decision-price-source'
PAIRS = [(f'2026-10-{d}',m) for d in (26,27,28,29) for m in ('KOSPI','KOSDAQ')]


def git(*args, data=None, env=None):
    p = subprocess.run(['git',*args],input=data,capture_output=True,env=env,timeout=90)
    if p.returncode: raise ValueError('evidence_git_operation_failed')
    return p.stdout.decode().strip()


def check_control(c, at=None):
    at = at or dt.datetime.now(dt.timezone.utc)
    start,end = (dt.datetime.fromisoformat(c[k]) for k in ('not_before','expires_at'))
    if (c.get('approval')!='USER_APPROVED_HISTORICAL_RECOVERY' or c.get('pairs')!=[list(p) for p in PAIRS]
            or c.get('max_http')!=8 or start.tzinfo is None or end.tzinfo is None
            or not 0 < (end-start).total_seconds() <= 600 or not start <= at < end):
        raise ValueError('historical_reference_control_invalid_or_expired')
    local = at.astimezone(ZoneInfo('Asia/Seoul'))
    if 18 <= local.hour < 23: raise ValueError('operating_collection_window')
    if os.getenv('GITHUB_REF')!='refs/heads/feat/decision-evidence-v2': raise ValueError('feature_branch_only')
    if os.getenv('GITHUB_RUN_ATTEMPT')!='1': raise ValueError('automatic_or_manual_workflow_rerun_forbidden')


def operating_clear(api=None):
    api = api or urllib.request.urlopen
    for status in ('queued','in_progress','waiting','pending','requested'):
        url='https://api.github.com/repos/0baselabkim-debug/qweasd/actions/runs?status='+status+'&per_page=100'
        with api(urllib.request.Request(url,headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'}),timeout=30) as r:
            doc=json.load(r)
        if doc.get('total_count',0)>100: raise ValueError('operating_state_pagination_unresolved')
        for run in doc['workflow_runs']:
            if str(run['id'])==os.getenv('GITHUB_RUN_ID'): continue
            if run['head_branch']=='main' or run.get('path')=='.github/workflows/kis-market-2030.yml':
                raise ValueError('operating_work_has_priority')


def append(root, expected, *, git_call=git):
    actual=git_call('ls-remote','--exit-code','origin',REF).split()[0]
    if actual!=expected: raise ValueError('evidence_head_changed_stop_without_retry')
    git_call('fetch','--no-tags','origin',REF)
    if git_call('rev-parse','FETCH_HEAD')!=expected: raise ValueError('evidence_fetch_race')
    fd,index=tempfile.mkstemp(prefix='historical-index-');os.close(fd);os.unlink(index)
    env=dict(os.environ,GIT_INDEX_FILE=index)
    try:
        git_call('read-tree',expected,env=env)
        for p in sorted(root.rglob('*')):
            if not p.is_file() or p.is_symlink(): continue
            blob=git_call('hash-object','-w','--stdin',data=p.read_bytes())
            git_call('update-index','--add','--cacheinfo','100644',blob,str(p),env=env)
        tree=git_call('write-tree',env=env)
        commit=git_call('-c','user.name=Historical evidence recovery','-c','user.email=historical-evidence@users.noreply.github.com',
                        'commit-tree',tree,'-p',expected,data=b'Preserve bounded historical KRX recovery evidence\n')
        git_call('push','origin',commit+':'+REF)
        if git_call('ls-remote','--exit-code','origin',REF).split()[0]!=commit: raise ValueError('evidence_append_readback_failed')
        return commit
    finally:
        Path(index).unlink(missing_ok=True)


def collect_one(day,market,root,budget,credential,opener=None):
    key=f'krx:{market}:{day}'
    if cached(root,key): return 'REUSED'
    url=f'https://data-dbg.krx.co.kr/svc/apis/sto/{"stk" if market=="KOSPI" else "ksq"}_bydd_trd?basDd='+day.replace('-','')
    budget.before_http(url)
    opener=opener or urllib.request.build_opener(NoRedirect()).open
    raw=b'';http=None;status='transport_error'
    try:
        try:
            with opener(urllib.request.Request(url,headers={'AUTH_KEY':credential}),timeout=25) as response:
                http=response.status;raw=response.read(16000001)
        except urllib.error.HTTPError as error:
            http=error.code;raw=error.read(16000001)
        if len(raw)>16000000: raise ValueError('response_size_limit')
        h=sha(raw)
        if credential.encode() in raw: raise ValueError('credential_echo_body_withheld')
        destination=root/'recovery-raw'/(h+'.bin');destination.parent.mkdir(parents=True,exist_ok=True)
        if not destination.exists():
            with destination.open('xb') as f:f.write(raw)
        if http!=200: raise ValueError('KRX_HTTP_'+str(http))
        payload=json.loads(raw);status,rows=classify_rows(payload,day)
        if status!='RECEIVED': raise ValueError(status)
        cache(root,key,dict(asof=day,market=market,url=url,observed_at=now(),http=http,
                           rows=rows,response_sha256=h,raw_response=payload))
        return status
    finally:
        budget.finish(status,http=http,response_sha256=sha(raw),response_bytes=len(raw))


def main(control_path):
    c=json.loads(Path(control_path).read_bytes());check_control(c);guard();operating_clear()
    if os.getenv('COLLECTION_CLASS')!='daily_priority': raise ValueError('priority_budget_required')
    root=Path('auxiliary-prices')
    if not (root/'requests.json').is_file(): raise ValueError('previous_physical_journal_required')
    head=git('rev-parse','FETCH_HEAD')
    if head!=c['previous_evidence_head']: raise ValueError('previous_evidence_anchor_changed')
    budget=Budget(root,limit=8,seconds=600)
    prior=json.loads((root/'requests.json').read_bytes())
    if len(prior['requests'])!=c['previous_recorded_http']: raise ValueError('journal_count_anchor_changed')
    if any(x['status']=='reserved' for x in prior.get('leases',{}).values()): raise ValueError('unresolved_other_request_reservation')
    spent=sum(x.get('budget_class')=='daily_priority' and x.get('budget_date')==budget.budget_date for x in prior['requests'])
    if spent+8>40: raise ValueError('priority_day_budget_exceeded')
    budget.state.setdefault('leases',{})[budget.lease_id]=dict(status='reserved',limit=8,reserved_at=now(),budget_class='daily_priority',budget_date=budget.budget_date,initial_recorded_count=budget.initial)
    save(budget.path,budget.state);head=append(root,head)
    result=dict(schema=1,run_id=os.getenv('GITHUB_RUN_ID'),status='STARTED',planned_http=8,physical_http=0,results=[],automatic_retries=0)
    try:
        credential=os.environ.get('KRX_API_KEY')
        if not credential: raise ValueError('KRX_credential_not_bound')
        for day,market in PAIRS:
            check_control(c);operating_clear()
            status=collect_one(day,market,root,budget,credential)
            result['results'].append(dict(asof=day,market=market,status=status))
            save(root/'historical-recovery.json',result);head=append(root,head)
        result['status']='PASS'
    except Exception as error:
        result['status']='FAIL';result['reason']=str(error) if isinstance(error,ValueError) else type(error).__name__
    finally:
        result['physical_http']=len(budget.state['requests'])-budget.initial;result['completed_at']=now()
        budget.state['leases'][budget.lease_id].update(status='recorded',physical_requests=result['physical_http'])
        save(budget.path,budget.state);save(root/'historical-recovery.json',result)
        head=append(root,head)
        print(json.dumps(result,ensure_ascii=False))
        if os.getenv('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('Bounded historical KRX recovery\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
    if result['status']!='PASS': raise SystemExit(1)
