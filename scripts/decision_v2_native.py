"""Finite native artifact verification. No KIS credentials, requests or operating writes."""
import base64
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import gzip
import zipfile
import traceback
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.decision_evidence.collector_artifact import ARTIFACT,RUN,ZIP_SHA,ASOF,extract,inspect,GENERATION
from screener.decision_support.evidence import encoded,sha
REPO='0baselabkim-debug/qweasd'
REF='refs/heads/feat/decision-evidence-v2'
STATE='evidence/decision-v2-source-20261029'
CONTROL=Path('.github/trials/decision-v2-source-20261029.json')


def api(path,method='GET',value=None):
    args=['gh','api','repos/'+REPO+'/'+path,'--method',method]
    if value is not None:args+=['--input','-']
    p=subprocess.run(args,input=encoded(value) if value is not None else None,capture_output=True,timeout=60)
    if p.returncode:
        if b'HTTP 404' in p.stderr:raise FileNotFoundError(path)
        raise ValueError('official_GitHub_API_failed_'+str(p.returncode))
    return json.loads(p.stdout)


def publish(name,value):
    raw=encoded(value)
    if len(raw)>850000:raise ValueError('native_report_size_limit')
    if os.getenv('GH_TOKEN') and os.environ['GH_TOKEN'].encode() in raw:raise ValueError('credential_echo')
    try:api('git/ref/heads/'+STATE)
    except FileNotFoundError:
        tree=api('git/trees','POST',dict(tree=[dict(path='FORMAT.json',mode='100644',type='blob',content='{"scope":"independent-decision-v2-read-only-source-reports"}')]))
        commit=api('git/commits','POST',dict(message='Independent V2 evidence reports; no operating state',tree=tree['sha'],parents=[]))
        api('git/refs','POST',dict(ref='refs/heads/'+STATE,sha=commit['sha']))
    marker=api('contents/FORMAT.json?ref='+STATE)
    if json.loads(base64.b64decode(marker['content']))!={'scope':'independent-decision-v2-read-only-source-reports'}:
        raise ValueError('native_report_branch_marker_mismatch')
    api('contents/'+name,'PUT',dict(branch=STATE,message='Independent V2 artifact verification '+name,content=base64.b64encode(raw+b'\n').decode()))


