"""Explicit push-controlled, staged trial. All market HTTP shares one ledger."""
import argparse
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

from .core import Budget, Failure, Stop, Store, collect_tasks, encoded, now, sha
from .providers import Providers, task
from .validation import parse

BRANCH='refs/heads/feat/market-data-collector-v1'
REPO='0baselabkim-debug/qweasd'
CONTROL='.github/trials/collector-v1.json'
WORKFLOW='collector-v1-trial.yml'
ARTIFACT='collector-v1-trial-state'
ROOT=Path('data/collector-v1-trial')
ADMIN=Path('data/collector-v1-trial-admin')
TARGETS={'000720':('KOSPI','현대건설'),'064400':('KOSPI','LG씨엔에스'),'300080':('KOSDAQ','플리토')}
SECRET_NAMES=('KIS_APP_KEY','KIS_APP_SECRET','KRX_API_KEY','DART_API_KEY','ECOS_API_KEY','FRED_API_KEY')


def local_date():
    return dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()


def control():
    c=json.loads(Path(CONTROL).read_bytes())
    if set(c)!={'schema','trial_id','request_id','stage','asof','previous_run','max_total_http'}:
        raise Failure('control_schema_mismatch')
    if c['schema']!=1 or c['stage'] not in ('master','market') or c['max_total_http']!=20:
        raise Failure('unknown_trial_stage')
    dt.date.fromisoformat(c['asof'])
    for key in ('trial_id','request_id'):
        if not isinstance(c[key],str) or not __import__('re').fullmatch(r'[a-zA-Z0-9_-]{1,80}',c[key]):
            raise Failure('invalid_control_identity')
    if c['previous_run'] is not None and (not isinstance(c['previous_run'],str) or not c['previous_run'].isdigit()):
        raise Failure('invalid_previous_run')
    if c['stage']=='market':
        from .market_trial import stage_guard
        stage_guard(c)
    elif c['asof']!=local_date():
        raise Failure('observation_date_mismatch_no_collection')
    return c


def git(*args):
    return subprocess.check_output(['git',*args],text=True).strip()


def github_json(path):
    # Supported GitHub CLI route. Never print error bodies or authentication.
    r=subprocess.run(['gh','api',path],capture_output=True,text=True)
    if r.returncode:raise Failure('github_metadata_access_blocked')
    return json.loads(r.stdout)


def predecessor(runs,current,c):
    if len(runs)>=100:raise Failure('history_pagination_required')
    prior=[r for r in runs if int(r['id'])<int(current) and r['event']=='push' and r['head_branch']=='feat/market-data-collector-v1']
    latest=max(prior,key=lambda r:int(r['id'])) if prior else None
    if latest:
        if str(latest['id'])!=c['previous_run'] or latest['status']!='completed':
            raise Failure('latest_trial_receipt_required')
        return str(latest['id'])
    if c['previous_run'] is not None:raise Failure('unexpected_previous_run')
    return ''


def preflight():
    if os.getenv('GITHUB_REPOSITORY')!=REPO or os.getenv('GITHUB_REF')!=BRANCH or os.getenv('GITHUB_EVENT_NAME')!='push':
        raise Failure('wrong_repository_branch_or_event')
    if os.getenv('GITHUB_RUN_ATTEMPT')!='1':raise Failure('rerun_requires_new_control_and_receipt')
    event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
    if event.get('deleted') or event.get('forced') or event.get('created'):
        raise Failure('non_incremental_push_not_authorized')
    if event.get('before')!=git('rev-parse','HEAD^') or event.get('after')!=git('rev-parse','HEAD'):
        raise Failure('single_control_commit_required')
    changed=git('diff','--name-only',event['before'],event['after']).splitlines()
    if changed!=[CONTROL]:raise Failure('control_only_commit_required')
    c=control()
    # No reset-to-zero by pushing a new control; newest predecessor must restore.
    current_run=github_json(f'repos/{REPO}/actions/runs/{os.environ["GITHUB_RUN_ID"]}')
    if current_run.get('path')!='.github/workflows/'+WORKFLOW:raise Failure('unexpected_workflow_identity')
    history=github_json(f'repos/{REPO}/actions/workflows/{current_run["workflow_id"]}/runs?branch=feat%2Fmarket-data-collector-v1&per_page=100')['workflow_runs']
    previous=predecessor(history,os.environ['GITHUB_RUN_ID'],c)
    if previous:
        data=github_json(f'repos/{REPO}/actions/runs/{previous}/artifacts?per_page=100')
        matches=[a for a in data['artifacts'] if a['name']==ARTIFACT and not a['expired']]
        if data['total_count']>=100 or len(matches)!=1:raise Failure('previous_state_artifact_unavailable')
        if c['stage']=='market':
            from .market_trial import metadata_guard
            metadata_guard(matches[0])
    ADMIN.mkdir(parents=True,exist_ok=True)
    snapshots={str(p):sha(p.read_bytes()) for p in Path('snapshot').rglob('*') if p.is_file()}
    (ADMIN/'snapshot-before.json').write_bytes(encoded(snapshots))
    (ADMIN/'preflight.json').write_bytes(encoded(dict(status='PASS',control=c,source_commit=git('rev-parse','HEAD'),previous_run=previous,checked_at=now())))
    with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('previous_run='+previous+'\n'+'stage='+c['stage']+'\n')


