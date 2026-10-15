"""Bounded seven-symbol diagnostics. Only public market fields are persisted."""
import datetime as dt,json,os,urllib.request,urllib.parse
from pathlib import Path
from scripts.technical_batch import PRIORITY,chart,read,write,CACHE

def run(api,data):
    asof=str(data['flows'].trade_date.max().date());out={'asof':asof,'run_id':os.getenv('GITHUB_RUN_ID'),'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'official_documents':[],'symbols':{},'krx':{}}
    base='https://apiportal.koreainvestment.com'
    for endpoint in ['inquire-daily-itemchartprice','investor-trade-by-stock-daily']:
        url=base+'/api/apis/public/detail?'+urllib.parse.urlencode({'accessUrl':'/uapi/domestic-stock/v1/quotations/'+endpoint})
        try:
            with urllib.request.urlopen(url,timeout=20) as r:d=json.load(r)
            fields_url=base+'/api/apis/guide/property/'+d['id']
            with urllib.request.urlopen(fields_url,timeout=20) as r:fields=json.load(r)
            out['official_documents'].append({'url':url,'fields_url':fields_url,'summary':d.get('apiSummary'),'fields':[{k:x.get(k) for k in ['propertyCd','propertyNm','description','bodyType']} for x in fields],'finality_field_candidates':[x.get('propertyCd') for x in fields if any(w in str(x.get('propertyNm',''))+str(x.get('description','')) for w in ['확정','최종'])]})
        except Exception as e:out['official_documents'].append({'url':url,'error_type':type(e).__name__,'http':getattr(e,'code',None)})
    for symbol in PRIORITY:
        item={};b=read(CACHE/(symbol+'.json.gz'),{})
        for market,flag in [('J','0'),('J','1'),('NX','1'),('UN','1')]:
            try:
                d=chart(api,symbol,asof,asof,market,flag);rows=[r for r in d.get('output2',[]) if r.get('stck_bsop_date')==asof.replace('-','')]
                item[market+'_'+flag]=[{k:r.get(k) for k in ['stck_bsop_date','stck_clpr','stck_oprc','stck_hgpr','stck_lwpr','prtt_rate','revl_issu_reas','acml_vol','acml_tr_pbmn']} for r in rows]
            except Exception as e:item[market+'_'+flag]={'error_type':type(e).__name__}
        try:
            d=api.request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':asof.replace('-',''),'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''},'FHPTJ04160001')
            rows=[r for r in d.get('output2',[]) if r.get('stck_bsop_date')]
            mapped={dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat():r for r in rows}
            item['flow_mismatches']=[r['trade_date'] for r in b.get('flows',[]) if r['trade_date'] not in mapped or int(mapped[r['trade_date']]['frgn_ntby_tr_pbmn'])*1000000!=r['net_amount']]
            item['flow_latest_close']=mapped.get(asof,{}).get('stck_clpr');item['flow_checked_dates']=len(b.get('flows',[]))
            if item['flow_mismatches']:
                b['price_conflicts'].append('Requeried KRX flow differs from preserved source')
                from screener.technical import validate
                b['validation']=validate(b,asof);write(CACHE/(symbol+'.json.gz'),b)
        except Exception as e:item['flow_check_error']=type(e).__name__
        item['preserved_comparison']=b.get('price_comparison');out['symbols'][symbol]=item
    # Independent official KRX daily data, only if an existing key is bound. Never rotate/request keys here.
    key=os.environ.get('KRX_API_KEY')
    for market,path in [('KOSPI','stk'),('KOSDAQ','ksq')]:
        url='https://data-dbg.krx.co.kr/svc/apis/sto/'+path+'_bydd_trd?basDd='+asof.replace('-','')
        if not key:out['krx'][market]={'status':'credential_not_bound'};continue
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'AUTH_KEY':key}),timeout=25) as r:d=json.load(r)
            rows=d.get('OutBlock_1',[]);out['krx'][market]={'url':url,'received_rows':len(rows),'samples':[r for r in rows if r.get('ISU_CD') in PRIORITY]}
        except Exception as e:out['krx'][market]={'url':url,'error_type':type(e).__name__,'http':getattr(e,'code',None)}
    from scripts.technical_batch import crosscheck
    references={r['ISU_CD']:r for m in out['krx'].values() for r in m.get('samples',[])}
    for symbol in PRIORITY:
        bundle=read(CACHE/(symbol+'.json.gz'))
        if bundle:write(CACHE/(symbol+'.json.gz'),crosscheck(bundle,references,out['symbols'].get(symbol)))
    out['corporate_probe']=corporate_probe(api,asof)
    out['requests_including_collection']=len(api.metrics);out['api_metrics']=api.metrics
    serialized=json.dumps(out,ensure_ascii=False)
    assert all(v not in serialized for v in [api.key,api.secret,api.token])
    write(Path('reports/technical-expansion/provider-evidence.json'),out)

