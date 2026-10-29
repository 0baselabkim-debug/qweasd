"""Manually approved KIS-only sample/full collection. No scheduler and no KRX."""
import argparse,datetime as dt,json,os,re,time,urllib.error,urllib.parse,urllib.request
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import Failure,Stop,Store,encoded,now,sha
from .providers import Q,task
from .validation import parse
from .trial import TARGETS
from .experiment import engine
from .experiment.storage import Evidence,Metrics,append,events
ASOF='2026-10-26'
CONTROL='.github/trials/kis-manual-20261026.json'
ROOT=Path('data/kis-manual-20261026')
SAMPLE_APPROVAL='USER_APPROVED_THREE_KIS_SYMBOLS_ONLY'
FULL_APPROVAL='USER_SEPARATELY_APPROVED_FULL_KIS_COLLECTION'
ENDPOINTS={Q+'investor-trade-by-stock-daily',Q+'inquire-daily-itemchartprice',Q+'inquire-daily-indexchartprice','/oauth2/tokenP'}


def validate_control(c,current=None):
    if set(c)!={'schema','trial_id','request_id','mode','asof','previous_run','max_total_http','not_before','expires_at','approval'}:raise Failure('manual_control_schema')
    mode=c['mode']
    if c['schema']!=1 or mode not in ('sample','full') or c['asof']!=ASOF:raise Failure('manual_control_scope')
    if (c['trial_id'],c['max_total_http'],c['approval'])!=('kis-'+mode+'-20261026',12 if mode=='sample' else 5600,SAMPLE_APPROVAL if mode=='sample' else FULL_APPROVAL):raise Failure('manual_explicit_approval_or_budget_missing')
    if not re.fullmatch('[A-Za-z0-9_-]{1,80}',c['request_id']):raise Failure('bad_manual_request_id')
    if c['previous_run'] is not None and not str(c['previous_run']).isdigit():raise Failure('bad_previous_run')
    start=dt.datetime.fromisoformat(c['not_before']);end=dt.datetime.fromisoformat(c['expires_at'])
    if start.tzinfo is None or end.tzinfo is None or not 0<(end-start).total_seconds()<=(86400 if mode=='sample' else 10200):raise Failure('manual_window_invalid')
    current=current or dt.datetime.now(dt.timezone.utc)
    if not start<=current<end:raise Failure('manual_execution_window_closed')
    if current.astimezone(ZoneInfo('Asia/Seoul'))<dt.datetime(2026,10,26,15,30,tzinfo=ZoneInfo('Asia/Seoul')):raise Failure('manual_before_market_close')


class ManualBudget:
    def __init__(self,store,control,metrics,run_id,*,validator=None):
        self.store=store;self.control=control;self.metrics=metrics;self.run_id=run_id;self.round_id=1;self.validate=validator or validate_control
        self.path=store.root/'http.jsonl';self.entries=events(self.path)
        self.used=sum(e['event']=='started' for e in self.entries);self.initial=self.used
        self.limit=control['max_total_http'];self.current=None;self.last=0
        self.signatures=Counter(e.get('request_signature',e['endpoint']) for e in self.entries if e['event']=='started')
        starts=[dt.datetime.fromisoformat(e['started_at']) for e in self.entries if e['event']=='started']
        if starts:self.last=time.monotonic()-max(0,(dt.datetime.now(dt.timezone.utc)-max(starts)).total_seconds())
    def check(self):
        self.validate(self.control)
        if (self.store.root/'STOP').exists():raise Stop('operator_stop')
        if self.used>=self.limit:raise Stop('manual_http_budget_exhausted')
    def before(self,source,endpoint,reason):
        if source!='KIS' or endpoint not in ENDPOINTS:raise Failure('manual_non_KIS_endpoint_forbidden')
        t=getattr(self,'current_task',{})
        signature=endpoint+'|'+(t.get('symbol',t.get('market','')) if endpoint!='/oauth2/tokenP' else '')
        repeated=self.signatures[signature]>0
        self.check();start=time.monotonic();time.sleep(max(0,1.05-(time.monotonic()-self.last)));self.metrics.add('rate_wait',time.monotonic()-start)
        if repeated:self.metrics.add('retry_rate_wait',time.monotonic()-start)
        self.check()
        self.last=time.monotonic();self.used+=1
        entry=dict(event='started',sequence=self.used,round=1,mode=self.control['mode'],source=source,endpoint=endpoint,reason=reason,source_run=self.run_id,started_at=now(),request_signature=signature,repeated_signature=repeated,task_kind=t.get('kind'),symbol=t.get('symbol'),market=t.get('market'))
        if t.get('generation_id'):entry['generation_id']=t['generation_id']
        append(self.path,entry);self.entries.append(entry);self.current=entry;self.signatures[signature]+=1;return entry
    def finish(self,entry,status,**metadata):
        e=dict(event='finished',sequence=entry['sequence'],status=status,completed_at=now(),**metadata);append(self.path,e);self.entries.append(e)
    def remaining_seconds(self):return (dt.datetime.fromisoformat(self.control['expires_at'])-dt.datetime.now(dt.timezone.utc)).total_seconds()


