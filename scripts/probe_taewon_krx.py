"""One official KOSPI date query; only target row retained, no collection retries."""
import datetime as dt,hashlib,json,os,urllib.request,urllib.error
from pathlib import Path
p=Path('reports/taewon-20261020/krx.json');key=os.getenv('KRX_API_KEY')
u='https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20261020'
out={'url':u,'requested_date':'20261020','symbol':'001420','observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'request_attempted':False,'rows':[]}
if not key:out['status']='CREDENTIAL_NOT_BOUND'
else:
 try:
  out['request_attempted']=True
  with urllib.request.urlopen(urllib.request.Request(u,headers={'AUTH_KEY':key}),timeout=25) as r:
   raw=r.read(16000000);out.update(http=r.status,response_sha256=hashlib.sha256(raw).hexdigest(),content_type=r.headers.get('Content-Type'))
  if key.encode() in raw:raise ValueError('Credential echo; refuse persistence')
  x=json.loads(raw);rows=x.get('OutBlock_1',[])
  out.update(received_rows=len(rows),returned_dates=sorted({r.get('BAS_DD','') for r in rows}),rows=[r for r in rows if r.get('ISU_CD')=='001420'],status='RECEIVED')
 except urllib.error.HTTPError as e:out.update(status='HTTP_ERROR',http=e.code)
 except Exception as e:out.update(status=type(e).__name__)
p.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False))
