"""Public, unauthenticated evidence for the market endpoint itself; no inferred units."""
import datetime as dt,json,urllib.parse,urllib.request
from pathlib import Path

def main():
 base='https://apiportal.koreainvestment.com';endpoint='/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market'
 url=base+'/api/apis/public/detail?'+urllib.parse.urlencode({'accessUrl':endpoint})
 result={'endpoint':endpoint,'tr_id':'FHPTJ04040000','url':url,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(url,timeout=25) as r:detail=json.load(r)
  result['summary']=detail.get('apiSummary');result['fields_url']=base+'/api/apis/guide/property/'+detail['id']
  with urllib.request.urlopen(result['fields_url'],timeout=25) as r:fields=json.load(r)
  result['fields']=[{k:f.get(k) for k in ['propertyCd','propertyNm','description','bodyType']} for f in fields]
 except Exception as e:result.update(error_type=type(e).__name__,http=getattr(e,'code',None))
 # A human review of explicit endpoint-specific unit and market scope is required.
 result['publication_status']='unit_scope_pending'
 out=Path('liquidity/provider-spec/investor-market.json');out.parent.mkdir(parents=True,exist_ok=True)
 out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:result.get(k) for k in ['tr_id','publication_status','error_type','http']}))
if __name__=='__main__':main()
