"""Operational daily acceptance, explicitly distinct from provider finality.

No clock-only acceptance: a prior Seoul date, independently issued KRX OHLCV,
and a second KIS J flow response must agree with the exact evaluated payload.
"""
import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

POLICY = 'krx-daily-crosscheck-v1'
LEGACY_CONFLICT = 'Preserved raw close differs from current KRX raw close; cause unresolved'
FIELDS = {'open':'TDD_OPNPRC','high':'TDD_HGPRC','low':'TDD_LWPRC',
          'close':'TDD_CLSPRC','volume':'ACC_TRDVOL','turnover':'ACC_TRDVAL'}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def daily_accepted(bundle):
    """Re-evaluate evidence; a saved status/boolean alone can never open the gate."""
    try:
        e=bundle['daily_acceptance'];row=e['krx_row'];flows=e['requeried_flows']
        day=bundle['asof'];symbol=bundle['symbol'];market=bundle['market']
        observed=dt.datetime.fromisoformat(e['observed_at'])
        if observed.tzinfo is None or day>=observed.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat():return False
        if e['policy']!=POLICY or e['asof']!=day or e['symbol']!=symbol:return False
        if bundle['exchange']!='KRX' or bundle['adjustment']!='adjusted':return False
        part={'KOSPI':'stk','KOSDAQ':'ksq'}[market]
        url=f'https://data-dbg.krx.co.kr/svc/apis/sto/{part}_bydd_trd?basDd='+day.replace('-','')
        if e['krx_url']!=url or row['BAS_DD']!=day.replace('-','') or row['ISU_CD']!=symbol:return False
        official_observed=dt.datetime.fromisoformat(row['_retrieved_at'])
        if official_observed.tzinfo is None or day>=official_observed.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat():return False
        if row['_source_url']!=url:return False
        if e['flow_endpoint']!='KIS FHPTJ04160001 / J / million KRW to KRW':return False
        if e['bars_sha256']!=digest(bundle['bars']) or e['flows_sha256']!=digest(bundle['flows']):return False
        if bundle['bars'][-1]['trade_date']!=day:return False
        if not all(bundle['bars'][-1][k]==float(row[v]) for k,v in FIELDS.items()):return False
        if bundle['price_comparison']['kis_unadjusted']!=float(row['TDD_CLSPRC']):return False
        if len(flows)!=20 or flows!=bundle['flows'][-20:]:return False
        if [r['trade_date'] for r in flows]!=[r['trade_date'] for r in bundle['bars'][-20:]]:return False
        return True
    except (KeyError,ValueError,TypeError,IndexError,OverflowError):return False

def blocking_conflicts(bundle):
    # The archived dashboard price is not an input to this separate KRX series.
    # Retain its discrepancy in the bundle; only exempt it with complete evidence.
    accepted=daily_accepted(bundle)
    return [x for x in bundle.get('price_conflicts',[]) if not (accepted and x==LEGACY_CONFLICT)]
