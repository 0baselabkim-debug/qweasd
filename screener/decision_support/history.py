"""Qualify a whole auxiliary price vintage, independent of the immutable DB.

Old operating observations are comparisons, never sources for patched candles.
An auxiliary bar needs both KIS bases and exact official KRX six-field evidence.
"""
import collections
import datetime as dt
from .evidence import read,encoded,sha,date,timestamp,bar,KIS_FIELDS,accept_chart,adjustment_pending,corporate_adjustment_pending


def load_objects(directory,inventory):
    result=collections.defaultdict(list)
    for sid,source in inventory.get('auxiliary',{}).items():
        obj=read(directory/source['file'])
        result[source['key']].append((sid,obj))
    return result


def pages(objects,asof,code,option):
    rows={};sources=[];times=[]
    end=asof.replace('-','')
    for page in range(3 if option=='index' else 2):
        versions=objects.get(f'kis:{asof}:{code}:{option}:{page}',[])
        if len(versions)!=1:raise ValueError('history_page_missing_or_ambiguous')
        sid,obj=versions[0];p=obj.get('params',{});response=obj.get('response',{})
        is_index=option=='index'
        endpoint='/uapi/domestic-stock/v1/quotations/'+('inquire-daily-indexchartprice' if is_index else 'inquire-daily-itemchartprice')
        expected={'FID_COND_MRKT_DIV_CODE':'U' if is_index else 'J','FID_INPUT_ISCD':code,
                  'FID_INPUT_DATE_2':end,'FID_PERIOD_DIV_CODE':'D'}
        if not is_index:expected['FID_ORG_ADJ_PRC']=option
        if obj.get('endpoint')!=endpoint or response.get('rt_cd')!='0' or any(p.get(k)!=v for k,v in expected.items()):raise ValueError('history_request_identity_mismatch')
        if not is_index and response.get('output1',{}).get('stck_shrn_iscd')!=code:raise ValueError('history_symbol_mismatch')
        times.append(timestamp(obj['observed_at']));sources.append(sid)
        values=response.get('output2',[])
        if not values:raise ValueError('empty_history_page')
        for raw in values:
            d=date(raw['stck_bsop_date'])
            if d in rows or not p.get('FID_INPUT_DATE_1','99999999')<=raw['stck_bsop_date']<=end:raise ValueError('history_duplicate_or_date_mismatch')
            rows[d]=raw
        end=(dt.date.fromisoformat(min(date(r['stck_bsop_date']) for r in values))-dt.timedelta(days=1)).strftime('%Y%m%d')
    if (max(times)-min(times)).total_seconds()>900:raise ValueError('history_page_vintages_differ')
    return rows,sources,max(times).isoformat()


