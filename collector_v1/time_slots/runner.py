"""One push arms a finite dependency graph. No cron, dispatch, issue or KRX calls."""
import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import time
from ..core import Budget, Failure, Stop, Store, encoded, now, sha
from ..experiment import runtime
from . import policy

ROOT = Path('data/market-stability-20261027')
REPORTS = 'reports/market-stability-20261027'


def configuration():
    c = json.loads(Path(policy.CONTROL).read_bytes())
    policy.validate_configuration(c)
    return c


def context():
    if (os.getenv('GITHUB_REPOSITORY'), os.getenv('GITHUB_REF'), os.getenv('GITHUB_EVENT_NAME'), os.getenv('GITHUB_RUN_ATTEMPT')) != (policy.REPO, policy.BRANCH, 'push', '1'):
        raise Failure('experiment_context_rejected')
    c = configuration()
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
    parent = subprocess.check_output(['git', 'rev-parse', 'HEAD^'], text=True).strip()
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if event.get('before') != parent or event.get('after') != head or any(event.get(k) for k in ('forced', 'created', 'deleted')):
        raise Failure('single_incremental_control_commit_required')
    if subprocess.check_output(['git', 'diff', '--name-only', parent, head], text=True).splitlines() != [policy.CONTROL]:
        raise Failure('only_experiment_control_may_change')
    subprocess.run(['git', 'merge-base', '--is-ancestor', policy.BASELINE, 'HEAD'], check=True)
    return c


def publish_file(relative, value, *, cached=None):
    raw = encoded(value)
    for key in ('KIS_APP_KEY', 'KIS_APP_SECRET', 'GH_TOKEN', 'GITHUB_TOKEN'):
        v = os.getenv(key)
        if v and v.encode() in raw:
            raise Failure('experiment_publication_credential_echo')
    body = dict(message='report: dated KIS experiment '+relative, content=base64.b64encode(raw).decode(), branch=policy.BRANCH.removeprefix('refs/heads/'))
    if cached:
        body['sha'] = cached
    response = runtime.api(f'repos/{policy.REPO}/contents/{REPORTS}/{relative}', 'PUT', body)
    return response['content']['sha']


def summary(text):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
        f.write(text+'\n')


def authorize():
    c = context()
    if policy.utcnow() >= policy.STARTS[0]:
        raise Failure('arming_after_first_scheduled_start_forbidden')
    current = runtime.api(f'repos/{policy.REPO}/actions/runs/{os.environ["GITHUB_RUN_ID"]}')
    if current.get('path') != '.github/workflows/'+policy.WORKFLOW:
        raise Failure('unexpected_experiment_workflow')
    history = runtime.api(f'repos/{policy.REPO}/actions/workflows/{current["workflow_id"]}/runs?per_page=100')['workflow_runs']
    # This dated experiment can be armed once, not reset by another push/root.
    if any(str(r['id']) != os.environ['GITHUB_RUN_ID'] for r in history):
        raise Failure('dated_experiment_already_armed_no_reset')
    schedule = dict(status='ARMED_NOT_COLLECTED', run_id=os.environ['GITHUB_RUN_ID'], source_commit=os.environ['GITHUB_SHA'],
        armed_at=now(), execution_method='one push, finite wait jobs, sequential collectors; no cron',
        rounds=[dict(**r, state='SCHEDULED_NOT_RUN') for r in c['rounds']], asof=policy.ASOF,
        master_status='SCHEDULED_NOT_COLLECTED', maximum_experiment_http=4*5600+4,
        completion_policy='No jobs or market HTTP after round4/comparison; reruns rejected')
    Store(ROOT/'plan').write('schedule.json', schedule)
    publish_file('schedule.json', schedule)
    summary('# 시간대별 4회 관측 예약\n\n```json\n'+json.dumps(schedule, ensure_ascii=False, indent=2)+'\n```')


def wait_until(target, *, clock=None, sleeper=None):
    clock = clock or policy.utcnow; sleeper = sleeper or time.sleep
    current = clock()
    if current >= policy.STARTS[-1]+dt.timedelta(minutes=170):
        raise Failure('dated_experiment_expired')
    if (target-current).total_seconds() > 4.5*3600:
        raise Failure('wait_phase_exceeds_job_budget')
    while (target-clock()).total_seconds() > 0:
        sleeper(max(0, min(30, (target-clock()).total_seconds())))


