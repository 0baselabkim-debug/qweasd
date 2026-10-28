"""Fail-closed, date/identity/unit/hash qualification; no imputed observations."""
import csv
import datetime as dt
import io
import json
import re
import zipfile
from zoneinfo import ZoneInfo
from .core import Failure, encoded, sha
from .providers import LAYOUTS
from scripts.market_reference import classify_rows
from screener.corporate import filing_record, financial_record
from screener.financials import number
from screener.decision_support.evidence import institution
from screener.macro import catalog, record, valid_indicator

FIELDS = {'open':'stck_oprc','high':'stck_hgpr','low':'stck_lwpr','close':'stck_clpr','volume':'acml_vol','turnover':'acml_tr_pbmn'}
KRX_FIELDS = {'open':'TDD_OPNPRC','high':'TDD_HGPRC','low':'TDD_LWPRC','close':'TDD_CLSPRC','volume':'ACC_TRDVOL','turnover':'ACC_TRDVAL'}


def integer(x):
    if not isinstance(x, (int,str)) or isinstance(x,bool) or not re.fullmatch(r'-?\d+',str(x)):
        raise Failure('missing_or_invalid_integer')
    return int(x)


def date(x):
    return dt.datetime.strptime(x, '%Y%m%d').date().isoformat()


def unique(rows, field):
    if not isinstance(rows,list) or not rows:
        raise Failure('empty_source')
    keys=[r[field] for r in rows]
    if len(keys)!=len(set(keys)):raise Failure('duplicate_source_date_or_symbol')
    return rows


def master(raw, t, collected_at):
    # Current master has no historical vintage; do not backdate it.
    observed=dt.datetime.fromisoformat(collected_at).astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
    if observed!=t['asof']:raise Failure('historical_universe_unverified')
    layout=LAYOUTS[t['market']];width=sum(layout['widths']);out=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text=z.read(layout['member']).decode('cp949')
    for line in text.splitlines():
        symbol=line[:9].strip()
        if not re.fullmatch(r'[0-9A-Z]{6}',symbol):continue
        if len(line)<width+21:raise Failure('master_layout_mismatch')
        tail=line[-width:];vals=[];pos=0
        for n in layout['widths']:vals.append(tail[pos:pos+n].strip());pos+=n
        fields=dict(zip(layout['columns'],vals));reasons=[]
        for names, reason in [(('거래정지','거래정지 여부'),'halted'),(('관리종목','관리 종목 여부'),'managed'),(('정리매매','정리매매 여부'),'liquidation'),(('SPAC','기업인수목적회사여부'),'spac')]:
            value=next((fields[k] for k in names if k in fields),None)
            if value not in ('Y','N'):reasons.append('unknown_'+reason)
            elif value=='Y':reasons.append(reason)
        if vals[layout['group_index']]!='ST':reasons.append('non_common_equity_group')
        if vals[layout['preferred_index']]!='0':reasons.append('preferred')
        listing=date(vals[layout['listing_date_index']])
        if listing>t['asof']:reasons.append('not_yet_listed')
        out.append(dict(symbol=symbol,market=t['market'],name=line[21:-width].strip(),listing_date=listing,eligible=not reasons,exclusion_reasons=reasons,official_attributes=fields,delisted=None))
    unique(out,'symbol')
    return out