class ManualAPI(engine.ProvisionalAPI):
    def __init__(self,store,budget,metrics,opener=None):
        from .providers import NoRedirect
        original=opener or urllib.request.build_opener(NoRedirect()).open
        self.pending_error=None
        def capture_error(req,timeout):
            try:return original(req,timeout=timeout)
            except urllib.error.HTTPError as exc:
                # Token response bodies are never retained. Other error bodies
                # are read inside network timing and preserved outside it.
                if budget.current['endpoint']!='/oauth2/tokenP':
                    raw=exc.read(1000001)
                    self.pending_error=(raw[:1000000],dict(http=exc.code,truncated=len(raw)>1000000,params=dict(urllib.parse.parse_qsl(urllib.parse.urlparse(req.full_url).query))))
                raise
        super().__init__(store,budget,metrics,capture_error)
        self._shared_required = opener is None
    def fetch(self,t):
        self.pending_error=None
        try:return super().fetch(t)
        except Failure:
            if self.pending_error is not None:
                raw,meta=self.pending_error
                e=self.store.preserve(raw,dict(task=t,source='KIS',endpoint=self.budget.current['endpoint'],collected_at=now(),source_run=self.budget.run_id,**meta))
                append(self.store.root/'http-errors.jsonl',dict(task=t,evidence=e))
            raise
    def check_task(self,t):
        self.budget.validate(self.budget.control)
        if t['asof']!=self.budget.control['asof'] or t['round']!=1 or t['kind'] not in ('flow','price','index') or t['market'] not in ('KOSPI','KOSDAQ'):raise Failure('manual_task_scope')
        if t.get('generation_id')!=self.budget.control.get('generation_id'):raise Failure('manual_task_generation_mismatch')
        if t['kind']!='index' and t['symbol'] not in self.allowed:raise Failure('manual_symbol_not_authorized')


def eligible(universe):
    selected=[r for r in universe if r['eligible']]
    if len(universe)!=3950 or len({r['symbol'] for r in universe})!=3950 or len(selected)!=2349:raise Failure('manual_master_population_mismatch')
    for code,(market,name) in TARGETS.items():
        matches=[r for r in selected if r['symbol']==code and r['market']==market and r.get('name')==name]
        if len(matches)!=1:raise Failure('manual_target_master_mismatch')
    return selected


def recover_preserved_responses(store,cache,*,asof=ASOF,generation_id=None):
    # Program fixes may reparse already saved successful HTTP without resending it.
    for event in events(store.root/'responses.jsonl'):
        e=event['evidence'];t=e['task'];key=engine.identity(t)
        if key in cache:continue
        if t['asof']!=asof or t.get('round')!=1 or t.get('generation_id')!=generation_id or t['kind'] not in ('flow','price','index'):raise Failure('manual_response_generation_mismatch')
        try:parse(store.raw(e),e)
        except (Failure,ValueError,KeyError,TypeError):continue
        cache[key]=e;append(store.root/'tasks.jsonl',dict(task=t,status='received',evidence=e,recovery='offline_reparse'))