def wait_phase(n):
    context()
    if n not in (1, 2, 3):
        raise Failure('unknown_wait_phase')
    wait_until(policy.WAIT_TARGETS[n-1])
    summary(f'Finite wait phase {n} completed at {now()}; no market HTTP.')


def seal(folder, kind):
    from ..trial import secret_scan
    s = Store(folder)
    # Refuse upload after any integrity/secret scan failure.
    marker = s.root/'UPLOAD_ALLOWED'
    marker.unlink(missing_ok=True)
    (s.root/'manifest.json').unlink(missing_ok=True)
    secret_scan(s, None)
    s.write('manifest.json', dict(kind=kind, experiment_id=policy.EXPERIMENT, asof=policy.ASOF,
        run_id=os.environ['GITHUB_RUN_ID'], source_commit=os.environ['GITHUB_SHA'],
        files=runtime.inventory(s.root)))
    marker.touch()
    # The marker must be inventoried as well, for exact restoration.
    manifest = s.read('manifest.json'); manifest['files'] = runtime.inventory(s.root)
    s.write('manifest.json', manifest)


def restore(name, target, kind):
    data = runtime.api(f'repos/{policy.REPO}/actions/runs/{os.environ["GITHUB_RUN_ID"]}/artifacts?per_page=100')
    if data.get('total_count', 0) >= 100:
        raise Failure('experiment_artifact_pagination_required')
    matches = [a for a in data['artifacts'] if a['name'] == name and not a['expired']]
    if len(matches) != 1 or not matches[0].get('digest', '').startswith('sha256:'):
        raise Failure('experiment_artifact_missing_or_digest_unavailable')
    meta = matches[0]; archive = ROOT/'downloads'/(str(meta['id'])+'.zip')
    archive.parent.mkdir(parents=True, exist_ok=True)
    runtime.api(f'repos/{policy.REPO}/actions/artifacts/{meta["id"]}/zip', binary=archive)
    runtime.safe_extract(archive, target, meta['digest'][7:])
    manifest = runtime.verify(target)
    if (manifest.get('asof'), manifest.get('run_id'), manifest.get('kind'), manifest.get('experiment_id'), manifest.get('source_commit')) != (policy.ASOF, os.environ['GITHUB_RUN_ID'], kind, policy.EXPERIMENT, os.environ['GITHUB_SHA']):
        raise Failure('restored_experiment_artifact_identity_mismatch')
    return dict(artifact_id=meta['id'], zip_sha256=meta['digest'][7:], name=name)


class MasterBudget(Budget):
    def __init__(self, store):
        super().__init__(store, calls=4, cumulative=4, seconds=240, interval=1.05)
    def check(self):
        if policy.utcnow().astimezone(policy.KST).date().isoformat() != policy.ASOF:
            raise Stop('master_observation_date_mismatch')
        super().check()
    def before(self, source, endpoint, reason):
        from ..providers import LAYOUTS, Q
        allowed = source == 'KIS_MASTER' and endpoint in {LAYOUTS[m]['url'] for m in ('KOSPI','KOSDAQ')}
        allowed |= source == 'KIS' and endpoint in ('/oauth2/tokenP', Q+'chk-holiday')
        if not allowed:
            raise Failure('master_endpoint_forbidden')
        return super().before(source, endpoint, reason)


