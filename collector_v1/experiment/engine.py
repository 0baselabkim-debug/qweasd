"""Fresh per-round observations, bounded recovery and exact provisional crosschecks."""
import datetime as dt
import gzip,json,os,time,urllib.request
from pathlib import Path
from ..core import Failure,Stop,encoded,now,sha,atomic
from ..providers import Providers,Q,NoRedirect,task,LAYOUTS
from ..validation import parse,integer,KRX_FIELDS
from .. import trial
from ..market_trial import ASOF,MASTER_RUN,ARTIFACT_ID,ZIP_SHA
from .storage import Evidence,ExperimentBudget,Metrics,events,append
from .policy import window,retry_policy,ROUND_LIMIT,PROBE_LIMIT
from .compare import value_hash


class ProvisionalAPI(Providers):
    """Separate after-close observation route; the pilot's next-day guards stay intact."""
    def __init__(self,store,budget,metrics,opener=None):
        raw_opener=opener or urllib.request.build_opener(NoRedirect()).open
        def timed(req,timeout):
            started=time.monotonic();kind=budget.current['endpoint'] if budget.current else 'unknown'
            repeated=budget.current.get('repeated_signature',False) if budget.current else False
            def record_elapsed():
                elapsed=time.monotonic()-started;metrics.add('api:'+kind,elapsed)
                if repeated:metrics.add('retry_api:'+kind,elapsed)
            try:r=raw_opener(req,timeout=timeout)
            except Exception:
                record_elapsed();raise
            class Response:
                status=r.status
                def __enter__(self):r.__enter__();return self
                def read(self,n):return r.read(n)
                def __exit__(self,*a):
                    try:return r.__exit__(*a)
                    finally:record_elapsed()
            return Response()
        super().__init__(store,budget,timed)
        self._shared_required = opener is None
    def check_task(self,t):
        window(self.budget.round_id)
        if t['asof']!=ASOF or t['round']!=self.budget.round_id:raise Failure('experiment_generation_mismatch')
    def fetch(self,t):
        self.check_task(t)
        kind=t['kind'];day=t['asof'].replace('-','');source='KIS';params={}
        if kind=='krx':
            source='KRX';key=os.getenv('KRX_API_KEY')
            if not key:raise Failure('missing_KRX_API_KEY')
            endpoint='/svc/apis/sto/'+('stk' if t['market']=='KOSPI' else 'ksq')+'_bydd_trd'
            params={'basDd':day};raw=self.http(source,endpoint,'https://data-dbg.krx.co.kr'+endpoint,params,{'AUTH_KEY':key})
        else:
            p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':t.get('symbol'),'FID_INPUT_DATE_1':day}
            if kind=='flow':
                endpoint=Q+'investor-trade-by-stock-daily';params={**p,'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''};tr='FHPTJ04160001'
            elif kind in ('price','index'):
                index=kind=='index';endpoint=Q+('inquire-daily-indexchartprice' if index else 'inquire-daily-itemchartprice')
                params={**p,'FID_INPUT_DATE_1':(dt.date.fromisoformat(t['asof'])-dt.timedelta(days=60)).strftime('%Y%m%d'),'FID_INPUT_DATE_2':day,'FID_PERIOD_DIV_CODE':'D'}
                if index:params.update(FID_COND_MRKT_DIV_CODE='U',FID_INPUT_ISCD='0001' if t['market']=='KOSPI' else '1001')
                else:params['FID_ORG_ADJ_PRC']='1'
                tr='FHKUP03500100' if index else 'FHKST03010100'
            else:raise Failure('experiment_endpoint_not_authorized')
            raw=self.kis(endpoint,params,tr)
        # A successful HTTP body survives parsing/validation failure unchanged.
        for attempt in range(2):
            try:e=self.store.preserve(raw,dict(task=t,source=source,endpoint=endpoint,params=params,collected_at=now(),source_run=self.budget.run_id));break
            except OSError:
                if attempt:raise Failure('local_storage_unavailable') from None
                time.sleep(2)  # Retry local storage, never resend successful HTTP.
        append(self.store.root/'responses.jsonl',{'task':t,'evidence':e})
        d=json.loads(raw)
        if source=='KIS' and d.get('rt_cd')!='0':
            code=d.get('msg_cd')
            if code in ('EGW00121','EGW00123'):raise Failure('token_expired')
            if code=='EGW00201':raise Failure('provider_rate_limit')
            raise Failure('KIS_response_rejected')
        return e


def identity(t):return sha(encoded(t))


def replay(store,round_id,*,asof=ASOF,generation_id=None):
    cache={}
    for event in events(store.root/'tasks.jsonl'):
        t=event['task']
        if t.get('round')!=round_id or t['asof']!=asof or t.get('generation_id')!=generation_id:raise Failure('cross_round_cache_forbidden')
        if event['status']=='received':store.raw(event['evidence']);cache[identity(t)]=event['evidence']
    return cache


def fetch_task(t,api,budget,cache,attempts,metrics):
    key=identity(t)
    if key in cache:return cache[key]
    if key in getattr(api.store,'blocked_tasks',set()):raise Failure('permanent_task_failure_requires_review')
    initial=attempts.get(key,0);batch_limit=min(6,initial+3)
    while attempts.get(key,0)<batch_limit:
        budget.check();attempts[key]=attempts.get(key,0)+1
        try:
            budget.current_task=t
            e=api.fetch(t);started=time.monotonic();parse(api.store.raw(e),e);metrics.add('parse',time.monotonic()-started)
            cache[key]=e;append(api.store.root/'tasks.jsonl',dict(task=t,status='received',evidence=e,attempt=attempts[key]));return e
        except (Failure,ValueError,KeyError,TypeError) as exc:
            code=exc.code if isinstance(exc,Failure) else 'source_schema_mismatch'
            retryable=retry_policy(code,1) is not None or code=='token_expired'
            if not retryable:
                api.store.blocked_tasks=getattr(api.store,'blocked_tasks',set())|{key}
            append(api.store.root/'failures.jsonl',dict(task=t,status='failed',reason=code,retryable=retryable,attempt=attempts[key],observed_at=now()))
            if code=='token_expired' and getattr(api,'reauth_count',0)<1:
                if budget.remaining_seconds()<=90:raise Stop('no_time_for_safe_reauthentication')
                begin=time.monotonic();time.sleep(60);metrics.add('reauth_backoff',time.monotonic()-begin)
                api.reauth_count=getattr(api,'reauth_count',0)+1;api.token=None;continue
            delay=retry_policy(code,attempts[key]-initial)
            if delay is None or budget.remaining_seconds()<=delay+30:raise Failure(code) from None
            start=time.monotonic();time.sleep(delay);metrics.add('backoff_wait',time.monotonic()-start)
    raise Failure('task_retry_budget_exhausted')


def qualify_kis(row,cache,store,round_id,*,asof=ASOF,generation_id=None):
    """KIS internal checks; a passing row is still pending official validation."""
    code=row['symbol'];market=row['market'];identity_fields={'generation_id':generation_id} if generation_id else {}
    fe=cache[identity(task('flow',asof,round=round_id,symbol=code,market=market,**identity_fields))]
    pe=cache[identity(task('price',asof,round=round_id,symbol=code,market=market,**identity_fields))]
    f=parse(store.raw(fe),fe)[0];p=parse(store.raw(pe),pe)[0]
    if any(f[k]!=p[k] for k in ('close','volume','turnover')):raise Failure('flow_price_conflict')
    if f['institution_net_quantity'] is None:raise Failure('institution_quantity_unverified')
    if f['institution_amount_raw'] in (None,''):raise Failure('institution_raw_amount_missing')
    return dict(symbol=code,market=market,asof=asof,foreign_net_quantity=f['foreign_net_quantity'],foreign_net_amount=f['foreign_net_amount'],
                institution_net_quantity=f['institution_net_quantity'],institution_amount_raw=f['institution_amount_raw'],institution_net_amount=None,
                **{k:p[k] for k in KRX_FIELDS},market_cap=None,validation_status='PENDING_KRX',kis_validation_status='PASS',official_price_status='PENDING_KRX',
                observation_mode='after_close_provisional',provider_finality='UNVERIFIED',exchange='KRX',adjustment='original',foreign_amount_unit='KRW',institution_amount_status='unit_unverified',
                flow_observed_at=fe['collected_at'],price_observed_at=pe['collected_at'],observation_ids=[sha(encoded(fe)),sha(encoded(pe))],source_hashes=[fe['source_hash'],pe['source_hash']])


def qualify(row,cache,store,round_id):
    result=qualify_kis(row,cache,store,round_id)
    code=row['symbol'];market=row['market'];ke=cache[identity(task('krx',ASOF,round=round_id,market=market))]
    maps=getattr(store,'reference_maps',{});refkey=sha(encoded(ke))
    if refkey not in maps:maps[refkey]={r['ISU_CD']:r for r in parse(store.raw(ke),ke)}
    store.reference_maps=maps
    if code not in maps[refkey]:raise Failure('KRX_symbol_missing_or_duplicate')
    ref=maps[refkey][code]
    diffs={k:{'kis':result[k],'krx':integer(ref[field]),'delta':result[k]-integer(ref[field])} for k,field in KRX_FIELDS.items() if result[k]!=integer(ref[field])}
    if diffs:
        exc=Failure('KIS_KRX_six_field_conflict');exc.details=diffs;exc.symbol=code;raise exc
    cap=integer(ref['MKTCAP'])
    if cap<=0:raise Failure('invalid_market_cap')
    result.update(market_cap=cap,validation_status='PASS',official_price_status='SIX_FIELD_MATCH',reference_observed_at=ke['collected_at'])
    result['observation_ids'].append(sha(encoded(ke)));result['source_hashes'].append(ke['source_hash'])
    return result


def plan(round_id,universe,kis_only=False):
    common=[task(k,ASOF,round=round_id,market=m) for k in (('index',) if kis_only else ('index','krx')) for m in ('KOSPI','KOSDAQ')]
    selected=[r for r in universe if r['eligible']]
    probe=[r for r in selected if r['symbol'] in trial.TARGETS]
    rest=[r for r in selected if r['symbol'] not in trial.TARGETS]
    tasks=lambda rows:[task(k,ASOF,round=round_id,symbol=r['symbol'],market=r['market']) for r in rows for k in ('flow','price')]
    if len(selected)!=2349 or len(probe)!=3:raise Failure('experiment_universe_not_verified')
    return common,tasks(probe),tasks(rest),selected


def run_round(root,round_id,universe,rate_proof,run_id,opener=None,config=None,*,kis_only=False,budget_factory=ExperimentBudget):
    if kis_only and opener is None:raise Failure('live_execution_requires_separate_approval_and_launcher')
    scheduled,deadline=window(round_id);metrics=Metrics();profile=config or {'gzip_level':9,'profile':'baseline'}
    store=Evidence(root,metrics,profile['gzip_level']);previous=store.read('report.json',{});start=previous.get('actual_started_at') or now();wall=time.monotonic();cache=replay(store,round_id)
    old_metrics=previous.get('metrics',{});metrics.seconds.update(old_metrics.get('seconds',{}));metrics.counts.update(old_metrics.get('counts',{}))
    attempts={}
    store.blocked_tasks=set()
    for e in events(store.root/'failures.jsonl'):
        attempts[identity(e['task'])]=max(attempts.get(identity(e['task']),0),e['attempt'])
        if not e.get('retryable',True):store.blocked_tasks.add(identity(e['task']))
    budget=budget_factory(store,round_id,metrics,run_id);api=ProvisionalAPI(store,budget,metrics,opener)
    common,probe,rest,selected=plan(round_id,universe,kis_only);qualifier=qualify_kis if kis_only else qualify;rows=[];errors=[];probe_proof=store.read('probe.json',{})
    status='UNRESOLVED'
    try:
        if rate_proof.get('status')!='PASS' or rate_proof.get('interval_seconds')!=1.05:raise Failure('request_policy_evidence_missing')
        # All three live probe rows and common official references qualify first.
        for t in common+probe:fetch_task(t,api,budget,cache,attempts,metrics)
        for row in selected:
            if row['symbol'] in trial.TARGETS:qualifier(row,cache,store,round_id)
        probe_proof=dict(status='PASS',round=round_id,symbols=sorted(trial.TARGETS),physical_http=budget.used,
                         mode='after_close_provisional',standard_next_day_pilot_status='NOT_RUN',validation_basis='KIS_INTERNAL' if kis_only else 'KIS_KRX_SIX_FIELDS',checked_at=now())
        store.write('probe.json',probe_proof);budget.promote(probe_proof)
        costs={}
        for kind,suffix in [('flow','investor-trade-by-stock-daily'),('price','inquire-daily-itemchartprice')]:
            key='api:'+Q+suffix;costs[kind]=max(1.05,metrics.seconds.get(key,0)/max(metrics.counts.get(key,0),1))
        projected=sum(costs[t['kind']] for t in rest if identity(t) not in cache)+60
        probe_proof.update(projected_remaining_seconds=projected,available_seconds=budget.remaining_seconds());store.write('probe.json',probe_proof)
        if projected>budget.remaining_seconds():raise Failure('insufficient_time_for_full_population_at_verified_rate')
        # Failed transport work alone is revisited; successful evidence is reused.
        pending=rest;health_streak=0;circuit_trips=0
        for recovery_pass in range(3):
            failed=[]
            for t in pending:
                if attempts.get(identity(t),0)>=6 or identity(t) in store.blocked_tasks:
                    failed.append(t);continue
                try:
                    fetch_task(t,api,budget,cache,attempts,metrics);health_streak=0
                except Failure as exc:
                    if exc.code in ('authentication_failure','token_expired','missing_KIS_APP_KEY','missing_KIS_APP_SECRET','network_policy_denied','local_storage_unavailable'):raise
                    failed.append(t)
                    if exc.code in ('network_failure','http_429','provider_rate_limit') or exc.code.startswith('http_5'):health_streak+=1
                if health_streak>=3:
                    circuit_trips+=1
                    if circuit_trips>=3 or budget.remaining_seconds()<180:raise Failure('provider_circuit_open')
                    begin=time.monotonic();time.sleep(120);metrics.add('provider_circuit_wait',time.monotonic()-begin);health_streak=0
            pending=[t for t in failed if attempts.get(identity(t),0)<6 and identity(t) not in store.blocked_tasks]
            if not pending:break
        # One bounded same-date reference refresh can recover stale publication.
        for row in selected:
            started=time.monotonic()
            try:rows.append(qualifier(row,cache,store,round_id))
            except (Failure,KeyError,ValueError,TypeError) as exc:
                errors.append(dict(symbol=row['symbol'],market=row['market'],reason=exc.code if isinstance(exc,Failure) else 'required_evidence_missing',details=getattr(exc,'details',None)))
            metrics.add('validation',time.monotonic()-started)
        conflicts={e['market'] for e in errors if e['reason']=='KIS_KRX_six_field_conflict'}
        refreshes=store.read('reference-refreshes.json',{})
        if not kis_only and conflicts and budget.remaining_seconds()>90:
            for market in sorted(conflicts):
                if refreshes.get(market,0)>=2:continue
                refreshes[market]=refreshes.get(market,0)+1;store.write('reference-refreshes.json',refreshes)
                t=task('krx',ASOF,round=round_id,market=market)
                cache.pop(identity(t),None);attempts.pop(identity(t),None)
                try:fetch_task(t,api,budget,cache,attempts,metrics)
                except Failure:pass
            rows=[];errors=[]
            for row in selected:
                try:rows.append(qualifier(row,cache,store,round_id))
                except (Failure,KeyError,ValueError,TypeError) as exc:errors.append(dict(symbol=row['symbol'],market=row['market'],reason=exc.code if isinstance(exc,Failure) else 'required_evidence_missing',details=getattr(exc,'details',None)))
        status='COMPLETE' if len(rows)==len(selected) and not errors else 'PARTIAL'
    except (Failure,Stop) as exc:errors.append(dict(reason=exc.code if isinstance(exc,Failure) else str(exc),symbol=getattr(exc,'symbol',None),details=getattr(exc,'details',None)))
    except Exception as exc:
        import traceback
        errors.append(dict(reason='unexpected_experiment_failure',exception_type=type(exc).__name__,locations=[{'file':Path(t.filename).name,'line':t.lineno,'function':t.name} for t in traceback.extract_tb(exc.__traceback__)]))
    # Qualify already-preserved successes even when the collection loop stopped.
    if not rows:
        for row in selected:
            try:rows.append(qualifier(row,cache,store,round_id))
            except (Failure,KeyError,ValueError,TypeError):pass
    # Even blocked/partial rounds have an explicit report; never infer completeness.
    complete_received=sum(all(identity(task(k,ASOF,round=round_id,symbol=r['symbol'],market=r['market'])) in cache for k in ('flow','price')) for r in selected)
    observed=[e['collected_at'] for e in cache.values()]
    hard={'authentication_failure','token_expired','missing_KIS_APP_KEY','missing_KIS_APP_SECRET','network_policy_denied','local_storage_unavailable','institution_quantity_unverified','institution_raw_amount_missing','flow_price_conflict','KIS_KRX_six_field_conflict','request_policy_evidence_missing','insufficient_time_for_full_population_at_verified_rate'}
    gate_exhausted=any(identity(t) not in cache and (attempts.get(identity(t),0)>=6 or identity(t) in store.blocked_tasks) for t in common+probe)
    can_retry=not gate_exhausted and budget.used<budget.limit and any(identity(t) not in cache and attempts.get(identity(t),0)<6 and identity(t) not in store.blocked_tasks for t in common+probe+rest) and not any(e['reason'] in hard for e in errors)
    if can_retry and status!='COMPLETE':status='PARTIAL / RECOVERING'
    report=dict(recovery_possible=can_retry,schema=1,round=round_id,asof=ASOF,status=status,source_run=run_id,scheduled_at=scheduled.isoformat(),actual_started_at=start,completed_at=now(),deadline=deadline.isoformat(),
                elapsed_seconds=(dt.datetime.fromisoformat(now())-dt.datetime.fromisoformat(start)).total_seconds(),this_run_elapsed_seconds=time.monotonic()-wall,start_delay_seconds=(dt.datetime.fromisoformat(start)-scheduled).total_seconds(),
                first_http_at=min((e['started_at'] for e in budget.entries if e['event']=='started'),default=None),last_http_at=max((e['completed_at'] for e in budget.entries if e['event']=='finished'),default=None),population_hash=sha(encoded(universe)),target_symbols=sorted(r['symbol'] for r in selected),universe_rows=len(universe),excluded=len(universe)-len(selected),
                target=len(selected),received=complete_received,validated=len(rows),failed_or_held=len(selected)-len(rows),success_rate=len(rows)/len(selected),
                physical_http=budget.used,physical_http_this_run=budget.used-budget.initial,provider_finality='UNVERIFIED',observation_mode='after_close_provisional',rows=rows,errors=errors,
                retries=sum(max(0,n-1) for n in attempts.values()),metrics=metrics.result(),config=profile,probe=probe_proof,
                observation_window={'first':min(observed) if observed else None,'last':max(observed) if observed else None},comparison_value_sha256=value_hash(rows))
    if kis_only:
        report.update(phase='KIS_PROVISIONAL',kis_validated=len(rows),kis_failed_or_held=len(selected)-len(rows),
                      validated=0,failed_or_held=len(selected),official_validation_status='PENDING_KRX',official_accepted=0,
                      kis_success_rate=len(rows)/len(selected),success_rate=0,
                      status='PROVISIONAL_COMPLETE' if status=='COMPLETE' else status)
        indices=[]
        for market in ('KOSPI','KOSDAQ'):
            e=cache.get(identity(task('index',ASOF,round=round_id,market=market)))
            if e:indices.append(dict(**parse(store.raw(e),e)[0],observed_at=e['collected_at'],source_hash=e['source_hash'],observation_id=sha(encoded(e))))
        report['indices']=indices
    t=time.monotonic()
    try:
        import duckdb
        path=store.root/'derived'/(sha(encoded(rows))+'.duckdb');path.parent.mkdir(exist_ok=True);existing=path.exists()
        with duckdb.connect(str(path),read_only=existing) as db:
            if not existing:
                db.execute('CREATE TABLE observations(symbol VARCHAR PRIMARY KEY,payload JSON)')
                db.execute('BEGIN');db.executemany('INSERT INTO observations VALUES (?,?)',[(r['symbol'],encoded(r).decode()) for r in rows]) if rows else None;db.execute('COMMIT')
            saved={s:json.loads(p) for s,p in db.execute('SELECT symbol,payload FROM observations').fetchall()}
            if saved!={r['symbol']:r for r in rows}:raise Failure('experiment_DB_JSON_mismatch')
        report['db_json_match']=True;report['database_file']=str(path.relative_to(store.root))
    except Exception:report.update(status='UNRESOLVED',db_json_match=False)
    metrics.add('DuckDB_batch',time.monotonic()-t)
    api_seconds=sum(v for k,v in metrics.seconds.items() if k.startswith('api:')) if previous.get('reason')!='process_or_preparation_interrupted' else None
    failures=events(store.root/'failures.jsonl');failed_keys={identity(e['task']) for e in failures}
    report['recovery']={'failed_attempts':len(failures),'recovered_tasks':len(failed_keys & set(cache)),'remaining_failed_tasks':len(failed_keys-set(cache)),'backoff_seconds':metrics.seconds.get('backoff_wait',0)+metrics.seconds.get('provider_circuit_wait',0)}
    report['metrics_complete']=api_seconds is not None
    report.update(bottleneck=max(metrics.seconds,key=metrics.seconds.get) if metrics.seconds else None,metrics=metrics.result(),pure_api_seconds=api_seconds,symbols_per_minute=complete_received/max(report['elapsed_seconds']/60,1e-9))
    begin=time.monotonic();store.write('report.json',report);store.write('universe.json',universe);metrics.add('JSON_generation_and_storage',time.monotonic()-begin);store.write('report.json',report)
    return report