def main():
    c=json.loads(CONTROL.read_bytes())
    if (os.getenv('GITHUB_REPOSITORY'),os.getenv('GITHUB_REF'),os.getenv('GITHUB_EVENT_NAME'),os.getenv('GITHUB_RUN_ATTEMPT'))!=(REPO,REF,'push','1'):
        raise ValueError('native_runtime_scope_mismatch')
    if (c['artifact_id'],c['source_run_id'],c['zip_sha256'],c['asof'])!=(ARTIFACT,RUN,ZIP_SHA,ASOF) or c['mode'] not in ('inspect','analyze'):
        raise ValueError('native_control_scope_mismatch')
    request=c['request_id']
    if not request.replace('-','').isalnum() or len(request)>80:raise ValueError('native_request_id_invalid')
    at=dt.datetime.now(dt.timezone.utc)
    if not dt.datetime.fromisoformat(c['not_before'])<=at<dt.datetime.fromisoformat(c['expires_at']):raise ValueError('native_window_closed')
    result=dict(request_id=request,run_id=os.environ['GITHUB_RUN_ID'],code_commit=os.environ['GITHUB_SHA'],
        market_HTTP=0,oauth_HTTP=0,source_run_id=RUN,artifact_id=ARTIFACT,verified_at=at.isoformat())
    publish(request+'-started.json',dict(result,status='STARTED'))
    folder=Path(tempfile.mkdtemp(prefix='decision-v2-source-'))
    try:
        meta=api('actions/artifacts/'+ARTIFACT)
        if (str(meta.get('workflow_run',{}).get('id')),meta.get('digest'),meta.get('expired'))!=(RUN,'sha256:'+ZIP_SHA,False):raise ValueError('official_artifact_metadata_mismatch')
        archive=folder/'source.zip'
        with archive.open('xb') as out:
            p=subprocess.run(['gh','api','repos/'+REPO+'/actions/artifacts/'+ARTIFACT+'/zip'],stdout=out,stderr=subprocess.PIPE,timeout=180)
        if p.returncode:raise ValueError('official_artifact_download_failed')
        proof=extract(archive,folder/'source')
        result.update(status='PASS_ARTIFACT_INSPECTION',artifact=meta,zip_verification=proof,inspection=inspect(folder/'source'))
        if c['mode']=='analyze':
            from screener.decision_evidence.build import build,verify_output
            from screener.decision_evidence.price_diagnosis import diagnose,save
            bundle=Path(os.environ['RUNNER_TEMP'])/'decision-v2-analysis';bundle.mkdir()
            cutoff=dt.datetime.now(dt.timezone.utc).isoformat()
            built=build(folder/'source',bundle/'derived',cutoff,source_kind='collector-artifact',reference_directory=Path('snapshot/decision-support'))
            directory=Path(built['directory']);market=json.loads(gzip.decompress((directory/'market.json.gz').read_bytes()))
            q=json.loads((directory/'quality.json').read_bytes())
            summary,details=diagnose(folder/'source',Path('snapshot'),cutoff);save(bundle,summary,details)
            checks=validate_analysis(folder/'source',market)
            shutil.copyfile(archive,bundle/'original-12910068161.zip')
            result.update(status='PASS_ANALYSIS_WITH_PRICE_HOLDS',analysis=built,analysis_quality=q,legacy_diagnosis=summary,
                independent_recalculation=checks,analysis_sample=[r for r in market['stocks'] if r['stock_code'] in ('000720','064400','300080')],
                provider_finality='UNVERIFIED',official_price_status='PENDING_KRX',source_cohort_mixed=False,
                price_reference_budget=price_budget(folder/'source'))
            (bundle/'native-result.json').write_bytes(encoded(result))
            inventory={str(p.relative_to(bundle)):sha(p.read_bytes()) for p in bundle.rglob('*') if p.is_file()}
            (bundle/'bundle-manifest.json').write_bytes(encoded(dict(files=inventory,source_zip_sha256=ZIP_SHA,
                dataset_id=built['dataset_id'],source_generation_id=GENERATION)))
            with open(os.environ['GITHUB_OUTPUT'],'a') as output:output.write('analysis_bundle=true\n')
    except Exception as exc:
        result.update(status='FAIL',reason=str(exc) if isinstance(exc,ValueError) else getattr(exc,'code',type(exc).__name__),
            locations=[dict(file=Path(t.filename).name,line=t.lineno,function=t.name) for t in traceback.extract_tb(exc.__traceback__)])
    (Path(os.environ['RUNNER_TEMP'])/'decision-v2-inspection.json').write_bytes(encoded(result))
    publish(request+'-complete.json',result)
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('# Independent V2 artifact inspection\n\n'+result['status']+'; KIS HTTP 0; OAuth 0\n')
    print(json.dumps({k:result[k] for k in ('status','run_id','market_HTTP','oauth_HTTP')}))
    return 0 if result['status'] in ('PASS_ARTIFACT_INSPECTION','PASS_ANALYSIS_WITH_PRICE_HOLDS') else 1