def master():
    context(); folder = ROOT/'master'; s = Store(folder); api = None; budget = None
    if any(folder.iterdir()):
        raise Failure('master_existing_state_no_reset')
    report = dict(status='FAIL', asof=policy.ASOF, actual_started_at=now())
    try:
        wait_until(policy.WAIT_TARGETS[-1])
        from ..providers import Providers, task
        from ..validation import parse
        from ..trial import secret_scan, TARGETS
        budget = MasterBudget(s); api = Providers(s, budget); rows=[]; evidence=[]
        for t in [task('calendar', policy.ASOF)]+[task('master', policy.ASOF, market=m) for m in ('KOSPI','KOSDAQ')]:
            e=api.fetch(t); values=parse(s.raw(e), e); evidence.append(e)
            if t['kind']=='master':rows.extend(values)
        selected=policy.eligible(rows)
        bycode={r['symbol']:r for r in rows}
        if any(not bycode.get(code) or (bycode[code]['market'],bycode[code]['name'])!=(market,name) for code,(market,name) in TARGETS.items()):
            raise Failure('master_target_identity_mismatch')
        s.write('universe.json', rows)
        report.update(status='PASS', master_rows=len(rows), eligible=len(selected), excluded=len(rows)-len(selected),
            population_sha256=sha(encoded(rows)), baseline_http_per_round=2*len(selected)+3, evidence=evidence)
        secret_scan(s,api)
    except (Failure, Stop) as e:
        report['reason']=e.code if isinstance(e,Failure) else str(e)
    finally:
        report.update(completed_at=now(), physical_http=budget.used if budget else 0)
        s.write('report.json',report);seal(folder,'master')
        publish_file('master.json',report)
        summary('# 10월 27일 공식 마스터\n\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```')
    return 0 if report['status']=='PASS' else 1


def verify_master(folder, *, experiment_policy=None):
    active_policy = experiment_policy or policy
    from ..validation import parse
    s=Store(folder);r=s.read('report.json',{});rows=[]
    for e in r.get('evidence',[]):
        values=parse(s.raw(e),e)
        if e['task']['asof']!=active_policy.ASOF:raise Failure('master_task_asof_mismatch')
        if e['task']['kind']=='master':rows.extend(values)
    ledger=s.read('requests.json',[])
    if r.get('physical_http')!=4 or len(ledger)!=4 or any(e.get('status')!='received' or e.get('http')!=200 for e in ledger):
        raise Failure('master_ledger_mismatch')
    active_policy.verify_population(rows,r)
    if rows!=s.read('universe.json'):raise Failure('master_raw_JSON_mismatch')
    return rows,r


def verify_generation(folder, report, *, experiment_policy=None):
    active_policy = experiment_policy or policy
    from ..core import Store
    from ..experiment import engine
    from ..experiment.storage import events
    import duckdb
    n=report['round'];g=active_policy.generation(n);s=Store(folder/'collection')
    if report.get('asof')!=active_policy.ASOF or report.get('generation_id')!=g:
        raise Failure('generation_report_identity_mismatch')
    universe=Store(folder).read('universe.json',[])
    master_receipt=Store(folder).read('master-receipt.json',{})
    selected=active_policy.verify_population(universe,master_receipt)
    expected={r['symbol']:r['market'] for r in selected}
    if (report.get('population_sha256'),report.get('target'),report.get('target_symbols'))!=(sha(encoded(universe)),len(selected),sorted(expected)):
        raise Failure('generation_fixed_population_mismatch')
    cache=engine.replay(s,1,asof=active_policy.ASOF,generation_id=g)
    rows=report.get('rows',[])
    if len({r['symbol'] for r in rows})!=len(rows):raise Failure('duplicate_qualified_row')
    for row in rows:
        if row['symbol'] not in expected or row['market']!=expected[row['symbol']]:raise Failure('generation_row_market_or_population_mismatch')
        if engine.qualify_kis(row,cache,s,1,asof=active_policy.ASOF,generation_id=g)!=row:
            raise Failure('generation_row_raw_mismatch')
    entries=events(s.root/'http.jsonl')
    starts=[e for e in entries if e['event']=='started'];ends=[e for e in entries if e['event']=='finished']
    if len(starts)!=report['physical_http'] or len(starts)>5600 or any(e.get('generation_id')!=g for e in starts):
        raise Failure('generation_ledger_mismatch')
    sequences=[e['sequence'] for e in starts]
    if sequences!=list(range(1,len(starts)+1)) or len({e['sequence'] for e in ends})!=len(ends) or any(e['sequence'] not in sequences for e in ends):
        raise Failure('generation_ledger_sequence_mismatch')
    if report.get('status')=='PASS' and (len(rows)!=report['target'] or len(ends)!=len(starts) or not report.get('db_json_match')):
        raise Failure('incomplete_generation_marked_pass')
    dbpath=(s.root/report['database_file']).resolve()
    if s.root not in dbpath.parents:raise Failure('generation_unsafe_DB_path')
    with duckdb.connect(str(dbpath),read_only=True) as db:
        saved={symbol:json.loads(value) for symbol,value in db.execute('SELECT symbol,payload FROM observations').fetchall()}
    if saved!={r['symbol']:r for r in rows}:raise Failure('generation_DB_JSON_mismatch')


