"""Bounded, public-only re-observation of completed but price-conflicting symbols."""
import copy,datetime as dt,json,time
from pathlib import Path
from screener.technical import indicators,levels,reward_risk,rules,validate,buy_guide
from screener.technical_quality import daily_accepted,digest,LEGACY_CONFLICT,blocking_conflicts
from scripts.technical_acceptance import reference_ready,verify_bundle,selected_observations
from scripts import technical_batch as batch

class RecoveryBudget(Exception):pass

class BoundedAPI:
    """Physical-boundary caps for KIS, conservative fallback for other adapters."""
    def __init__(self,api,max_calls=60,seconds=240):
        self.api=api;self.start=time.monotonic();self.max_calls=max_calls;self.seconds=seconds
        self.used=0;self.observations=[];self.physical=[];self.logical=0;self.cache_hits=0
        self.reserved=0;self.refunded=0;self.sink=None;self.resume=[];self.anchors=[];self.generation=None;self.legacy_generation=None
        target=api.__dict__.get('factory',type(api))
        self.physical_supported=bool(getattr(target,'physical_budget_supported',False))
    def check(self):
        if self.used>=self.max_calls or time.monotonic()-self.start>=self.seconds:raise RecoveryBudget()
    def before_http(self,path,attempt):
        self.check()
        # One slot reserved and consumed at the actual transport boundary. No unused retries.
        self.used+=1;self.reserved+=1
        self.physical.append({'kind':'authentication' if path=='/oauth2/tokenP' else 'market','attempt':attempt+1})
    def begin(self,observations,sink):
        self.resume=observations;self.anchors=[];self.sink=sink;self.generation=None
        self.legacy_generation=None
        if len(observations)>=2 and not any(o.get('anchor_generation') for o in observations):
            first=observations[:2];keys=[self.signature(o['path'],o['params']) for o in first]
            if len(set(keys))==2 and all(sum(self.signature(o['path'],o['params'])==k for o in observations)==1 for k in keys):
                self.legacy_generation=self.anchor_digest(first)
    @staticmethod
    def anchor_digest(anchors):
        return digest([{'path':o['path'],'params':o['params'],'output2':o['output'].get('output2')} for o in anchors])
    @staticmethod
    def signature(path,params):return digest({'path':path,'params':params})
    def request(self,*args,**kwargs):
        from scripts.kis_public_client import REQUEST_BUDGET
        path=args[0];params=args[1] if len(args)>1 else kwargs.get('params',{})
        key=self.signature(path,params);self.logical+=1
        # Always reobserve the adjusted overlap and same-date raw anchor first.
        # A cache is usable only when BOTH anchors match the interrupted vintage.
        if len(self.anchors)==2:
            matches=[o for o in self.resume if self.signature(o['path'],o['params'])==key and o.get('anchor_generation',self.legacy_generation)==self.generation]
            if matches:
                value=copy.deepcopy(matches[-1]);self.cache_hits+=1
                value['anchor_generation']=self.generation
                self.observations.append(value)
                if self.sink:self.sink(value)
                return copy.deepcopy(value['output'])
        self.check()
        if not self.physical_supported:
            reserve=6 if self.api.__dict__.get('client',True) is None else 3
            if self.used+reserve>self.max_calls:raise RecoveryBudget()
            self.used+=reserve;self.reserved+=reserve
        token=REQUEST_BUDGET.set(self) if self.physical_supported else None
        try:response=self.api.request(*args,**kwargs)
        finally:
            if token is not None:REQUEST_BUDGET.reset(token)
        observation={'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'path':path,'params':params,
                     'output':{k:response[k] for k in ['output','output1','output2'] if k in response}}
        if len(self.anchors)<2:
            self.anchors.append(observation)
            if len(self.anchors)==2:self.generation=self.anchor_digest(self.anchors)
        if self.generation:observation['anchor_generation']=self.generation
        self.observations.append(observation)
        if self.sink:self.sink(observation) # survive process termination after any completed response
        return response
    def prices(self,symbol,start,end):return batch.chart(self,symbol,start,end)