def run(root,control,universe,run_id,opener=None,progress=None,*,policy=None,memory_session=None,progress_step=200):
    if type(progress_step) is not int or not 1<=progress_step<=2000:raise Failure('invalid_progress_step')
    validator=policy.validate_control if policy else validate_control
    validator(control);asof=control['asof'];generation_id=control.get('generation_id')
    selected=policy.eligible(universe) if policy else eligible(universe)
    def observation_task(kind,**fields):
        if generation_id:fields['generation_id']=generation_id
        return task(kind,asof,round=1,**fields)
    def qualify(row,cache,store):return engine.qualify_kis(row,cache,store,1,asof=asof,generation_id=generation_id)
    if control['mode']=='sample':selected=[r for r in selected if r['symbol'] in TARGETS]
    root=Path(root);metrics=Metrics();store=Evidence(root,metrics)
    with store.lock():
        binding=dict(trial_id=control['trial_id'],mode=control['mode'],asof=asof,max_total_http=control['max_total_http'],population_sha256=sha(encoded(universe)))
        if generation_id:binding['generation_id']=generation_id
        old=store.read('binding.json')
        if old and old!=binding:raise Failure('manual_state_binding_mismatch')
        if old and not (root/'http.jsonl').exists():raise Failure('manual_lost_ledger_no_reset')
        if not old:
            if any(p.name!='writer.lock' for p in root.iterdir()):raise Failure('manual_existing_root_without_binding')
            (root/'http.jsonl').touch();store.write('binding.json',binding)
        cache=engine.replay(store,1,asof=asof,generation_id=generation_id);recover_preserved_responses(store,cache,asof=asof,generation_id=generation_id)
        attempts={};store.blocked_tasks=set()
        for e in events(root/'failures.jsonl'):
            k=engine.identity(e['task']);attempts[k]=max(attempts.get(k,0),e['attempt'])
            if not e.get('retryable',True) and k not in cache:store.blocked_tasks.add(k)
        budget=ManualBudget(store,control,metrics,run_id,validator=validator);api=ManualAPI(store,budget,metrics,opener);api.allowed={r['symbol'] for r in selected}
        if memory_session is not None:memory_session.transfer(api)
        previous=store.read('report.json',{});metrics.seconds.update(previous.get('metrics',{}).get('seconds',{}));metrics.counts.update(previous.get('metrics',{}).get('counts',{}));started=previous.get('actual_started_at') or now();wall=time.monotonic();errors=[]
        common=[observation_task('index',market=m) for m in ('KOSPI','KOSDAQ')]
        probes=[r for r in selected if r['symbol'] in TARGETS];rest=[r for r in selected if r['symbol'] not in TARGETS]
        tasks=lambda rows:[observation_task(k,symbol=r['symbol'],market=r['market']) for r in rows for k in ('flow','price')]
        last_progress=budget.used
        def checkpoint(force=False,phase='COLLECTING'):
            nonlocal last_progress
            if not force and budget.used-last_progress<progress_step:return
            complete=sum(all(engine.identity(observation_task(k,symbol=r['symbol'],market=r['market'])) in cache for k in ('flow','price')) for r in selected)
            payload=dict(run_id=run_id,mode=control['mode'],asof=asof,status=phase,target=len(selected),received=complete,physical_http=budget.used,http_limit=budget.limit,task_failures=len(events(root/'failures.jsonl')),observed_at=now(),actual_started_at=started)
            store.write('progress.json',payload);last_progress=budget.used
            if progress:
                begin=time.monotonic()
                try:progress(payload)
                except Exception:metrics.counts['progress_reporting_failures']=metrics.counts.get('progress_reporting_failures',0)+1
                metrics.add('progress_reporting',time.monotonic()-begin)
        try:
            checkpoint(True)
            for t in common+tasks(probes):engine.fetch_task(t,api,budget,cache,attempts,metrics)
            for row in probes:qualify(row,cache,store)
            if rest:
                projected=sum(max(1.05,metrics.seconds.get('api:'+Q+suffix,0)/max(metrics.counts.get('api:'+Q+suffix,0),1)) for suffix in ('investor-trade-by-stock-daily','inquire-daily-itemchartprice'))*len(rest)+60
                if projected>budget.remaining_seconds():raise Failure('manual_insufficient_time_for_population')
            pending=tasks(rest);health_streak=0;circuit_trips=0
            for recovery_pass in range(2):
                failed=[]
                for t in pending:
                    if attempts.get(engine.identity(t),0)>=6:continue
                    try:engine.fetch_task(t,api,budget,cache,attempts,metrics);health_streak=0;checkpoint()
                    except Failure as exc:
                        if exc.code in ('authentication_failure','network_policy_denied','token_expired','provider_rate_limit','http_429','credential_echo','local_storage_unavailable'):raise
                        failed.append(t)
                        if exc.code in ('network_failure','KIS_response_rejected') or exc.code.startswith('http_5'):health_streak+=1
                        if health_streak>=3:
                            circuit_trips+=1
                            if circuit_trips>=3 or budget.remaining_seconds()<180:raise Failure('manual_provider_circuit_open')
                            begin=time.monotonic();time.sleep(120);metrics.add('provider_circuit_wait',time.monotonic()-begin);health_streak=0
                pending=failed
                if not pending:break
        except (Failure,Stop) as exc:errors.append(dict(reason=exc.code if isinstance(exc,Failure) else str(exc),endpoint=budget.current.get('endpoint') if budget.current else None))
        checkpoint(True,'VALIDATING')
        rows=[]
        validation_begin=time.monotonic()
        for row in selected:
            try:rows.append(qualify(row,cache,store))
            except (Failure,KeyError,TypeError,ValueError) as exc:errors.append(dict(symbol=row['symbol'],reason=exc.code if isinstance(exc,Failure) else 'missing_required_evidence',details=getattr(exc,'details',None)))
        metrics.add('internal_validation',time.monotonic()-validation_begin)
        indices=[]
        for t in common:
            e=cache.get(engine.identity(t))
            if e:indices.append(dict(**parse(store.raw(e),e)[0],observed_at=e['collected_at'],source_hash=e['source_hash']))
        status='PASS' if len(rows)==len(selected) and len(indices)==2 and not errors else 'FAIL'
        report=dict(schema=1,mode=control['mode'],asof=asof,trial_id=control['trial_id'],request_id=control['request_id'],run_id=run_id,status=status,target=len(selected),kis_validated=len(rows),official_accepted=0,official_price_status='PENDING_KRX',provider_finality='UNVERIFIED',rows=rows,indices=indices,errors=errors,
                    actual_started_at=started,completed_at=now(),elapsed_seconds=(dt.datetime.fromisoformat(now())-dt.datetime.fromisoformat(started)).total_seconds(),this_run_seconds=time.monotonic()-wall,
                    physical_http=budget.used,physical_http_this_run=budget.used-budget.initial,http_limit=budget.limit,retries=sum(max(0,n-1) for n in attempts.values()),metrics=metrics.result(),population_sha256=binding['population_sha256'],target_symbols=sorted(api.allowed),
                    first_http_at=min((e['started_at'] for e in budget.entries if e['event']=='started'),default=None),last_http_at=max((e['completed_at'] for e in budget.entries if e['event']=='finished'),default=None))
        import duckdb
        db_begin=time.monotonic()
        path=root/'derived'/(sha(encoded(rows))+'.duckdb');path.parent.mkdir(exist_ok=True);exists=path.exists()
        with duckdb.connect(str(path),read_only=exists) as db:
            if not exists:
                db.execute('CREATE TABLE observations(symbol VARCHAR PRIMARY KEY,payload JSON)')
                if rows:db.executemany('INSERT INTO observations VALUES (?,?)',[(r['symbol'],encoded(r).decode()) for r in rows])
            saved={s:json.loads(v) for s,v in db.execute('SELECT symbol,payload FROM observations').fetchall()}
        metrics.add('DuckDB_write_and_readback',time.monotonic()-db_begin)
        report['db_json_match']=saved=={r['symbol']:r for r in rows};report['database_file']=str(path.relative_to(root))
        if not report['db_json_match']:report['status']='FAIL'
        response_scan_begin=time.monotonic()
        report['failure_responses']=[]
        for event in events(root/'responses.jsonl'):
            e=event['evidence'];d=json.loads(store.raw(e))
            if d.get('rt_cd')!='0':report['failure_responses'].append(dict(endpoint=e['endpoint'],params=e['params'],source_hash=e['source_hash'],rt_cd=d.get('rt_cd'),msg_cd=d.get('msg_cd'),msg1=d.get('msg1')))
        metrics.add('preserved_response_rescan',time.monotonic()-response_scan_begin)
        report['completed_at']=now()
        report['elapsed_seconds']=(dt.datetime.fromisoformat(report['completed_at'])-dt.datetime.fromisoformat(started)).total_seconds()
        report['this_run_seconds']=time.monotonic()-wall
        report['elapsed_semantics']='cumulative_wall_including_recovery_pauses'
        report['metrics']=metrics.result()
        report['pure_api_seconds']=sum(v for k,v in metrics.seconds.items() if k.startswith('api:'))
        finished=[e for e in budget.entries if e['event']=='finished']
        report['failed_http_requests']=sum(e['status']!='received' for e in finished)+budget.used-len(finished)
        report['http_failure_rate']=report['failed_http_requests']/budget.used if budget.used else 0
        failures=events(root/'failures.jsonl')
        report['task_failures']=len(failures)
        report['http_error_evidence']=[e['evidence'] for e in events(root/'http-errors.jsonl')]
        report['recovered_tasks']=len({engine.identity(e['task']) for e in failures}&set(cache))
        report['recovery_intervals']=[]
        for key in {engine.identity(e['task']) for e in failures}:
            failed_at=min(e['observed_at'] for e in failures if engine.identity(e['task'])==key)
            recovered_at=cache[key]['collected_at'] if key in cache else None
            report['recovery_intervals'].append(dict(task=key,failed_at=failed_at,recovered_at=recovered_at,wall_seconds=(dt.datetime.fromisoformat(recovered_at)-dt.datetime.fromisoformat(failed_at)).total_seconds() if recovered_at else None))
        completed_codes={r['symbol'] for r in rows};failed_keys={engine.identity(e['task']) for e in failures}
        first_pass=[r['symbol'] for r in selected if r['symbol'] in completed_codes and all(attempts.get(engine.identity(t),0)==1 and engine.identity(t) not in failed_keys for t in tasks([r]))]
        received=[r['symbol'] for r in selected if all(engine.identity(t) in cache for t in tasks([r]))]
        attempted=[r['symbol'] for r in selected if any(engine.identity(t) in attempts for t in tasks([r]))]
        report.update(received_symbols=len(received),attempted_symbols=len(attempted),first_attempt_success_symbols=len(first_pass),first_attempt_success_rate=len(first_pass)/len(selected),first_attempt_success_denominator='all_required_symbols',final_validation_rate=len(rows)/len(selected),uncollected_symbols=[r['symbol'] for r in selected if r['symbol'] not in received],validation_failed_symbols=[s for s in received if s not in completed_codes],excluded_symbols=[dict(symbol=r['symbol'],market=r['market'],reasons=r.get('exclusion_reasons',[])) for r in universe if not r['eligible']],duplicate_master_codes=[],duplicate_source_failures=[e for e in failures if 'duplicate' in e['reason']],physical_retries=sum(e.get('repeated_signature',False) for e in budget.entries if e['event']=='started'))
        report['retry_extra_seconds']=sum(v for k,v in metrics.seconds.items() if k.startswith('retry_api:'))+sum(metrics.seconds.get(k,0) for k in ('retry_rate_wait','backoff_wait','reauth_backoff','provider_circuit_wait'))
        report['retry_time_semantics']='repeated physical request signatures plus backoff; excludes unrelated work during unresolved task intervals'
        report['symbols_per_minute']=len(rows)/max(report['elapsed_seconds']/60,1e-9)
        report['bottleneck']=max(metrics.seconds,key=metrics.seconds.get) if metrics.seconds else None
        report['code_commit']=__import__('subprocess').check_output(['git','rev-parse','HEAD'],text=True).strip()
        report['config_sha256']=sha(encoded(control))
        json_begin=time.monotonic();store.write('report.json',report);metrics.add('JSON_generation_and_storage',time.monotonic()-json_begin)
        report['metrics']=metrics.result();store.write('report.json',report)
        return report