def inventory(root):
    return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file() and p.name not in ('writer.lock','state-manifest.json')}


def seal(store):
    store.write('state-manifest.json',{'schema':1,'files':inventory(store.root)})


def restore_check(store,c,previous):
    manifest=store.read('state-manifest.json')
    if previous:
        if not manifest or inventory(store.root)!=manifest['files']:raise Failure('artifact_hash_or_inventory_mismatch')
        receipt=store.read('receipt.json')
        if not receipt or receipt['github_run_id']!=previous or receipt['asof']!=c['asof'] or receipt['trial_id']!=c['trial_id'] or receipt['stage']!='master':
            raise Failure('artifact_receipt_mismatch')
        ledger=store.read('requests.json')
        if ledger is None or len(ledger)!=receipt['total_http']:raise Failure('artifact_ledger_mismatch')
        if receipt['request_id']==c['request_id']:raise Failure('duplicate_request_id')
        if receipt['status']!='PASS':raise Failure('failed_previous_trial_requires_review')
        return receipt
    if any(p.name!='writer.lock' for p in store.root.iterdir()):raise Failure('unexpected_initial_state')
    store.write('requests.json',[])
    return None


class MasterBudget(Budget):
    def __init__(self,store,asof):
        super().__init__(store,calls=20,seconds=240,cumulative=20)
        self.asof=asof
    def check(self):
        if local_date()!=self.asof:raise Stop('observation_date_changed')
        super().check()
    def before(self,source,endpoint,reason):
        self.check()
        allowed=(source=='KIS_MASTER' or (source=='KIS' and endpoint in ('/oauth2/tokenP','/uapi/domestic-stock/v1/quotations/chk-holiday')))
        if not allowed:raise Failure('stage_forbids_endpoint')
        return super().before(source,endpoint,reason)


def master_result(store,tasks):
    state=store.read('state.json',{'tasks':{}});universe=[];sources=[]
    for t in tasks:
        e=state['tasks'][sha(encoded(t))]['evidence'];values=parse(store.raw(e),e)
        if t['kind']=='master':
            minimum={'KOSPI':500,'KOSDAQ':1000}[t['market']]
            if len(values)<minimum:raise Failure('master_population_too_small')
            universe.extend(values);sources.append(e)
    codes=[r['symbol'] for r in universe]
    if len(codes)!=len(set(codes)) or len(codes)<2000:raise Failure('duplicate_or_missing_master_population')
    bycode={r['symbol']:r for r in universe};checks={}
    for symbol,(market,name) in TARGETS.items():
        row=bycode.get(symbol)
        if not row or (row['market'],row['name'])!=(market,name):raise Failure('target_symbol_market_or_name_mismatch')
        checks[symbol]={'market':market,'name':name,'eligible':row['eligible'],'exclusion_reasons':row['exclusion_reasons'],'status':'PASS'}
    store.write('masters.json',universe)
    return dict(status='PASS',rows=len(universe),eligible=sum(r['eligible'] for r in universe),markets={m:sum(r['market']==m for r in universe) for m in ('KOSPI','KOSDAQ')},targets=checks,sources=sources,masters_sha256=sha((store.root/'masters.json').read_bytes()),provider_finality='UNVERIFIED')


def secret_scan(store,api):
    forbidden=[os.getenv(n) for n in SECRET_NAMES+('GH_TOKEN','GITHUB_TOKEN') if os.getenv(n)]
    if api and api.token:forbidden.append(api.token)
    for p in store.root.rglob('*'):
        if not p.is_file():continue
        raw=p.read_bytes()
        if p.suffix=='.gz':raw=gzip.decompress(raw)
        if any(v.encode() in raw for v in forbidden):raise Failure('credential_in_trial_output')


