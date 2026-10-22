"""Qualify a whole auxiliary price vintage, independent of the immutable DB.

Old operating observations are comparisons, never sources for patched candles.
An auxiliary bar needs both KIS bases and exact official KRX six-field evidence.
"""
import collections
import datetime as dt
from .evidence import read,encoded,sha,date,timestamp,bar,KIS_FIELDS,accept_chart,adjustment_pending


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
        blocked=any(adjustment_pending(adjusted[d]) or adjustment_pending(original[d]) for d in days if d in adjusted and d in original)
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
            q=accept_chart(raw,code,d,market,at,official,listing,blocked=blocked)
            try:
                unadjusted=bar(original[d],KIS_FIELDS)
                if unadjusted!=q['value']:raise ValueError('adjusted_original_six_field_conflict')
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
