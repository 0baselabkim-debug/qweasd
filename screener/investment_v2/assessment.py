"""Versioned partial scoring, falsifiable hypotheses and conditional strategies."""
from fractions import Fraction
from decimal import Decimal

from . import POLICY
from .contracts import value, number, amount
from .context import financial_metrics
from .portfolio import sizing

STRATEGIES = ('pullback', 'trend_confirmation', 'breakout', 'trend_add', 'flow_maintained_reversal_add')


def compare(x, predicate):
    return None if x is None else bool(predicate(x))


def all_known(*items):
    # A failed condition is evidence of inapplicability, even with other unknowns.
    return False if False in items else None if None in items else True


def structure(row):
    p = lambda k: value(row, 'price_trend_metrics', k)
    if not row['data_quality']['latest_price_verified'] or p('close') is None:
        return dict(status='UNAVAILABLE', support_candidates=[], resistance_candidates=[],
                    support_test_confirmed=None, n_structure_candidate=None, rebound_candidate=None,
                    final_defense_candidate=None, reason='latest_price_not_verified')
    close, low = p('close'), p('current_low')
    supports, resistances = [], []
    for label, key in (('confirmed_low_pivot', 'confirmed_bar_swing_lows'), ('confirmed_high_pivot', 'confirmed_bar_swing_highs')):
        for pivot in p(key) or []:
            price = pivot['price']
            item = dict(price=price, basis=label, occurred_on=pivot['occurred_on'], confirmed_on=pivot['confirmed_on'],
                        state='EXPECTED_LEVEL', price_test_confirmed=(low <= price < close) if low is not None else None)
            if price < close:
                supports.append(item)
            elif price > close:
                resistances.append(item)
    ma = p('sma_20')
    if ma is not None and ma < close:
        supports.append(dict(price=ma, basis='sma20', state='EXPECTED_LEVEL',
                             price_test_confirmed=low <= ma < close if low is not None else None))
    for key in ('prior_high_20', 'high_60'):
        price = p(key)
        if price is not None and price > close:
            resistances.append(dict(price=price, basis=key, state='OBSERVED_RESISTANCE_CANDIDATE'))
    supports.sort(key=lambda v: v['price'], reverse=True)
    resistances.sort(key=lambda v: v['price'])
    confirmed = [s for s in supports if s['basis'] == 'confirmed_low_pivot']
    defense = confirmed[0] if confirmed else None
    lows = p('confirmed_bar_swing_lows')
    highs = p('confirmed_bar_swing_highs')
    n_shape = None
    if lows is not None and highs is not None:
        n_shape = False
        if len(lows) >= 2:
            between = [h for h in highs if lows[-2]['occurred_on'] < h['occurred_on'] < lows[-1]['occurred_on']]
            n_shape = bool(between and lows[-1]['price'] > lows[-2]['price'] and close > between[-1]['price'])
    return dict(status='OBSERVED_STRUCTURE_NOT_FUTURE_SUPPORT_GUARANTEE', support_candidates=supports,
                resistance_candidates=resistances, support_test_confirmed=(any(s['price_test_confirmed'] for s in supports)
                    if supports and low is not None else None), n_structure_candidate=n_shape,
                rebound_candidate=all_known(compare(p('close_change_1d_pct'), lambda v: v > 0),
                    compare(row.get('flow_price_relationship', {}).get('previous_5d_return_pct', {}).get('value'), lambda v: v < 0)),
                final_defense_candidate=defense, defense_policy='exact_confirmed_low_pivot_no_arbitrary_offset',
                support_confirmation_meaning='observed_low_test_and_close_reclaim_only')


