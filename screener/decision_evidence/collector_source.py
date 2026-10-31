"""Adapt one verified immutable full-market generation, never old published prices/flows."""
import datetime as dt
import json
from pathlib import Path
from decimal import Decimal
from zoneinfo import ZoneInfo
from collections import Counter

from collector_v1.core import Store
from collector_v1.providers import task,Q
from collector_v1.experiment.engine import replay,identity
from screener.decision_support.evidence import (encoded,sha,read,timestamp,date,integer,bar,
    institution,official_rows,accept_chart,corporate_adjustment_pending,KIS_FIELDS)
from .collector_artifact import inspect,ASOF,RUN,GENERATION,ZIP_SHA,MANIFEST_SHA


def saved_rows(obj, evidence, kind, symbol, market, *, asof=ASOF, generation_id=GENERATION, round_id=1):
    p=evidence['params'];t=evidence['task']
    expected={'FID_COND_MRKT_DIV_CODE':'U' if kind=='index' else 'J',
        'FID_INPUT_ISCD':('0001' if market=='KOSPI' else '1001') if kind=='index' else symbol}
    if kind=='flow':expected.update(FID_INPUT_DATE_1=asof.replace('-',''),FID_ORG_ADJ_PRC='',FID_ETC_CLS_CODE='')
    else:
        expected.update(FID_INPUT_DATE_2=asof.replace('-',''),FID_PERIOD_DIV_CODE='D',
            FID_INPUT_DATE_1=(dt.date.fromisoformat(asof)-dt.timedelta(days=60)).strftime('%Y%m%d'))
        if kind=='price':expected['FID_ORG_ADJ_PRC']='1'
    endpoint=Q+{'flow':'investor-trade-by-stock-daily','price':'inquire-daily-itemchartprice','index':'inquire-daily-indexchartprice'}[kind]
    if (obj.get('rt_cd')!='0' or p!=expected or evidence['endpoint']!=endpoint or
            t!=task(kind,asof,round=round_id,market=market,**({'generation_id':generation_id} if generation_id else {}),**({'symbol':symbol} if kind!='index' else {}))):
        raise ValueError('collector_response_request_binding_mismatch')
    if kind=='price' and obj.get('output1',{}).get('stck_shrn_iscd')!=symbol:raise ValueError('collector_price_symbol_mismatch')
    rows=obj.get('output2')
    if not isinstance(rows,list) or not rows:raise ValueError('collector_history_empty')
    result={}
    for row in rows:
        day=date(row['stck_bsop_date'])
        if day in result or day>asof:raise ValueError('collector_history_duplicate_or_future_date')
        if kind!='flow' and row['stck_bsop_date']<p['FID_INPUT_DATE_1']:raise ValueError('collector_history_outside_requested_window')
        result[day]=row
    if asof not in result:raise ValueError('collector_current_date_missing')
    return result


def flow_observation(raw,price):
    """Same exact quantity and ±1 provider-million amount identity as screener.daily.

    The existing million-unit identity rule is not applied to OHLCV: price
    comparisons below remain exact in every field.
    """
    reasons=[];amount=quantity=None
    try:
        q=integer(raw['frgn_ntby_qty']);buy=integer(raw['frgn_shnu_vol']);sell=integer(raw['frgn_seln_vol'])
        net=integer(raw['frgn_ntby_tr_pbmn']);buy_amount=integer(raw['frgn_shnu_tr_pbmn']);sell_amount=integer(raw['frgn_seln_tr_pbmn'])
        if min(buy,sell,buy_amount,sell_amount)<0 or buy-sell!=q:raise ValueError('foreign_quantity_identity_mismatch')
        if abs(buy_amount-sell_amount-net)>1:raise ValueError('foreign_provider_million_identity_mismatch')
        if price is None or any(integer(raw[f])!=integer(price[f]) for f in ('stck_clpr','acml_vol','acml_tr_pbmn')):
            raise ValueError('historical_flow_price_internal_conflict')
        if min(integer(raw[f]) for f in ('stck_clpr','acml_vol','acml_tr_pbmn'))<=0:raise ValueError('no_trade_flow')
        amount=net*1_000_000;quantity=q
    except (ValueError,KeyError) as error:reasons.append(str(error))
    inst=institution(raw)
    return dict(net_amount_krw=amount,net_quantity_shares=quantity,
        institution_net_quantity_shares=inst['net_quantity'],institution_net_amount_krw=None,
        institution_net_amount_provider_units=inst['net_amount_provider_units'],
        reasons=reasons,institution_reasons=inst['reasons'])


