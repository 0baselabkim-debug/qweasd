"""Versioned wire contract; no financial calculations or ranking."""
import hashlib,json
SCHEMA_VERSION=3
UNKNOWN='업종 미분류'
STOCK_FIELDS=('net_1d','net_5d','net_20d','previous_5d','change_5d','intensity_5d','positive_days_20d','observed_days_20d')
SECTOR_FIELDS=STOCK_FIELDS+('members','evaluable_1d','evaluable_5d','evaluable_20d','positive_5d','positive_20d','breadth_5d','breadth_20d')
DEFINITIONS={
 'net_1d':'KRW; last trading day foreign net purchases',
 'net_5d':'KRW; last 5 trading days, all observations required',
 'net_20d':'KRW; last 20 trading days, all observations required',
 'previous_5d':'KRW; five trading days immediately before the latest five',
 'change_5d':'KRW; net_5d minus previous_5d (nonoverlapping)',
 'intensity_5d':'ratio; net_5d / positive asof market_cap; multiply by 100 for percent',
 'positive_days_20d':'days; strictly positive complete daily net amounts; null unless 20 complete days',
 'observed_days_20d':'days; available complete observations in calendar window of at most 20 days',
 'breadth_5d':'percent; positive_5d / evaluable_5d * 100',
 'breadth_20d':'percent; positive_20d / evaluable_20d * 100',
 'members':'included dashboard universe, not all registered securities',
 'registered_members':'instruments with this asof membership, including those outside dashboard universe'}
def sector_id(market,sector):
 return market.lower()+'-'+hashlib.sha256((market+'\0'+sector).encode()).hexdigest()[:24]
def dataset_id(asof,sha):return 'flow-v3-'+asof+'-'+sha

def payload(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()
def validity(row,fields):
 return {k:('valid' if row[k] is not None else 'missing') for k in fields}
def quality(row,fields):
 missing=[k for k in fields if row[k] is None]
 return {'incomplete':bool(missing),'missing_metrics':missing,
         'missing_reason':'Incomplete trading-day observations or unavailable positive market capitalization; see metric definitions' if missing else None}