def candidate(objects,code,market,asof,listing,official,db_prices,operating_days,operating_benchmarks):
    """Returns no candidate on provenance failure, per-date holds otherwise."""
    prefix=f'kis:{asof}:{code}:'
    if not any(k.startswith(prefix) for k in objects):return None
    try:
        adjusted,aid,at=pages(objects,asof,code,'0')
        original,oid,ot=pages(objects,asof,code,'1')
        index,iid,it=pages(objects,asof,'0001' if market=='KOSPI' else '1001','index')
        if abs((timestamp(at)-timestamp(ot)).total_seconds())>900:raise ValueError('price_basis_observation_window_differs')
        days=sorted(index)[-121:]
        if len(days)<121 or days[-1]!=asof:raise ValueError('official_index_calendar_short')
        # Exact overlap: never manufacture missing sessions from stock rows.
        overlap=[d for d in operating_days if days[0]<=d<=asof]
        if overlap!=[d for d in days if d in set(operating_days)]:raise ValueError('calendar_order_conflict')
        if operating_days and [d for d in days if d>=max(days[0],operating_days[0])]!=overlap:raise ValueError('calendar_session_conflict')
        if any(d not in index for d in adjusted if days[0]<=d<=asof):raise ValueError('stock_date_outside_official_calendar')
        # Never bridge an unverified adjustment. Subsequent no-adjustment bars
        # can still qualify independently; a past event cannot taint every
        # later candle forever. Indicators require a fully qualified suffix.
        barriers=[d for d in days if d in adjusted and d in original and
                  (corporate_adjustment_pending(adjusted[d]) or corporate_adjustment_pending(original[d]))]
        barrier=max(barriers) if barriers else None
        accepted={};series=[];benchmarks={};comparison=[]
        for d in days:
            try:
                close=float(index[d]['bstp_nmix_prpr'])
                if close<=0:raise ValueError()
                if d in operating_benchmarks and abs(close-operating_benchmarks[d])>1e-9:raise ValueError()
                benchmarks[d]=close
            except (KeyError,ValueError,TypeError):pass
            raw=adjusted.get(d)
            if raw is None:continue
            q=accept_chart(raw,code,d,market,at,official,listing,blocked=bool(barrier and d<=barrier))
            try:
                unadjusted=bar(original[d],KIS_FIELDS)
                if unadjusted!=q['value']:raise ValueError('adjusted_original_six_field_conflict')
                if original[d].get('mod_yn')!='N':raise ValueError('original_no_trade_or_indicative_candle')
            except (ValueError,KeyError) as e:
                q['reasons'].append(str(e) if isinstance(e,ValueError) else 'original_price_missing')
                q['accepted']=None;q['status']='hold'
            old=db_prices.get((code,d))
            if q['value'] and old is not None:
                diffs={k:{'operating':old[i],'candidate':q['value'][k]} for i,k in enumerate(('close','volume','turnover')) if old[i]!=q['value'][k]}
                if diffs:comparison.append({'trade_date':d,'differences':diffs,'operating_original_unchanged':True})
            if q['accepted']:accepted[d]=q['accepted']
            series.append({'symbol':code,'trade_date':d,'market':market,'exchange':'KRX','basis':'KIS_adjusted_and_original_KRX_equal',
                'observed_at':at,'source_id':sha(encoded(aid+oid)),'raw_row_sha256':sha(encoded(raw)),
                'validation_status':q['status'],'provider_finality':'UNVERIFIED','reasons':q['reasons'],
                'official_source_ids':q['official_source_ids'],
                'adjustment_fields':{k:raw.get(k) for k in ('flng_cls_code','prtt_rate','mod_yn','revl_issu_reas')},
                **(q['value'] or {k:None for k in KIS_FIELDS})})
        return {'days':days,'series':series,'accepted':accepted,'benchmarks':benchmarks,'observed_at':at,
                'source_ids':aid+oid+iid,'operating_version_comparison':comparison,'error':None}
    except (ValueError,KeyError,TypeError) as e:
        return {'error':str(e) if isinstance(e,ValueError) else 'malformed_auxiliary_history'}