def references(directory, cutoff, *, asof=ASOF):
    if directory is None:return {},{},[],{}
    directory=Path(directory);manifest=read(directory/'source-manifest.json');paths=[];hashes={str((directory/'source-manifest.json').resolve()):sha((directory/'source-manifest.json').read_bytes())}
    for group in ('references','auxiliary'):
        for sid,value in manifest.get(group,{}).items():
            if not value.get('url','').startswith('https://data-dbg.krx.co.kr/'):continue
            path=(directory/value['file']).resolve()
            if not path.is_relative_to(directory.resolve()):raise ValueError('unsafe_official_reference_path')
            h=sha(path.read_bytes())
            if h!=value['sha256']:raise ValueError('preserved_official_hash_mismatch')
            if timestamp(value['observed_at'])>cutoff:continue
            paths.append(path);hashes[str(path)]=h
    official,sources,errors=official_rows(list(set(paths)),asof)
    return official,sources,errors,hashes


def load(root,knowledge_cutoff,reference_directory=None):
    root=Path(root).resolve();cutoff=timestamp(knowledge_cutoff)
    if sha((root/'manifest.json').read_bytes())!=MANIFEST_SHA:raise ValueError('collector_manifest_anchor_mismatch')
    proof=inspect(root);report=read(root/'report.json');known=timestamp(report['pipeline_completed_at'])
    if known>cutoff:raise ValueError('collector_completed_after_knowledge_cutoff')
    files={str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    official,official_sources,official_errors,ref_hashes=references(reference_directory,cutoff)
    store=Store(root/GENERATION/'collection');cache=replay(store,1,asof=ASOF,generation_id=GENERATION)
    universe=read(root/GENERATION/'preparation/universe.json');instruments={r['symbol']:r for r in universe}
    if len(instruments)!=len(universe):raise ValueError('collector_duplicate_master_symbol')
    calendars={};benchmarks={};index_evidence={}
    for market in ('KOSPI','KOSDAQ'):
        e=cache[identity(task('index',ASOF,round=1,market=market,generation_id=GENERATION))]
        raw=saved_rows(json.loads(store.raw(e)),e,'index',None,market)
        values={d:Decimal(r['bstp_nmix_prpr']) for d,r in raw.items()}
        if any(not v.is_finite() or v<=0 for v in values.values()):raise ValueError('collector_invalid_index_value')
        calendars[market]=sorted(raw);benchmarks[market]={d:float(v) for d,v in values.items()};index_evidence[market]=e
    rows=[];flow_holds=Counter();history_lengths=Counter();approved_dates=Counter();compared_dates=Counter()
    for current in report['rows']:
        code=current['symbol'];market=current['market'];instrument=instruments[code];days=calendars[market]
        fe=cache[identity(task('flow',ASOF,round=1,symbol=code,market=market,generation_id=GENERATION))]
        pe=cache[identity(task('price',ASOF,round=1,symbol=code,market=market,generation_id=GENERATION))]
        for e in (fe,pe):
            if timestamp(e['collected_at'])>cutoff:raise ValueError('collector_source_after_cutoff')
        flow=saved_rows(json.loads(store.raw(fe)),fe,'flow',code,market)
        price=saved_rows(json.loads(store.raw(pe)),pe,'price',code,market)
        if any(d not in days for d in price):raise ValueError('collector_price_date_outside_index_calendar')
        if any(d not in days for d in flow):raise ValueError('collector_flow_date_outside_index_calendar')
        history_lengths[str(len(price))]+=1
        fcols=['trade_date','net_amount_krw','net_quantity_shares','institution_net_quantity_shares','institution_net_amount_krw','institution_net_amount_provider_units']
        fseries=[];flow_quality=[]
        for d,r in sorted(flow.items()):
            normalized=flow_observation(r,price.get(d));flow_holds.update(normalized['reasons'])
            fseries.append([d,*[normalized[k] for k in fcols[1:]]])
            flow_quality.append(dict(trade_date=d,**{k:normalized[k] for k in ('reasons','institution_reasons')},raw_row_sha256=sha(encoded(r))))
        if fseries[-1][1] is not None and fseries[-1][1:4]!=[current['foreign_net_amount'],current['foreign_net_quantity'],current['institution_net_quantity']]:
            raise ValueError('collector_current_flow_not_reproduced')
        events=[d for d,r in price.items() if corporate_adjustment_pending(r)];barrier=max(events) if events else None
        pcols=['trade_date','open','high','low','close','volume','turnover','validation_status','reasons'];pseries=[]
        for d,r in sorted(price.items()):
            q=accept_chart(r,code,d,market,pe['collected_at'],official,instrument['listing_date'],blocked=bool(barrier and d<=barrier))
            if official.get((code,d,market)):compared_dates[d]+=1
            if q['accepted']:approved_dates[d]+=1
            values=q['value'] or dict.fromkeys(KIS_FIELDS)
            pseries.append([d,*[values[k] for k in pcols[1:-2]],q['status'],q['reasons']])
        observed=timestamp(pe['collected_at']).astimezone(ZoneInfo('Asia/Seoul'))
        row=dict(symbol=code,name=instrument['name'],market=market,exchange='KRX',asof=ASOF,provider_finality='UNVERIFIED',
            validity={'historical_backtest_eligible':False},flow_series=dict(columns=fcols,rows=fseries),
            price_series=dict(columns=pcols,rows=pseries,basis='KIS_original_option_1_no_adjustment_bridge',observed_at=pe['collected_at']),
            metric_provenance=dict(price_source_ids=[sha(encoded(pe))],foreign_observed_at=fe['collected_at'],
                source_hashes=current['source_hashes'],index_source_hash=index_evidence[market]['source_hash']),
            _closed_price_days=[d for d in days if dt.datetime.combine(dt.date.fromisoformat(d),dt.time(15,30),ZoneInfo('Asia/Seoul'))<=observed],
            _price_calendar=days,_benchmarks=benchmarks[market],_flow_quality=flow_quality)
        rows.append(row)
    units=dict(foreign_amount='KRW; provider million KRW x 1000000',quantity='shares',price='KRW',volume='shares',turnover='KRW',institution_amount='unit_unverified; normalized amount null',returns='percent',excess_return='percentage_points')
    index=dict(asof=ASOF,dataset_id=GENERATION,generated_at=report['pipeline_completed_at'],source_snapshot_sha256=ZIP_SHA,
        source_collected_at=report['pipeline_completed_at'],provider_finality='UNVERIFIED',validation_status='INTERNAL_PASS_COMPLETE_PROVISIONAL',
        units=units,coverage=dict(stocks=len(rows),KIS_internal_pass=len(rows),provider_final_confirmed=0))
    return dict(index=index,rows=rows,calendars=calendars,benchmarks=benchmarks,official=official,official_sources=official_sources,
        official_errors=official_errors,priority_symbols=[],macro=[],macro_source_file=None,input_file_hashes=files,
        reference_file_hashes=ref_hashes,source_manifest_sha256=MANIFEST_SHA,input_index_sha256=sha((root/'report.json').read_bytes()),
        known_source_observation_latest=known.isoformat(),source_publication_time=None,historical_backtest_eligible=False,
        source_verification=dict(status='PASS_PINNED_ZIP_MANIFEST_RAW_MASTER_LEDGER_DB_JSON',proof=proof['status'],
            raw_price_history_lengths=dict(history_lengths),historical_flow_hold_counts=dict(flow_holds),
            approved_price_bars_by_trade_date=dict(approved_dates),official_comparison_coverage_by_trade_date=dict(compared_dates),
            physical_HTTP_original=report['total_physical_http'],official_reference_max_asof=max((v['asof'] for v in official_sources.values()),default=None)),
        calendar_provenance='KIS_SAVED_SAME_GENERATION_U_INDEX_RESPONSE_DATES; not official KRX calendar; no fabricated weekdays')
