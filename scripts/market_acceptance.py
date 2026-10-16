import copy,datetime as dt
from screener.liquidity_quality import POLICY,digest,daily_accepted
from screener.market_validation import validate
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
    from screener.liquidity_quality import FIELDS
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

def verify_bundle(api,bundle,references):
    from scripts.market_crosscheck import crosscheck
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