def candidate(api,original,references,paged):
    """A coherent KIS J series, never an inserted KRX close or an in-place edit."""
    if not reference_ready(original,references):raise ValueError('REFERENCE_NOT_AVAILABLE_FOR_SYMBOL')
    b=copy.deepcopy(original);day=b['asof'];symbol=b['symbol'];oldbars=b['bars']
    def parse(rows):
        if any(r.get('stck_bsop_date','')>day.replace('-','') for r in rows):raise ValueError('response_after_requested_date')
        result=batch.parsed(rows,day)
        if day not in result:raise ValueError('requested_date_missing')
        return result
    overlap=parse(batch.chart(api,symbol,oldbars[-min(10,len(oldbars))]['trade_date'],day).get('output2',[]))
    raw=parse(batch.chart(api,symbol,day,day,adjustment='1').get('output2',[]))
    if set(raw)!={day}:raise ValueError('raw_response_date_mismatch')
    expected={r['trade_date']:r for r in oldbars[-10:]}
    rebuilt=(set(overlap)!=set(expected) or any(expected.get(d)!=v['bar'] or batch.action_present(v['event']) for d,v in overlap.items()))
    if rebuilt:
        # Full consistent adjustment vintage is required after an overlap revision.
        response=paged(api,symbol,day,count=max(320,len(oldbars)))
        series=parse([row for _,row in response])
        b['bars']=[v['bar'] for _,v in sorted(series.items())]
        b['corporate_action_observations']=[v['event'] for _,v in sorted(series.items())]
    else:
        b['bars']=copy.deepcopy(oldbars)
    if rebuilt:b['corporate_actions_verified']=all(e.get('rate') not in [None,''] and e.get('reason') is not None for e in b.get('corporate_action_observations',[]))
    b['corporate_actions']=[e for e in b.get('corporate_action_observations',[]) if batch.action_present(e)]
    b['observed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
    b['recovery']={'original_sha256':digest(original),'rebuilt_history':rebuilt,
                   'source':'KIS FHKST03010100 / J / flags 0 and 1','requested_date':day}
    rawclose=raw[day]['bar']['close'];b['price_comparison'].update(kis_unadjusted=rawclose,kis_adjusted=b['bars'][-1]['close'])
    b['price_comparison']['adjustment_difference']=b['bars'][-1]['close']!=rawclose
    # Only the new raw response can resolve this old availability error.
    b['price_conflicts']=[x for x in b.get('price_conflicts',[]) if x!='Same-date unadjusted KRX close unavailable']
    preserved=b['price_comparison'].get('preserved_unadjusted')
    if preserved is not None and preserved!=rawclose and LEGACY_CONFLICT not in b['price_conflicts']:b['price_conflicts'].append(LEGACY_CONFLICT)
    structural=validate({**b,'price_conflicts':[]},day,require_final=False)
    b['structure_errors']=structural;b['structure_valid']=not structural
    b['indicator_ready']=not [x for x in structural if '수급' not in x]
    if b['indicator_ready']:
        f=indicators(b['bars']);r=f.iloc[-1]
        b['computed']={k:float(r[k]) for k in ['ma5','ma20','ma60','ma120','tenkan','kijun','cloud_a','cloud_b','future_span_a','future_span_b','atr','prior_turnover20','recent_high','recent_low']}
        b['observed_levels']=levels(f);support=b['observed_levels']['support'];resistance=b['observed_levels']['resistance']
        stop=support[0]['lower']-rules()['buy']['stop_atr']*r.atr if support else None
        target=resistance[0]['lower'] if resistance else None
        b['observed_reward_risk']=reward_risk(r.close,stop,target) if stop is not None and target is not None else None
    return verify_bundle(api,b,references)


def fair_tasks(tasks,ledger,now):
    """Rotate dates, then oldest eligible attempt; paused work has a resume lane."""
    dates=sorted({t[3]['asof'] for t in tasks})
    last=ledger.get('last_processed_date','')
    dates=[d for d in dates if d>last]+[d for d in dates if d<=last]
    queues={d:[] for d in dates}
    for t in tasks:
        job=ledger['jobs'].setdefault(t[1],{})
        job.setdefault('first_seen_at',now)
        queues[t[3]['asof']].append(t)
    for q in queues.values():
        q.sort(key=lambda t:(ledger['jobs'][t[1]].get('last_result',{}).get('error_type')!='RecoveryBudget',
                             t[0] or ledger['jobs'][t[1]]['first_seen_at'],t[1]))
    while any(queues.values()):
        for d in dates:
            if queues[d]:yield queues[d].pop(0)


def run(api,data,limit=3,max_calls=60,seconds=240,symbols=None):
    from scripts.collect_technical import paged
    latest=str(data['flows'].trade_date.max().date());start=dt.datetime.now(dt.timezone.utc)
    ledger_path=batch.ROOT/'recovery-state.json';ledger=batch.read(ledger_path,{'jobs':{},'runs':[]})
    from scripts.publish_recovery import seed_dates
    seeded=seed_dates(data,limit=2,symbols=symbols) if 'prices' in data else []
    tasks=[];seen=set();refs_by_date={};status_by_date={};excluded_queue={'short_history':0,'short_flow_history':0,'backoff':0}
    # Completion flags never exclude a conflicting observation from this queue.
    paths=list(batch.CACHE.glob('*.json.gz'))+list((batch.ROOT/'history').glob('*/*.json.gz'))+list((batch.ROOT/'pending').glob('*/*.json.gz'))
    for path,b in selected_observations(paths,latest):
        if symbols and b['symbol'] not in symbols:continue
        key=b['asof']+'/'+b['symbol']
        if key in seen:continue
        seen.add(key);j=ledger['jobs'].get(key,{})
        if len(b.get('bars',[]))<rules()['min_bars']:
            excluded_queue['short_history']+=1;continue
        if len(b.get('flows',[]))<20:
            excluded_queue['short_flow_history']+=1;continue
        retry_after=j.get('retry_after','')
        if j.get('last_result',{}).get('error_type')=='RecoveryBudget' and j.get('attempted_at'):
            retry_after=(dt.datetime.fromisoformat(j['attempted_at'])+dt.timedelta(minutes=15)).isoformat()
        if retry_after>start.isoformat():
            excluded_queue['backoff']+=1;continue
        tasks.append((j.get('attempted_at',''),key,path,b))
    bounded=BoundedAPI(api,max_calls=min(60,max_calls),seconds=min(240,seconds));results=[];deferred_reference=[];not_conflicting=[]
    for _,key,path,b in fair_tasks(tasks,ledger,start.isoformat()):
        if len(results)>=min(10,limit) or time.monotonic()-bounded.start>=bounded.seconds:break
        day=b['asof']
        if day not in refs_by_date:
            # At most two dates' availability checks per run; retain older queues.
            if len(refs_by_date)>=2:continue
            refs_by_date[day],status_by_date[day]=batch.krx_reference(day,refresh=True)
        refs=refs_by_date[day]
        if not reference_ready(b,refs):
            deferred_reference.append(key);continue
        checked=batch.crosscheck(copy.deepcopy(b),refs)
        if checked.get('price_finality')!='CONFLICT' and not b.get('seed_provenance') and not blocking_conflicts(b):
            not_conflicting.append(key);continue
        job=ledger['jobs'].setdefault(key,{});attempts=job.get('attempts',0)+1
        observation_start=len(bounded.observations)
        before=digest(b)
        saved=[]
        previous=job.get('last_result',{})
        session_path=batch.ROOT/'recovery-sessions'/day/(b['symbol']+'.json')
        session=batch.read(session_path,{})
        if session.get('original_sha256')==before and session.get('status')=='in_progress':
            previous={**session,'error_type':'RecoveryBudget'}
        if previous.get('original_sha256')==before and previous.get('error_type')=='RecoveryBudget':
            for h in previous.get('response_observations',[]):
                o=batch.read(batch.ROOT/'provider-observations'/day/b['symbol']/(h+'.json.gz'))
                if o and digest(o)==h:saved.append(o)
        session={'original_sha256':before,'status':'in_progress','response_observations':[digest(o) for o in saved]}
        def persist(o):
            h=digest(o)
            batch.write(batch.ROOT/'provider-observations'/day/b['symbol']/(h+'.json.gz'),o)
            if h not in session['response_observations']:session['response_observations'].append(h)
            batch.write(session_path,session)
        bounded.begin(saved,persist)
        physical_start=bounded.used;cache_start=bounded.cache_hits;item_start=time.monotonic()
        result={'symbol':b['symbol'],'asof':day,'original_sha256':before,'promoted':False,'previously_blocking':bool(blocking_conflicts(b))}
        # Archive the complete original (including the previous flow evidence).
        batch.write(batch.CACHE.parent/'observations'/day/b['symbol']/(before+'.json.gz'),b)
        try:
            proposed=candidate(bounded,b,refs,paged)
            candidate_hash=digest(proposed)
            batch.write(batch.ROOT/'candidates'/day/b['symbol']/(candidate_hash+'.json.gz'),proposed)
            result.update(candidate_sha256=candidate_hash,reasons=validate(proposed,day),guide=buy_guide(proposed,day)['status'])
            if daily_accepted(proposed) and not result['reasons']:
                batch.write(path,proposed)
                # Update the dated pointer; do not replace a newer market observation.
                batch.write(batch.CACHE.parent/'history'/day/(b['symbol']+'.json.gz'),proposed)
                current=batch.read(batch.CACHE/(b['symbol']+'.json.gz'))
                if current and current['asof']==day:batch.write(batch.CACHE/(b['symbol']+'.json.gz'),proposed)
                result['promoted']=True
        except Exception as e:result['error_type']=type(e).__name__
        raw_evidence=bounded.observations[observation_start:]
        for observation in raw_evidence:
            batch.write(batch.ROOT/'provider-observations'/day/b['symbol']/(digest(observation)+'.json.gz'),observation)
        result['response_observations']=list(dict.fromkeys([digest(o) for o in saved+raw_evidence]))
        result.update(request_attempts=bounded.used-physical_start,cache_hits=bounded.cache_hits-cache_start,seconds=round(time.monotonic()-item_start,3))
        assert digest(b)==before
        delay=dt.timedelta(minutes=15) if result.get('error_type')=='RecoveryBudget' else dt.timedelta(hours=min(24,6*2**min(attempts-1,2)))
        job.update(attempted_at=start.isoformat(),attempts=attempts,
                   retry_after=(start+delay).isoformat(),last_result=result)
        session.update(status='paused' if result.get('error_type')=='RecoveryBudget' else 'finished',result=result)
        batch.write(session_path,session)
        ledger['last_processed_date']=day
        results.append(result);batch.write(ledger_path,ledger)
        if result.get('error_type')=='RecoveryBudget':break
    report={'asof':latest,'started_at':start.isoformat(),'finished_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'limit':min(10,limit),'max_physical_request_attempts':bounded.max_calls,'reserved_request_attempts':bounded.reserved,
            'physical_request_attempts':len(bounded.physical),'authentication_attempts':sum(x['kind']=='authentication' for x in bounded.physical),
            'retry_attempts':sum(x['attempt']>1 for x in bounded.physical),'unused_reservations':0,'refunded_reservations':bounded.refunded,
            'metering':'physical_boundary' if bounded.physical_supported else 'conservative_adapter_reservation',
            'logical_requests':bounded.logical,'cached_responses_reused':bounded.cache_hits,
            'run_id':__import__('os').environ.get('GITHUB_RUN_ID'),'event':__import__('os').environ.get('GITHUB_EVENT_NAME'),
            'scheduled_cron':__import__('os').environ.get('RECOVERY_SCHEDULE'),'excluded_from_price_recovery':excluded_queue,
            'time_budget_seconds':bounded.seconds,'seeded_date_tasks':seeded,'reference_status':status_by_date,'results':results,
            'promoted':sum(r['promoted'] for r in results),'kis_metrics':api.metrics,
            'queue':{'eligible_date_symbol_tasks':len(tasks),'attempted':len(results),'reference_deferred':len(deferred_reference),'not_price_conflicting':len(not_conflicting),
                     'carried_forward':len(tasks)-sum(r['promoted'] for r in results)-len(not_conflicting)},
            'budget_exhausted':bounded.used>=bounded.max_calls or time.monotonic()-bounded.start>=bounded.seconds,
            'reference_deferred_tasks':deferred_reference,'recovered_existing_blocked':sum(r['promoted'] and r['previously_blocking'] for r in results)}
    from scripts.recovery_outcome import outcome
    report['data_outcome']=outcome(report)
    ledger['runs']=(ledger['runs']+[report])[-30:];batch.write(ledger_path,ledger)
    batch.write(batch.ROOT/'recovery-report.json',report);batch.refresh_summary(latest)
    print(json.dumps({'examined_conflicts':len(results),'promoted':report['promoted'],'reserved_attempts':bounded.used}))
    return report
