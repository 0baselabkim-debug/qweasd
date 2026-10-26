"""GitHub orchestration for exactly four dated rounds. No repository data writes."""
import argparse,datetime as dt,gzip,hashlib,json,os,re,subprocess,zipfile
from pathlib import Path,PurePosixPath
from ..core import Failure,encoded,sha,now
from .policy import *

REPO='0baselabkim-debug/qweasd'
WORKFLOW='full-market-experiment.yml'
ROOT=Path('data/full-market-20261026')
RATE_URL='https://raw.githubusercontent.com/koreainvestment/open-trading-api/main/examples_user/kis_auth.py'
RATE_SHA='2c23617eb9829934dc14f530cfae126426676919b630a07aa2a4832682ffdb9f'


def api(path,method='GET',data=None,binary=None):
    args=['gh','api',path,'--method',method]
    if data is not None:
        args+=['--input','-'];payload=encoded(data)
    else:payload=None
    if binary:
        with Path(binary).open('wb') as out:r=subprocess.run(args,input=payload,stdout=out,stderr=subprocess.PIPE)
    else:r=subprocess.run(args,input=payload,capture_output=True)
    if r.returncode:raise Failure('GitHub_API_or_artifact_access_blocked')
    return None if binary else json.loads(r.stdout or b'{}')


