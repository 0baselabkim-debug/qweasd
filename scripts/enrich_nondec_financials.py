"""Recover 2025 non-December fiscal filings; never convert USD or invent disclosures."""
import sys,requests,concurrent.futures,json,gzip,collections,re,time
from pathlib import Path
import pandas as pd,duckdb
from dotenv import dotenv_values
import os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.financials import parse_full_response
ROOT=Path(__file__).resolve().parents[1];RAW=ROOT/'data/raw';OUT=ROOT/'data/enrichment';key=(os.environ.get('DART_API_KEY') or dotenv_values(ROOT/'.env').get('DART_API_KEY'))
corps={r['stock_code']:r['corp_code'] for r in json.loads((RAW/'dart_corp_codes.json').read_text())};groups=collections.defaultdict(list)
for p in RAW.glob('dart_major_2025_*.json'):
 for r in json.loads(p.read_text()).get('list',[]):groups[r['corp_code']].append(r)
with duckdb.connect(str(ROOT/'data/dashboard.duckdb'),read_only=True) as c:missing=c.execute('SELECT i.symbol FROM instruments i LEFT JOIN financials f USING(symbol) WHERE f.symbol IS NULL').fetchall()
def get(endpoint,params):
 try:
  r=requests.get('https://opendart.fss.or.kr/api/'+endpoint,params={'crtfc_key':key,**params},timeout=45);return r.json() if r.status_code==200 else {'http':r.status_code}
 except requests.RequestException as e:return {'error':type(e).__name__}
def fetch(row):
 code=row[0];corp=corps.get(code);major=groups[corp]
 if not corp:return None,{'symbol':code,'reason':'corp_code_missing'}
 if major and {r.get('currency') for r in major}!={'KRW'}:return None,{'symbol':code,'reason':'currency_not_KRW'}
 scopes=['CFS'] if any(r.get('fs_div')=='CFS' for r in major) else ['OFS'] if major else ['CFS','OFS']
 for scope in scopes:
  d=get('fnlttSinglAcntAll.json',{'corp_code':corp,'bsns_year':'2025','reprt_code':'11011','fs_div':scope})
  payload={'symbol':code,'scope':scope,'requested_receipt':d.get('list',[{}])[0].get('rcept_no'),'response':d,'origin':'live non-December recovery'}
  p=OUT/'dart_full'/f'{code}_{scope}_2025.json.gz'
  with gzip.open(p,'wt',encoding='utf-8') as f:json.dump(payload,f,ensure_ascii=False)
  if d.get('status')!='000':continue
  receipt=payload['requested_receipt'];listing=get('list.json',{'corp_code':corp,'bgn_de':'20250101','end_de':'20261014','pblntf_detail_ty':'A001','page_count':100})
  (OUT/f'dart_filing_extra_{code}.json').write_text(json.dumps(listing,ensure_ascii=False))
  filing=next((r for r in listing.get('list',[]) if r.get('rcept_no')==receipt),None)
  if not filing:return None,{'symbol':code,'reason':'disclosure_not_matched','receipt':receipt}
  match=re.search(r'\((\d{4})\.(\d{2})\)',filing['report_nm'])
  if not match or match[1]!='2025':return None,{'symbol':code,'reason':'period_not_2025'}
  try:accounts=parse_full_response(payload,receipt,scope)
  except ValueError as e:return None,{'symbol':code,'reason':str(e)}
  result={'symbol':code,'period_end':str((pd.Timestamp(int(match[1]),int(match[2]),1)+pd.offsets.MonthEnd()).date()),'disclosed_on':str(pd.Timestamp(filing['rcept_dt']).date()),'disclosure_verified':True,'scope':scope,'currency':'KRW','receipt':receipt,'source':'OpenDART full / actual non-December disclosure'}
  for k,v in accounts.items():
   result[k]=v['value']
   if k in ['revenue','operating_profit','equity']:result['previous_'+k]=v['previous']
  return result,{'symbol':code,'reason':'recovered','scope':scope,'period_end':result['period_end'],'disclosed_on':result['disclosed_on']}
 return None,{'symbol':code,'reason':'annual_2025_not_available','last_status':d.get('status',d.get('http'))}
additions=json.loads((OUT/'financial_additions.json').read_text()) if (OUT/'financial_additions.json').exists() else []
audit=json.loads((OUT/'financial_missing_diagnosis.json').read_text()) if (OUT/'financial_missing_diagnosis.json').exists() else []
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
 for record,result in ex.map(fetch,missing):
  if record:
   additions=[r for r in additions if r['symbol']!=record['symbol']]+[record]
  audit=[r for r in audit if r['symbol']!=result['symbol']]+[result];print(json.dumps(result,ensure_ascii=False),flush=True)
(OUT/'financial_additions.json').write_text(json.dumps(additions,ensure_ascii=False,indent=2));(OUT/'financial_missing_diagnosis.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
