"""Calendar-aligned additions to the existing validated decision-support features."""
import datetime as dt
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from collections import Counter

from screener.decision_support.evidence import bar
from screener.decision_support.indicators import metric, flow_metrics, technical

FIELDS = ('open', 'high', 'low', 'close', 'volume', 'turnover')
ACCEPTED = 'operational_crosscheck_pass'


def checked_days(days, asof):
    if not days or days != sorted(set(days)) or days[-1] != asof:
        raise ValueError('verified_calendar_missing_duplicated_or_wrong_asof')
    for day in days:
        dt.date.fromisoformat(day)
    return days


def table(series):
    columns = series.get('columns', [])
    if len(columns) != len(set(columns)):
        raise ValueError('duplicate_series_columns')
    rows = {}
    for values in series.get('rows', []):
        if len(values) != len(columns):
            raise ValueError('series_shape_mismatch')
        row = dict(zip(columns, values)); day = row['trade_date']
        dt.date.fromisoformat(day)
        for key in FIELDS+('net_amount_krw','net_quantity_shares','institution_net_quantity_shares'):
            value=row.get(key)
            if value is None:continue
            try:
                number=Decimal(str(value))
                if type(value) is bool or not number.is_finite() or number!=number.to_integral_value():raise ValueError('nonintegral_or_invalid_source_unit')
                row[key]=int(number)
            except InvalidOperation:raise ValueError('invalid_source_number') from None
        if day in rows:
            raise ValueError('duplicate_trade_date_preserved_not_deduplicated')
        rows[day] = row
    return rows


def ratio(numerator, denominator, reason='denominator_below_minimum_resolvable_unit', positive_net=False):
    if numerator is None or denominator is None:
        return metric(None, 'missing_ratio_input')
    if positive_net and numerator <= 0:
        return metric(None, 'net_flow_not_positive')
    if denominator < 1:
        return metric(None, reason)
    value = Fraction(numerator) / Fraction(denominator)
    out = metric(float(value))
    out['exact_ratio'] = dict(numerator=str(value.numerator), denominator=str(value.denominator))
    return out


def complete(days, mapping, n, offset=0, field=None):
    end = len(days)-offset
    if end < n:
        return None
    dates = days[end-n:end]
    values = [mapping.get(d) for d in dates]
    if any(v is None for v in values):
        return None
    if field:
        if any(v.get(field) is None for v in values):return None
        return [v[field] for v in values]
    return values


def observed_return(days, accepted, n, offset=0):
    values = complete(days, accepted, n+1, offset, 'close')
    if values is None:
        return metric(None, f'missing_{n+1}_consecutive_verified_closes')
    out = ratio((values[-1]-values[0])*100, values[0])
    out['unit'] = 'percent'
    return out


def exact_return(days, observations, n, offset=0, field='close'):
    values=complete(days,observations,n+1,offset,field)
    if values is None or any(v<=0 for v in values):return None
    return (Fraction(str(values[-1]))/Fraction(str(values[0]))-1)*100


def transition(current, previous):
    if current is None or previous is None:
        return metric(None, 'missing_nonoverlapping_10_session_flow')
    if current > 0:
        state = ('new_net_buying' if previous <= 0 else 'buying_strengthening' if current > previous
                 else 'buying_continuing' if current == previous else 'buying_slowing')
    elif current < 0:
        state = ('new_net_selling' if previous >= 0 else 'selling_expanding' if current < previous
                 else 'selling_continuing' if current == previous else 'selling_easing')
    else:
        state = 'zero_net_flow'
    return metric(state)