def safe_extract(path,target,digest):
    if sha(path.read_bytes())!=digest:raise Failure('artifact_zip_digest_mismatch')
    if target.exists():raise Failure('artifact_restore_would_overwrite_state')
    target.mkdir(parents=True);total=0;seen=set()
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            p=PurePosixPath(i.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in i.filename or i.filename in seen or (i.external_attr>>16)&0o170000==0o120000:raise Failure('unsafe_archive')
            seen.add(i.filename);total+=i.file_size
            if total>160_000_000:raise Failure('artifact_storage_cap_exceeded')
            q=target.joinpath(*p.parts)
            if i.is_dir():q.mkdir(parents=True,exist_ok=True)
            else:q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(z.read(i))


def inventory(root):return {str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file() and p.name not in ('manifest.json','writer.lock')}

def verify(root):
    m=json.loads((root/'manifest.json').read_bytes())
    if inventory(root)!=m['files']:raise Failure('restored_round_hash_mismatch')
    return m


def seed():
    from ..market_trial import ARTIFACT_ID,ZIP_SHA,MASTER_RUN
    from ..core import Store
    from ..validation import parse
    meta=api(f'repos/{REPO}/actions/artifacts/{ARTIFACT_ID}')
    from ..market_trial import metadata_guard
    metadata_guard(meta)
    if meta['expired'] or meta['digest']!='sha256:'+ZIP_SHA or str(meta['workflow_run']['id'])!=MASTER_RUN:raise Failure('master_artifact_identity_mismatch')
    archive=ROOT/'master.zip';api(f'repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip',binary=archive)
    folder=ROOT/'seed';safe_extract(archive,folder,ZIP_SHA)
    from ..trial import inventory as old_inventory
    s=Store(folder);m=s.read('state-manifest.json');r=s.read('receipt.json')
    if old_inventory(folder)!=m['files'] or len(m['files'])!=11 or (r['github_run_id'],r['status'],r['asof'],r['total_http'])!=(MASTER_RUN,'PASS',ASOF,4):raise Failure('master_seed_integrity_failed')
    ledger=s.read('requests.json',[])
    if r.get('stage')!='master' or len(ledger)!=4 or any(x.get('http')!=200 or x.get('status')!='received' for x in ledger):raise Failure('seed_request_ledger_mismatch')
    rows=[]
    for entry in s.read('state.json')['tasks'].values():
        e=entry['evidence'];values=parse(s.raw(e),e)
        if e['task']['kind']=='master':rows.extend(values)
    if len(rows)!=3950 or len({r['symbol'] for r in rows})!=3950 or sum(r['eligible'] for r in rows)!=2349:raise Failure('master_seed_population_mismatch')
    return rows


def history():
    current=api(f'repos/{REPO}/actions/runs/{os.environ["GITHUB_RUN_ID"]}')
    if current['path']!='.github/workflows/'+WORKFLOW:raise Failure('wrong_experiment_workflow')
    data=api(f'repos/{REPO}/actions/workflows/{current["workflow_id"]}/runs?per_page=100')
    if len(data['workflow_runs'])>=100:raise Failure('workflow_history_pagination_required')
    return current,data['workflow_runs']


def run_round_id(run):
    m=re.fullmatch(EXPERIMENT_ID+r' R([1-4])',run.get('display_title',''))
    return int(m[1]) if m else None


def latest(runs,round_id):
    matches=[r for r in runs if run_round_id(r)==round_id and str(r['id'])!=os.environ['GITHUB_RUN_ID']]
    return max(matches,key=lambda r:int(r['id'])) if matches else None


def restore_round(run,target):
    data=api(f'repos/{REPO}/actions/runs/{run["id"]}/artifacts?per_page=100')
    matches=[a for a in data['artifacts'] if a['name'].startswith(EXPERIMENT_ID+'-R') and not a['expired']]
    if len(matches)!=1:raise Failure('latest_round_artifact_missing_unknown_requests_reserved_to_full_limit')
    a=matches[0];path=ROOT/(str(a['id'])+'.zip');api(f'repos/{REPO}/actions/artifacts/{a["id"]}/zip',binary=path)
    if not a.get('digest','').startswith('sha256:'):raise Failure('artifact_digest_not_available')
    safe_extract(path,target,a['digest'][7:]);m=verify(target)
    if str(m['run_id'])!=str(run['id']) or m['asof']!=ASOF or m['round']!=run_round_id(run):raise Failure('wrong_round_receipt')
    return json.loads((target/'report.json').read_bytes())


LIVE_STEP='Bounded fresh round and in-round recovery'
MISSING_ARTIFACT='latest_round_artifact_missing_unknown_requests_reserved_to_full_limit'


def zero_provider_proof(run):
    # Only an official completed job record with the sole provider step skipped
    # proves zero. Lost artifacts after a started provider step remain unknown.
    if run.get('status')!='completed' or run.get('path')!='.github/workflows/'+WORKFLOW or run.get('head_branch')!='main':raise Failure('zero_call_proof_unavailable')
    data=api(f'repos/{REPO}/actions/runs/{run["id"]}/jobs?filter=all&per_page=100')
    jobs=data['jobs']
    if data.get('total_count',100)>=100 or len(jobs)!=data.get('total_count'):raise Failure('zero_call_proof_incomplete_jobs')
    live=[]
    for job in jobs:
        if job['name']!='observe':continue
        steps=[x for x in job.get('steps',[]) if x['name']==LIVE_STEP]
        if job.get('status')!='completed' or len(steps)!=1 or steps[0].get('status')!='completed' or steps[0].get('conclusion')!='skipped':raise Failure('zero_call_proof_provider_may_have_started')
        live.append({'job_id':job['id'],'provider_step':'skipped'})
    if not live:raise Failure('zero_call_proof_missing_observe_job')
    return dict(run_id=run['id'],physical_http=0,proof='official_jobs_all_attempts_provider_step_skipped',jobs=live)


def restore_or_zero(run,target):
    try:return restore_round(run,target)
    except Failure as e:
        if e.code!=MISSING_ARTIFACT:raise
        proof=zero_provider_proof(run)
        ROOT.mkdir(parents=True,exist_ok=True)
        (ROOT/('zero-provider-'+str(run['id'])+'.json')).write_bytes(encoded(proof))
        return dict(status='PREPARATION_FAILED',physical_http=0,recovery_possible=True,zero_provider_proof=proof)


def compact_comparison(value):
    if isinstance(value,list):return [compact_comparison(v) for v in value]
    if not isinstance(value,dict):return value
    out={}
    for key,item in value.items():
        if key in ('changed','unavailable','status_changed') and isinstance(item,list):out[key+'_count']=len(item)
        elif key=='changed_by_category':out[key]={k:len(v) for k,v in item.items()}
        else:out[key]=compact_comparison(item)
    return out


def issue_report(report,kind='round'):
    # Explicitly user-authorized experiment reports; no credentials or raw bodies.
    title='['+EXPERIMENT_ID+'] 네 회차 수집·복구·안정성 보고'
    items=api(f'repos/{REPO}/issues?state=all&per_page=100')
    match=next((i for i in items if i['title']==title and 'pull_request' not in i),None)
    if not match:match=api(f'repos/{REPO}/issues','POST',{'title':title,'body':'2026-10-26 일회성 실험. 공급자 최종성은 UNVERIFIED입니다. 모든 회차 결과 및 차단 사유를 여기에 기록합니다.'})
    keys=('round','asof','status','scheduled_at','actual_started_at','completed_at','elapsed_seconds','target','received','validated','failed_or_held','physical_http','retries','reason','comparison','next_profile','physical_http_reserved_limit','stability_notice','recovery_possible','recovery','bottleneck','pure_api_seconds','symbols_per_minute')
    summary={k:compact_comparison(report[k]) if k=='comparison' else report[k] for k in keys if k in report}
    if report.get('errors'):summary['first_errors']=report['errors'][:3]
    body='### '+kind+'\n\n```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n\nRun: https://github.com/'+REPO+'/actions/runs/'+os.environ['GITHUB_RUN_ID']
    api(f'repos/{REPO}/issues/{match["number"]}/comments','POST',{'body':body})
    return match['html_url']


def optimize(root):
    import time
    samples=list((root/'raw').glob('*.gz'))[:100]
    if len(samples)<100:return {'gzip_level':9,'profile':'baseline','reason':'insufficient_actual_calibration_samples'}
    raw=[gzip.decompress(p.read_bytes()) for p in samples];results={}
    for level in (9,1):
        start=time.monotonic();packed=[gzip.compress(b,compresslevel=level,mtime=0) for b in raw]
        if any(gzip.decompress(a)!=b for a,b in zip(packed,raw)):raise Failure('compression_profile_not_lossless')
        results[level]={'seconds':time.monotonic()-start,'bytes':sum(map(len,packed))}
    use=results[1]['seconds']<results[9]['seconds']*.95 and results[1]['bytes']<=results[9]['bytes']*1.25
    return dict(gzip_level=1 if use else 9,profile='verified_lossless_compression' if use else 'baseline',calibration=results,api_interval=INTERVAL,
                reason='Only local storage calibration verified; end-to-end improvement requires the next real round')


def prepare(round_id):
    window(round_id)
    if os.getenv('GITHUB_REPOSITORY')!=REPO or os.getenv('GITHUB_REF')!='refs/heads/main' or os.getenv('GITHUB_RUN_ATTEMPT')!='1':raise Failure('wrong_context_or_untracked_rerun')
    current,runs=history();prior=latest(runs,round_id);folder=ROOT/'round';round_profile=None
    if prior and prior['status']=='queued' and int(prior['id'])>int(current['id']):prior=None
    if prior:
        if prior['status']!='completed':raise Failure('same_round_already_active')
        previous=restore_or_zero(prior,folder)
        round_profile=previous.get('config')
        if previous.get('code_commit') and previous['code_commit']!=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip():raise Failure('same_round_code_change_requires_review')
        (ROOT/'recovery-parent.json').write_bytes(encoded({'run_id':prior['id'],'restored_http':previous['physical_http']}))
        if previous['status']=='COMPLETE':raise Failure('round_already_complete_no_fresh_repeat')
        if previous['physical_http']>=ROUND_LIMIT:raise Failure('round_budget_already_spent')
    ROOT.mkdir(parents=True,exist_ok=True)
    if not folder.exists():folder.mkdir()
    universe=seed()
    import urllib.request
    with urllib.request.urlopen(RATE_URL,timeout=20) as response:raw=response.read(2000000)
    if sha(raw)!=RATE_SHA or b'_smartSleep = 0.5' not in raw:raise Failure('official_rate_evidence_changed_or_missing')
    proof=dict(status='PASS',interval_seconds=INTERVAL,source_url=RATE_URL,source_sha256=RATE_SHA,
               scope='Below official live and paper sample intervals; no daily-unlimited quota claim')
    (ROOT/'rate-proof.json').write_bytes(encoded(proof));(ROOT/'universe.json').write_bytes(encoded(universe))
    snapshot={str(p):sha(p.read_bytes()) for p in Path('snapshot').rglob('*') if p.is_file()}
    (ROOT/'snapshot-before.json').write_bytes(encoded(snapshot))
    # Durable reservation announcement: lost artifact => never reset an unknown run to zero.
    issue_report(dict(round=round_id,status='RESERVED',asof=ASOF,physical_http_reserved_limit=ROUND_LIMIT,reason='Before any provider HTTP; missing final artifact blocks automatic recovery'),'reservation')
    profile=round_profile or {'gzip_level':9,'profile':'baseline'}
    if round_id>1:
        p=latest(runs,round_id-1)
        if p and p['status']=='completed':
            try:
                previous=restore_round(p,ROOT/'previous')
                if round_profile is None:profile=previous.get('next_profile',profile)
            except Failure:profile['reason']='previous_round_not_available; preserve current verified profile'
    (ROOT/'profile.json').write_bytes(encoded(profile))


def execute(round_id):
    from .engine import run_round
    from .compare import compare
    from ..core import Store
    prepare(round_id)
    with Store(ROOT/'round').lock():
        r=run_round(ROOT/'round',round_id,json.loads((ROOT/'universe.json').read_bytes()),json.loads((ROOT/'rate-proof.json').read_bytes()),os.environ['GITHUB_RUN_ID'],config=json.loads((ROOT/'profile.json').read_bytes()))
        before=json.loads((ROOT/'snapshot-before.json').read_bytes());after={str(p):sha(p.read_bytes()) for p in Path('snapshot').rglob('*') if p.is_file()}
        r['code_commit']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();r['launcher_commit']=os.environ.get('GITHUB_SHA');r['config_sha256']=sha(encoded(r['config']))
        r['operating_snapshot_unchanged']=before==after
        if before!=after:r.update(status='UNRESOLVED',reason='operating_snapshot_changed')
        old=ROOT/'previous/report.json'
        r['comparison']=compare(json.loads(old.read_bytes()),r) if old.exists() else {'classification':'NOT_COMPARABLE','reason':'previous_round_not_available'}
        r['next_profile']=optimize(ROOT/'round')
        if r['comparison']['classification']=='COMPLETE_MATCH':r['stability_notice']='Provisional complete equality observed; provider finality UNVERIFIED; remaining rounds continue'
        (ROOT/'round/report.json').write_bytes(encoded(r))
        (ROOT/'round/manifest.json').write_bytes(encoded(dict(asof=ASOF,round=round_id,run_id=os.environ['GITHUB_RUN_ID'],files=inventory(ROOT/'round'))))
        r['issue_url']=issue_report(r,'provisional stability' if 'stability_notice' in r else 'round result')
        summary={k:compact_comparison(v) if k=='comparison' else v for k,v in r.items() if k not in ('rows','target_symbols','errors')}
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n')
        return 0 if r['status']=='COMPLETE' else 1


def finalize():
    current,runs=history();reports=[]
    for n in range(1,5):
        run=latest(runs,n)
        if n==4 and (ROOT/'round/report.json').exists():reports.append(json.loads((ROOT/'round/report.json').read_bytes()));continue
        if run and run['status']=='completed':
            try:reports.append(restore_round(run,ROOT/('final-R'+str(n))));continue
            except Failure:pass
        reports.append(dict(round=n,asof=ASOF,status='UNRESOLVED',reason='no_verified_completed_round_artifact'))
    from .analysis import aggregate
    analysis=aggregate(reports)
    report=dict(analysis=analysis,status='COMPLETE' if all(r['status']=='COMPLETE' for r in reports) else 'UNRESOLVED',asof=ASOF,rounds=reports,provider_finality='UNVERIFIED',operating_time_recommendation='Do not infer a stable collection time without complete comparable rounds')
    (ROOT/'final-report.json').write_bytes(encoded(report))
    try:issue_report({'status':report['status'],'asof':ASOF,'reason':report['operating_time_recommendation'],'comparison':[{'round':r['round'],'status':r['status'],'elapsed_seconds':r.get('elapsed_seconds'),'physical_http':r.get('physical_http'),'comparison':r.get('comparison')} for r in reports]},'final experiment report')
    finally:api(f'repos/{REPO}/actions/workflows/{current["workflow_id"]}/disable','PUT')


def watch():
    current,runs=history();now_=utcnow()
    if now_>=STARTS[-1]+dt.timedelta(hours=3):
        active=[r for r in runs if run_round_id(r) and r['status']!='completed']
        if active:return
        finalize();return
    for n,start in enumerate(STARTS,1):
        if not start+dt.timedelta(minutes=15)<=now_<start+dt.timedelta(hours=3,minutes=-10):continue
        previous=latest(runs,n)
        if previous and previous['status']!='completed':continue
        if previous:
            try:r=restore_or_zero(previous,ROOT/('watch-R'+str(n)))
            except Failure:
                issue_report(dict(round=n,asof=ASOF,status='UNRESOLVED',reason='missing_latest_artifact; unknown calls reserved to full round budget'),'watchdog');continue
            if r['status']=='COMPLETE' or r['physical_http']>=ROUND_LIMIT or not r.get('recovery_possible',False):continue
        api(f'repos/{REPO}/actions/workflows/{current["workflow_id"]}/dispatches','POST',{'ref':'main','inputs':{'round':str(n)}})


def seal_round(round_id):
    from .storage import events
    folder=ROOT/'round'
    if not folder.exists():return
    path=folder/'report.json'
    if not path.exists():
        try:used=sum(e['event']=='started' for e in events(folder/'http.jsonl'))
        except Exception:used=ROUND_LIMIT
        path.write_bytes(encoded(dict(round=round_id,asof=ASOF,status='UNRESOLVED',reason='process_or_preparation_interrupted',physical_http=used,rows=[],target_symbols=[],population_hash=None,observation_window={},next_profile={'gzip_level':9,'profile':'baseline'})))
    size=sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
    if size>80_000_000:raise Failure('artifact_size_cap_exceeded')
    (folder/'manifest.json').write_bytes(encoded(dict(round=round_id,asof=ASOF,run_id=os.environ['GITHUB_RUN_ID'],files=inventory(folder))))
    # Check current bound values only; no full environment or token dump.
    from ..trial import secret_scan
    from ..core import Store
    try:secret_scan(Store(folder),None)
    except Failure:
        (ROOT/'DO_NOT_UPLOAD').touch();(folder/'manifest.json').unlink(missing_ok=True);raise


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['run','watch','finalize','seal']);p.add_argument('--round',type=int);a=p.parse_args();ROOT.mkdir(parents=True,exist_ok=True)
    try:
        if a.mode=='seal':return seal_round(a.round)
        if a.mode=='run':return execute(a.round)
        if utcnow().date() not in (dt.date(2026,10,26),dt.date(2026,10,27)):raise Failure('outside_experiment_registration_date')
        return watch() if a.mode=='watch' else finalize()
    except (Failure,Exception) as e:
        code=e.code if isinstance(e,Failure) else 'experiment_execution_blocked'
        r=dict(asof=ASOF,round=a.round,status='UNRESOLVED',reason=code,provider_calls_not_assumed_zero=True,observed_at=now())
        (ROOT/'blocked.json').write_bytes(encoded(r));print(json.dumps(r))
        summary=os.getenv('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary,'a') as f:f.write('```json\n'+json.dumps(r,ensure_ascii=False,indent=2)+'\n```\n')
        try:issue_report(r,'orchestration blocked')
        except Exception:pass
        return 1


if __name__=='__main__':raise SystemExit(main())