def plans(row, technical, context, portfolio):
    p = lambda k: value(row, 'price_trend_metrics', k)
    f = lambda k: value(row, 'foreign_flow_metrics', k)
    close, atr = p('close'), p('atr_14')
    defense = technical['final_defense_candidate']
    stop = defense['price'] if defense else None
    position = portfolio.get('holdings', {}).get(row['stock_code'])
    if position and position.get('defense'):
        stop = float(number(position['defense']['price_krw']))
    upper = [r['price'] for r in technical['resistance_candidates'] if close is not None and r['price'] > close]
    # Preserve independent levels, without deduplicating source observations.
    targets = sorted(set(upper))[:2]
    reward_risk = ((Fraction(str(targets[0]))-Fraction(str(close)))/(Fraction(str(close))-Fraction(str(stop)))) if targets and stop is not None and close > stop else None
    distance = (Fraction(str(close))-Fraction(str(stop)))/Fraction(str(atr)) if stop is not None and close is not None and atr and atr > 0 else None
    risk_ok = reward_risk is not None and reward_risk >= 2 and distance is not None and distance >= 1
    held = row['stock_code'] in portfolio.get('holdings', {})
    nonstructural = next((x['value'] for x in context['facts'] if x.get('stock_code') == row['stock_code'] and x['metric'] == 'adversity_duration'), None)
    trend = all_known(compare(p('sma_20_slope_1d'), lambda v: v > 0),
                      None if close is None or p('sma_20') is None else close > p('sma_20'))
    flow_up = all_known(compare(f('net_5d'), lambda v: v > 0), compare(f('change_5d'), lambda v: v >= 0))
    support_test = technical['support_test_confirmed']
    recovery = all_known(technical['rebound_candidate'], support_test)
    raw_conditions = {
        'pullback': all_known(trend, support_test),
        'trend_confirmation': trend,
        'breakout': p('close_breakout_20'),
        'trend_add': all_known(held, trend, flow_up),
        'flow_maintained_reversal_add': all_known(held, flow_up, recovery,
            None if nonstructural is None else nonstructural == 'temporary'),
    }
    size = sizing(portfolio, row['stock_code'], close, stop) if close is not None and stop is not None else dict(status='UNAVAILABLE', quantity=None)
    out = {}
    for name, condition in raw_conditions.items():
        reasons = []
        if condition is None:
            reasons.append('required_observation_or_event_character_unknown')
        elif condition is False:
            reasons.append('strategy_conditions_not_met')
        if reward_risk is None:
            reasons.append('observed_upside_or_structural_defense_missing')
        elif reward_risk < 2:
            reasons.append('reward_risk_below_two_at_current_entry')
        if distance is None or distance < 1:
            reasons.append('ATR_missing_or_defense_inside_one_ATR')
        if portfolio.get('constraints'):
            reasons.append('portfolio_constraint_breached')
        if position and position.get('defense_breached'):
            reasons.append('existing_defense_already_breached')
        status = 'APPLICABLE_FOR_REVIEW' if condition is True and risk_ok and not portfolio.get('constraints') and not (position and position.get('defense_breached')) else 'UNAVAILABLE' if condition is None or close is None or reward_risk is None else 'INAPPLICABLE'
        out[name] = dict(status=status, observed_conditions_met=condition, inapplicable_or_missing=reasons,
            entry_zone=dict(low=amount(number(close)-number(atr)/4), high=amount(close)) if close is not None and atr is not None and 0 < number(atr) < 4*number(close) else None,
            entry_reference=close, defense=stop, upside_observation_1=targets[0] if targets else None,
            upside_observation_2=targets[1] if len(targets) > 1 else None,
            reward_risk_exact=str(reward_risk) if reward_risk is not None else None,
            defense_distance_ATR_exact=str(distance) if distance is not None else None,
            sizing=size, observation_target_action='REASSESS_TREND_HOLD_IF_INTACT_NOT_AUTOMATIC_TAKE_PROFIT',
            invalidation_conditions=['close_below_fixed_defense', 'foreign_flow_sign_reversal',
                                     'verified_structural_adverse_event', 'rebound_failure_below_tested_level'],
            automatic_order=False)
    return out


