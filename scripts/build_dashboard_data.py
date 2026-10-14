"""Import preserved observations without replacing enriched existing keys.

Initial/next-date import remains available; use enrich_snapshot.py for reviewed repairs.
No live API calls, generated prices, or zero-filling.
"""
import sys,json,re,collections
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.store import ROOT,DB,connect,insert_frame
from screener.financials import select_account
RAW=ROOT/'data/raw'
def number(v):
 if v is None or str(v).strip() in ['','-']:return None
 s=str(v).strip().replace(',','')
 if s.startswith('(') and s.endswith(')'):s='-'+s[1:-1]
 try:return float(s)
 except ValueError:return None

def parse_financials():
 corpfile=RAW/'dart_corp_codes.json'
 corp_map={r['corp_code']:r['stock_code'] for r in json.loads(corpfile.read_text())} if corpfile.exists() else {}
 disclosures={}
 for path in list(RAW.glob('dart_filings_*.json'))+list(RAW.glob('dart_annual_filings_*.json')):
  for r in json.loads(path.read_text()).get('list',[]):disclosures[r['rcept_no']]=r
 groups=collections.defaultdict(list)
 for path in RAW.glob('dart_major_2025_*.json'):
  for r in json.loads(path.read_text()).get('list',[]):groups[(r['corp_code'],r.get('fs_div',''),r['rcept_no'])].append(r)
 # Full accounts replace the matching CFS report only; never blend report revisions or scopes.
 for path in RAW.glob('dart_full_*_2025.json'):
  rows=json.loads(path.read_text()).get('list',[])
  if rows:
   corp_map.setdefault(rows[0]['corp_code'],path.name.split('_')[2]);groups[(rows[0]['corp_code'],'CFS',rows[0]['rcept_no'])]=rows
 preferred={}
 for (corp,scope,receipt),rows in groups.items():
  symbol=corp_map.get(corp)
  if not symbol:continue
  disclosure=disclosures.get(receipt)
  if not disclosure or disclosure.get('rcept_dt','')>'20261014':continue
  match=re.search(r'\((\d{4})\.(\d{2})\)',disclosure.get('report_nm',''))
  if not match:continue
  end=(pd.Timestamp(int(match[1]),int(match[2]),1)+pd.offsets.MonthEnd(0)).date()
  if end.year!=2025:continue
  # Require the supplier's explicit currency; no silent assumption or conversion.
  currencies={r.get('currency') for r in rows if r.get('currency')}
  if currencies!={'KRW'}:continue
  result={'symbol':symbol,'period_end':end,'disclosed_on':pd.Timestamp(disclosure['rcept_dt']).date(),'disclosure_verified':True,'scope':scope,'currency':'KRW','receipt':receipt,'source':'OpenDART'}
  for field in ['revenue','operating_profit','net_income','equity','liabilities','ocf']:
   account=select_account(rows,field);result[field]=account['value']
   if field in ['revenue','operating_profit','equity']:result['previous_'+field]=account['previous']
  key=(symbol,end)
  priority=(scope=='CFS',result['disclosed_on'])
  if key not in preferred or priority>preferred[key][0]:preferred[key]=(priority,result)
 return [r[1] for r in preferred.values()]

