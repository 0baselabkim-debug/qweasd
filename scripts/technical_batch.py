"""Resumable public KRX observations. Private symbols/positions never enter this module."""
import collections,datetime as dt,gzip,json,math,os,time
from pathlib import Path
from zoneinfo import ZoneInfo
from screener.technical import validate,indicators,levels,buy_guide,reward_risk,rules
PRIORITY=['003280','005880','003160','006800','005930','000660','035420']
ROOT=Path('technical');CACHE=ROOT/'market';STATE=ROOT/'collection-state.json'
FIELDS={'open':'stck_oprc','high':'stck_hgpr','low':'stck_lwpr','close':'stck_clpr','volume':'acml_vol','turnover':'acml_tr_pbmn'}

def read(path,default=None):
    try:
        raw=path.read_bytes();return json.loads(gzip.decompress(raw) if path.suffix=='.gz' else raw)
    except (OSError,ValueError,EOFError):return default

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    if path.suffix=='.gz':raw=gzip.compress(raw,mtime=0)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_bytes(raw);tmp.replace(path)

def chart(api,symbol,start,end,market='J',adjustment='0'):
    return api.request('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',{'FID_COND_MRKT_DIV_CODE':market,'FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':start.replace('-',''),'FID_INPUT_DATE_2':end.replace('-',''),'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':adjustment},'FHKST03010100')

def select_targets(universe,state,asof,limit):
    # Least recently attempted first, so repeated permanent failures cannot starve the queue.
    pending=[s for s in universe if state.get(s,{}).get('completed_asof')!=asof]
    priority=[s for s in PRIORITY if s in pending and not state.get(s,{}).get('status')=='api_or_schema_error']
    pending=[s for s in pending if s not in priority]
    fresh=[s for s in pending if state.get(s,{}).get('completed_asof')]
    backfill=[s for s in pending if s not in fresh]
    key=lambda s:(state.get(s,{}).get('attempted_at',''),s)
    fresh.sort(key=key);backfill.sort(key=key)
    # Interleave updates and initial history, so published symbols don't starve during backfill.
    ordered=[]
    while fresh or backfill:
        if fresh:ordered.append(fresh.pop(0))
        if backfill:ordered.append(backfill.pop(0))
    return (priority+ordered)[:limit]

def parsed(rows,asof):
    result={}
    for r in rows:
        if not r.get('stck_bsop_date'):continue
        day=dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat()
        if day>asof:continue
        if day in result:raise ValueError('duplicate_date')
        bar={'trade_date':day,**{k:float(r[v]) for k,v in FIELDS.items()}}
        if not all(math.isfinite(bar[k]) for k in FIELDS):raise ValueError('nonfinite_value')
        result[day]={'bar':bar,'event':{'trade_date':day,'rate':r.get('prtt_rate'),'reason':r.get('revl_issu_reas')}}
    return result

def action_present(event):
    # Official reason code 0/00 is no adjustment; blank is unknown, not proof of absence.
    return float(event.get('rate') or 0)!=0 or str(event.get('reason') or '').strip() not in ('','0','00')

def price_rows(api,symbol,asof,old,paged,count=320):
    if old and old.get('bars') and old.get('asof','')<asof:
        start=old['bars'][-min(10,len(old['bars']))]['trade_date']
        fresh=parsed(chart(api,symbol,start,asof).get('output2',[]),asof)
        oldrows={r['trade_date']:r for r in old['bars']}
        overlap=set(oldrows)&set(fresh)
        revised=any(oldrows[d]!=fresh[d]['bar'] for d in overlap)
        events=any(action_present(v['event']) for v in fresh.values())
        new_dates=sorted(d for d in fresh if d>old['asof'])
        if overlap and new_dates and not revised and not events:
            merged=oldrows|{d:v['bar'] for d,v in fresh.items()}
            dates=sorted(merged)[-count:]
            oldevents={r['trade_date']:r for r in old.get('corporate_action_observations',[])}
            oldevents.update({d:v['event'] for d,v in fresh.items()})
            return [merged[d] for d in dates],[oldevents.get(d,{'trade_date':d,'rate':None,'reason':None}) for d in dates],'incremental_overlap_checked'
        # Historical adjustment revisions require a consistent full window, never mixed scales.
    raw=paged(api,symbol,asof,count=count)
    rows=parsed([r for _,r in raw],asof)
    return [v['bar'] for _,v in sorted(rows.items())],[v['event'] for _,v in sorted(rows.items())],'full_window'