def parse(raw, evidence):
    t=evidence['task'];kind=t['kind'];day=t['asof'].replace('-','')
    if kind=='master':return master(raw,t,evidence['collected_at'])
    if kind=='corp_codes':
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(raw)) as z:root=ET.fromstring(z.read('CORPCODE.xml'))
        return [{'symbol':n.findtext('stock_code'),'corp_code':n.findtext('corp_code'),'modify_date':n.findtext('modify_date')} for n in root.findall('list') if (n.findtext('stock_code') or '').strip()]
    if kind=='macro':
        meta=catalog()[t['indicator']]
        if meta['provider']=='FRED':
            data=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
            observations=[dict(date=r.get('observation_date',r.get('DATE')),value=r[meta['series']]) for r in data]
        else:
            data=json.loads(raw)['StatisticSearch']['row']
            name,unit=('국고채(3년)','연%') if meta['id']=='kr3y' else ('원/미국달러(매매기준율)','원')
            if any(r['ITEM_NAME1']!=name or r['UNIT_NAME']!=unit for r in data):raise Failure('macro_identity_unit_mismatch')
            observations=[dict(date=date(r['TIME']),value=r['DATA_VALUE']) for r in data]
            meta={**meta,'daily_fixing':meta['id']=='usdkrw'}
        observed=dt.datetime.fromisoformat(evidence['collected_at'])
        # Preserve publication observation time separately; restrict observations to selected date.
        value=record(meta,[r for r in observations if r['date']<=t['asof']],observed)
        if not valid_indicator(t['indicator'],value):raise Failure('invalid_macro_record')
        return [value]
    d=json.loads(raw)
    if kind in ('calendar','flow','price','index') and d.get('rt_cd')!='0':raise Failure('KIS_response_rejected')
    if kind=='calendar':
        rows=unique(d['output'],'bass_dt');matches=[r for r in rows if r['bass_dt']==day]
        if len(matches)!=1 or matches[0].get('opnd_yn') not in ('Y','N'):raise Failure('unverified_trading_day')
        if matches[0]['opnd_yn']!='Y':raise Failure('exchange_closed')
        return matches
    if kind=='krx':
        status,rows=classify_rows(d,t['asof'])
        if status!='RECEIVED':raise Failure(status)
        for r in rows:
            if 'MKT_NM' in r and r['MKT_NM'] not in ({'KOSPI','유가증권'} if t['market']=='KOSPI' else {'KOSDAQ','코스닥'}):raise Failure('market_mismatch')
        return rows
    if kind in ('flow','price','index'):
        if kind=='price' and d.get('output1',{}).get('stck_shrn_iscd')!=t['symbol']:raise Failure('symbol_mismatch')
        # Flow rows have no echoed code: retain the exact J request binding, crosscheck dated close/volume/turnover.
        rows=unique(d['output2'],'stck_bsop_date')
        if any(date(r['stck_bsop_date'])>t['asof'] for r in rows):raise Failure('future_source_date')
        rows=[r for r in rows if r['stck_bsop_date']==day]
        if len(rows)!=1:raise Failure('requested_date_missing')
        r=rows[0]
        if kind=='index':
            value=float(r['bstp_nmix_prpr'])
            if not 0<value<float('inf'):raise Failure('invalid_index')
            return [dict(market=t['market'],close=value,trade_date=t['asof'])]
        if kind=='flow':
            foreign_amount=integer(r.get('frgn_ntby_tr_pbmn'))*1_000_000
            institutional=institution(r)
            return [dict(symbol=t['symbol'],trade_date=t['asof'],foreign_net_amount=foreign_amount,foreign_net_quantity=integer(r.get('frgn_ntby_qty')),institution_net_quantity=institutional['net_quantity'],institution_quantity_reasons=institutional['reasons'],institution_net_amount=None,institution_amount_status='unit_unverified',institution_amount_raw=r.get('orgn_ntby_tr_pbmn'),amount_unit='KRW',quantity_unit='shares',close=integer(r.get('stck_clpr')),volume=integer(r.get('acml_vol')),turnover=integer(r.get('acml_tr_pbmn')))]
        value={k:integer(r.get(v)) for k,v in FIELDS.items()}
        if min(value.values())<0 or value['close']<=0 or not value['low']<=min(value['open'],value['close'])<=max(value['open'],value['close'])<=value['high']:raise Failure('invalid_OHLCV')
        if r.get('mod_yn')!='N':raise Failure('indicative_or_no_trade_candle')
        return [dict(symbol=t['symbol'],trade_date=t['asof'],exchange='KRX',adjustment='original',adjustment_fields={k:r.get(k) for k in ('mod_yn','prtt_rate','flng_cls_code','revl_issu_reas')},**value)]
    if kind=='filings':
        if d.get('status')=='013':return []
        if d.get('status')!='000':raise Failure('DART_response_rejected')
        if d.get('total_page',0)>t.get('page',1):raise Failure('filing_pagination_required')
        result=[]
        for row in d.get('list',[]):
            if row['corp_code']!=t['corp_code']:raise Failure('corporate_identity_mismatch')
            f=filing_record(row,t['symbol'])
            if f and f['disclosed_on']<=t['asof']:
                f['financial_collection_status']='eligible' if f['report_code'] else 'held_nondec_fiscal_period'
                result.append(f)
        return result
    if kind=='financial':
        if t['filing']['disclosed_on']>t['asof']:raise Failure('future_disclosure')
        value=financial_record(d,t['filing'],t['scope'])
        assets=[r for r in d['list'] if r.get('sj_div')=='BS' and r.get('account_detail','-') in ('-','') and r.get('account_id') in ('ifrs-full_Assets','ifrs_Assets')]
        amounts={number(r.get('thstrm_amount')) for r in assets}-{None}
        value['values']['assets']=next(iter(amounts)) if len(amounts)==1 else None
        value['account_evidence']['assets']={'account_ids':[r.get('account_id') for r in assets],'ambiguous':len(amounts)>1,'method':'standard_account_id' if assets else 'missing'}
        return [value]
    raise Failure('unsupported_validation')