def persistence(days, amounts, accepted, foreign):
    recent = complete(days, amounts, 5)
    n5 = foreign['net_5d']['value']; n20 = foreign['net_20d']['value']
    out = dict(positive_days_5d=metric(sum(v > 0 for v in recent) if recent else None, 'missing_5_session_flow'),
               positive_days_20d=foreign['positive_days_20d'],
               transition=transition(n5, foreign['previous_5d']['value']))
    count = 0; exact = False
    for day in reversed(days):
        value = amounts.get(day)
        if value is None:break
        if value <= 0:
            exact = True; break
        count += 1
    latest = amounts.get(days[-1])
    out['consecutive_net_buy_days'] = metric(count if exact and latest is not None else None, 'streak_left_censored_or_missing')
    out['consecutive_net_buy_days_lower_bound'] = metric(count if latest is not None else None, 'latest_flow_missing')
    out['streak_is_exact'] = exact and latest is not None
    out['daily_net_amounts_5d'] = metric([dict(trade_date=d, net_amount_krw=amounts[d]) for d in days[-5:]] if recent else None, 'missing_5_session_flow')
    positive = [v for v in recent or [] if v > 0]
    out['max_positive_day_share_5d'] = (ratio(max(positive), sum(positive)) if positive and n5 > 0
        else metric(None, 'net_flow_not_positive_or_incomplete'))
    out['mean_positive_day_net_amount_krw'] = (ratio(sum(positive), len(positive)) if positive and n5 > 0
        else metric(None, 'net_flow_not_positive_or_incomplete'))
    for n in (5, 20):
        flows = complete(days, amounts, n); turns = complete(days, accepted, n, field='turnover')
        out[f'net_{n}d_to_turnover_ratio'] = ratio(sum(flows) if flows is not None else None,
            sum(turns) if turns is not None else None, positive_net=True)
    valid_positive_days = [d for d in days[-5:] if amounts.get(d, 0) is not None and amounts.get(d, 0) > 0]
    if recent and positive and n5 > 0 and all(d in accepted for d in valid_positive_days):
        strength = sum((Fraction(amounts[d], accepted[d]['turnover']) for d in valid_positive_days), Fraction())/len(valid_positive_days)
        out['mean_positive_day_turnover_ratio'] = dict(metric(float(strength)), exact_ratio=dict(numerator=str(strength.numerator), denominator=str(strength.denominator)))
    else:out['mean_positive_day_turnover_ratio'] = metric(None, 'flow_or_verified_positive_day_turnover_missing')
    sign = lambda x: 'positive' if x > 0 else 'negative' if x < 0 else 'zero'
    out['short_medium_direction'] = metric(dict(short_5d=sign(n5), medium_20d=sign(n20)) if n5 is not None and n20 is not None else None, 'missing_5_or_20_session_flow')
    return out


