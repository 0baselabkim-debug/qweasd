"""Retrospective price paths, explicitly separate from strategy/backtest returns."""
from fractions import Fraction
from screener.decision_support.evidence import timestamp


def path(symbol,market,asof,calendar,bars,cutoff):
    days=calendar.get('days',[])
    if calendar.get('status')!='OFFICIAL_POSITIVE_SESSIONS_INDEPENDENTLY_CROSSCHECKED' or days!=sorted(set(days)) or asof not in days:
        raise ValueError('verified_observed_calendar_required')
    result=dict(symbol=symbol,market=market,asof=asof,analysis_mode='RETROSPECTIVE_PRICE_PATH_ONLY',
        actual_user_trade=None,strategy_performance=False,historical_backtest_eligible=False,outcomes={})
    start=days.index(asof)
    for n in (5,10,20):
        span=days[start:start+n+1];values=[bars.get(d) for d in span]
        if len(span)!=n+1 or any(v is None for v in values):
            result['outcomes'][str(n)]=dict(status='UNAVAILABLE',reason='incomplete_verified_future_path');continue
        for d,v in zip(span,values):
            if (v['symbol'],v['market'],v['trade_date'])!=(symbol,market,d):raise ValueError('future_path_identity_mismatch')
            if not v.get('official_source_ids') or not v.get('sources'):raise ValueError('future_path_unverified')
        if any(timestamp(s['observed_at'])>timestamp(cutoff) for v in values for s in v['sources']):
            result['outcomes'][str(n)]=dict(status='UNAVAILABLE',reason='future_observation_after_cutoff');continue
        base,end=values[0]['value']['close'],values[-1]['value']['close']
        if base<=0 or end<=0:raise ValueError('invalid_future_path_price')
        r=Fraction(end-base,base)*100
        result['outcomes'][str(n)]=dict(status='OBSERVED_RETROSPECTIVELY_NOT_STRATEGY_VALIDATION',target_trade_date=span[-1],
            gross_price_change_pct=float(r),exact_percent=dict(numerator=str(r.numerator),denominator=str(r.denominator)),
            base_close=base,target_close=end,dividends_fees_taxes='NOT_INCLUDED',defense_or_order_execution_inferred=False)
    return result