def validate(store, tasks, require_market=True):
    state=store.read('state.json',{'tasks':{}});parsed={};errors=[];evidence={}
    if not tasks or len({t['asof'] for t in tasks})!=1:
        raise Failure('empty_or_mixed_generation_dates')
    for t in tasks:
        key=sha(encoded(t));entry=state['tasks'].get(key,{})
        try:
            if entry.get('collection_status')!='received':raise Failure('collection_incomplete')
            e=entry['evidence']
            if e['task']!=t:raise Failure('source_identity_mismatch')
            stamp=dt.datetime.fromisoformat(e['collected_at'])
            if stamp.tzinfo is None:raise Failure('naive_collection_time')
            parsed[key]=parse(store.raw(e),e);evidence[key]=e
        except (Failure,ValueError,KeyError,TypeError,zipfile.BadZipFile) as exc:
            errors.append(dict(task=t,stage='validation',reason=exc.code if isinstance(exc,Failure) else 'source_schema_mismatch'))
    by_kind={}
    for t in tasks:
        k=sha(encoded(t))
        if k in parsed:by_kind.setdefault(t['kind'],[]).append((t,parsed[k],evidence[k]))
    rows=[]
    def add(t, values, sources):
        for i,value in enumerate(values):
            rows.append(dict(kind=t['kind'],identity=sha(encoded([t,i])),request_identity=t,asof=t['asof'],collected_at=max(e['collected_at'] for e in sources),source=[e['source'] for e in sources],source_run=[e['source_run'] for e in sources],source_hash=[e['source_hash'] for e in sources],validation_status='validated',provider_finality='UNVERIFIED',publication_status='candidate',value=value))
    for kind,entries in by_kind.items():
        if kind not in ('flow','price'):
            for t,values,e in entries:add(t,values,[e])
    if require_market:
        universe={(r['symbol'],r['market']):r for t,rs,e in by_kind.get('master',[]) for r in rs}
        refs={(r['ISU_CD'],t['market']):(r,e) for t,rs,e in by_kind.get('krx',[]) for r in rs}
        flows={t['symbol']:(t,rs[0],e) for t,rs,e in by_kind.get('flow',[])}
        prices={t['symbol']:(t,rs[0],e) for t,rs,e in by_kind.get('price',[])}
        calendar=by_kind.get('calendar',[])
        for t in [x for x in tasks if x['kind']=='flow']:
            try:
                symbol=t['symbol'];u=universe.get((symbol,t['market']))
                if not u or not u['eligible']:raise Failure('universe_missing_or_excluded')
                if len(calendar)!=1 or calendar[0][0]['asof']!=t['asof']:raise Failure('calendar_unverified')
                ft,f,fe=flows[symbol];pt,p,pe=prices[symbol];r,re=refs[(symbol,t['market'])]
                if ft['asof']!=pt['asof'] or r['BAS_DD']!=t['asof'].replace('-',''):raise Failure('generation_date_mismatch')
                if abs((dt.datetime.fromisoformat(fe['collected_at'])-dt.datetime.fromisoformat(pe['collected_at'])).total_seconds())>900:raise Failure('price_flow_vintage_mismatch')
                if any(p[k]!=integer(r[field]) for k,field in KRX_FIELDS.items()):raise Failure('KIS_KRX_six_field_conflict')
                if any(f[k]!=p[k] for k in ('close','volume','turnover')):raise Failure('flow_price_conflict')
                # Prior-date official reference policy retained from market_reference.py.
                for e in (re,fe,pe):
                    if dt.datetime.fromisoformat(e['collected_at']).astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()<=t['asof']:raise Failure('prior_date_acceptance_required')
                cap=integer(r['MKTCAP'])
                if cap<=0:raise Failure('invalid_market_cap')
                add(ft,[f],[fe,pe,re]);add(pt,[dict(p,market_cap=cap,market_cap_source='KRX')],[pe,re])
            except (Failure,KeyError,ValueError,TypeError) as exc:
                errors.append(dict(task=t,stage='cross_validation',reason=exc.code if isinstance(exc,Failure) else 'required_evidence_missing'))
    elif by_kind.get('flow') or by_kind.get('price'):
        errors.append(dict(stage='validation',reason='market_validation_required'))
    if by_kind.get('filings'):
        financial_receipts={t['filing']['receipt'] for t,rs,e in by_kind.get('financial',[])}
        for t,filings,e in by_kind['filings']:
            for filing in filings:
                if not filing['report_code'] or filing['receipt'] not in financial_receipts:
                    errors.append(dict(task=t,stage='financial_coverage',reason='financial_period_missing_or_unsupported',receipt=filing['receipt']))
    report=dict(schema=1,held=len(errors),errors=errors,validated_rows=len(rows),collection_status='complete' if all(state['tasks'].get(sha(encoded(t)),{}).get('collection_status')=='received' for t in tasks) else 'partial',asof=tasks[0]['asof'] if tasks else None,source_hashes=sorted(e['source_hash'] for e in evidence.values()),validation_policy='collector-v1-exact-6-field')
    store.write('quality.json',report)
    store.write('validated.json',rows)
    return rows,report