def collect_master():
    c=control();pre=json.loads((ADMIN/'preflight.json').read_bytes())
    if c['stage']!='master':raise Failure('master_command_requires_master_stage')
    if pre['control']!=c or pre['source_commit']!=git('rev-parse','HEAD'):raise Failure('preflight_binding_mismatch')
    store=Store(ROOT);api=None;budget=None;started=now();result={'status':'BLOCKED'}
    with store.lock():
        # Restoration errors must NOT replace/erase a preceding receipt or ledger.
        restore_check(store,c,pre['previous_run'])
        try:
            presence={n:bool(os.getenv(n)) for n in SECRET_NAMES}
            result['secret_presence']=presence
            if not all(presence.values()):raise Failure('repository_secret_injection_incomplete')
            budget=MasterBudget(store,c['asof']);api=Providers(store,budget)
            tasks=[task('calendar',c['asof'])]+[task('master',c['asof'],market=m) for m in ('KOSPI','KOSDAQ')]
            def checked_fetch(t):
                evidence=api.fetch(t)
                parse(store.raw(evidence),evidence)  # closed/invalid date stops before masters
                return evidence
            run=collect_tasks(store,tasks,checked_fetch,budget,fail_fast=True)
            if run['collection_status']!='complete':
                result.update(status='BLOCKED' if run['stopped'] and ('missing_' in run['stopped'] or run['stopped'] in ('authentication_failure','network_policy_denied','network_failure')) else 'FAIL',collection=run)
            else:result.update(master_result(store,tasks))
            secret_scan(store,api)
        except (Failure,Stop) as e:
            result.update(status='BLOCKED',reason=e.code if isinstance(e,Failure) else str(e))
        except Exception:
            result.update(status='FAIL',reason='unexpected_collection_or_storage_error')
        finally:
            result.update(stage='master',asof=c['asof'],trial_id=c['trial_id'],request_id=c['request_id'],github_run_id=os.environ['GITHUB_RUN_ID'],source_commit=pre['source_commit'],started_at=started,completed_at=now(),physical_http_this_run=budget.used if budget else 0,total_http=len(store.read('requests.json',[])),stage2_executed=False)
            before=json.loads((ADMIN/'snapshot-before.json').read_bytes())
            after={str(p):sha(p.read_bytes()) for p in Path('snapshot').rglob('*') if p.is_file()}
            result['snapshot_unchanged']=before==after
            if before!=after:result.update(status='FAIL',reason='operating_snapshot_changed')
            store.write('receipt.json',result)
            # On suspected exposure do not upload potentially contaminated output.
            try:secret_scan(store,api)
            except Failure:
                (ADMIN/'DO_NOT_UPLOAD_STATE').touch()
                raise
            seal(store)
        print(json.dumps({'status':result['status'],'asof':result['asof'],'physical_http_this_run':result['physical_http_this_run'],'total_http':result['total_http'],'stage2_executed':False}))
        return 0 if result['status']=='PASS' else 1


def finalize():
    if not ROOT.exists() or not (ROOT/'requests.json').exists():return 0
    store=Store(ROOT)
    with store.lock():
        try:secret_scan(store,None)
        except Failure:
            ADMIN.mkdir(parents=True,exist_ok=True);(ADMIN/'DO_NOT_UPLOAD_STATE').touch()
            raise
        receipt=store.read('receipt.json')
        if not receipt or receipt.get('github_run_id')!=os.environ['GITHUB_RUN_ID']:
            pre=json.loads((ADMIN/'preflight.json').read_bytes());c=pre['control']
            if receipt:store.write('prior-receipts/'+receipt['github_run_id']+'.json',receipt)
            store.write('receipt.json',dict(status='BLOCKED',reason='trial_did_not_complete',stage=c['stage'],asof=c['asof'],trial_id=c['trial_id'],request_id=c['request_id'],github_run_id=os.environ['GITHUB_RUN_ID'],total_http=len(store.read('requests.json',[])),completed_at=now(),stage2_executed=c['stage']=='market' and len(store.read('requests.json',[]))>4))
        seal(store)
    return 0


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['preflight','master','finalize']);a=p.parse_args()
    try:return preflight() if a.mode=='preflight' else finalize() if a.mode=='finalize' else collect_master()
    except (Failure,Stop) as e:
        ADMIN.mkdir(parents=True,exist_ok=True)
        code=e.code if isinstance(e,Failure) else str(e)
        (ADMIN/'blocked.json').write_bytes(encoded({'status':'BLOCKED','reason':code,'observed_at':now(),'stage2_executed':False}))
        print(json.dumps({'status':'BLOCKED','reason':code}));return 1


if __name__=='__main__':raise SystemExit(main())
