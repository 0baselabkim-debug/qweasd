"""Finite read-only GitHub reproduction and compact artifact preservation.

Only GitHub's own API is used. No market/OpenAI credentials, schedules, operating
writes, or shared-account ownership. Each expanded derived job is temporary;
its complete verified JSON/DB/Parquet bytes survive in a checked tar.gz.
"""
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.historical_v2.assets import extract_verified,file_sha
from screener.historical_v2.collector import inspect
from screener.historical_v2.calendar import build as calendar_build
from screener.historical_v2.price_pool import build as pool_build
from screener.decision_support.evidence import encoded,sha
from scripts.reprocess_historical_v2 import execute

REPO='0baselabkim-debug/qweasd'


def download(path,destination):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as f:
        r=subprocess.run(['gh','api','repos/'+REPO+'/'+path,'-H','Accept: application/vnd.github.raw+json'],stdout=f,stderr=subprocess.PIPE,timeout=180)
    if r.returncode:raise ValueError('official_GitHub_download_failed')


def packed_job(args):
    job,config,output=args;output=Path(output)
    with tempfile.TemporaryDirectory(prefix='historical-derived-',dir=os.environ['RUNNER_TEMP']) as tmp:
        local=dict(config,output_root=tmp);r=execute((job,local))
        if r['status']!='PASS_OFFLINE_REPROCESSING':return r
        folder=Path(tmp)/job['id'];expected={str(p.relative_to(folder)):file_sha(p) for p in folder.rglob('*') if p.is_file()}
        target=output/(job['id']+'.tar.gz')
        with target.open('xb') as out:
            with tarfile.open(fileobj=out,mode='w:gz',compresslevel=6) as tar:
                for name in sorted(expected):tar.add(folder/name,arcname=name,recursive=False)
        with tarfile.open(target,'r:gz') as tar:
            actual={}
            for entry in tar:
                if not entry.isfile():raise ValueError('non_file_in_derived_archive')
                f=tar.extractfile(entry);actual[entry.name]=sha(f.read())
        if actual!=expected:raise ValueError('derived_archive_restore_hash_mismatch')
        return dict(status=r['status'],job=job['id'],asof=r['asof'],source_kind=r['source_kind'],archive=target.name,
            archive_sha256=file_sha(target),bytes=target.stat().st_size,files_verified=len(actual),analysis_mode='RETROSPECTIVE_REANALYSIS',
            evidence_dataset_id=r['evidence']['dataset_id'],investment_dataset_id=r['investment']['dataset_id'],
            evidence_validation=r['evidence']['validation'],investment_validation=r['investment']['validation'],
            coverage=r['evidence']['coverage'],quality=r['investment']['quality'],code_sha256=r['code_sha256'],
            original_sources_unchanged=True,restoration='safe extract into a NEW directory; verify manifests and all three formats before use')


