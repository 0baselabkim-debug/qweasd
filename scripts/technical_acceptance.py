"""Receive public evidence before applying operational (not finality) acceptance."""
import copy
import datetime as dt
import os,time
from pathlib import Path
from screener.technical_quality import POLICY,digest,daily_accepted
from screener.technical import validate,buy_guide,rules

class LazyAPI:
    """No KIS token issuance until an evidence-ready symbol needs a request."""
    def __init__(self,factory):self.factory=factory;self.client=None
    @property
    def metrics(self):return self.client.metrics if self.client is not None else []
    def __getattr__(self,name):
        if self.client is None:self.client=self.factory()
        return getattr(self.client,name)
    def request(self,*args,**kwargs):
        if self.client is None:self.client=self.factory()
        return self.client.request(*args,**kwargs)

def reference_ready(bundle,references):
    """A symbol's same-date official row must exist before any KIS recheck."""
    row=references.get(bundle['symbol'],{})
    from screener.technical_quality import FIELDS
    try:
        import math
        observed=dt.datetime.fromisoformat(row['_retrieved_at'])
        from zoneinfo import ZoneInfo
        part={'KOSPI':'stk','KOSDAQ':'ksq'}[bundle['market']]
        url=f'https://data-dbg.krx.co.kr/svc/apis/sto/{part}_bydd_trd?basDd='+bundle['asof'].replace('-','')
        return (row['BAS_DD']==bundle['asof'].replace('-','') and row['ISU_CD']==bundle['symbol']
                and row['_source_url']==url
                and observed.tzinfo is not None
                and observed.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()>bundle['asof']
                and all(math.isfinite(float(row[k])) for k in FIELDS.values()))
    except (KeyError,ValueError,TypeError,OverflowError):return False

def archive_pending(bundle):
    from scripts.technical_batch import ROOT,write
    if daily_accepted(bundle):return
    path=ROOT/'pending'/bundle['asof']/(bundle['symbol']+'.json.gz')
    if not path.exists():write(path,bundle)

