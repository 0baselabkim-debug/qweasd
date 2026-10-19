"""Independent SQL/raw-observation reconciliation, not dashboard aggregators."""
import datetime as dt,math
from collections import defaultdict
import duckdb

def equal(actual,expected,label):
    if expected is None:
        if actual is not None:raise ValueError('Missing observation fabricated: '+label)
    elif actual is None or not math.isfinite(float(actual)) or (actual!=expected if isinstance(expected,int) else not math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-12)):
        raise ValueError('Raw value mismatch: '+label)

def verify(result,database):
    day=dt.date.fromisoformat(result['asof'])
    with duckdb.connect(str(database),read_only=True) as c:
        if c.execute("select max(trade_date) from flows where exchange='KRX'").fetchone()[0]!=day:raise ValueError('Latest raw date differs from briefing')
        members=c.execute('''select i.symbol,i.name,i.market,coalesce(m.sector,'업종 미분류'),p.market_cap
            from flows f join instruments i using(symbol)
            left join memberships m on m.symbol=f.symbol and m.valid_from<=? and (m.valid_to is null or m.valid_to>=?)
            left join prices p on p.symbol=f.symbol and p.trade_date=f.trade_date and p.exchange='KRX'
            where f.trade_date=? and f.exchange='KRX' and (p.volume is null or p.volume>0)
            and not exists(select 1 from exclusions e where e.symbol=f.symbol and e.trade_date=f.trade_date)''',[day,day,day]).fetchall()
        if len({r[0] for r in members})!=len(members):raise ValueError('Duplicate membership/universe')
        calendars={m:[r[0] for r in c.execute('select trade_date from calendar where market=? and trade_date<=? order by trade_date desc limit 20',[m,day]).fetchall()][::-1] for m in ['KOSPI','KOSDAQ']}
        if any(not days or days[-1]!=day or len(days)!=len(set(days)) for days in calendars.values()):raise ValueError('Trading calendar date/uniqueness mismatch')
        earliest=min(d for days in calendars.values() for d in days)
        rows=c.execute("select symbol,trade_date,net_amount from flows where exchange='KRX' and trade_date between ? and ?",[earliest,day]).fetchall()
    observations={}
    for code,date,amount in rows:
        if (code,date) in observations:raise ValueError('Duplicate raw flow')
        observations[code,date]=amount
    def total(codes,dates,n):
        if len(dates)!=n:return None
        values=[observations.get((code,date)) for code in codes for date in dates]
        return sum(values) if all(v is not None for v in values) else None
    def metrics(codes,market):
        days=calendars[market]
        values={f'net_{n}d':total(codes,days[-n:],n) for n in [1,5,20]}
        prior=total(codes,days[-10:-5],5)
        values['change_5d']=values['net_5d']-prior if values['net_5d'] is not None and prior is not None else None
        return values
    groups=defaultdict(list);stocks={}
    for code,name,market,sector,cap in members:
        groups[market,sector].append(code)
        stocks[code]={'name':name,'market':market,'cap':cap,**metrics([code],market)}
    totals={key:metrics(codes,key[0]) for key,codes in groups.items()}
    ranked=sorted([key for key,v in totals.items() if key[1]!='업종 미분류' and v['net_5d'] is not None and v['net_5d']>0],key=lambda k:(-totals[k]['net_5d'],*k))[:3]
    if len(ranked)!=3:raise ValueError('Insufficient eligible positive sectors for TOP 3')
    if [(s['market'],s['sector']) for s in result['top_sectors']]!=ranked:raise ValueError('Sector ranking mismatch')
    checks=0;extra=0
    for rank,entry in enumerate(result['top_sectors'],1):
        key=entry['market'],entry['sector'];codes=groups[key]
        if entry['rank']!=rank:raise ValueError('Sector rank number mismatch')
        for field,v in totals[key].items():
            equal(entry[field],v,key[1]+'/'+field)
            if field=='change_5d':extra+=1
            else:checks+=1
        valid=[code for code in codes if stocks[code]['net_5d'] is not None];positive=[code for code in valid if stocks[code]['net_5d']>0]
        caps=[stocks[code]['cap'] for code in codes]
        cap=sum(caps) if all(v is not None and math.isfinite(v) and v>0 for v in caps) else None
        for k,v in {'members':len(codes),'evaluable_5d':len(valid),'positive_5d':len(positive),'breadth_5d':len(positive)/len(valid)*100 if valid else None,'intensity_5d':totals[key]['net_5d']/cap if cap else None}.items():equal(entry[k],v,k);extra+=1
        ranked_stocks=sorted(valid,key=lambda code:(-stocks[code]['net_5d'],code))[:5]
        if [s['symbol'] for s in entry['top_stocks']]!=ranked_stocks:raise ValueError('Stock ranking mismatch')
        for stock in entry['top_stocks']:
            raw=stocks[stock['symbol']]
            if stock['name']!=raw['name']:raise ValueError('Stock identity mismatch')
            for field in ['net_1d','net_5d','net_20d','change_5d']:
                equal(stock[field],raw[field],stock['symbol']+'/'+field)
                if field=='change_5d':extra+=1
                else:checks+=1
    return {'status':'passed','checks':checks,'windows':[1,5,20],'additional_value_checks':extra,'sector_rank_checks':3,'stock_rank_checks':sum(len(s['top_stocks']) for s in result['top_sectors']),'universe_count':len(members)}
