"""Bounded date-specific turnover recovery, independent of trading signals."""
import copy,datetime as dt,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from scripts.market_io import read,write,chart,parsed,ROOT
from scripts.market_budget import BoundedAPI,RecoveryBudget,fair_tasks
from scripts.market_reference import fetch_references
from scripts.market_acceptance import LazyAPI,reference_ready,verify_saved_or_live
from scripts.kis_public_client import KIS
from screener.liquidity_quality import digest,daily_accepted
from screener.market_validation import validate
from screener.liquidity_evidence import permits,price_identity
from screener.store import load,PACKAGED_DB

def tasks_for(data,ledger,now,symbols=None):
    tasks=[];prices=data['prices'];latest=str(data['flows'].trade_date.max().date())
    instruments=data['instruments'].set_index('symbol')
    for day,rows in sorted(data['metadata'].get('adjusted_price_conflicts',{}).items()):
        if day>latest:continue
        for r in rows:
            code=r['symbol'];key=day+'/'+code
            if symbols and code not in symbols:continue
            p=prices[(prices.symbol==code)&(prices.trade_date==pd.Timestamp(day))&(prices.exchange=='KRX')]
            if len(p)!=1 or code not in instruments.index:continue
            old=data['metadata'].get('liquidity_price_evidence',{}).get(day,{}).get(code)
            if old and permits(p.iloc[0].to_dict(),old,latest):continue
            j=ledger['jobs'].get(key,{})
            if j.get('retry_after','')>now:continue
            market=instruments.loc[code,'market']
            calendar=sorted(data['calendar'].loc[(data['calendar'].market==market)&(data['calendar'].trade_date<=pd.Timestamp(day)),'trade_date'].dt.strftime('%Y-%m-%d').unique())[-25:]
            if len(calendar)!=25 or calendar[-1]!=day:continue
            flow=data['flows'];flow=flow[(flow.symbol==code)&(flow.exchange=='KRX')&flow.trade_date.isin(pd.to_datetime(calendar[-20:]))].sort_values('trade_date')
            if len(flow)!=20:continue
            b={'symbol':code,'market':market,'asof':day,'calendar':calendar,'exchange':'KRX','adjustment':'adjusted','finality':'UNVERIFIED','flows':[{'trade_date':str(x.trade_date.date()),'net_amount':int(x.net_amount)} for x in flow.itertuples()], 'price_comparison':{'preserved_unadjusted':float(p.iloc[0].close)},'price_conflicts':[]}
            tasks.append((j.get('attempted_at',''),key,None,b))
    return tasks

def candidate(api,base,refs):
    b=copy.deepcopy(base);day=b['asof'];code=b['symbol']
    response=chart(api,code,b['calendar'][0],day);rows=response.get('output2',[])
    if any(r.get('stck_bsop_date','')>day.replace('-','') for r in rows):raise ValueError('future_price_date')
    bars=parsed(rows,day)
    # One coherent 25-day adjusted window; no long history or mixed-price splicing.
    if sorted(bars)!=b['calendar']:raise ValueError('incomplete_25_day_window')
    raw_rows=chart(api,code,day,day,adjustment='1').get('output2',[])
    if any(r.get('stck_bsop_date') and r['stck_bsop_date']!=day.replace('-','') for r in raw_rows):raise ValueError('raw_price_date_mismatch')
    raw=parsed(raw_rows,day)
    if list(raw)!=[day]:raise ValueError('raw_price_date_mismatch')
    b['bars']=[bars[d]['bar'] for d in b['calendar']]
    b['price_comparison'].update(kis_adjusted=b['bars'][-1]['close'],kis_unadjusted=raw[day]['bar']['close'])
    b['observed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
    return verify_saved_or_live(api,b,refs)

def run(data,limit=6,max_calls=60,seconds=240,symbols=None,api=None):
    started=dt.datetime.now(dt.timezone.utc).isoformat();state=read(ROOT/'recovery-state.json',{'jobs':{},'runs':[]});state.setdefault('jobs',{})
    transport=api or LazyAPI(KIS);bounded=BoundedAPI(transport,min(max_calls,60),min(seconds,240));results=[];references={};reference_status={}
    for _,key,_,base in fair_tasks(tasks_for(data,state,started,symbols),state,started):
        if len(results)>=min(limit,6) or time.monotonic()-bounded.start>=bounded.seconds:break
        day=base['asof'];code=base['symbol'];job=state['jobs'][key]
        if day not in references:
            if len(references)>=2:continue
            references[day],reference_status[day]=fetch_references(day,ROOT,refresh=True)
        refs=references[day]
        if not reference_ready(base,refs):
            job.update(attempted_at=started,retry_after=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)).isoformat(),status='reference_unavailable');continue
        seed_hash=digest(base);session_path=ROOT/'sessions'/day/(code+'.json');session=read(session_path,{})
        hashes=session.get('responses',[]) if session.get('seed_sha256')==seed_hash else []
        saved=[read(ROOT/'provider-observations'/day/code/(h+'.json.gz')) for h in hashes]
        saved=[v for v,h in zip(saved,hashes) if v and digest(v)==h]
        session={'seed_sha256':seed_hash,'responses':list(hashes)}
        def persist(value):
            h=digest(value);write(ROOT/'provider-observations'/day/code/(h+'.json.gz'),value)
            if h not in session['responses']:session['responses'].append(h)
            write(session_path,session)
        bounded.begin(saved,persist);before=bounded.used;row={'symbol':code,'asof':day,'accepted':False};attempt=job.get('attempts',0)+1
        try:
            proposed=candidate(bounded,base,refs);h=digest(proposed);write(ROOT/'candidates'/day/code/(h+'.json.gz'),proposed)
            issues=validate(proposed,day);row.update(reasons=issues,candidate_sha256=h)
            if daily_accepted(proposed) and not issues:
                write(ROOT/'accepted'/day/(code+'.json.gz'),proposed);row['accepted']=True
        except Exception as exc:row['error_type']=type(exc).__name__
        row.update(physical_requests=bounded.used-before,response_observations=session['responses'])
        delay=900 if row.get('error_type')=='RecoveryBudget' else min(86400,3600*2**min(attempt-1,4))
        job.update(attempted_at=started,attempts=attempt,last_result=row,retry_after='' if row['accepted'] else (dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=delay)).isoformat())
        state['last_processed_date']=day;results.append(row);write(ROOT/'recovery-state.json',state)
    result={'run_id':os.getenv('GITHUB_RUN_ID','local'),'event':os.getenv('GITHUB_EVENT_NAME','local'),'started_at':started,'finished_at':dt.datetime.now(dt.timezone.utc).isoformat(),'limit':min(limit,6),'physical_requests':bounded.used,'physical_attempts':bounded.physical,'cache_hits':bounded.cache_hits,'reference_status':reference_status,'results':results,'accepted':sum(r['accepted'] for r in results),'publication':'NOT_YET_PUBLISHED','api_metrics':transport.metrics}
    state.setdefault('runs',[]).append(result);write(ROOT/'recovery-state.json',state);write(ROOT/'recovery-report.json',result);return result

if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=6);p.add_argument('--symbols',default='');a=p.parse_args()
    if not 1<=a.limit<=6:raise ValueError('limit must be 1..6')
    print(json.dumps(run(load(PACKAGED_DB),limit=a.limit,symbols=set(a.symbols.split(',')) if a.symbols else None),ensure_ascii=False))
