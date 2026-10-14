"""Capture the provider's public field documentation, without authentication."""
import json,os,time,urllib.request,urllib.parse
from pathlib import Path
out=Path(os.environ.get('DASHBOARD_CHECK_OUTPUT','reports/live-dashboard'));out.mkdir(parents=True,exist_ok=True)
base='https://apiportal.koreainvestment.com';records=[]
for endpoint in ['inquire-price','investor-trade-by-stock-daily']:
 try:
  url=base+'/api/apis/public/detail?'+urllib.parse.urlencode({'accessUrl':'/uapi/domestic-stock/v1/quotations/'+endpoint})
  with urllib.request.urlopen(url,timeout=25) as r:detail=json.load(r)
  guide=base+'/api/apis/guide/property/'+detail['id']
  with urllib.request.urlopen(guide,timeout=25) as r:fields=json.load(r)
  selected=[r for r in fields if r.get('propertyCd','').lower() in ['hts_avls','lstn_stcn','frgn_ntby_tr_pbmn','frgn_ntby_qty','fid_cond_mrkt_div_code','stck_bsop_date']]
  records.append({'url':url,'fields_url':guide,'endpoint':endpoint,'summary':detail.get('apiSummary'),'fields':selected})
 except Exception as e:records.append({'endpoint':endpoint,'error_type':type(e).__name__,'status':getattr(e,'code',None)})
(out/'official-market-specs.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
print('Public specification endpoints checked:',len(records))
