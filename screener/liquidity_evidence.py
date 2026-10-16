"""Date-specific turnover release, not a general release of raw price conflicts."""
import math
from screener.market_validation import validate
from screener.liquidity_quality import daily_accepted,digest

def price_identity(row):
    return {k:str(row[k]) for k in ['symbol','trade_date','close','adjusted_close','volume','turnover','exchange','source']}

def permits(row,evidence,asof):
    try:
        b=evidence['bundle'];day=str(row['trade_date'])[:10]
        if day>str(asof)[:10] or b['asof']!=day or b['symbol']!=row['symbol'] or row['exchange']!='KRX':return False
        if digest(price_identity(row))!=evidence['original_price_sha256'] or digest(b)!=evidence['bundle_sha256']:return False
        if not daily_accepted(b) or validate(b,day):return False
        last=b['bars'][-1]
        # Only release this existing turnover value; do not substitute a provider close
        # into the raw DB or infer that other dates' conflicts have been resolved.
        return all(math.isfinite(float(row[k])) and float(row[k])==last[k] for k in ['volume','turnover'])
    except (KeyError,ValueError,TypeError,IndexError):return False