def validate_analysis(root,market):
    """Independent direct sums from original raw rows; no feature function reuse."""
    from collector_v1.core import Store
    from collector_v1.experiment.engine import replay,identity
    from collector_v1.providers import task
    from screener.decision_evidence.collector_source import saved_rows
    from screener.decision_support.evidence import date
    root=Path(root);r=json.loads((root/'report.json').read_bytes());store=Store(root/GENERATION/'collection')
    cache=replay(store,1,asof=ASOF,generation_id=GENERATION);original={row['symbol']:row for row in r['rows']}
    if set(original)!={row['stock_code'] for row in market['stocks']} or len(market['stocks'])!=len(original):raise ValueError('analysis_population_mismatch')
    calendars={}
    for m in ('KOSPI','KOSDAQ'):
        e=cache[identity(task('index',ASOF,round=1,market=m,generation_id=GENERATION))]
        calendars[m]=sorted(date(x['stck_bsop_date']) for x in json.loads(store.raw(e))['output2'])
    count=0;institution_checks=0;available={};latest=0
    for row in market['stocks']:
        code=row['stock_code'];m=row['market'];e=cache[identity(task('flow',ASOF,round=1,symbol=code,market=m,generation_id=GENERATION))]
        raw=saved_rows(json.loads(store.raw(e)),e,'flow',code,m)
        pe=cache[identity(task('price',ASOF,round=1,symbol=code,market=m,generation_id=GENERATION))]
        prices=saved_rows(json.loads(store.raw(pe)),pe,'price',code,m)
        qualified={}
        for day,value in raw.items():
            try:
                valid=(int(value['frgn_shnu_vol'])-int(value['frgn_seln_vol'])==int(value['frgn_ntby_qty'])
                    and min(int(value[k]) for k in ('frgn_shnu_vol','frgn_seln_vol','frgn_shnu_tr_pbmn','frgn_seln_tr_pbmn'))>=0
                    and abs(int(value['frgn_shnu_tr_pbmn'])-int(value['frgn_seln_tr_pbmn'])-int(value['frgn_ntby_tr_pbmn']))<=1
                    and day in prices and all(value[k]==prices[day][k] for k in ('stck_clpr','acml_vol','acml_tr_pbmn'))
                    and min(int(value[k]) for k in ('stck_clpr','acml_vol','acml_tr_pbmn'))>0)
                if valid:qualified[day]=int(value['frgn_ntby_tr_pbmn'])*1000000
            except (ValueError,KeyError):pass
        for n in (1,3,5,20):
            dates=calendars[m][-n:];values=[qualified[d] for d in dates if d in qualified]
            expected=sum(values) if len(values)==n else None;actual=row['foreign_flow_metrics'][f'net_{n}d']['value']
            if actual!=expected:raise ValueError('analysis_foreign_recalculation_mismatch')
            available[str(n)]=available.get(str(n),0)+(actual is not None);count+=1
        for n in (1,5,20):
            values=[]
            for day in calendars[m][-n:]:
                value=raw.get(day,{})
                try:
                    buy=int(value['orgn_shnu_vol']);sell=int(value['orgn_seln_vol']);net=int(value['orgn_ntby_qty'])
                    if min(buy,sell)<0 or buy-sell!=net:raise ValueError()
                    values.append(net)
                except (ValueError,KeyError):pass
            expected=sum(values) if len(values)==n else None
            if row['institution_flow_metrics'][f'net_quantity_{n}d']['value']!=expected:raise ValueError('analysis_institution_quantity_mismatch')
            institution_checks+=1
        if any(v['value'] is not None for k,v in row['institution_flow_metrics'].items() if not k.startswith('net_quantity_')):
            raise ValueError('analysis_institution_amount_promoted')
        if row['data_quality']['latest_price_verified']:latest+=1
        if row['data_quality']['historical_backtest_eligible'] is not False:raise ValueError('analysis_historical_eligibility_promoted')
    return dict(stock_population_match=True,stocks=len(original),foreign_window_checks=count,
        foreign_available=available,latest_price_approved=latest,approval_scope='exact_KRX_six_fields_only',
        institution_quantity_checks=institution_checks,source_generation_id=GENERATION,historical_backtest_eligible=False)


def price_budget(root):
    """Read-only quota/window review; never reserves or sends any market request."""
    from zoneinfo import ZoneInfo
    from scripts.collect_decision_prices import guard,TOTAL_LIMIT
    from collector_v1.core import Store
    from collector_v1.experiment.engine import replay,identity
    from collector_v1.providers import task
    from screener.decision_support.evidence import date
    ref=api('git/ref/heads/evidence/decision-price-source')['object']['sha']
    v=api('contents/auxiliary-prices/requests.json?ref='+ref);journal=json.loads(base64.b64decode(v['content']))
    v=api('contents/auxiliary-prices/index.json?ref='+ref);index=json.loads(base64.b64decode(v['content']))
    store=Store(Path(root)/GENERATION/'collection');cache=replay(store,1,asof=ASOF,generation_id=GENERATION);missing=[]
    for market in ('KOSPI','KOSDAQ'):
        e=cache[identity(task('index',ASOF,round=1,market=market,generation_id=GENERATION))]
        days=sorted(date(r['stck_bsop_date']) for r in json.loads(store.raw(e))['output2'])
        missing.extend(dict(asof=d,market=market) for d in days if 'krx:'+market+':'+d not in index['objects'])
    requests=journal['requests'];unresolved=[v for v in journal.get('leases',{}).values() if v.get('status')=='reserved']
    spent=sum(r.get('budget_class')!='routine_reference' for r in requests)
    spent+=sum(r['maximum_unrecorded_requests'] for r in journal.get('unrecovered_runs',[]))
    spent+=sum(r['limit'] for r in unresolved if r.get('budget_class')!='routine_reference')
    try:guard();window='OPEN'
    except ValueError as exc:window=str(exc)
    today=dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    day_spent={}
    for kind,limit in (('routine_reference',4),('daily_priority',40)):
        used=sum(r.get('budget_class')==kind and r.get('budget_date')==today for r in requests)
        used+=sum(r['limit'] for r in unresolved if r.get('budget_class')==kind and r.get('budget_date')==today)
        day_spent[kind]=dict(KST_date=today,used_or_reserved=used,daily_limit=limit,remaining=limit-used,
            maximum_per_run=2 if kind=='routine_reference' else 20,whole_8_request_batch_fits=used+len(missing)<=limit and len(missing)<=(2 if kind=='routine_reference' else 20))
    return dict(status='REVIEW_ONLY_NO_HTTP',journal_commit=ref,recorded_physical_requests=len(requests),
        existing_bootstrap_limit=TOTAL_LIMIT,existing_bootstrap_used_or_reserved=spent,remaining=TOTAL_LIMIT-spent,
        missing_reference_tasks=missing,expected_KRX_GETs=len(missing),authentication_requests=0,automatic_retries=0,
        fits_existing_total_budget=spent+len(missing)<=TOTAL_LIMIT,existing_window=window,
        separate_existing_daily_classes=day_spent,budget_policy_source='scripts.collect_decision_prices.Budget; existing daily classes are separate from bootstrap',
        KRX_key_injected=False,actual_KRX_HTTP=0,actual_KIS_HTTP=0,
        execution_policy='No market calls in this workflow. Existing time/quota guards are preserved; no new root or budget reset.')


