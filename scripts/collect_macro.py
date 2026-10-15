"""Bounded public-data collection. Exceptions are sanitized; no token/key URLs logged."""
import argparse,csv,datetime as dt,io,json,os,sys,urllib.parse,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from screener.macro import catalog,record,read,merge,cutoff

def get(url,params=None):
 if params:url+='?'+urllib.parse.urlencode(params)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'korea-flow-research/1.0'}),timeout=25) as response:return response.read()
def fred(meta):
 key=os.getenv('FRED_API_KEY')
 if key:
  args={'api_key':key,'series_id':meta['series'],'file_type':'json'}
  info=json.loads(get('https://api.stlouisfed.org/fred/series',args))['seriess'][0]
  if info['frequency_short']!='D':raise ValueError('Not a daily series')
  unit=info['units']
  if (meta['rate'] and unit!='Percent') or (meta['id']=='wti' and unit!='Dollars per Barrel') or (meta['unit']=='포인트' and not unit.startswith('Index')):raise ValueError('Unexpected FRED unit')
  data=json.loads(get('https://api.stlouisfed.org/fred/series/observations',args|{'sort_order':'desc','limit':30}))['observations']
  meta=meta|{'official_title':info['title'],'official_unit':info['units'],'source_url':'https://fred.stlouisfed.org/series/'+meta['series']}
 else:
  # FRED's public export is the same official series, not a substitute series.
  raw=get('https://fred.stlouisfed.org/graph/graph.csv',{'id':meta['series'],'cosd':(dt.date.today()-dt.timedelta(days=65)).isoformat()})
  rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
  if not rows or meta['series'] not in rows[0]:raise ValueError('Unexpected official CSV columns')
  data=[{'date':r.get('observation_date',r.get('DATE')),'value':r[meta['series']]} for r in rows]
  meta=meta|{'source_url':'https://fred.stlouisfed.org/series/'+meta['series'],'transport':'official CSV export'}
 return record(meta,data)
def ecos(meta):
 key=os.getenv('ECOS_API_KEY')
 if not key:raise RuntimeError('MISSING_CREDENTIAL')
 today=dt.date.today();start=today-dt.timedelta(days=65)
 stat,item=meta['series'].split('/')
 rows=json.loads(get(f'https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/100/{stat}/D/{start:%Y%m%d}/{today:%Y%m%d}/{item}/'))['StatisticSearch']['row']
 name,unit=('국고채(3년)','연%') if meta['id']=='kr3y' else ('원/미국달러(매매기준율)','원')
 if any(r['ITEM_NAME1']!=name or r['UNIT_NAME']!=unit for r in rows):raise ValueError('Unexpected ECOS identity/unit')
 return record(meta|{'source_url':'https://ecos.bok.or.kr/','official_unit':unit,'daily_fixing':meta['id']=='usdkrw'},[{'date':dt.datetime.strptime(r['TIME'],'%Y%m%d').date().isoformat(),'value':r['DATA_VALUE']} for r in rows])
def kis_collect(candidate):
 from scripts.kis_public_client import KIS
 client=KIS();end=cutoff('Asia/Seoul').replace('-','');start=(dt.date.fromisoformat(cutoff('Asia/Seoul'))-dt.timedelta(days=40)).strftime('%Y%m%d')
 for key in ['kospi','kosdaq']:
  meta=catalog()[key]
  try:
   d=client.benchmark(meta['series'],start,end)
   rows=d.get('output2',[])
   candidate['indicators'][key]=record(meta|{'source_url':'https://apiportal.koreainvestment.com/apiservice'},[{'date':dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat(),'value':r['bstp_nmix_prpr']} for r in rows if r.get('stck_bsop_date')])
   candidate['attempts'][key]={'status':'success'}
  except Exception as e:candidate['attempts'][key]={'status':'failed','error_type':type(e).__name__}
 for market,code,board in [('KOSPI','0001','KSP'),('KOSDAQ','1001','KSQ')]:
  try:
   d=client.request('/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market',{'FID_COND_MRKT_DIV_CODE':'U','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':end,'FID_INPUT_ISCD_1':board,'FID_INPUT_DATE_2':end,'FID_INPUT_ISCD_2':code},'FHPTJ04040000')
   # Raw market statistics are retained as evidence, not treated as verified won amounts until official unit/scope evidence is available.
   candidate.setdefault('investor_probe',{})[market]={'output':d.get('output',[])[:3],'endpoint':'inquire-investor-daily-by-market','requested_day':end,'status':'unit_scope_pending'}
   candidate['attempts']['investor_'+market]={'status':'unit_scope_pending','data_date':max((r['stck_bsop_date'] for r in d.get('output',[]) if r.get('stck_bsop_date','')<=end),default=None)}
  except Exception as e:candidate['attempts']['investor_'+market]={'status':'failed','error_type':type(e).__name__}
 candidate['kis_metrics']=client.metrics

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--providers',default='FRED,ECOS,KIS');parser.add_argument('--candidate',default='macro-candidate.json');parser.add_argument('--publish',action='store_true');args=parser.parse_args()
 providers=args.providers.split(',');result={'schema':1,'indicators':{},'investors':{},'attempts':{},'last_attempt_at':dt.datetime.now(dt.timezone.utc).isoformat(),'credential_presence':{k:bool(os.getenv(k)) for k in ['FRED_API_KEY','ECOS_API_KEY','KIS_APP_KEY','KIS_APP_SECRET']}}
 for key,meta in catalog().items():
  if meta['provider'] not in providers or meta['provider']=='KIS':continue
  try:
   result['indicators'][key]=(fred if meta['provider']=='FRED' else ecos)(meta);result['attempts'][key]={'status':'success'}
  except Exception as e:result['attempts'][key]={'status':'missing_credential' if str(e)=='MISSING_CREDENTIAL' else 'failed','error_type':type(e).__name__,'http':getattr(e,'code',None)}
 if 'KIS' in providers:
  try:kis_collect(result)
  except Exception as e:
   for key in ['kospi','kosdaq','investor_KOSPI','investor_KOSDAQ']:result['attempts'][key]={'status':'failed','error_type':type(e).__name__}
 Path(args.candidate).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 if args.publish:
  target=ROOT/'snapshot/macro.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(merge(read(),result),ensure_ascii=False,indent=2)+'\n');temp.replace(target)
 print(json.dumps({'collected':list(result['indicators']),'attempts':result['attempts'],'credential_presence':result['credential_presence']},ensure_ascii=False))
if __name__=='__main__':main()
