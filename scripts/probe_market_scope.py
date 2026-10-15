"""New bounded KRX/NXT/integrated comparison; never replaces preserved market data."""
import datetime as dt,json,os
from pathlib import Path
from kis_public_client import KIS
api=KIS();out={'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'run_id':os.environ.get('GITHUB_RUN_ID'),'requested_date':'20261014','records':{},'basis':'FID_COND_MRKT_DIV_CODE official investor-trade-by-stock-daily specification: J KRX, NX NXT, UN integrated'}
for symbol in ['005930','000660','035420']:
 out['records'][symbol]={}
 for market in ['J','NX','UN']:
  try:
   d=api.request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',{'FID_COND_MRKT_DIV_CODE':market,'FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':'20261014','FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''},'FHPTJ04160001')
   rows=[r for r in d.get('output2',[]) if r.get('stck_bsop_date')=='20261014']
   out['records'][symbol][market]={'rows':[{k:r.get(k) for k in ['stck_bsop_date','stck_clpr','frgn_ntby_tr_pbmn','frgn_ntby_qty']} for r in rows]}
  except Exception as e:out['records'][symbol][market]={'error_type':type(e).__name__}
out['metrics']=api.metrics
text=json.dumps(out,ensure_ascii=False,indent=2)
assert all(s not in text for s in [api.key,api.secret,api.token])
Path('market-scope-probe.json').write_text(text+'\n');print(json.dumps({'symbols':3,'scopes':['J','NX','UN'],'saved':True}))
