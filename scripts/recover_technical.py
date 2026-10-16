"""Bounded, public-only re-observation of completed but price-conflicting symbols."""
import copy,datetime as dt,json,time
from pathlib import Path
from screener.technical import indicators,levels,reward_risk,rules,validate,buy_guide
from screener.technical_quality import daily_accepted,digest,LEGACY_CONFLICT
from scripts.technical_acceptance import reference_ready,verify_bundle
from scripts import technical_batch as batch

class RecoveryBudget(Exception):pass

class BoundedAPI:
    # KIS retries a request at most three times. Reserve all three before dispatch.
    def __init__(self,api,max_calls=60,seconds=240):
        self.api=api;self.start=time.monotonic();self.max_calls=max_calls;self.seconds=seconds
        self.used=0;self.observations=[]
    def request(self,*args,**kwargs):
        reserve=6 if getattr(self.api,'__dict__',{}).get('client',True) is None else 3
        if self.used+reserve>self.max_calls or time.monotonic()-self.start>=self.seconds:raise RecoveryBudget()
        self.used+=reserve
        response=self.api.request(*args,**kwargs)
        self.observations.append({'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'path':args[0],'params':args[1] if len(args)>1 else {},
            'output':{k:response[k] for k in ['output','output1','output2'] if k in response}})
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


def run(api,data,limit=3,max_calls=60,seconds=240):
    from scripts.collect_technical import paged
    latest=str(data['flows'].trade_date.max().date());start=dt.datetime.now(dt.timezone.utc)
    ledger_path=batch.ROOT/'recovery-state.json';ledger=batch.read(ledger_path,{'jobs':{},'runs':[]})
    tasks=[];seen=set();refs_by_date={};status_by_date={}
    # Completion flags never exclude a conflicting observation from this queue.
    for path in list(batch.CACHE.glob('*.json.gz'))+list((batch.ROOT/'history').glob('*/*.json.gz'))+list((batch.ROOT/'pending').glob('*/*.json.gz')):
        b=batch.read(path)
        if not b or b['asof']>latest or daily_accepted(b):continue
        key=b['asof']+'/'+b['symbol']
        if key in seen:continue
        seen.add(key);j=ledger['jobs'].get(key,{})
        retry_after=j.get('retry_after','')
        if j.get('last_result',{}).get('error_type')=='RecoveryBudget' and j.get('attempted_at'):
            retry_after=(dt.datetime.fromisoformat(j['attempted_at'])+dt.timedelta(minutes=15)).isoformat()
        if retry_after>start.isoformat():continue
        tasks.append((j.get('attempted_at',''),key,path,b))
    bounded=BoundedAPI(api,max_calls=min(60,max_calls),seconds=min(240,seconds));results=[]
    for _,key,path,b in sorted(tasks,key=lambda x:(x[0],x[3]['asof']!=latest,x[1])):
        if len(results)>=min(10,limit) or time.monotonic()-bounded.start>=bounded.seconds:break
        day=b['asof']
        if day not in refs_by_date:
            # At most two dates' availability checks per run; retain older queues.
            if len(refs_by_date)>=2:continue
            refs_by_date[day],status_by_date[day]=batch.krx_reference(day,refresh=True)
        refs=refs_by_date[day]
        if not reference_ready(b,refs):continue
        checked=batch.crosscheck(copy.deepcopy(b),refs)
        if checked.get('price_finality')!='CONFLICT':continue
        job=ledger['jobs'].setdefault(key,{});attempts=job.get('attempts',0)+1
        observation_start=len(bounded.observations)
        before=digest(b);result={'symbol':b['symbol'],'asof':day,'original_sha256':before,'promoted':False}
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
        result['response_observations']=[digest(o) for o in raw_evidence]
        assert digest(b)==before
        delay=dt.timedelta(minutes=15) if result.get('error_type')=='RecoveryBudget' else dt.timedelta(hours=min(24,6*2**min(attempts-1,2)))
        job.update(attempted_at=start.isoformat(),attempts=attempts,
                   retry_after=(start+delay).isoformat(),last_result=result)
        results.append(result);batch.write(ledger_path,ledger)
        if result.get('error_type')=='RecoveryBudget':break
    report={'asof':latest,'started_at':start.isoformat(),'finished_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'limit':min(10,limit),'max_physical_request_attempts':bounded.max_calls,'reserved_request_attempts':bounded.used,
            'time_budget_seconds':bounded.seconds,'reference_status':status_by_date,'results':results,
            'promoted':sum(r['promoted'] for r in results),'kis_metrics':api.metrics}
    ledger['runs']=(ledger['runs']+[report])[-30:];batch.write(ledger_path,ledger)
    batch.write(batch.ROOT/'recovery-report.json',report);batch.refresh_summary(latest)
    print(json.dumps({'examined_conflicts':len(results),'promoted':report['promoted'],'reserved_attempts':bounded.used}))
    return report
