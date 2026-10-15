"""Receive public evidence before applying operational (not finality) acceptance."""
import copy
import datetime as dt
import time
from pathlib import Path
from screener.technical_quality import POLICY,digest,daily_accepted
from screener.technical import validate,buy_guide

def verify_bundle(api,bundle,references):
    from scripts.technical_batch import crosscheck
    b=copy.deepcopy(bundle);b.pop('daily_acceptance',None)
    b=crosscheck(b,references)
    day=b['asof'];symbol=b['symbol']
    from zoneinfo import ZoneInfo
    if day>=dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat():
        b['acceptance_error']='Prior Seoul date required; final publication time unconfirmed'
        b['validation']=validate(b,day)
        return b
    response=api.request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',
        {'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':day.replace('-',''),
         'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''},'FHPTJ04160001')
    wanted={r['trade_date'] for r in b['flows'][-20:]};rows=[];seen=set()
    for r in response.get('output2',[]):
        if not r.get('stck_bsop_date'):continue
        date=dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat()
        if date not in wanted:continue
        if date in seen:raise ValueError('Duplicate flow date')
        seen.add(date)
        rows.append({'trade_date':date,'net_amount':int(r['frgn_ntby_tr_pbmn'])*1_000_000})
    rows.sort(key=lambda r:r['trade_date'])
    part={'KOSPI':'stk','KOSDAQ':'ksq'}[b['market']]
    b['daily_acceptance']={'policy':POLICY,'symbol':symbol,'asof':day,
        'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'krx_url':f'https://data-dbg.krx.co.kr/svc/apis/sto/{part}_bydd_trd?basDd='+day.replace('-',''),
        'krx_row':references.get(symbol,{}),'bars_sha256':digest(b['bars']),'flows_sha256':digest(b['flows']),
        'flow_endpoint':'KIS FHPTJ04160001 / J / million KRW to KRW','requeried_flows':rows,
        'limit':'Operational prior-day evidence only. No provider finality claim; later corrections remain possible.'}
    b['daily_acceptance']['status']='ACCEPTED_DAILY' if daily_accepted(b) else 'REVIEW_REQUIRED'
    b['validation']=validate(b,day)
    return b

def incremental_probe(api,bundle):
    """Replay a real D-1 -> D transition, without publishing or fabricating bars."""
    from scripts.technical_batch import price_rows
    old=copy.deepcopy(bundle);old['bars']=old['bars'][:-1];old['asof']=old['bars'][-1]['trade_date']
    old['corporate_action_observations']=[r for r in old.get('corporate_action_observations',[]) if r['trade_date']<=old['asof']]
    before=digest(bundle);start=len(api.metrics)
    def forbidden(*args,**kwargs):raise ValueError('Overlap revision requires full window; probe does not refetch')
    try:
        bars,events,mode=price_rows(api,bundle['symbol'],bundle['asof'],old,forbidden)
        return {'symbol':bundle['symbol'],'from':old['asof'],'to':bundle['asof'],'mode':mode,
                'matches_saved_320_bars':bars==bundle['bars'],'requests':len(api.metrics)-start,
                'source_preserved':digest(bundle)==before,'replay_not_live_new_trading_day':True}
    except Exception as e:return {'symbol':bundle['symbol'],'status':'review_required','error_type':type(e).__name__,'requests':len(api.metrics)-start}

def scope_probe(api,bundle):
    from scripts.technical_batch import chart
    old=bundle['price_comparison'].get('preserved_unadjusted');official=bundle.get('price_crosscheck',{}).get('raw_close_match')
    result={'symbol':bundle['symbol'],'asof':bundle['asof'],'legacy':old,'krx':bundle['price_comparison'].get('kis_unadjusted'),'official_krx_match':official}
    for market in ['UN','NX']:
        try:
            rows=chart(api,bundle['symbol'],bundle['asof'],bundle['asof'],market,'1').get('output2',[])
            rows=[r for r in rows if r.get('stck_bsop_date')==bundle['asof'].replace('-','')]
            result[market]=float(rows[0]['stck_clpr']) if len(rows)==1 else None
        except Exception as e:result[market]={'error_type':type(e).__name__}
    result['status']='MATCHES_CURRENT_UN' if old==result['UN'] else 'MATCHES_CURRENT_NX' if old==result['NX'] else 'UNRESOLVED'
    result['limit']='Matching a current market series does not prove the historic request route.'
    return result

def run(api,data,limit=600,budget=1500):
    from scripts.technical_batch import CACHE,ROOT,PRIORITY,read,write,krx_reference,refresh_summary
    start=time.monotonic();day=str(data['flows'].trade_date.max().date());refs,status=krx_reference(day)
    report=read(ROOT/'acceptance-audit.json',{'symbols':{},'incremental':{},'scope':{}})
    tasks=[]
    for path in sorted(CACHE.glob('*.json.gz')):
        b=read(path)
        if b and b['asof']==day and not daily_accepted(b):tasks.append((path,b))
    tasks.sort(key=lambda pair:(pair[1]['symbol'] not in PRIORITY,pair[1]['symbol']))
    for path,b in tasks[:limit]:
        if time.monotonic()-start>budget:break
        try:
            checked=verify_bundle(api,b,refs);write(path,checked)
            result=buy_guide(checked,day)
            report['symbols'][b['symbol']]={'asof':day,'accepted':daily_accepted(checked),'status':result['status'],'reasons':result['reasons']}
            # Continue the previously unresolved 86 using both documented market flags.
            if b.get('price_conflicts') and not b.get('legacy_price_discrepancy'):
                report['scope'][b['symbol']]=scope_probe(api,checked)
            if b['symbol'] in ['005930','000660','035420']:
                report['incremental'][b['symbol']]=incremental_probe(api,b)
        except Exception as e:report['symbols'][b['symbol']]={'asof':day,'accepted':False,'error_type':type(e).__name__}
        write(ROOT/'acceptance-audit.json',report)
    report.update(asof=day,finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),requests=len(api.metrics),elapsed_seconds=round(time.monotonic()-start,2),krx_status=status)
    write(ROOT/'acceptance-audit.json',report)
    refresh_summary(day)
    print({'verified':sum(r.get('accepted',False) for r in report['symbols'].values()),'requests':len(api.metrics)})