def corporate_probe(api,asof):
    """Public KSD schedule feed, not an account-rights query. Incomplete coverage is explicit."""
    from scripts.technical_batch import ROOT
    start=(dt.date.fromisoformat(asof)-dt.timedelta(days=900)).strftime('%Y%m%d');records=[]
    previous={s['endpoint']:s for s in read(ROOT/'corporate-actions.json.gz',{}).get('sources',[])}
    for name,tr in [('rev-split','HHKDB669105C0'),('merger-split','HHKDB669104C0'),('bonus-issue','HHKDB669101C0'),('cap-dcrs','HHKDB669106C0')]:
        path='/uapi/domestic-stock/v1/ksdinfo/'+name
        params={'SHT_CD':'','CTS':'','F_DT':start,'T_DT':asof.replace('-','')}
        if name=='rev-split':params['MARKET_GB']='0'
        record={'endpoint':path,'from':start,'through':asof,'official_example':'https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/ksdinfo_'+name.replace('-','_')}
        try:
            d=api.request(path,params,tr);rows=d.get('output1',[])
            if isinstance(rows,dict):rows=[rows]
            record.update(rows=rows,received=len(rows),continuation=getattr(api,'last_continuation',None),coverage='FIRST_PAGE_ONLY_NOT_COMPLETE_ACTION_LEDGER',status='received')
        except Exception as exc:record.update(status='failed',error_type=type(exc).__name__)
        record=merge_corporate_source(previous.get(path),record)
        records.append(record)
    result={'asof':asof,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'sources':records,'complete':False,'reason':'Pagination and complete split/rights coverage require verification; personal quantities/costs never adjusted'}
    raw=json.dumps(result,ensure_ascii=False);assert all(v not in raw for v in [api.key,api.secret,api.token])
    write(ROOT/'corporate-actions.json.gz',result)
    from scripts.technical_batch import corporate_observations
    for path in CACHE.glob('*.json.gz'):
        b=read(path)
        if not b:continue
        b['official_corporate_action_observations']=corporate_observations(b['symbol'],b['asof'])
        b['corporate_actions_verified']=False
        write(path,b)
    return {'received':sum(r.get('received',0) for r in records),'failed_endpoints':sum(r['status']=='failed' for r in records),'complete':False}


def merge_corporate_source(previous,current):
    """Keep already received facts even if a subsequent schedule page fails or shifts."""
    previous=previous or {};known={json.dumps(r,sort_keys=True,ensure_ascii=False):r for r in previous.get('rows',[])}
    known.update({json.dumps(r,sort_keys=True,ensure_ascii=False):r for r in current.get('rows',[])})
    current['rows']=list(known.values());current['stored_rows']=len(known)
    current['previous_rows_preserved']=len(previous.get('rows',[]))
    current['last_success_through']=current['through'] if current.get('status')=='received' else previous.get('last_success_through',previous.get('through'))
    return current