def finalize():
    """Verify actual uploaded bytes, distinct from a successful runner calculation."""
    c=json.loads(CONTROL.read_bytes());result=json.loads((Path(os.environ['RUNNER_TEMP'])/'decision-v2-inspection.json').read_bytes())
    if result['status']!='PASS_ANALYSIS_WITH_PRICE_HOLDS':return 1
    artifact=os.environ['RESULT_ARTIFACT_ID'];digest=os.environ['RESULT_ARTIFACT_DIGEST'].removeprefix('sha256:')
    if not artifact.isdigit() or len(digest)!=64:raise ValueError('result_artifact_output_missing')
    meta=api('actions/artifacts/'+artifact)
    if (meta['digest'],str(meta['workflow_run']['id']),meta['expired'])!=('sha256:'+digest,os.environ['GITHUB_RUN_ID'],False):raise ValueError('result_artifact_identity_mismatch')
    bundle=Path(os.environ['RUNNER_TEMP'])/'decision-v2-analysis';expected=json.loads((bundle/'bundle-manifest.json').read_bytes())
    archive=bundle.parent/'verified-result.zip'
    with archive.open('wb') as out:p=subprocess.run(['gh','api','repos/'+REPO+'/actions/artifacts/'+artifact+'/zip'],stdout=out,stderr=subprocess.PIPE,timeout=180)
    if p.returncode or sha(archive.read_bytes())!=digest:raise ValueError('stored_result_ZIP_digest_mismatch')
    with zipfile.ZipFile(archive) as z:
        names=[i.filename for i in z.infolist() if not i.is_dir()]
        if len(names)!=len(set(names)) or set(names)!=set(expected['files'])|{'bundle-manifest.json'}:raise ValueError('stored_result_population_mismatch')
        if z.read('bundle-manifest.json')!=(bundle/'bundle-manifest.json').read_bytes():raise ValueError('stored_result_manifest_mismatch')
        if any(sha(z.read(name))!=h for name,h in expected['files'].items()):raise ValueError('stored_result_file_hash_mismatch')
    receipt=dict(status='PASS_STORED_ARTIFACT_VERIFIED_WITH_PRICE_HOLDS',run_id=os.environ['GITHUB_RUN_ID'],artifact=meta,
        uploaded_zip_sha256=digest,bundle_manifest_sha256=sha((bundle/'bundle-manifest.json').read_bytes()),files_verified=len(names),
        analysis_dataset_id=expected['dataset_id'],source_generation_id=GENERATION,market_HTTP=0,oauth_HTTP=0,
        official_price_status='PENDING_KRX',provider_finality='UNVERIFIED')
    publish(c['request_id']+'-artifact.json',receipt)
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('\nStored artifact verified: '+artifact+'; dataset '+expected['dataset_id']+'; PENDING_KRX\n')
    return 0

if __name__=='__main__':raise SystemExit(finalize() if len(sys.argv)>1 and sys.argv[1]=='finalize' else main())
