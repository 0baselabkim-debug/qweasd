"""Independent daily published macro observations; never modifies the market snapshot."""
import datetime as dt,json,math
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'snapshot/macro.json'
CATALOG=[
 ('us2y','미국 국채 2년','금리','FRED','DGS2','%',True,'America/New_York',''),
 ('us10y','미국 국채 10년','금리','FRED','DGS10','%',True,'America/New_York',''),
 ('kr3y','한국 국고채 3년','금리','ECOS','817Y002/010200000','%',True,'Asia/Seoul',''),
 ('usdkrw','USD/KRW','환율·원자재','ECOS','731Y001/0000001','원',False,'Asia/Seoul','한국은행 공표 매매기준율 · 시장 종가와 구분'),
 ('vix','VIX','환율·원자재','FRED','VIXCLS','포인트',False,'America/New_York','CBOE 일별 종가'),
 ('wti','WTI 현물','환율·원자재','FRED','DCOILWTICO','달러/배럴',False,'America/New_York','EIA Cushing WTI 현물, 선물 아님'),
 ('sp500','S&P 500','미국 증시','FRED','SP500','포인트',False,'America/New_York','일별 종가'),
 ('nasdaq','나스닥 종합','미국 증시','FRED','NASDAQCOM','포인트',False,'America/New_York','일별 종가'),
 ('sox','필라델피아 반도체','미국 증시','FRED','NASDAQSOX','포인트',False,'America/New_York','일별 종가'),
 ('dow','다우존스 산업평균','미국 증시','FRED','DJIA','포인트',False,'America/New_York','일별 종가'),
 ('kospi','KOSPI','한국 증시','KIS','0001','포인트',False,'Asia/Seoul','KRX'),
 ('kosdaq','KOSDAQ','한국 증시','KIS','1001','포인트',False,'Asia/Seoul','KRX')]
def catalog():
 return {r[0]:dict(zip(['id','name','group','provider','series','unit','rate','timezone','basis'],r)) for r in CATALOG}
def read(path=PATH):
 return json.loads(Path(path).read_text()) if Path(path).exists() else {'schema':1,'indicators':{},'investors':{},'attempts':{},'history':[]}
def cutoff(zone,now=None):
 local=(now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(zone))
 # A same-day intraday price cannot be promoted to a completed daily close.
 return (local.date() if local.hour>=20 else local.date()-dt.timedelta(days=1)).isoformat()
def normalize(observations,last_allowed):
 result={}
 for row in observations:
  date=str(row['date']);dt.date.fromisoformat(date)
  if date>last_allowed:continue
  try:value=float(row['value'])
  except (TypeError,ValueError):continue
  if not math.isfinite(value):continue
  if date in result and result[date]!=value:raise ValueError('Conflicting observations on one date')
  result[date]=value
 return [{'date':k,'value':v} for k,v in sorted(result.items())]
def record(meta,observations,now=None):
 limit=((now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(meta['timezone'])).date().isoformat() if meta.get('daily_fixing') else cutoff(meta['timezone'],now))
 values=normalize(observations,limit)
 if len(values)<2:raise ValueError('Two published observations required')
 previous,latest=values[-2:];delta=latest['value']-previous['value']
 return {**meta,'observations':values,'date':latest['date'],'value':latest['value'],'previous_date':previous['date'],'previous_value':previous['value'],
         'change':delta*100 if meta['rate'] else delta,'change_unit':'bp' if meta['rate'] else meta['unit'],
         'change_percent':None if meta['rate'] or previous['value']<=0 or latest['value']<0 else delta/previous['value']*100,
         'observed_at':(now or dt.datetime.now(dt.timezone.utc)).isoformat(),'finality':'공급원 공표 일별값 · 추후 수정 가능'}
def merge(old,candidate):
 """Failures and older responses never erase a last-good observation; revisions are versioned."""
 out=json.loads(json.dumps(old));out.setdefault('history',[])
 for section in ['indicators','investors']:
  out.setdefault(section,{})
  for key,row in candidate.get(section,{}).items():
   if section=='indicators' and not valid_indicator(key,row):
    candidate.setdefault('attempts',{})[key]={'status':'validation_failed'}
    continue
   if section=='investors' and (row.get('unit')!='억원' or row.get('scope') not in ['KRX','NXT','통합'] or not row.get('unit_scope_evidence') or set(row.get('amounts',{}))!={'개인','외국인','기관'} or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in row.get('amounts',{}).values())):
    candidate.setdefault('attempts',{})['investor_'+key]={'status':'validation_failed'}
    continue
   prev=out[section].get(key)
   if prev and row['date']<prev['date']:continue
   if prev and prev.get('observations',prev.get('amounts'))!=row.get('observations',row.get('amounts')):out['history'].append({'section':section,'key':key,'record':prev})
   out[section][key]=row
 out.setdefault('attempts',{}).update(candidate.get('attempts',{}))
 out['last_attempt_at']=candidate.get('last_attempt_at');out['schema']=1
 return out


def valid_indicator(key,row):
 try:
  meta=catalog()[key]
  if row['id']!=key or row['unit']!=meta['unit'] or row['rate']!=meta['rate']:return False
  values=row['observations']
  if len(values)<2 or values!=normalize(values,'9999-12-31'):return False
  if any(v['value']<=0 for v in values) and key in ['usdkrw','vix','sp500','nasdaq','sox','dow','kospi','kosdaq']:return False
  a,b=values[-2:]
  if (row['date'],row['value'],row['previous_date'],row['previous_value'])!=(b['date'],b['value'],a['date'],a['value']):return False
  expected=(b['value']-a['value'])*(100 if row['rate'] else 1)
  if not math.isclose(row['change'],expected,rel_tol=1e-10,abs_tol=1e-8):return False
  percent=None if row['rate'] or a['value']<=0 or b['value']<0 else (b['value']-a['value'])/a['value']*100
  return row['change_percent'] is None if percent is None else math.isclose(row['change_percent'],percent,rel_tol=1e-10,abs_tol=1e-8)
 except (KeyError,ValueError,TypeError,IndexError):return False
