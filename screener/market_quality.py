"""Conservative alignment of an undated latest quote with an archived close."""
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo
from .financials import number


def quote_cap(quote, archived, share_thousands, asof, observed_at):
    """Return producer cap in KRW, or None with explicit failed checks.

    This snapshot accepts a quote only after that day's close and before 09:00
    the next calendar day, plus exact close/volume and dated-master share checks.
    It is corroborated latest-close data, not an explicit-date KRX API response.
    """
    shares,cap,close,volume=[number(quote.get(k)) for k in ['lstn_stcn','hts_avls','stck_prpr','acml_vol']]
    master_shares=number(share_thousands)
    failures=[]
    if any(v is None for v in [shares,cap,close,volume,master_shares]):return None,['missing_numeric_field']
    if shares<=0 or cap<=0:failures.append('nonpositive_cap_or_shares')
    if close!=archived['close']:failures.append('close_mismatch')
    if volume!=archived['volume']:failures.append('volume_mismatch')
    if shares//1000!=master_shares:failures.append('dated_master_shares_mismatch')
    if abs(close*shares-cap*100000000)>=100000000:failures.append('cap_unit_or_share_mismatch')
    try:
        observed=datetime.fromisoformat(observed_at.replace('Z','+00:00'))
        if observed.tzinfo is None:raise ValueError('timezone_required')
        observed=observed.astimezone(ZoneInfo('Asia/Seoul'))
        day=date.fromisoformat(asof)
        start=datetime.combine(day,time(15,30),tzinfo=observed.tzinfo)
        end=datetime.combine(day+timedelta(days=1),time(9),tzinfo=observed.tzinfo)
        if not start<=observed<end:failures.append('outside_verified_close_window')
    except (ValueError,TypeError,AttributeError):failures.append('observation_timestamp_missing')
    return (None,failures) if failures else (cap*100000000,[])
