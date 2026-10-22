"""Preserve response versions and derive explicitly qualified observations."""
import datetime as dt
import gzip
import hashlib
import json
import re
from pathlib import Path
from decimal import Decimal

KIS_FIELDS={'open':'stck_oprc','high':'stck_hgpr','low':'stck_lwpr','close':'stck_clpr','volume':'acml_vol','turnover':'acml_tr_pbmn'}
KRX_FIELDS={'open':'TDD_OPNPRC','high':'TDD_HGPRC','low':'TDD_LWPRC','close':'TDD_CLSPRC','volume':'ACC_TRDVOL','turnover':'ACC_TRDVAL'}


def encoded(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(path):
    raw=Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)

def integer(value):
    if not re.fullmatch(r'-?\d+',str(value)):raise ValueError('missing_or_invalid_integer')
    return int(value)

def date(value):return dt.datetime.strptime(value,'%Y%m%d').date().isoformat()

def timestamp(value):
    t=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if t.tzinfo is None:raise ValueError('unqualified_observation_time')
    return t


def bar(raw, fields):
    out={k:integer(raw.get(v)) for k,v in fields.items()}
    if not (0<out['low']<=min(out['open'],out['close'])<=max(out['open'],out['close'])<=out['high']):raise ValueError('invalid_ohlc')
    if out['volume']<=0 or out['turnover']<=0:raise ValueError('no_trade_or_invalid_volume_turnover')
    return out


def corporate_adjustment_pending(row):
    if row.get('flng_cls_code') not in ('00',) or row.get('revl_issu_reas') not in ('',None):return True
    try:return Decimal(row['prtt_rate'])!=0
    except (KeyError,ValueError,ArithmeticError):return True


def adjustment_pending(row):
    return row.get('mod_yn')!='N' or corporate_adjustment_pending(row)


def institution(raw, amount_multiplier=None):
    """Only endpoint-specific documented multipliers are accepted by caller."""
    reasons=[];qty=None;amount=None
    try:
        qty=integer(raw.get('orgn_ntby_qty'))
        buy=integer(raw.get('orgn_shnu_vol'));sell=integer(raw.get('orgn_seln_vol'))
        if min(buy,sell)<0 or buy-sell!=qty:raise ValueError('institution_quantity_identity')
    except ValueError as e:qty=None;reasons.append(str(e))
    try:
        amount=integer(raw.get('orgn_ntby_tr_pbmn'))
        buy=integer(raw.get('orgn_shnu_tr_pbmn'));sell=integer(raw.get('orgn_seln_tr_pbmn'))
        if min(buy,sell)<0 or abs(buy-sell-amount)>1:raise ValueError('institution_amount_identity')
    except ValueError as e:amount=None;reasons.append(str(e))
    return {'net_quantity':qty,'net_amount_provider_units':amount,
            'net_amount_krw':amount*amount_multiplier if amount is not None and amount_multiplier is not None else None,
            'amount_status':'unit_pending' if amount_multiplier is None else 'valid' if amount is not None else 'invalid',
            'reasons':reasons,'subgroups':{k:v for k,v in raw.items() if any(k.startswith(p) for p in ('scrt_','ivtr_','pe_fund_','bank_','insu_','mrbn_','fund_')) and ('ntby' in k)}}


def official_rows(paths, asof):
    """All saved official versions; disagreement is never resolved by file order."""
    by_key={};sources={};errors=[]
    for path in sorted(paths):
        raw=path.read_bytes();h=sha(raw);obj=read(path)
        if not isinstance(obj,dict) or not isinstance(obj.get('rows'),list):continue
        day=obj.get('asof');url=obj.get('url','')
        if not day or day>asof:continue
        market='KOSPI' if '/stk_bydd_trd?' in url else 'KOSDAQ' if '/ksq_bydd_trd?' in url else None
        expected=f'https://data-dbg.krx.co.kr/svc/apis/sto/{"stk" if market=="KOSPI" else "ksq"}_bydd_trd?basDd='+day.replace('-','')
        if market is None or url!=expected:continue
        try:timestamp(obj['observed_at'])
        except (ValueError,KeyError):continue
        sources[h]={'file':str(path),'sha256':h,'observed_at':obj['observed_at'],'url':url,'asof':day,'market':market}
        seen=set()
        for r in obj['rows']:
            code=r.get('ISU_CD');key=(code,day,market)
            try:
                if not re.fullmatch(r'\d{6}',str(code)) or r.get('BAS_DD')!=day.replace('-','') or r.get('MKT_NM') not in (market, 'KOSDAQ GLOBAL' if market=='KOSDAQ' else market):raise ValueError('official_identity_mismatch')
                if code in seen:raise ValueError('official_duplicate_symbol')
                seen.add(code);value=bar(r,KRX_FIELDS)
                by_key.setdefault(key,[]).append({'value':value,'source_id':h,'observed_at':obj['observed_at']})
            except ValueError as e:
                errors.append({'symbol':code,'asof':day,'source_id':h,'reason':str(e)})
                by_key.setdefault(key,[]).append({'value':None,'source_id':h,'reason':str(e)})
    return by_key,sources,errors


def accept_chart(raw, code, day, market, observed, official, listing_date, blocked=False):
    reasons=[];value=None;verified=None
    try:
        value=bar(raw,KIS_FIELDS)
        timestamp(observed)
        if date(raw['stck_bsop_date'])!=day or day<listing_date:raise ValueError('chart_date_or_listing_mismatch')
    except (ValueError,KeyError) as e:reasons.append(str(e) if isinstance(e,ValueError) else 'missing_chart_field')
    if corporate_adjustment_pending(raw):reasons.append('corporate_action_adjustment_unverified')
    if raw.get('mod_yn')!='N':reasons.append('no_trade_or_indicative_candle_unverified')
    if blocked:reasons.append('historical_adjustment_basis_unverified')
    refs=official.get((code,day,market),[])
    variants={encoded(r['value']) for r in refs}
    if not refs:reasons.append('official_KRX_not_available')
    elif len(variants)!=1 or refs[0]['value'] is None:reasons.append('official_reference_conflict_or_invalid')
    elif value is not None and value!=refs[0]['value']:reasons.append('KIS_KRX_six_field_conflict')
    elif not reasons:verified=value
    return {'value':value,'accepted':verified,'status':'operational_crosscheck_pass' if verified else 'hold',
            'reasons':reasons,'official_source_ids':sorted({r['source_id'] for r in refs}),
            'provider_finality':'UNVERIFIED'}
