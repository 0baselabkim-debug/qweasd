"""Select official chart metadata, with the original quote checks intact.

Both endpoints use J (KRX). output1 is an undated latest observation, so it
must match dated output2/flow and an observed daily master; never backdate it.
No shares are inferred from the rounded thousands field in the master.
"""
from .market_quality import quote_cap
from .daily import integer
FIELDS=['stck_shrn_iscd','stck_prpr','acml_vol','acml_tr_pbmn','lstn_stcn','hts_avls','bstp_kor_isnm','iscd_stat_cls_code']

def chart_quote(series,flow,instrument,day,observed_at,has_industry):
    rows=[r for r in series.get('output2',[]) if r.get('stck_bsop_date')==day]
    if len(rows)!=1 or flow.get('stck_bsop_date')!=day:raise ValueError('Dated price/flow missing')
    # A third undated quote cannot repair a dated close/volume/turnover mismatch.
    for k in ['stck_clpr','acml_vol','acml_tr_pbmn']:
        if integer(rows[0].get(k))!=integer(flow.get(k)):raise ValueError('Dated chart/flow mismatch: '+k)
    head=series.get('output1',{})
    q={k:head.get(k) for k in FIELDS}
    reason=[]
    if q.get('stck_shrn_iscd')!=instrument['symbol']:reason.append('chart_symbol_missing_or_mismatch')
    _,errors=quote_cap(q,{'close':integer(flow['stck_clpr']),'volume':integer(flow['acml_vol'])},instrument['shares_thousands'],f'{day[:4]}-{day[4:6]}-{day[6:]}',observed_at)
    reason.extend(errors)
    try:
        if integer(q.get('acml_tr_pbmn'))!=integer(flow['acml_tr_pbmn']):reason.append('chart_header_turnover_mismatch')
    except ValueError:reason.append('chart_header_turnover_missing')
    if not has_industry:reason.append('industry_quote_required')
    return q,reason