def make_bundle(api,symbol,asof,calendar,data,old,paged,count=320):
    bars,events,mode=price_rows(api,symbol,asof,old,paged,count)
    flow=data['flows'];flow=flow[(flow.symbol==symbol)&(flow.trade_date.astype(str)<=asof)].sort_values('trade_date').tail(20)
    flows=[{'trade_date':str(r.trade_date.date()),'net_amount':None if r.net_amount is None else int(r.net_amount)} for r in flow.itertuples()]
    p=data['prices'];p=p[(p.symbol==symbol)&(p.trade_date.astype(str)==asof)]
    original=float(p.close.iloc[0]) if len(p)==1 else None
    # An unadjusted same-date close is compared to the unadjusted preserved close.
    raw=parsed(chart(api,symbol,asof,asof,adjustment='1').get('output2',[]),asof)
    raw_close=raw.get(asof,{}).get('bar',{}).get('close')
    conflicts=[]
    if original is not None and raw_close is not None and original!=raw_close:conflicts.append('Preserved raw close differs from current KRX raw close; cause unresolved')
    if raw_close is None:conflicts.append('Same-date unadjusted KRX close unavailable')
    today=dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    b={'schema':2,'symbol':symbol,'market':str(data['instruments'].set_index('symbol').loc[symbol,'market']) if 'instruments' in data else None,'units':{'price':'KRW','volume':'shares','turnover':'KRW','foreign_net':'KRW'},'adjustment_asof':asof,'asof':asof,'exchange':'KRX','adjustment':'adjusted','adjustment_basis':'KIS FID_ORG_ADJ_PRC=0; provider adjustment factors retained','source':'KIS FHKST03010100 / J','flow_source':'Preserved snapshot KIS FHPTJ04160001 / J / KRW','calendar_source':'KIS KOSPI daily index observations','bars':bars,'flows':flows,'calendar':calendar,'finality':'UNVERIFIED','finality_evidence':None,'price_finality':'UNVERIFIED','flow_finality':sorted(set(flow.finality)),'finality_review':{'past_seoul_date':asof<today,'reason':'No documented final-confirmation field; elapsed time or repeated price equality is insufficient'},'price_conflicts':conflicts,'price_comparison':{'preserved_unadjusted':original,'kis_unadjusted':raw_close,'kis_adjusted':bars[-1]['close'] if bars else None,'adjustment_difference':bool(bars and raw_close is not None and raw_close!=bars[-1]['close'])},'corporate_action_observations':events,'corporate_actions':[e for e in events if action_present(e)],'corporate_actions_verified':False,'corporate_action_review':'KIS adjustment flags retained; not a complete official entitlement/corporate-action ledger','observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'collection_mode':mode}
    b['validation']=validate(b,asof)
    structural=validate({**b,'price_conflicts':[]},asof,require_final=False)
    b['structure_valid']=not structural;b['structure_errors']=structural
    # Indicator readiness is independent of foreign-flow coverage and finality.
    price_errors=[x for x in structural if '수급' not in x]
    b['indicator_ready']=not price_errors
    if b['indicator_ready']:
        f=indicators(bars);last=f.iloc[-1]
        b['computed']={k:float(last[k]) for k in ['ma5','ma20','ma60','ma120','tenkan','kijun','cloud_a','cloud_b','future_span_a','future_span_b','atr','prior_turnover20','recent_high','recent_low']}
        b['observed_levels']=levels(f);c=rules();support=b['observed_levels']['support'];resistance=b['observed_levels']['resistance']
        stop=support[0]['lower']-c['buy']['stop_atr']*last.atr if support else None;target=resistance[0]['lower'] if resistance else None
        b['observed_reward_risk']=reward_risk(last.close,stop,target) if stop is not None and target is not None else None
    return b

def classify(b,listing_date):
    if not b['bars']:return 'no_price_data'
    if b['bars'][-1]['trade_date']!=b['asof']:return 'stale_price'
    if b['bars'][-1]['volume']==0:return 'no_trade_or_halt_review'
    if len(b['bars'])<160:
        return 'new_listing_short_history' if listing_date and str(listing_date)>=b['bars'][0]['trade_date'] else 'insufficient_history'
    if not b['indicator_ready']:return 'invalid_price_history'
    return 'collected'

def run(api,data,paged,limit=150,budget=900,probe=False):
    started=time.monotonic();asof=str(data['flows'].trade_date.max().date());state=read(STATE,{'symbols':{},'runs':[]})
    universe=sorted(data['instruments'].symbol.unique());excluded=set(data['exclusions'].loc[data['exclusions'].trade_date.astype(str)==asof,'symbol'])
    universe=[s for s in universe if s not in excluded];tasks=select_targets(universe,state['symbols'],asof,limit)
    calendar=[d for d,r in paged(api,'0001',asof,index=True)] if tasks else []
    references,reference_status=krx_reference(asof) if tasks else ({},{})
    scopes=read(Path('reports/technical-expansion/provider-evidence.json'),{})
    if scopes.get('asof')!=asof:scopes={}
    succeeded=failed=0
    for code in tasks:
        if time.monotonic()-started>budget:break
        old=read(CACHE/(code+'.json.gz'));record={'attempted_at':dt.datetime.now(dt.timezone.utc).isoformat(),'attempted_asof':asof}
        try:
            b=make_bundle(api,code,asof,calendar,data,old,paged)
            b=crosscheck(b,references,scopes.get('symbols',{}).get(code))
            listing=data['instruments'].set_index('symbol').loc[code,'listing_date']
            category=classify(b,str(listing)[:10] if str(listing)!='NaT' else None)
            # Preserve valid previous observations when an incoming window is incomplete or invalid.
            good=not old or b['indicator_ready'] or (not old.get('indicator_ready') and len(b['bars'])>=len(old['bars']))
            if good:write(CACHE/(code+'.json.gz'),b)
            else:write(ROOT/'rejected'/(code+'.json.gz'),b)
            record.update(status=category,bars=len(b['bars']),indicator_ready=b['indicator_ready'],structure_valid=b['structure_valid'],signal_ready=not b['validation'],reasons=b['validation'],previous_preserved=not good)
            if category=='collected':record['completed_asof']=asof;succeeded+=1
            else:failed+=1
        except Exception as exc:
            failed+=1;record.update(status='api_or_schema_error',error_type=type(exc).__name__,previous_preserved=old is not None)
        state['symbols'][code]=record;write(STATE,state)
    current=[read(CACHE/(s+'.json.gz')) for s in universe];current=[b for b in current if b]
    for b in current:
        if b['asof']==asof and b['symbol'] in state['symbols']:
            state['symbols'][b['symbol']].update(reasons=validate(b,asof),signal_ready=not validate(b,asof))
    summary={'run_id':os.getenv('GITHUB_RUN_ID'),'asof':asof,'finished_at':dt.datetime.now(dt.timezone.utc).isoformat(),'target_count':len(universe),'excluded_count':len(excluded),'batch_collected':succeeded,'batch_failed_or_incomplete':failed,'cached_symbols':len(current),'completed_current_asof':sum(state['symbols'].get(s,{}).get('completed_asof')==asof for s in universe),'indicator_ready':sum(b.get('indicator_ready',False) for b in current),'signal_ready':sum(not validate(b,asof) for b in current),'price_conflicts':sum(bool(b.get('price_conflicts')) for b in current),'finality_verified':sum(b.get('finality')=='VERIFIED' for b in current),'requests':len(api.metrics),'http_or_api_errors':sum(m['http']!=200 or m['code'] not in ('','MCA00000') for m in api.metrics),'rate_limit_errors':sum(m['code']=='EGW00201' for m in api.metrics),'elapsed_seconds':round(time.monotonic()-started,2),'hold_reasons':dict(collections.Counter(x for b in current for x in validate(b,asof))),'status_counts':dict(collections.Counter(v['status'] for v in state['symbols'].values()))}
    summary['krx_reference_status']=reference_status
    summary['legacy_discrepancies']=sum(b.get('price_comparison',{}).get('preserved_unadjusted') is not None and b.get('price_comparison',{}).get('preserved_unadjusted')!=b.get('price_comparison',{}).get('kis_unadjusted') for b in current)
    summary['legacy_basis_identified']=sum(bool(b.get('legacy_price_discrepancy')) for b in current)
    summary['official_daily_crosschecked']=sum(b.get('price_crosscheck',{}).get('raw_close_match',False) for b in current)
    summary['cache_bytes']=sum(p.stat().st_size for p in CACHE.glob('*.gz'))
    summary['remaining']=len(universe)-summary['completed_current_asof'];state['runs']=(state.get('runs',[])+[summary])[-60:];write(STATE,state);write(ROOT/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False));return summary