def full_plan(sample,universe):
    selected=eligible(universe)
    if sample.get('status')!='PASS' or sample.get('mode')!='sample' or sample.get('kis_validated')!=3 or sample.get('physical_http',13)>12 or set(sample.get('target_symbols',[]))!=set(TARGETS) or sample.get('population_sha256')!=sha(encoded(universe)):raise Failure('full_requires_verified_sample_pass')
    metrics=sample['metrics'];costs={}
    for kind,suffix in [('flow','investor-trade-by-stock-daily'),('price','inquire-daily-itemchartprice')]:
        name='api:'+Q+suffix;costs[kind]=max(1.05,metrics['seconds'].get(name,0)/max(1,metrics['counts'].get(name,0)))
    return dict(status='READY_NOT_EXECUTED',asof=ASOF,target=len(selected),baseline_http=2*len(selected)+3,max_total_http=5600,
                interval_seconds=1.05,minimum_spacing_seconds=(2*len(selected)+2)*1.05,estimated_api_spacing_seconds=len(selected)*sum(costs.values())+3*1.05,
                estimated_from_only_three_symbols=True,timeout_minutes=175,automatic_schedule=False,KRX_required=False,full_execution_approved=False,
                command='python -m collector_v1.kis_manual_actions run',condition='Separate full approval and full-mode control-only push; restore latest state and verified sample first',population_sha256=sha(encoded(universe)))