def scorecard(row, context, technical, strategy_plans, portfolio):
    f = lambda k: value(row, 'foreign_flow_metrics', k)
    p = lambda k: value(row, 'price_trend_metrics', k)
    i = lambda k: value(row, 'institution_flow_metrics', k)
    persistence = lambda k: value(row, 'flow_persistence_metrics', k)
    financial = financial_metrics(context, row['stock_code'])
    fin = lambda k: Fraction(financial[k]['value']) if financial[k]['value'] is not None else None
    industry = next((x['value'] for x in context['facts'] if x['kind'] == 'industry' and x.get('stock_code') in (None, row['stock_code']) and x['metric'] == 'outlook_positive'), None)
    chosen = strategy_plans['trend_confirmation']
    size = chosen['sizing']
    categories = {
        'foreign': [(5, compare(f('net_5d'), lambda x: x > 0), 'net_5d_positive'),
                    (5, compare(f('net_20d'), lambda x: x > 0), 'net_20d_positive'),
                    (5, compare(persistence('positive_days_5d'), lambda x: x >= 3), 'positive_days_5d_at_least_three'),
                    (5, compare(persistence('positive_days_20d'), lambda x: x >= 10), 'positive_days_20d_at_least_ten'),
                    (5, compare(f('change_5d'), lambda x: x >= 0), 'nonweakening_flow'),
                    (5, compare(persistence('net_5d_to_turnover_ratio'), lambda x: x > 0), 'verified_turnover_buying_intensity')],
        'price': [(5, None if p('close') is None or p('sma_20') is None else p('close') > p('sma_20'), 'close_above_sma20'),
                  (5, compare(p('sma_20_slope_1d'), lambda x: x > 0), 'sma20_rising'),
                  (5, None if p('sma_5') is None or p('sma_20') is None else p('sma_5') > p('sma_20'), 'sma5_above_sma20'),
                  (5, compare(p('return_5d_pct'), lambda x: x > 0), 'return5_positive'),
                  (5, compare(value(row, 'relative_strength_metrics', 'market_excess_5d_pp'), lambda x: x > 0), 'market_outperformance'),
                  (5, technical['support_test_confirmed'], 'observed_support_test')],
        'institution_volume': [(5, compare(i('net_quantity_5d'), lambda x: x > 0), 'institution_net_buying'),
                               (5, compare(i('net_quantity_change_5d'), lambda x: x >= 0), 'institution_pressure_easing'),
                               (5, compare(p('current_volume_to_mean_5d_ratio'), lambda x: x >= 1), 'volume_at_least_mean5')],
        'fundamental': [(5, compare(fin('revenue_growth_pct'), lambda x: x > 0), 'revenue_growth_positive'),
                        (5, compare(fin('operating_margin_ratio'), lambda x: x > 0), 'profitable_operations'),
                        (5, industry if type(industry) is bool else None, 'source_verified_industry_outlook')],
        'risk': [(5, compare(chosen['reward_risk_exact'], lambda x: Fraction(x) >= 2), 'reward_risk_at_least_two'),
                 (5, size['quantity'] > 0 if size.get('quantity') is not None else None, 'account_risk_and_weight_room')],
    }
    out = {}
    for key, criteria in categories.items():
        details = [dict(maximum=weight, passed=flag, criterion=name,
                        score=weight if flag is True else 0 if flag is False else None) for weight, flag, name in criteria]
        out[key] = dict(maximum=sum(w for w, _, _ in criteria),
                        earned_known=sum(d['score'] for d in details if d['score'] is not None),
                        assessed_maximum=sum(d['maximum'] for d in details if d['score'] is not None), criteria=details)
    known = sum(v['assessed_maximum'] for v in out.values())
    earned = sum(v['earned_known'] for v in out.values())
    return dict(policy_version=POLICY, categories=out, partial_score=earned, assessed_maximum=known,
                total_score=earned if known == 100 else None, maximum=100, missing_points=100-known,
                priority_review=earned >= 80 if known == 100 else None, normalized_score=None,
                meaning='80+ is priority review, never an automatic buy', financial_metrics=financial)


