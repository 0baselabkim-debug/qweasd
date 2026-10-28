"""Prepared stage two. Executed only by a separately approved control-file push."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import zipfile

from . import trial
from .core import Budget, Failure, Stop, Store, encoded, now, publish, sha
from .providers import Providers, Q, LAYOUTS, task
from .validation import KRX_FIELDS, integer, parse, validate

ASOF='2026-10-26'
NOT_BEFORE='2026-10-27'
MASTER_RUN='40289601356'
ARTIFACT_ID='12636170227'
ZIP_SHA='5cbc6ef7db90e4a1f1aaaf95b5e8d78a90bec648a201a488a68e119b42b22a1a'


def stage_guard(c):
    if c['stage']!='market' or c['asof']!=ASOF or c['previous_run']!=MASTER_RUN or c['trial_id']!='master-20261026':
        raise Failure('pinned_market_control_mismatch')
    if trial.local_date()<NOT_BEFORE:raise Failure('market_stage_too_early')


def metadata_guard(meta):
    if (str(meta.get('id'))!=ARTIFACT_ID or meta.get('name')!=trial.ARTIFACT or meta.get('expired') is not False
        or meta.get('digest')!='sha256:'+ZIP_SHA or str((meta.get('workflow_run') or {}).get('id'))!=MASTER_RUN):
        raise Failure('pinned_artifact_metadata_mismatch')


def master_tasks():
    return [task('calendar',ASOF)]+[task('master',ASOF,market=m) for m in ('KOSPI','KOSDAQ')]


def market_tasks():
    out=[task('index',ASOF,market=m) for m in ('KOSPI','KOSDAQ')]
    out += [task('krx',ASOF,market=m) for m in ('KOSPI','KOSDAQ')]
    for s,(m,_) in trial.TARGETS.items():out += [task(k,ASOF,symbol=s,market=m) for k in ('flow','price')]
    return out


def verify_master(store,c):
    """Read-only proof of exact prior receipt/ledger and all three source objects."""
    stage_guard(c)
    manifest=store.read('state-manifest.json')
    if not manifest or len(manifest.get('files',{}))!=11 or trial.inventory(store.root)!=manifest['files']:
        raise Failure('master_inventory_hash_mismatch')
    r=store.read('receipt.json',{})
    if (r.get('github_run_id')!=MASTER_RUN or r.get('asof')!=ASOF or r.get('stage')!='master'
        or r.get('status')!='PASS' or r.get('trial_id')!=c['trial_id'] or r.get('total_http')!=4
        or r.get('rows')!=3950 or r.get('eligible')!=2349 or r.get('request_id')==c['request_id']):
        raise Failure('master_receipt_mismatch')
    ledger=store.read('requests.json',[])
    expected=[('KIS','/oauth2/tokenP'),('KIS',Q+'chk-holiday')]+[('KIS_MASTER',LAYOUTS[m]['url']) for m in ('KOSPI','KOSDAQ')]
    if len(ledger)!=4 or [(e.get('source'),e.get('endpoint')) for e in ledger]!=expected or any(e.get('sequence')!=i+1 or e.get('status')!='received' or e.get('http')!=200 for i,e in enumerate(ledger)):
        raise Failure('master_http_ledger_mismatch')
    state=store.read('state.json',{}).get('tasks',{})
    if set(state)!={sha(encoded(t)) for t in master_tasks()}:raise Failure('master_task_set_mismatch')
    universe=[]
    for t in master_tasks():
        entry=state[sha(encoded(t))];e=entry.get('evidence',{})
        if entry.get('collection_status')!='received' or e.get('task')!=t:raise Failure('master_state_mismatch')
        rows=parse(store.raw(e),e)
        if t['kind']=='master':universe.extend(rows)
    if len(universe)!=3950 or sum(r['eligible'] for r in universe)!=2349 or len({r['symbol'] for r in universe})!=3950:
        raise Failure('master_population_mismatch')
    if encoded(universe)!=(store.root/'masters.json').read_bytes() or r.get('masters_sha256')!=sha(encoded(universe)):
        raise Failure('normalized_master_mismatch')
    bycode={r['symbol']:r for r in universe}
    for s,(m,name) in trial.TARGETS.items():
        row=bycode.get(s)
        if not row or (row['market'],row['name'],row['eligible'])!=(m,name,True):raise Failure('target_not_eligible_in_master')
    return r


def extract_verified(archive,c):
    """Verify ZIP bytes BEFORE extraction, then atomically install the sole state root."""
    stage_guard(c)
    if sha(archive.read_bytes())!=ZIP_SHA:raise Failure('artifact_zip_sha256_mismatch')
    if trial.ROOT.exists():raise Failure('existing_trial_root_no_reset_allowed')
    stage=trial.ROOT.with_name(trial.ROOT.name+'-restore-staging')
    if stage.exists():raise Failure('restore_staging_requires_review')
    stage.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as z:
            seen=set();size=0
            for info in z.infolist():
                p=PurePosixPath(info.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in info.filename or info.filename in seen or stat.S_ISLNK(info.external_attr>>16):
                    raise Failure('unsafe_artifact_archive')
                seen.add(info.filename);size+=info.file_size
                if size>128_000_000:raise Failure('artifact_uncompressed_size_exceeded')
                dest=stage.joinpath(*p.parts)
                if info.is_dir():dest.mkdir(parents=True,exist_ok=True);continue
                dest.parent.mkdir(parents=True,exist_ok=True)
                with dest.open('xb') as f:f.write(z.read(info))
        verify_master(Store(stage),c)
        os.rename(stage,trial.ROOT)
    except Exception:
        # A bad archive never becomes the root that can issue provider requests.
        raise


def restore():
    c=trial.control();stage_guard(c)
    pre=json.loads((trial.ADMIN/'preflight.json').read_bytes())
    if pre['control']!=c or pre['source_commit']!=trial.git('rev-parse','HEAD'):raise Failure('preflight_binding_mismatch')
    meta=trial.github_json(f'repos/{trial.REPO}/actions/artifacts/{ARTIFACT_ID}')
    metadata_guard(meta)
    archive=trial.ADMIN/'master-artifact.zip'
    with archive.open('xb') as f:
        r=subprocess.run(['gh','api',f'repos/{trial.REPO}/actions/artifacts/{ARTIFACT_ID}/zip'],stdout=f,stderr=subprocess.PIPE)
    if r.returncode:
        archive.unlink(missing_ok=True);raise Failure('artifact_download_blocked')
    if sha(archive.read_bytes())!=ZIP_SHA:
        archive.unlink();raise Failure('artifact_zip_sha256_mismatch')
    extract_verified(archive,c)
    proof=dict(status='PASS',stage='market',asof=ASOF,previous_run=MASTER_RUN,artifact_id=ARTIFACT_ID,zip_sha256=ZIP_SHA,source_commit=pre['source_commit'],control=c,verified_at=now())
    (trial.ADMIN/'market-restoration.json').write_bytes(encoded(proof))
    return 0


class MarketBudget(Budget):
    def __init__(self,store):super().__init__(store,calls=16,seconds=240,cumulative=20)
    def check(self):
        if trial.local_date()<NOT_BEFORE:raise Stop('market_stage_too_early')
        super().check()
    def before(self,source,endpoint,reason):
        allowed=(source=='KIS' and endpoint in ('/oauth2/tokenP',Q+'inquire-daily-indexchartprice',Q+'investor-trade-by-stock-daily',Q+'inquire-daily-itemchartprice')) or (source=='KRX' and endpoint in ('/svc/apis/sto/stk_bydd_trd','/svc/apis/sto/ksq_bydd_trd'))
        if not allowed:raise Failure('market_stage_forbids_endpoint')
        return super().before(source,endpoint,reason)


class FixedProviders(Providers):
    def fetch(self,t):
        if t not in market_tasks():raise Failure('market_task_outside_three_symbol_plan')
        return super().fetch(t)


def crosscheck_price(store,t,e,price):
    state=store.read('state.json')['tasks']
    ft=task('flow',ASOF,symbol=t['symbol'],market=t['market']);kt=task('krx',ASOF,market=t['market'])
    fe=state[sha(encoded(ft))]['evidence'];ke=state[sha(encoded(kt))]['evidence']
    flow=parse(store.raw(fe),fe)[0]
    official=[r for r in parse(store.raw(ke),ke) if r['ISU_CD']==t['symbol']]
    if len(official)!=1:raise Failure('KRX_symbol_missing_or_duplicate')
    diffs={k:dict(kis=price[k],krx=integer(official[0][field]),delta=price[k]-integer(official[0][field])) for k,field in KRX_FIELDS.items() if price[k]!=integer(official[0][field])}
    code='KIS_KRX_six_field_conflict'
    if not diffs:
        code='flow_price_conflict'
        diffs={k:dict(flow=flow[k],price=price[k],delta=flow[k]-price[k]) for k in ('close','volume','turnover') if flow[k]!=price[k]}
    if diffs:
        error=Failure(code);error.details=dict(symbol=t['symbol'],market=t['market'],asof=ASOF,differences=diffs,source_hashes=[e['source_hash'],fe['source_hash'],ke['source_hash']]);raise error


def check_database(folder,rows):
    import duckdb
    output=json.loads((folder/'analysis.json').read_bytes())
    with duckdb.connect(str(folder/'data.duckdb'),read_only=True) as db:
        saved=[json.loads(r[0]) for r in db.execute('SELECT payload FROM observations').fetchall()]
    byid=lambda values:{r['identity']:r for r in values}
    if len(saved)!=len(rows) or len(byid(saved))!=len(rows) or byid(saved)!=byid(output) or byid(output)!=byid(rows):
        raise Failure('DB_JSON_value_mismatch')


def collect():
    c=trial.control();stage_guard(c)
    pre=json.loads((trial.ADMIN/'preflight.json').read_bytes())
    proof=json.loads((trial.ADMIN/'market-restoration.json').read_bytes())
    if pre['control']!=c or proof.get('control')!=c or proof.get('zip_sha256')!=ZIP_SHA or proof.get('artifact_id')!=ARTIFACT_ID or pre['source_commit']!=trial.git('rev-parse','HEAD') or proof.get('source_commit')!=pre['source_commit']:
        raise Failure('restoration_preflight_binding_mismatch')
    archive=trial.ADMIN/'master-artifact.zip'
    if sha(archive.read_bytes())!=ZIP_SHA:raise Failure('artifact_zip_sha256_mismatch')
    with zipfile.ZipFile(archive) as z:
        if z.read('state-manifest.json')!=(trial.ROOT/'state-manifest.json').read_bytes():raise Failure('restored_manifest_not_from_pinned_zip')
    store=Store(trial.ROOT);budget=None;api=None;started=now();result=dict(status='BLOCKED')
    with store.lock():
        original=verify_master(store,c)  # Before credentials, token acquisition or any HTTP.
        store.write('prior-receipts/'+MASTER_RUN+'.json',original)
        try:
            presence={n:bool(os.getenv(n)) for n in trial.SECRET_NAMES};result['secret_presence']=presence
            if not all(presence[n] for n in ('KIS_APP_KEY','KIS_APP_SECRET','KRX_API_KEY')):raise Failure('market_credentials_missing')
            budget=MarketBudget(store);api=FixedProviders(store,budget)
            state=store.read('state.json')
            for t in market_tasks():
                key=sha(encoded(t));e=None
                try:
                    budget.check();e=api.fetch(t)
                    entry=dict(task=t,collection_status='received',validation_status='pending',evidence=e)
                    state['tasks'][key]=entry;store.write('state.json',state)
                    values=parse(store.raw(e),e)
                    if t['kind']=='flow' and values[0]['institution_net_quantity'] is None:raise Failure('institution_quantity_unverified')
                    if t['kind']=='price':crosscheck_price(store,t,e,values[0])
                    entry['validation_status']='PASS';store.write('state.json',state)
                except (Failure,Stop,ValueError,KeyError,TypeError) as exc:
                    code=exc.code if isinstance(exc,Failure) else str(exc) if isinstance(exc,Stop) else 'source_schema_mismatch'
                    if e is None:state['tasks'][key]=dict(task=t,collection_status='blocked',validation_status='NOT_RUN')
                    else:state['tasks'][key]['validation_status']='HOLD'
                    state['tasks'][key]['error']=code;store.write('state.json',state)
                    store.write('validation-failure.json',dict(task=t,reason=code,details=getattr(exc,'details',None),source_hash=e['source_hash'] if e else None))
                    raise Failure(code) from None
            tasks=master_tasks()+market_tasks()
            rows,quality=validate(store,tasks)
            if quality['held']:raise Failure('market_quality_hold')
            generation=sha(encoded(dict(stage='market',asof=ASOF,sources=quality['source_hashes'])))
            publish(store,generation,rows,quality,verify=check_database)
            # Same generation again must not add rows or create a second data version.
            publish(store,generation,rows,quality,verify=check_database)
            result.update(status='PASS',generation=generation,db_json_match=True,idempotent_storage=True,validated_symbols=3,provider_finality='UNVERIFIED')
        except (Failure,Stop) as exc:
            result.update(status='BLOCKED' if isinstance(exc,Stop) or exc.code in ('market_credentials_missing','authentication_failure','network_failure','network_policy_denied') else 'FAIL',reason=exc.code if isinstance(exc,Failure) else str(exc))
        except Exception:
            result.update(status='FAIL',reason='unexpected_market_or_storage_error')
        finally:
            result.update(stage='market',asof=ASOF,trial_id=c['trial_id'],request_id=c['request_id'],github_run_id=os.environ['GITHUB_RUN_ID'],source_commit=pre['source_commit'],started_at=started,completed_at=now(),master_run=MASTER_RUN,master_http=4,physical_http_this_run=budget.used if budget else 0,total_http=len(store.read('requests.json',[])),stage2_executed=bool(budget and budget.used))
            before=json.loads((trial.ADMIN/'snapshot-before.json').read_bytes());after={str(p):sha(p.read_bytes()) for p in Path('snapshot').rglob('*') if p.is_file()}
            result['snapshot_unchanged']=before==after
            if before!=after:result.update(status='FAIL',reason='operating_snapshot_changed')
            store.write('receipt.json',result)
            try:trial.secret_scan(store,api)
            except Failure:
                (trial.ADMIN/'DO_NOT_UPLOAD_STATE').touch();raise
            trial.seal(store)
        print(json.dumps({k:result.get(k) for k in ('status','reason','asof','physical_http_this_run','total_http','validated_symbols')}))
        return 0 if result['status']=='PASS' else 1


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['restore','collect']);a=p.parse_args()
    try:return restore() if a.mode=='restore' else collect()
    except (Failure,Stop,ValueError,KeyError,OSError,zipfile.BadZipFile) as e:
        trial.ADMIN.mkdir(parents=True,exist_ok=True)
        code=e.code if isinstance(e,Failure) else 'artifact_or_state_restore_blocked'
        (trial.ADMIN/'blocked.json').write_bytes(encoded(dict(status='BLOCKED',stage='market',reason=code,stage2_executed=False)))
        print(json.dumps(dict(status='BLOCKED',reason=code)));return 1


if __name__=='__main__':raise SystemExit(main())