def continuation(objects,code,market,asof,listing,official,db_prices,days,benchmarks,record,checkpoint_id):
    """Extend saved qualified history only across an exact 20+ session overlap.

No old bar is replaced. Every overlapping whole candle must agree, no pending
adjustment may cross the link, and each new candle needs official KRX evidence.
An unavailable official day remains a gap and blocks affected indicators.
"""
    prior=sorted({key.split(':')[1] for key in objects if key.startswith('kis:') and
                  key.split(':')[2]==code and key.split(':')[1]<asof},reverse=True)
    if not prior:return None
    base_asof=prior[0]
    base=candidate(objects,code,market,base_asof,listing,official,db_prices,
                   [d for d in days if d<=base_asof],benchmarks)
    if not base or base.get('error'):return base
    try:
        fresh={}
        for r in record['adjusted_prices']:
            d=date(r['stck_bsop_date'])
            if d in fresh or d>asof:raise ValueError('continuation_duplicate_or_future')
            fresh[d]=r
        observed=record.get('analysis_source',{}).get('chart_observed_at',record['observed_at'])
        timestamp(observed)
        old={r['trade_date']:r for r in base['series']}
        overlap=sorted(set(fresh)&set(old))
        if len(overlap)<20 or overlap[-1]!=base_asof:raise ValueError('continuation_overlap_short')
        for d in overlap:
            if adjustment_pending(fresh[d]) or any(bar(fresh[d],KIS_FIELDS)[k]!=old[d][k] for k in KIS_FIELDS):
                raise ValueError('continuation_overlap_or_adjustment_conflict')
        extension=[d for d in days if d>base_asof]
        if not extension or extension[-1]!=asof or any(d not in fresh for d in extension):raise ValueError('continuation_session_gap')
        for d in extension:
            raw=fresh[d];q=accept_chart(raw,code,d,market,observed,official,listing)
            if q['accepted']:base['accepted'][d]=q['accepted']
            base['series'].append({'symbol':code,'trade_date':d,'market':market,'exchange':'KRX',
                'basis':'KIS_adjusted_equals_official_KRX_original','observed_at':observed,'source_id':checkpoint_id,
                'raw_row_sha256':sha(encoded(raw)),'validation_status':q['status'],'provider_finality':'UNVERIFIED',
                'reasons':q['reasons'],'official_source_ids':q['official_source_ids'],
                'adjustment_fields':{k:raw.get(k) for k in ('flng_cls_code','prtt_rate','mod_yn','revl_issu_reas')},
                **(q['value'] or {k:None for k in KIS_FIELDS})})
        base['days']+=extension;base['observed_at']=observed;base['source_ids'].append(checkpoint_id)
        base['benchmarks'].update({d:benchmarks[d] for d in extension if d in benchmarks})
        base['link_proof']={'base_asof':base_asof,'overlap_days':len(overlap),'whole_six_field_overlap_equal':True,
                            'new_days':extension,'no_original_overwrite':True}
        return base
    except (ValueError,KeyError,TypeError) as e:
        return {'error':str(e) if isinstance(e,ValueError) else 'malformed_continuation'}


def refreshed_continuation(objects,code,market,asof,listing,official,db_prices,days,benchmarks):
    """A bounded paired re-observation may repair a whole-vintage link.

    Exact six-field overlap and corporate-action gates remain in continuation.
    Prior observations/operating DB are never mutated or mixed field by field.
    """
    versions=[objects.get(f'recent:{asof}:{code}:{option}',[]) for option in ('0','1')]
    if not any(versions):return None
    try:
        if any(len(v)!=1 for v in versions):raise ValueError('recent_pair_missing_or_ambiguous')
        maps=[];times=[];source_ids=[]
        for option,items in zip(('0','1'),versions):
            sid,obj=items[0];p=obj.get('params',{});response=obj.get('response',{})
            expected={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_2':asof.replace('-',''),'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':option}
            if obj.get('endpoint')!='/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice' or response.get('rt_cd')!='0' or response.get('output1',{}).get('stck_shrn_iscd')!=code or any(p.get(k)!=v for k,v in expected.items()):raise ValueError('recent_pair_identity_mismatch')
            m={};times.append(timestamp(obj['observed_at']));source_ids.append(sid)
            for r in response.get('output2',[]):
                d=date(r['stck_bsop_date'])
                if d in m or not p.get('FID_INPUT_DATE_1','99999999')<=r['stck_bsop_date']<=p['FID_INPUT_DATE_2']:raise ValueError('recent_pair_date_mismatch')
                m[d]=r
            maps.append(m)
        if (max(times)-min(times)).total_seconds()>900:raise ValueError('recent_pair_vintage_mismatch')
        if set(maps[0])!=set(maps[1]) or asof not in maps[0]:raise ValueError('recent_pair_calendar_mismatch')
        for d in maps[0]:
            if adjustment_pending(maps[0][d]) or adjustment_pending(maps[1][d]) or bar(maps[0][d],KIS_FIELDS)!=bar(maps[1][d],KIS_FIELDS):raise ValueError('recent_pair_adjustment_or_price_conflict')
        record={'adjusted_prices':list(maps[0].values()),'observed_at':max(times).isoformat()}
        result=continuation(objects,code,market,asof,listing,official,db_prices,days,benchmarks,record,sha(encoded(source_ids)))
        if result and not result.get('error'):
            result['source_ids']+=source_ids
            result['link_proof']['paired_reobservation_source_ids']=source_ids
        return result
    except (ValueError,KeyError,TypeError) as e:return {'error':str(e) if isinstance(e,ValueError) else 'malformed_recent_pair'}