def round_summary(report):
    keys=('status','round','generation_id','asof','scheduled_at','actual_job_started_at','collector_command_started_at','actual_started_at','start_delay_seconds',
        'first_http_at','last_http_at','completed_at','elapsed_seconds','target','received_symbols','kis_validated',
        'final_validation_rate','physical_http','failed_http_requests','physical_retries','recovered_tasks',
        'population_sha256','master_rows','excluded_count','official_price_status','metrics','reason')
    out={k:report.get(k) for k in keys}
    errors=report.get('errors',[])
    out.update(error_count=len(errors),first_errors=errors[:20],exception_type=report.get('exception_type'))
    if not out['reason'] and errors:out['reason']=errors[0].get('reason')
    return out


def collect(n):
    context();command_started=now();g=policy.generation(n);folder=ROOT/'generations'/g
    job_started=None
    try:
        jobs=runtime.api(f'repos/{policy.REPO}/actions/runs/{os.environ["GITHUB_RUN_ID"]}/jobs?per_page=100')['jobs']
        job_started=next((j['started_at'] for j in jobs if j['name']==os.getenv('GITHUB_JOB')),None)
    except Failure:pass
    s=Store(folder);report=dict(status='FAIL',round=n,generation_id=g,asof=policy.ASOF,
        scheduled_at=policy.STARTS[n-1].isoformat(),actual_job_started_at=job_started,collector_command_started_at=command_started,official_price_status='PENDING_KRX')
    if any(folder.iterdir()):raise Failure('generation_existing_state_no_reset')
    try:
        # Even preparation failure belongs to its assigned slot, never an early substitute.
        wait_until(policy.STARTS[n-1]);c=policy.control(n);policy.validate_control(c)
        master_path=ROOT/'restored-master'
        restoration=restore('market-20261027-master',master_path,'master')
        universe, master_report=verify_master(master_path)
        s.write('universe.json',universe);s.write('master-receipt.json',master_report)
        s.write('restoration.json',restoration)
        # Same source commit/artifact and dependency chain; no market caches are restored.
        policy.validate_control(c)
        from .. import kis_manual as manual
        cache_sha=None
        def progress(p):
            nonlocal cache_sha
            payload=dict(p,round=n,generation_id=g,scheduled_at=report['scheduled_at'])
            cache_sha=publish_file(g+'-progress.json',payload,cached=cache_sha)
        result=manual.run(folder/'collection',c,universe,os.environ['GITHUB_RUN_ID'],progress=progress,policy=policy)
        report.update(result,round=n,generation_id=g,scheduled_at=policy.STARTS[n-1].isoformat(),actual_job_started_at=job_started,collector_command_started_at=command_started,
            start_delay_seconds=(dt.datetime.fromisoformat(result['actual_started_at'])-policy.STARTS[n-1]).total_seconds(),
            master_rows=len(universe),excluded_count=len(universe)-len(policy.eligible(universe)),restoration=restoration)
        verification=time.monotonic();verify_generation(folder,report)
        from .compare import build_observations
        # Exercise every preserved comparison field now; do not publish raw responses to Git.
        views=build_observations(folder/'collection',report,universe)
        report['comparison_field_count']=sum(len(v['all_market_fields']) for v in views.values())
        report['metrics']['seconds']['raw_DB_reverification']=time.monotonic()-verification
        report['completed_at']=now();report['elapsed_seconds']=(dt.datetime.fromisoformat(report['completed_at'])-dt.datetime.fromisoformat(report['actual_started_at'])).total_seconds()
    except Exception as e:
        report['status']='FAIL';report['reason']=e.code if isinstance(e,Failure) else str(e) if isinstance(e,Stop) else 'experiment_program_failure'
        report['exception_type']=type(e).__name__;report['completed_at']=now()
        from ..experiment.storage import events
        report['physical_http']=sum(e['event']=='started' for e in events(folder/'collection'/'http.jsonl'))
        report['last_progress']=Store(folder/'collection').read('progress.json',{})
    finally:
        s.write('report.json',report);seal(folder,g)
        receipt=round_summary(report);receipt['run_id']=os.environ['GITHUB_RUN_ID']
        publish_file(g+'.json',receipt)
        summary('# '+g+'\n\n```json\n'+json.dumps(receipt,ensure_ascii=False,indent=2)+'\n```')
    return 0 if report['status']=='PASS' else 1


