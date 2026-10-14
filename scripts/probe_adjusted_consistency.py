"""Compare three official KRX chart/flow responses, retaining no authentication material."""
import datetime as dt,json,os
from pathlib import Path
from kis_public_client import KIS
api=KIS();out={'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'run_id':os.environ.get('GITHUB_RUN_ID'),'records':{}}
for code in ['005930','000660','035420']:
 r={}
 for flag in ['0','1']:
  p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':'20261012','FID_INPUT_DATE_2':'20261014','FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':flag}
  d=api.request('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',p,'FHKST03010100')
  r['chart_'+flag]={'output1':d.get('output1'),'output2':d.get('output2')}
 d=api.request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':'20261014','FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''},'FHPTJ04160001')
 r['flow']=d.get('output2',d.get('output'));out['records'][code]=r
out['metrics']=api.metrics
raw=json.dumps(out,ensure_ascii=False,indent=2)
assert all(v not in raw for v in [api.key,api.secret,api.token])
Path('reports/sector/adjustment-consistency-probe.json').write_text(raw+'\n')
print(json.dumps({'records':len(out['records']),'requests':len(api.metrics)}))