def assess(row, context, portfolio):
    technical = structure(row)
    strategy_plans = plans(row, technical, context, portfolio)
    scores = scorecard(row, context, technical, strategy_plans, portfolio)
    f5 = value(row, 'foreign_flow_metrics', 'net_5d')
    r5 = value(row, 'price_trend_metrics', 'return_5d_pct')
    macro = [f['id'] for f in context['facts'] if f['kind'] == 'macro']
    company = [f['id'] for f in context['facts'] if f.get('stock_code') == row['stock_code'] and f['kind'] in ('financial', 'event', 'industry')]
    checks = [
        dict(item='thesis_and_evidence', status='PASS' if f5 is not None else 'HOLD', evidence=['foreign_flow_metrics.net_5d']),
        dict(item='flow_persistence_and_change', status='PASS' if value(row, 'foreign_flow_metrics', 'change_5d') is not None else 'HOLD'),
        dict(item='price_trend_and_entry', status='PASS' if any(p['status'] == 'APPLICABLE_FOR_REVIEW' for p in strategy_plans.values()) else 'HOLD'),
        dict(item='company_industry_macro', status='PASS' if company and macro and scores['categories']['fundamental']['assessed_maximum'] == 15 else 'HOLD'),
        dict(item='counter_thesis_and_risks', status='PASS', reason='explicit competing scenarios; GPT must examine both sides'),
        dict(item='risk_reward_and_invalidation', status='PASS' if scores['categories']['risk']['assessed_maximum'] == 10 and scores['categories']['risk']['earned_known'] == 10 else 'HOLD'),
        dict(item='relative_to_existing_holdings', status='PASS' if portfolio['status'] == 'VALUED' else 'HOLD'),
    ]
    held = portfolio.get('holdings', {}).get(row['stock_code'])
    trend_up = compare(value(row, 'price_trend_metrics', 'sma_20_slope_1d'), lambda v: v > 0)
    holding_action = 'NOT_RUN_NO_USER_HOLDING'
    if held:
        holding_action = ('FINAL_DEFENSE_REVIEW' if held['defense_breached'] else
                          'HOLD_TREND_INTACT' if trend_up is True else 'REDUCE_OR_PROTECT_REVIEW' if trend_up is False else 'HOLD_DECISION_UNAVAILABLE')
    profit = number(held['unrealized_pnl_krw']) > 0 if held and held.get('unrealized_pnl_krw') is not None else None
    breached = held['defense_breached'] if held else None
    weight = number(held['weight_ratio']) > Decimal('.40') if held and held.get('weight_ratio') is not None else None
    bad_event = next((f['value'] for f in context['facts'] if f.get('stock_code') == row['stock_code'] and f['metric'] == 'thesis_invalidated'), None)
    sell_conditions = {
        'hold': all_known(bool(held), trend_up, not breached if breached is not None else None),
        'partial_profit': all_known(bool(held), profit, not trend_up if trend_up is not None else None, not breached if breached is not None else None),
        'reduce': all_known(bool(held), True if weight is True or trend_up is False else None if weight is None or trend_up is None else False),
        'final_defense': all_known(bool(held), breached),
        'thesis_invalidated': all_known(bool(held), bad_event if type(bad_event) is bool else None),
    }
    sell_policies = {name: dict(status='REVIEW_APPLICABLE' if flag is True else 'INAPPLICABLE' if flag is False else 'UNAVAILABLE',
        condition_observed=flag, automatic_order=False,
        limitation='No fill inferred; upside observation is not an exit trigger; original triggered defense remains binding') for name, flag in sell_conditions.items()}
    return dict(stock_code=row['stock_code'], stock_name=row['stock_name'], asof=row['asof'], market=row['market'],
                scorecard=scores, technical_structure=technical, strategies=strategy_plans, hypothesis_checks=checks,
                flow_price_divergence=dict(foreign_buying_price_falling=(f5 > 0 and r5 < 0) if f5 is not None and r5 is not None else None,
                    automatic_negative_conclusion=False, absorption='HYPOTHESIS_NOT_CONFIRMED',
                    market_decline=value(row, 'relative_strength_metrics', 'market_return_5d_pct'),
                    institution_net_quantity_5d=value(row, 'institution_flow_metrics', 'net_quantity_5d'),
                    structural_price_damage=value(row, 'price_trend_metrics', 'close_breakdown_20'),
                    external_adverse_event_evidence=[f['id'] for f in context['facts'] if f['kind'] == 'event' and f.get('stock_code') == row['stock_code']],
                    unobserved_causes='UNKNOWN_NOT_INFERRED_FROM_FLOW_OR_PRICE'),
                holding_action=holding_action, sell_hold_policies=sell_policies, preliminary_grade='C', final_grade=None,
                interpretation_status='AWAITING_GPT_NOT_AN_INVESTMENT_RECOMMENDATION', automatic_order=False)