def main():
 snapshots=sorted(RAW.glob('kis_snapshot_*.json'))
 if not snapshots:raise ValueError('Real snapshot missing')
 counts={}
 with connect() as c:
  c.execute('BEGIN')
  try:
   for path in snapshots:
    b=json.loads(path.read_text());date=pd.Timestamp(b['trade_date']).date()
    if b['exchange']!='KRX' or not b['master_hash_verified']:raise ValueError('Unverified source scope/mapping')
    assert len({r['symbol'] for r in b['rows']})==len(b['rows'])
    idx={r['code']:r['name'] for r in b['indices']}
    instruments=[];members=[];flows=[];prices=[]
    krx={}
    for market in ['KOSPI','KOSDAQ']:
     source=RAW/f'krx_{market}_{b["trade_date"]}.json'
     if source.exists():
      for r in json.loads(source.read_text()).get('OutBlock_1',[]):
       if r.get('BAS_DD')==b['trade_date']:krx[r.get('ISU_CD','')]=r
    for r in b['rows']:
     symbol=r['symbol'];m=r['master'];sf=r['source_fields']
     if not re.fullmatch(r'\d{6}',symbol) or r['trade_date']!=b['trade_date']:raise ValueError('Invalid code/date')
     if int(sf['frgn_ntby_tr_pbmn'])*1000000!=r['foreign_net_amount_krw']:raise ValueError('Amount unit mismatch')
     if m['halt']!='N':raise ValueError('Halted symbol in success bundle')
     code=m['industry_medium'] if m['industry_medium']!='0000' else m['industry_large']
     sector=idx.get(code,'업종 미분류')
     special=m['spac']=='Y' or any(w in sector for w in ['금융','보험','증권','부동산']) or '리츠' in r['name']
     instruments.append([symbol,r['name'],r['market'],pd.Timestamp(r['listing_date']).date(),special])
     members.append([symbol,r['market'],sector,date,date,'KIS official KRX industry master; middle else large; no themes'])
     flows.append([symbol,date,r['foreign_net_amount_krw'],r['foreign_net_quantity_shares'],'KRX','KIS FHPTJ04160001',b['finality']])
     k=krx.get(symbol)
     prices.append([symbol,date,number(k['TDD_CLSPRC']) if k else number(sf.get('stck_clpr')),None,number(k['ACC_TRDVAL']) if k else number(sf.get('acml_tr_pbmn')),int(k['ACC_TRDVOL']) if k else int(sf['acml_vol']),number(k['MKTCAP']) if k else None,'KRX 공식' if k else 'KIS 수급응답·KRX (공식시세 대조 대기)','KRX'])
    for table,rows in [('instruments',instruments),('memberships',members),('flows',flows),('prices',prices)]:
     base_cols={'instruments':['symbol','name','market','listing_date','special'],'memberships':['symbol','market','sector','valid_from','valid_to','source'],'flows':['symbol','trade_date','net_amount','net_quantity','exchange','source','finality'],'prices':['symbol','trade_date','close','adjusted_close','turnover','volume','market_cap','source','exchange']}
     insert_frame(c,table,pd.DataFrame(rows,columns=base_cols[table]),preserve_existing=True)
    for market in ['KOSPI','KOSDAQ']:c.execute('INSERT OR IGNORE INTO calendar VALUES (?,?)',[market,date])
    counts[b['trade_date']]={'flow_rows':len(flows),'official_krx_price_rows':len(krx),'mapped_sectors':len({(r[1],r[2]) for r in members if r[2]!='업종 미분류'}),'unmapped_symbols':sum(r[2]=='업종 미분류' for r in members),'source_run':b['source_run_id'],'finality':b['finality']}
   financials=parse_financials()
   if financials:insert_frame(c,'financials',pd.DataFrame(financials),preserve_existing=True)
   excluded_path=Path('/workspace/kis-missing-analysis/classification-113.csv')
   if excluded_path.exists():
    excluded=pd.read_csv(excluded_path,dtype={'symbol':str});rows=excluded[['symbol','name']].copy();rows['trade_date']=pd.Timestamp('2026-10-14').date();rows['reason']=excluded.category;insert_frame(c,'exclusions',rows,preserve_existing=True)
   metadata={'snapshots':counts,'financial_symbols':c.execute('SELECT COUNT(DISTINCT symbol) FROM financials').fetchone()[0],'financial_policy':'2025 annual vs 2024; actual DART filing-date matched; CFS preferred; same-scope comparisons','krx_status':'2026-10-14 both APIs HTTP200 but empty','stage':'local dashboard / no deployment','updated_at':pd.Timestamp.now(tz='Asia/Seoul').isoformat()}
   for key,value in metadata.items():c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',[key,json.dumps(value,ensure_ascii=False)])
   c.execute('COMMIT')
  except Exception:c.execute('ROLLBACK');raise
 (ROOT/'reports/stage2/import.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(metadata,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