def krx_reference(asof):
    """Official, independently issued daily unadjusted OHLCV; no finality inference."""
    import urllib.request
    key=os.getenv('KRX_API_KEY');result={};status={}
    for market,code in [('KOSPI','stk'),('KOSDAQ','ksq')]:
        path=ROOT/'reference'/(market+'.json.gz');cached=read(path,{})
        if cached.get('asof')==asof:
            rows=cached['rows'];status[market]='cached_same_date'
        elif key:
            try:
                url='https://data-dbg.krx.co.kr/svc/apis/sto/'+code+'_bydd_trd?basDd='+asof.replace('-','')
                with urllib.request.urlopen(urllib.request.Request(url,headers={'AUTH_KEY':key}),timeout=25) as response:d=json.load(response)
                rows=d.get('OutBlock_1',[])
                if not rows or any(r.get('BAS_DD')!=asof.replace('-','') for r in rows):raise ValueError('Official reference date mismatch')
                write(path,{'asof':asof,'url':url,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'rows':rows});status[market]='received'
            except Exception as exc:rows=[];status[market]=type(exc).__name__
        else:rows=[];status[market]='credential_not_bound'
        for row in rows:result[row['ISU_CD']]=row
    return result,status

def crosscheck(b,reference,scope_evidence=None):
    row=reference.get(b['symbol']);comparison=b.get('price_comparison',{})
    if not row or row.get('BAS_DD')!=b['asof'].replace('-',''):return b
    official=float(row['TDD_CLSPRC']);comparison['krx_official_unadjusted']=official
    b['price_crosscheck']={'source':'KRX official daily trading statistics','trade_date':b['asof'],'raw_close_match':comparison.get('kis_unadjusted')==official,'adjusted_latest_equals_raw':comparison.get('kis_adjusted')==official}
    b['price_finality']='CROSS_VALIDATED_DAILY' if b['price_crosscheck']['raw_close_match'] else 'CONFLICT'
    mapping={'open':'TDD_OPNPRC','high':'TDD_HGPRC','low':'TDD_LWPRC','close':'TDD_CLSPRC','volume':'ACC_TRDVOL','turnover':'ACC_TRDVAL'}
    if b.get('bars') and b['bars'][-1]['trade_date']==b['asof'] and all(k in row for k in mapping.values()) and b['price_crosscheck']['adjusted_latest_equals_raw']:
        b['price_crosscheck']['ohlcv_fields_match']={k:b['bars'][-1][k]==float(row[field]) for k,field in mapping.items()}
        if not all(b['price_crosscheck']['ohlcv_fields_match'].values()):
            b['price_conflicts'].append('Same-date KIS / KRX official OHLCV fields differ')
            b['price_finality']='CONFLICT'
    # Resolves only a documented market-basis discrepancy; never grants finality.
    scope=scope_evidence or {};integrated=scope.get('UN_1');krx=scope.get('J_1')
    if isinstance(integrated,list) and len(integrated)==1 and isinstance(krx,list) and len(krx)==1:
        un=float(integrated[0]['stck_clpr']);j=float(krx[0]['stck_clpr'])
        if comparison.get('preserved_unadjusted')==un and j==official and comparison.get('kis_unadjusted')==official and not scope.get('flow_mismatches') and scope.get('flow_checked_dates')==20:
            b['legacy_price_discrepancy']={'status':'MARKET_BASIS_IDENTIFIED','legacy_close':un,'krx_close':j,'evidence':'reports/technical-expansion/provider-evidence.json','limit':'Legacy request logs said J; historical provider routing cause cannot be proven. Preserve the original; technical analysis uses independently cross-checked J only.'}
            b['price_conflicts']=[x for x in b['price_conflicts'] if not x.startswith('Preserved raw close differs')]
    if not b['price_crosscheck']['raw_close_match']:
        if 'KIS raw / independent KRX daily close mismatch' not in b['price_conflicts']:b['price_conflicts'].append('KIS raw / independent KRX daily close mismatch')
    b['validation']=validate(b,b['asof']);return b