def structure(days, accepted, base):
    latest = accepted.get(days[-1]); prev = accepted.get(days[-2]) if len(days) > 1 else None
    out = {k:v for k,v in base.items() if any(k.startswith(p) for p in ('high_', 'low_', 'prior_high_', 'prior_low_', 'new_high_', 'new_low_', 'close_break'))}
    for n in (5, 10, 20, 60):
        values = complete(days, accepted, n)
        for label, fun in (('high', max), ('low', min)):
            key = f'{label}_{n}'
            if key not in out:out[key] = metric(fun(v[label] for v in values) if values else None, f'missing_{n}_verified_bars')
    if latest:
        span = latest['high']-latest['low']
        out['close_location_in_day_range'] = ratio(latest['close']-latest['low'], span, 'zero_day_range')
    else:out['close_location_in_day_range'] = metric(None, 'latest_price_not_verified')
    pl = base['prior_low_20']['value']; ph = base['prior_high_20']['value']
    out['new_low_then_close_reclaim_20d'] = metric(latest['low'] < pl <= latest['close'] if latest and pl is not None else None)
    prior22 = complete(days, accepted, 22)
    old_level = max(v['high'] for v in prior22[:-2]) if prior22 else None
    was_breakout = prev['close'] > old_level if prev and old_level is not None else None
    out['prior_breakout_close_held'] = metric(latest['close'] > old_level if latest and was_breakout else False if latest and was_breakout is False else None)
    out['prior_breakout_close_lost'] = metric(latest['close'] <= old_level if latest and was_breakout else False if latest and was_breakout is False else None)
    out['gap_direction'] = metric(('up' if latest['open'] > prev['high'] else 'down' if latest['open'] < prev['low'] else 'overlap') if latest and prev else None)
    for n in (5, 20):
        for field in ('volume', 'turnover'):
            values = complete(days, accepted, n, field=field)
            out[f'current_{field}_to_mean_{n}d_ratio'] = ratio(latest[field]*n if latest and values else None, sum(values) if values else None)
    out['down_day_volume_mean_20'] = base['down_day_volume_mean_20']
    out['up_day_volume_mean_20'] = base['up_day_volume_mean_20']
    out['down_volume_expansion'] = base['down_volume_expansion']
    out['down_volume_contraction'] = metric(latest['close']<prev['close'] and latest['volume']<prev['volume'] if latest and prev else None)
    # "Large" is a transparent descriptive threshold, not a score or order.
    events = []
    for i in range(max(5,len(days)-21), len(days)-1):
        day = days[i]; following = days[i+1]
        window = [accepted.get(d) for d in days[i-5:i]]
        current = accepted.get(day); after = accepted.get(following)
        if current and after and all(window) and current['volume']*5 >= 2*sum(b['volume'] for b in window):
            events.append(dict(trade_date=day, next_trade_date=following, threshold='volume >= 2 * previous_5_session_mean',
                next_close_return_pct=observed_return(days[:i+2], accepted, 1)))
    out['large_volume_following_session'] = metric(events[-5:] if complete(days,accepted,26) is not None else None, 'missing_26_consecutive_verified_bars')
    for field in ('high','low'):
        now=complete(days,accepted,5,field=field);prior=complete(days,accepted,5,5,field=field)
        extreme=max if field=='high' else min
        out[f'nonoverlapping_5d_{field}_change']=metric(extreme(now)-extreme(prior) if now and prior else None,'missing_10_consecutive_verified_bars')
    # Strict two-sided high/low pivots, confirmed two observed sessions later.
    window = complete(days, accepted, 40); pivots = dict(high=[], low=[])
    if window:
        dates = days[-40:]
        for i in range(2, 38):
            for label, cmp in (('high', lambda a,b:a>b), ('low', lambda a,b:a<b)):
                if all(cmp(window[i][label], window[j][label]) for j in range(i-2,i+3) if j != i):
                    pivots[label].append(dict(price=window[i][label], occurred_on=dates[i], confirmed_on=dates[i+2]))
    for label in ('high', 'low'):
        values = pivots[label]
        out[f'confirmed_bar_swing_{label}s'] = metric(values[-2:] if window else None, 'missing_40_consecutive_verified_bars')
        out[f'bar_swing_{label}_change'] = metric(values[-1]['price']-values[-2]['price'] if len(values) >= 2 else None, 'two_confirmed_pivots_missing')
        out[f'distance_to_confirmed_swing_{label}_pct'] = ratio((latest['close']-values[-1]['price'])*100 if latest and values else None, values[-1]['price'] if values else None)
    out['support_or_resistance_confirmed'] = metric(None, 'observed_levels_do_not_establish_support_resistance_or_stop_price')
    return out