def main():
    c=json.loads(Path('.github/recovery/historical-v2-validation.json').read_bytes())
    now=dt.datetime.now(dt.timezone.utc)
    if (os.getenv('GITHUB_REPOSITORY')!=REPO or os.getenv('GITHUB_REF')!='refs/heads/feat/decision-evidence-v2'
            or os.getenv('GITHUB_RUN_ATTEMPT')!='1' or c['mode'] not in ('OFFLINE_REPRODUCE_ALL_PRESERVED_SESSIONS','OFFLINE_INPUT_REVALIDATION_ONLY')
            or not dt.datetime.fromisoformat(c['not_before'])<=now<dt.datetime.fromisoformat(c['expires_at'])):raise ValueError('finite_offline_control_rejected')
    anchors=Path('reports/historical-v2-recovery/source-anchors.json').read_bytes()
    if sha(anchors)!=c['anchors_sha256']:raise ValueError('native_source_anchors_changed')
    anchors=json.loads(anchors);root=Path(os.environ['RUNNER_TEMP'])/'historical-inputs';root.mkdir()
    output=Path(os.environ['RUNNER_TEMP'])/'historical-results';output.mkdir()
    source=Path('preserved-main/snapshot');published=root/'published';published.mkdir()
    for n,h in anchors['published_files'].items():
        raw=(source/n).read_bytes()
        if sha(raw)!=h:raise ValueError('pinned_published_source_changed')
        (published/n).write_bytes(raw)
    refs=root/'references';refs.mkdir();rmeta=anchors['reference_artifact'];archive=root/'references.zip'
    download('actions/artifacts/'+rmeta['artifact_id']+'/zip',archive)
    if file_sha(archive)!=rmeta['zip_sha256']:raise ValueError('reference_artifact_digest_mismatch')
    import zipfile
    found={};permitted=anchors['reference_objects']
    with zipfile.ZipFile(archive) as z:
        for n in z.namelist():
            if not n.startswith('objects/') or not n.endswith('.gz'):continue
            obj=json.loads(gzip.decompress(z.read(n)))
            if not obj.get('url','').startswith('https://data-dbg.krx.co.kr/'):continue
            raw=gzip.compress(encoded(obj),mtime=0);h=sha(raw)
            if h not in permitted:continue
            p=refs/(h+'.json.gz');p.write_bytes(raw);found[h]=dict(permitted[h],file=p.name)
    if set(found)!=set(permitted):raise ValueError('reference_population_missing_or_changed')
    (refs/'source-manifest.json').write_bytes(encoded(dict(references=found,auxiliary={})))
    sources=[];masters={}
    for a in anchors['collectors']:
        path=root/(a['artifact_id']+'.zip');download('actions/artifacts/'+a['artifact_id']+'/zip',path)
        folder=root/'collectors'/a['artifact_id'];extract_verified(path,a['zip_sha256'],folder)
        proof={k:a[k] for k in ['artifact_id','run_id','zip_sha256','manifest_sha256','report_path','universe_path']}
        if (folder/'master-receipt.json').exists():
            origin=json.loads((folder/'restoration.json').read_bytes());identifier=str(origin['artifact_id'])
            meta=next((m for m in anchors.get('master_artifacts',[]) if m['artifact_id']==identifier),None)
            if meta is None or meta['zip_sha256']!=origin['zip_sha256']:raise ValueError('native_master_origin_anchor_missing')
            if identifier not in masters:
                mzip=root/('master-'+identifier+'.zip');download('actions/artifacts/'+identifier+'/zip',mzip)
                md=root/'masters'/identifier;extract_verified(mzip,meta['zip_sha256'],md);masters[identifier]=md
            proof['external_master_directory']=str(masters[identifier])
        verified=inspect(folder,proof)
        sources.append(dict(artifact_id=a['artifact_id'],asof=a['asof'],directory=str(folder),proof=proof,status='INTEGRITY_PASS',complete=verified['complete']))
    calendar=calendar_build(refs,source,sources)
    if calendar['days']!=anchors['session_days']:raise ValueError('native_session_set_changed')
    cp=root/'session-calendar.json';cp.write_bytes(encoded(calendar));pool=pool_build(sources,refs,root/'price-pool',now.isoformat(),legacy_snapshot=source)
    if file_sha(root/'price-pool/bars.json.gz')!=anchors['pool_bars_sha256']:raise ValueError('native_verified_pool_values_changed')
    common=dict(price_pool={k:pool[k] for k in ['directory','manifest_sha256']},calendar=dict(file=str(cp),sha256=sha(cp.read_bytes())))
    if c['mode']=='OFFLINE_INPUT_REVALIDATION_ONLY':
        from scripts.build_historical_outcomes import build as outcomes_build
        outcomes = outcomes_build(root/'price-pool',pool['manifest_sha256'],cp,sha(cp.read_bytes()),now.isoformat(),output/'retrospective-price-paths')
        (output/'input-validation.json').write_bytes(encoded(dict(status='PASS_CURRENT_STRICT_INPUT_GUARDS',run_id=os.environ['GITHUB_RUN_ID'],
            code_commit=os.environ['GITHUB_SHA'],sources=[dict(artifact_id=s['artifact_id'],asof=s['asof'],complete=s['complete'],master_raw_replayed=True) for s in sources],
            official_session_days=len(calendar['days']),verified_pool_bars=pool['bars'],verified_pool_bars_sha256=file_sha(root/'price-pool/bars.json.gz'),
            original_bytes_unchanged=True,retrospective_outcomes=dict(rows=outcomes['rows'],counts=outcomes['counts'],manifest_sha256=outcomes['manifest_sha256'],strategy_performance='NOT_RUN'),market_HTTP=0,oauth_HTTP=0,OpenAI_HTTP=0)))
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('Current strict historical input guards: PASS. Raw masters, physical budgets and failed-original states independently rechecked; no market or OAuth requests.\n')
        return
    jobs=[]
    for s in sources:
        proof=dict(s['proof'],asof=s['asof'],price_pool=common['price_pool'])
        if s['asof'] in calendar['days']:proof['calendar']=common['calendar']
        jobs.append(dict(id='collector-'+s['artifact_id'],directory=s['directory'],source_kind='historical-collector',proof=proof))
    for a in anchors['checkpoints']:
        folder=root/'checkpoints'/a['asof'];folder.mkdir(parents=True)
        download('git/blobs/'+a['git_blob'],folder/'bundle.json.gz')
        if file_sha(folder/'bundle.json.gz')!=a['bundle_sha256']:raise ValueError('checkpoint_Git_bytes_changed')
        jobs.append(dict(id='checkpoint-'+a['asof'].replace('-',''),directory=str(folder),source_kind='historical-checkpoint',proof=dict(a,**common)))
    for day in calendar['days']:
        jobs.append(dict(id='research-'+day.replace('-',''),directory=str(published),source_kind='historical-publication',proof=dict(asof=day,manifest_sha256=anchors['published_files']['manifest.json'],source_commit=anchors['main_commit'],**common)))
    if len(jobs)!=143:raise ValueError('native_job_population_changed')
    config=dict(reference_directory=str(refs),cutoff=now.isoformat())
    results=[]
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
            for r in executor.map(packed_job,[(j,config,output) for j in jobs]):
                results.append(r)
                (output/'results.json').write_bytes(encoded(dict(results=results,completed=len(results),total=len(jobs),market_HTTP=0,OpenAI_HTTP=0)))
                print(json.dumps({k:r.get(k) for k in ['job','asof','status','reason']},ensure_ascii=False),flush=True)
        status='PASS_OFFLINE_REPRODUCTION' if all(r['status']=='PASS_OFFLINE_REPROCESSING' for r in results) else 'FAIL'
    finally:
        shutil.copyfile(cp,output/'session-calendar.json');shutil.copyfile(root/'price-pool/manifest.json',output/'price-pool-manifest.json')
        shutil.copyfile(root/'price-pool/bars.json.gz',output/'verified-bars.json.gz')
        (output/'native-origin.json').write_bytes(encoded(dict(run_id=os.environ['GITHUB_RUN_ID'],commit=os.environ['GITHUB_SHA'],main_commit=anchors['main_commit'],
            anchors_sha256=c['anchors_sha256'],market_HTTP=0,oauth_HTTP=0,OpenAI_HTTP=0,operating_writes=False,source_artifacts=anchors['collectors'])))
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('Historical V2 offline reproduction\n\n'+str(len(results))+'/'+str(len(jobs))+' jobs completed; market/OAuth/OpenAI HTTP 0.\n\n')
            for r in results:f.write(r['job']+': '+r['status']+'\n\n')
    if status!='PASS_OFFLINE_REPRODUCTION':raise SystemExit(1)


if __name__=='__main__':
    try:main()
    except Exception as error:
        output=Path(os.environ.get('RUNNER_TEMP','/tmp'))/'historical-results';output.mkdir(parents=True,exist_ok=True)
        (output/'FAILURE.json').write_bytes(encoded(dict(status='FAIL',reason=str(error) if isinstance(error,ValueError) else type(error).__name__,
            market_HTTP=0,oauth_HTTP=0,OpenAI_HTTP=0,operating_writes=False)))
        raise SystemExit(1)
