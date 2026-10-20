"""Independent SQL universe and raw integer observations, no dashboard imports."""
import datetime as dt,math
from collections import defaultdict,Counter
import duckdb
from scripts.briefing_schema import UNKNOWN,sector_id

def reference(database,asof):
 day=dt.date.fromisoformat(asof)
 with duckdb.connect(str(database),read_only=True) as c:
  if c.execute("select max(trade_date) from flows where exchange='KRX'").fetchone()[0]!=day:raise ValueError('Raw asof mismatch')
  all_rows=c.execute('''select i.symbol,i.name,i.market,coalesce(m.sector,?),p.market_cap,p.volume,
      f.symbol is not null as has_flow,e.reason
      from instruments i left join memberships m on i.symbol=m.symbol and m.valid_from<=? and (m.valid_to is null or m.valid_to>=?)
      left join prices p on i.symbol=p.symbol and p.trade_date=? and p.exchange='KRX'
      left join flows f on i.symbol=f.symbol and f.trade_date=? and f.exchange='KRX'
      left join exclusions e on i.symbol=e.symbol and e.trade_date=? order by i.symbol''',[UNKNOWN,day,day,day,day,day]).fetchall()
  outside=c.execute('''select e.symbol,e.name,e.reason from exclusions e where trade_date=?
      and not exists(select 1 from instruments i where i.symbol=e.symbol) order by e.symbol''',[day]).fetchall()
  calendars={m:[r[0] for r in c.execute('select trade_date from calendar where market=? and trade_date<=? order by trade_date desc limit 20',[m,day]).fetchall()][::-1] for m in ('KOSPI','KOSDAQ')}
  if any(not d or d[-1]!=day or len(d)!=len(set(d)) for d in calendars.values()):raise ValueError('Calendar mismatch')
  rows=c.execute("select symbol,trade_date,net_amount from flows where exchange='KRX' and trade_date between ? and ?",[min(d[0] for d in calendars.values()),day]).fetchall()
 if len({r[0] for r in all_rows})!=len(all_rows):raise ValueError('Duplicate asof memberships/universe')
 observations={}
 for code,date,value in rows:
  if (code,date) in observations:raise ValueError('Duplicate raw observations')
  if value is not None and not math.isfinite(value):raise ValueError('Nonfinite raw flow')
  observations[code,date]=value
 def metrics(codes,market,cap):
  days=calendars[market]
  daily=[]
  for date in days:
   vals=[observations.get((code,date)) for code in codes]
   daily.append(sum(vals) if all(v is not None for v in vals) else None)
  def total(n,offset=0):
   vals=daily[-(n+offset):len(daily)-offset if offset else None]
   return sum(vals) if len(vals)==n and all(v is not None for v in vals) else None
  net5=total(5);prev=total(5,5)
  return {'net_1d':total(1),'net_5d':net5,'net_20d':total(20),'previous_5d':prev,
   'change_5d':net5-prev if net5 is not None and prev is not None else None,
   'intensity_5d':net5/cap if net5 is not None and cap is not None and math.isfinite(cap) and cap>0 else None,
   'positive_days_20d':sum(v>0 for v in daily) if len(daily)==20 and all(v is not None for v in daily) else None,
   'observed_days_20d':sum(v is not None for v in daily)}
 stocks={};groups=defaultdict(list);registered=Counter();omitted=[]
 for code,name,market,sector,cap,volume,has_flow,reason in all_rows:
  sid=sector_id(market,sector);registered[sid]+=1
  reasons=([reason] if reason is not None else [])
  if volume is not None and volume<=0:reasons.append('nonpositive_volume')
  status='excluded' if reasons else 'not_observed' if not has_flow else 'included'
  if status!='included':
   omitted.append({'symbol':code,'name':name,'market':market,'sector_id':sid,'status':status,'reasons':reasons or ['no_current_flow']});continue
  if market not in calendars:raise ValueError('Unsupported market')
  stocks[code]={'symbol':code,'name':name,'market':market,'sector_id':sid,'sector':sector,'cap':cap,**metrics([code],market,cap)}
  groups[sid].append(code)
 for code,name,reason in outside:
  omitted.append({'symbol':code,'name':name,'market':None,'sector_id':None,'status':'excluded','reasons':[reason]})
 sectors={}
 for sid,codes in sorted(groups.items()):
  first=stocks[codes[0]];caps=[stocks[code]['cap'] for code in codes]
  cap=sum(caps) if all(v is not None and math.isfinite(v) and v>0 for v in caps) else None
  record={'market':first['market'],'sector':first['sector'],'members':len(codes),'registered_members':registered[sid],**metrics(codes,first['market'],cap)}
  for n in (1,5,20):
   valid=[stocks[code]['net_'+str(n)+'d'] for code in codes if stocks[code]['net_'+str(n)+'d'] is not None]
   record['evaluable_'+str(n)+'d']=len(valid)
   record['positive_'+str(n)+'d']=sum(v>0 for v in valid)
   record['breadth_'+str(n)+'d']=sum(v>0 for v in valid)/len(valid)*100 if valid else None
  sectors[sid]=record
 coverage={'registered_instruments':len(all_rows),'population_total':len(all_rows)+len(outside),
  'included':len(stocks),'excluded':sum(r['status']=='excluded' for r in omitted),
  'not_observed':sum(r['status']=='not_observed' for r in omitted),
  'missing_5d':sum(s['net_5d'] is None for s in stocks.values()),'missing_20d':sum(s['net_20d'] is None for s in stocks.values()),
  'unclassified_included':sum(s['sector']==UNKNOWN for s in stocks.values()),
  'omitted':sorted(omitted,key=lambda r:r['symbol'])}
 if coverage['population_total']!=len(stocks)+len(omitted):raise ValueError('Population mismatch')
 return stocks,sectors,groups,coverage
