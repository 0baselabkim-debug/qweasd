import concurrent.futures,json,requests,duckdb,zipfile,io,re
from pathlib import Path
from dotenv import dotenv_values
import os
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/enrichment';key=(os.environ.get('DART_API_KEY') or dotenv_values(ROOT/'.env').get('DART_API_KEY'))
SYMBOLS=['035420','010120','267260','005930','000660','277810','454910','012450','079550','034020','052690']
with duckdb.connect(str(ROOT/'data/dashboard.duckdb'),read_only=True) as c:records=c.execute('SELECT symbol,receipt FROM financials').fetchall()
receipts=dict(records)
def fetch(code):
 p=OUT/f'business_{code}.zip';receipt=receipts.get(code)
 if not receipt:return code,'no filing'
 if not p.exists():
  try:r=requests.get('https://opendart.fss.or.kr/api/document.xml',params={'crtfc_key':key,'rcept_no':receipt},timeout=60);p.write_bytes(r.content)
  except requests.RequestException as e:return code,type(e).__name__
 try:
  with zipfile.ZipFile(p) as z:
   xmls=[(i.filename,z.read(i)) for i in z.infolist() if i.filename.lower().endswith(('.xml','.html')) and i.file_size<30000000]
   text='\n'.join(b.decode('utf-8',errors='replace') for _,b in xmls)
  (OUT/f'business_{code}.xml').write_text(text)
  return code,receipt,len(text)
 except zipfile.BadZipFile:return code,'not zip'
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
 for x in ex.map(fetch,SYMBOLS):print(x,flush=True)