def compare_all():
    context()
    from .compare import build_observations, build_indices, compare_pair, stabilization, markdown
    out=Store(ROOT/'comparison');rounds=[];restorations=[]
    for n in range(1,5):
        folder=ROOT/'restored'/policy.generation(n)
        try:
            meta=restore('market-20261027-'+policy.LABELS[n-1],folder,policy.generation(n));s=Store(folder)
            r=s.read('report.json',{});u=s.read('universe.json',[])
            if r.get('round')!=n or r.get('generation_id')!=policy.generation(n):raise Failure('comparison_generation_identity_mismatch')
            policy.verify_population(u,s.read('master-receipt.json',{}))
            if (folder/'collection'/'report.json').exists():
                verify_generation(folder,r)
                obs=build_observations(folder/'collection',r,u)
            else:obs={}
            rounds.append(dict(report=r,observations=obs,indices=build_indices(folder/'collection',r) if obs else {}));restorations.append(meta)
        except Exception as e:
            reason=e.code if isinstance(e,Failure) else 'comparison_integrity_or_program_failure'
            rounds.append(dict(report=dict(round=n,generation_id=policy.generation(n),asof=policy.ASOF,status='NOT_RUN_OR_UNVERIFIABLE',reason=reason),observations={}))
    pairs=[compare_pair(rounds[a],rounds[b]) for a,b in ((0,1),(1,2),(2,3),(0,3))]
    analysis=stabilization(pairs);reports=[x['report'] for x in rounds]
    for name,p in zip(('1800-2100','2100-0000','0000-0300','1800-0300'),pairs):out.write('pair-'+name+'.json',p)
    concise_pairs=[{k:v for k,v in p.items() if k not in ('changes','metadata_comparison','largest_absolute_changes','unchanged_symbols','core_unchanged_symbols','category_changed_symbols')} for p in pairs]
    final=dict(status='COMPLETE_OBSERVATION_COMPARISON' if all(r.get('status')=='PASS' for r in reports) and all(p['complete_population_comparable'] for p in pairs) else 'INCOMPLETE_COMPARISON',
        asof=policy.ASOF,run_id=os.environ['GITHUB_RUN_ID'],completed_at=now(),rounds=[round_summary(r) for r in reports],pairs=concise_pairs,
        analysis=analysis,restorations=restorations,official_price_status='PENDING_KRX',
        KRX_status='NOT_RUN_NO_VERIFIED_SAME_SCOPE_OFFICIAL_REFERENCE',NXT_status='NOT_OBSERVED_J_REQUESTS_ONLY',additional_generations_scheduled=False)
    out.write('final-report.json',final)
    text=markdown(reports,pairs,analysis);(out.root/'final-report.md').write_text(text)
    summary(text);publish_file('final-report.json',final);seal(out.root,'comparison')
    return 0 if final['status']=='COMPLETE_OBSERVATION_COMPARISON' else 1


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('authorize','wait','master','collect','compare','seal'))
    p.add_argument('--round',type=int);p.add_argument('--phase',type=int);p.add_argument('--kind')
    a=p.parse_args()
    if a.mode=='authorize':return authorize()
    if a.mode=='wait':return wait_phase(a.phase)
    if a.mode=='master':return master()
    if a.mode=='collect':return collect(a.round)
    if a.mode=='compare':return compare_all()
    context()
    folder=ROOT/'master' if a.kind=='master' else ROOT/'comparison' if a.kind=='comparison' else ROOT/'generations'/policy.generation(a.round)
    seal(folder,a.kind or policy.generation(a.round))


if __name__=='__main__':raise SystemExit(main())
