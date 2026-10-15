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
 ('usdkrw','USD/KRW','환율·원자재','FRED','DEXKOUS','원',False,'America/New_York','미 연준 뉴욕 정오 매입환율, 시장 종가와 구분'),
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
 values=normalize(observations,cutoff(meta['timezone'],now))
 if len(values)<2:raise ValueError('Two published observations required')
 previous,latest=values[-2:];delta=latest['value']-previous['value']
 return {**meta,'observations':values,'date':latest['date'],'value':latest['value'],'previous_date':previous['date'],'previous_value':previous['value'],
         'change':delta*100 if meta['rate'] else delta,'change_unit':'bp' if meta['rate'] else meta['unit'],
         'change_percent':None if meta['rate'] or previous['value']==0 else delta/previous['value']*100,
         'observed_at':(now or dt.datetime.now(dt.timezone.utc)).isoformat(),'finality':'공급원 공표 일별값 · 추후 수정 가능'}
def merge(old,candidate):
 """Failures and older responses never erase a last-good observation; revisions are versioned."""
 out=json.loads(json.dumps(old));out.setdefault('history',[])
 for section in ['indicators','investors']:
  out.setdefault(section,{})
  for key,row in candidate.get(section,{}).items():
   prev=out[section].get(key)
   if prev and row['date']<prev['date']:continue
   if prev and prev!=row:out['history'].append({'section':section,'key':key,'record':prev})
   out[section][key]=row
 out.setdefault('attempts',{}).update(candidate.get('attempts',{}))
 out['last_attempt_at']=candidate.get('last_attempt_at');out['schema']=1
 return out