def analyze(stock, flow_days, price_days, benchmarks):
    asof = stock['asof']; checked_days(flow_days, asof); checked_days(price_days, asof)
    flows = table(stock['flow_series']); raw = {d:r for d,r in table(stock['price_series']).items() if d <= asof}
    amounts = {d: r.get('net_amount_krw') for d,r in flows.items() if d <= asof}
    quantities = {d: r.get('net_quantity_shares') for d,r in flows.items() if d <= asof}
    institutional = {d: r.get('institution_net_quantity_shares') for d,r in flows.items() if d <= asof}
    for values in (amounts, quantities, institutional):
        if any(v is not None and (type(v) is not int) for v in values.values()):
            raise ValueError('flow_quantity_and_amount_must_be_exact_integer_units')
    accepted = {}; reasons = Counter(); missing = []
    closed = stock.get('_closed_price_days', [])
    for day in price_days:
        row = raw.get(day)
        if row is None:missing.append(day);continue
        if row.get('validation_status') != ACCEPTED or row.get('reasons'):
            reasons.update(row.get('reasons') or ['price_not_verified']); continue
        if day not in closed:
            reasons['daily_bar_not_completed_or_observation_time_unknown'] += 1;continue
        try:accepted[day] = bar(row, dict(zip(FIELDS, FIELDS)))
        except ValueError as error:reasons[str(error)] += 1
    foreign = flow_metrics(flow_days, amounts, quantities)
    institution = flow_metrics(flow_days, {}, institutional)
    for key in institution:
        if not key.startswith('net_quantity_'):institution[key] = metric(None, 'institution_amount_unit_not_officially_confirmed')
    base = technical(price_days, accepted, benchmarks)
    returns = {n:(base[f'return_{n}d_pct'] if n in (5,20) else observed_return(price_days, accepted,n)) for n in (1,3,5,20)}
    previous = observed_return(price_days, accepted,5,5)
    r5 = returns[5]['value']; rp5 = previous['value']; n5 = foreign['net_5d']['value']; change = foreign['change_5d']['value']
    relation = dict(periods={str(n):dict(foreign_net=foreign[f'net_{n}d'], price_return_pct=returns[n]) for n in (1,3,5,20)},
        previous_5d_return_pct=previous,
        return_momentum_change_pp=metric(r5-rp5 if r5 is not None and rp5 is not None else None))
    persistence_metrics = persistence(flow_days, amounts, accepted, foreign)
    structure_metrics = structure(price_days, accepted, base)
    index_returns = {n:metric(None, 'same_market_calendar_aligned_benchmark_missing') for n in (1,5,20)}
    for n in index_returns:
        values = complete(price_days, benchmarks, n+1)
        if values is not None and all(v > 0 for v in values):index_returns[n] = ratio((Fraction(str(values[-1]))-Fraction(str(values[0])))*100, Fraction(str(values[0])))
    # Exact fractions decide change tags; binary float cancellation is not evidence of improvement.
    stock_current_exact=exact_return(price_days,accepted,5)
    stock_prior_exact=exact_return(price_days,accepted,5,5)
    bench_rows={d:{'close':v} for d,v in benchmarks.items()}
    index_current_exact=exact_return(price_days,bench_rows,5)
    index_prior_exact=exact_return(price_days,bench_rows,5,5)
    current_rs_exact=stock_current_exact-index_current_exact if stock_current_exact is not None and index_current_exact is not None else None
    prior_rs_exact=stock_prior_exact-index_prior_exact if stock_prior_exact is not None and index_prior_exact is not None else None
    prior_rs = float(prior_rs_exact) if prior_rs_exact is not None else None
    rs = dict(stock_return_5d_pct=returns[5], stock_return_20d_pct=returns[20], market_return_5d_pct=index_returns[5],
        market_return_20d_pct=index_returns[20], market_excess_5d_pp=base['market_excess_5d_pp'],market_excess_20d_pp=base['market_excess_20d_pp'],
        previous_5d_market_excess_pp=metric(prior_rs),
        industry_excess_5d_pp=metric(None, 'official_industry_index_unavailable'),
        industry_excess_20d_pp=metric(None, 'official_industry_index_unavailable'))
    tag = lambda value, known: metric(value if known else None, 'required_verified_inputs_unavailable')
    price_known = n5 is not None and r5 is not None
    day_stock = returns[1]['value']; day_market = index_returns[1]['value']
    q5 = institution['net_quantity_5d']['value']
    tags = dict(
        foreign_flow_strengthening=tag(n5 is not None and n5>0 and change is not None and change>0, n5 is not None and change is not None),
        foreign_flow_weakening=tag(change is not None and change<0,change is not None),
        foreign_buying_price_falling=tag(price_known and n5>0 and r5<0,price_known),
        foreign_buying_price_rising=tag(price_known and n5>0 and r5>0,price_known),
        foreign_selling_price_rising=tag(price_known and n5<0 and r5>0,price_known),
        institutional_selling_pressure=tag(q5 is not None and q5<0,q5 is not None),
        relative_strength_improving=tag(current_rs_exact is not None and prior_rs_exact is not None and current_rs_exact>prior_rs_exact,current_rs_exact is not None and prior_rs_exact is not None),
        foreign_strengthening_and_relative_strength_improving=tag(n5 is not None and n5>0 and change is not None and change>0 and current_rs_exact is not None and prior_rs_exact is not None and current_rs_exact>prior_rs_exact,n5 is not None and change is not None and current_rs_exact is not None and prior_rs_exact is not None),
        flow_and_price_momentum_strengthening=tag(n5 is not None and n5>0 and change is not None and change>0 and stock_current_exact is not None and stock_prior_exact is not None and stock_current_exact>0 and stock_current_exact>stock_prior_exact,n5 is not None and change is not None and stock_current_exact is not None and stock_prior_exact is not None),
        price_breakout_observed=base['close_breakout_20'], price_breakdown_observed=base['close_breakdown_20'],
        rising_on_market_down_day=tag(day_stock is not None and day_market is not None and day_stock>0 and day_market<0,day_stock is not None and day_market is not None),
        outperforming_on_market_down_day=tag(day_stock is not None and day_market is not None and day_market<0 and day_stock>day_market,day_stock is not None and day_market is not None),
        underperforming_on_market_up_day=tag(day_stock is not None and day_market is not None and day_market>0 and day_stock<day_market,day_stock is not None and day_market is not None),
        recent_event_detected=metric(None, 'exact_time_verified_event_feed_not_connected'))
    relation['observation_types'] = []
    for name, condition in (
        ('positive_flow_price_rising',price_known and n5>0 and r5>0),
        ('strengthening_flow_price_falling',price_known and change is not None and n5>0 and change>0 and r5<0),
        ('weakening_flow_price_rising',price_known and change is not None and change<0 and r5>0),
        ('negative_flow_price_falling',price_known and n5<0 and r5<0),
        ('initial_flow_sign_reversal',persistence_metrics['transition']['value']=='new_net_buying'),
        ('initial_price_recovery',r5 is not None and rp5 is not None and rp5<=0<r5)):
        if condition:relation['observation_types'].append(name)
    suffix = 0
    for day in reversed(price_days):
        if day not in accepted:break
        suffix += 1
    quality = dict(raw_price_rows=len(raw),accepted_price_rows=len(accepted),verified_contiguous_price_days=suffix,
        missing_calendar_price_dates=missing,price_hold_reasons=dict(reasons),
        original_adjusted_basis=stock['price_series'].get('basis'), scope=stock.get('exchange'),
        historical_backtest_eligible=stock.get('validity',{}).get('historical_backtest_eligible',False),
        provider_finality=stock.get('provider_finality','UNVERIFIED'),latest_price_verified=asof in accepted,
        stale_verified_price_used_as_current=False)
    return dict(foreign_flow_metrics=foreign,institution_flow_metrics=institution,flow_persistence_metrics=persistence_metrics,
        flow_price_relationship=relation,price_trend_metrics={**base,**structure_metrics},relative_strength_metrics=rs,
        volume_liquidity_metrics={k:v for k,v in structure_metrics.items() if 'volume' in k or 'turnover' in k},
        volatility_metrics={k:base[k] for k in ('atr_14','atr_seed_start','intraday_range','intraday_range_pct')},
        observation_tags=tags,data_quality=quality)