def verify_bundle(api,bundle,references):
    from scripts.technical_batch import crosscheck
    if not reference_ready(bundle,references):
        # Preserve any previous evidence; absence of a new reference is not a revision.
        b=copy.deepcopy(bundle)
        b['acceptance_error']='REFERENCE_NOT_AVAILABLE_FOR_SYMBOL'
        b['validation']=validate(b,b['asof'])
        return b
    b=copy.deepcopy(bundle)
    previous=b.pop('daily_acceptance',None)
    if previous:
        history=b.setdefault('acceptance_history',{})
        history.setdefault(digest(previous),previous)
    b.pop('acceptance_error',None)
    b=crosscheck(b,references)
    day=b['asof'];symbol=b['symbol']
    if b.get('price_finality')=='CONFLICT':
        b['acceptance_error']='REFERENCE_PRICE_CONFLICT'
        b['validation']=validate(b,day)
        return b
    from zoneinfo import ZoneInfo
    if day>=dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat():
        b['acceptance_error']='Prior Seoul date required; final publication time unconfirmed'
        b['validation']=validate(b,day)
        return b
    response=api.request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',
        {'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':day.replace('-',''),
         'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''},'FHPTJ04160001')
    wanted={r['trade_date'] for r in b['flows'][-20:]};rows=[];raw=[];seen=set()
    for r in response.get('output2',[]):
        if not r.get('stck_bsop_date'):continue
        date=dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat()
        if date not in wanted:continue
        if date in seen:raise ValueError('Duplicate flow date')
        seen.add(date)
        rows.append({'trade_date':date,'net_amount':int(r['frgn_ntby_tr_pbmn'])*1_000_000})
        raw.append({k:r[k] for k in ['stck_bsop_date','frgn_ntby_tr_pbmn']})
    rows.sort(key=lambda r:r['trade_date'])
    part={'KOSPI':'stk','KOSDAQ':'ksq'}[b['market']]
    b['daily_acceptance']={'policy':POLICY,'symbol':symbol,'asof':day,
        'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'krx_url':f'https://data-dbg.krx.co.kr/svc/apis/sto/{part}_bydd_trd?basDd='+day.replace('-',''),
        'krx_row':references.get(symbol,{}),'bars_sha256':digest(b['bars']),'flows_sha256':digest(b['flows']),
        'flow_endpoint':'KIS FHPTJ04160001 / J / million KRW to KRW','checked_flows':rows,
        'flow_check_kind':'live_requery','raw_flow_rows':raw,
        'limit':'Operational prior-day evidence only. No provider finality claim; later corrections remain possible.'}
    b['daily_acceptance']['status']='ACCEPTED_DAILY' if daily_accepted(b) else 'REVIEW_REQUIRED'
    b['validation']=validate(b,day)
    return b

def accept_saved_responses(bundle,references,raw_rows,sources):
    """Revalidate existing real API responses; never describe this as a new call."""
    class SavedResponse:
        def request(self,*args,**kwargs):return {'output2':raw_rows}
    checked=verify_bundle(SavedResponse(),bundle,references)
    if checked.get('daily_acceptance'):
        checked['daily_acceptance'].update(flow_check_kind='saved_api_response_replay',flow_sources=sources)
        checked['daily_acceptance']['status']='ACCEPTED_DAILY' if daily_accepted(checked) else 'REVIEW_REQUIRED'
        checked['validation']=validate(checked,checked['asof'])
    return checked

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

def live_incremental_probe(api,bundle):
    """Receive a possible new day, but keep this preview outside published bundles."""
    from zoneinfo import ZoneInfo
    from scripts.technical_batch import price_rows
    end=dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    if end<=bundle['asof']:return {'status':'no_later_calendar_date'}
    start=len(api.metrics);before=digest(bundle)
    def forbidden(*args,**kwargs):raise ValueError('Revision or adjustment requires full rebuild; preview leaves source unchanged')
    try:
        bars,events,mode=price_rows(api,bundle['symbol'],end,bundle,forbidden)
        return {'symbol':bundle['symbol'],'from':bundle['asof'],'requested_through':end,'mode':mode,
            'requests':len(api.metrics)-start,'new_bars':[r for r in bars if r['trade_date']>bundle['asof']],
            'source_preserved':digest(bundle)==before,'published':False,
            'limit':'Collection-only preview. Not joined to old flows or used for signals.'}
    except Exception as e:return {'symbol':bundle['symbol'],'status':'review_required','error_type':type(e).__name__,'requests':len(api.metrics)-start,'published':False}

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

def review_pending(api,references,asof,limit=100,budget=150):
    from scripts.technical_batch import CACHE,read,write
    started=time.monotonic();pending=[]
    for path in CACHE.glob('*.json.gz'):
        b=read(path)
        if b and b['asof']==asof and not daily_accepted(b) and reference_ready(b,references):pending.append((path,b))
    pending.sort(key=lambda pair:(pair[1].get('acceptance_attempted_at',''),pair[1]['symbol']))
    for path,b in pending[:limit]:
        if time.monotonic()-started>=budget:break
        try:b=verify_saved_or_live(api,b,references)
        except Exception as e:b['acceptance_error']=type(e).__name__
        b['acceptance_attempted_at']=dt.datetime.now(dt.timezone.utc).isoformat();write(path,b)

def verify_saved_or_live(api,bundle,references):
    """Reuse exact saved raw flow responses; an absent/mismatched proof needs KIS."""
    e=bundle.get('daily_acceptance',{});raw=e.get('raw_flow_rows',[])
    try:
        normalized=sorted([{'trade_date':dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat(),
                            'net_amount':int(r['frgn_ntby_tr_pbmn'])*1_000_000} for r in raw],key=lambda r:r['trade_date'])
        reusable=(len(raw)==20 and normalized==bundle['flows'][-20:]
                  and e.get('flow_endpoint')=='KIS FHPTJ04160001 / J / million KRW to KRW'
                  and e.get('flow_check_kind') in ('live_requery','saved_api_response_replay')
                  and e.get('asof')==bundle['asof'] and e.get('symbol')==bundle['symbol']
                  and e.get('flows_sha256')==digest(bundle['flows']))
    except (KeyError,ValueError,TypeError):reusable=False
    if reusable:
        sources=e.get('flow_sources') or [{'kind':'previously_received_KIS_J_response',
                    'observed_at':e['observed_at'],'raw_rows_sha256':digest(raw)}]
        return accept_saved_responses(bundle,references,raw,sources)
    return verify_bundle(api,bundle,references)


def selected_observations(paths,day,probe=False):
    """One exact-date observation per symbol; accepted evidence outranks old queues."""
    from scripts.technical_batch import read
    chosen={}
    for path in paths:
        b=read(path)
        if not b or b['asof']>day:continue
        key=(b['asof'],b['symbol']);previous=chosen.get(key)
        if previous is None or (daily_accepted(b) and not daily_accepted(previous[1])):chosen[key]=(path,b)
    return [(path,b) for path,b in chosen.values() if probe or not daily_accepted(b)]


def run(api,data,limit=600,budget=1500,probe=False):
    from scripts.technical_batch import CACHE,ROOT,read,write,krx_reference,refresh_summary
    start=time.monotonic();day=str(data['flows'].trade_date.max().date())
    report=read(ROOT/'acceptance-audit.json',{'symbols':{},'incremental':{},'scope':{}})
    tasks=selected_observations(list(CACHE.glob('*.json.gz'))+list((ROOT/'history').glob('*/*.json.gz'))+list((ROOT/'pending').glob('*/*.json.gz')),day,probe)
    # Dates do not disappear when a new dashboard snapshot is published.
    dates=sorted({b['asof'] for _,b in tasks});all_status={};evaluated=0;deferred=0;short=0
    for date in dates:
        if time.monotonic()-start>budget:break
        refs,status=krx_reference(date);all_status[date]=status
        same=[(p,b) for p,b in tasks if b['asof']==date]
        same.sort(key=lambda pair:(pair[1].get('acceptance_attempted_at',''),pair[1]['symbol']))
        for path,b in same:
            if not reference_ready(b,refs):deferred+=1;continue
            if len(b.get('bars',[]))<rules()['min_bars'] or len(b.get('flows',[]))<20:
                short+=1;continue
            if evaluated>=limit or time.monotonic()-start>budget:break
            try:
                checked=verify_bundle(api,b,refs) if probe else verify_saved_or_live(api,b,refs)
                checked['acceptance_attempted_at']=dt.datetime.now(dt.timezone.utc).isoformat()
                write(path,checked)
                result=buy_guide(checked,date)
                item={'asof':date,'accepted':daily_accepted(checked),'status':result['status'],'reasons':result['reasons']}
                report.setdefault('by_date',{}).setdefault(date,{})[b['symbol']]=item
                if date==day:report['symbols'][b['symbol']]=item
                # Optional diagnostics are bounded and never run for an unavailable reference.
                if probe and b.get('price_conflicts') and not b.get('legacy_price_discrepancy'):
                    report['scope'][b['symbol']]=scope_probe(api,checked)
                if probe and b['symbol'] in ['005930','000660','035420']:
                    report['incremental'][b['symbol']]=incremental_probe(api,b)
                    report.setdefault('live_incremental',{})[b['symbol']]=live_incremental_probe(api,b)
            except Exception as e:
                b['acceptance_error']=type(e).__name__
                b['acceptance_attempted_at']=dt.datetime.now(dt.timezone.utc).isoformat();write(path,b)
                report.setdefault('by_date',{}).setdefault(date,{})[b['symbol']]={'asof':date,'accepted':False,'error_type':type(e).__name__}
            evaluated+=1
        if evaluated>=limit:break
    unprocessed=max(0,len(tasks)-evaluated-deferred-short)
    report.update(asof=day,run_id=os.getenv('GITHUB_RUN_ID'),finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        requests=len(api.metrics),api_errors=sum(m['http']!=200 or m['code'] not in ('','MCA00000') for m in api.metrics),
        rate_limit_errors=sum(m['code']=='EGW00201' for m in api.metrics),elapsed_seconds=round(time.monotonic()-start,2),
        krx_status=all_status.get(day,{}),reference_dates=all_status,evaluated=evaluated,
        deferred_no_reference=deferred,deferred_short_history=short,unprocessed=unprocessed,
        outcome='REVIEWED' if evaluated else 'BUDGET_EXHAUSTED' if unprocessed else 'WAITING_REFERENCE' if deferred else 'NO_ELIGIBLE_REVIEW')
    write(ROOT/'acceptance-audit.json',report)
    refresh_summary(day)
    summary=read(ROOT/'summary.json',{});summary['krx_reference_status']=all_status.get(day,summary.get('krx_reference_status',{}))
    summary['verification_run']={k:report[k] for k in ['run_id','finished_at','outcome','evaluated','requests','deferred_no_reference','deferred_short_history']}
    write(ROOT/'summary.json',summary)
    print({k:report[k] for k in ['outcome','evaluated','requests','deferred_no_reference','reference_dates']})
    return report
