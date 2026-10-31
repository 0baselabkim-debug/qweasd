"""Reuse immutable judgments and strict future-session evaluation, with review axes."""
import json
import datetime as dt
from pathlib import Path
from fractions import Fraction
from zoneinfo import ZoneInfo
from .contracts import independent, number
from .context import load_bundle

from screener.decision_evidence.judgments import record, evaluate
from screener.decision_support.evidence import encoded, sha, timestamp


def record_judgment(row, assessment, result, decision_at, output, portfolio, context=None):
    if result['status'] not in ('LIVE_VALIDATED', 'MOCK_VALIDATED') or not result.get('judgment'):
        return dict(status='NOT_RUN', reason='no_validated_explicit_GPT_judgment')
    evidence = {**row, 'investment_assessment': assessment,
                'verified_context_at_decision': context or dict(status='NOT_RUN', facts=[]),
                'user_portfolio_snapshot': portfolio, 'engine_provenance': {k: v for k, v in result.items() if k != 'judgment'}}
    if result['status'] == 'MOCK_VALIDATED':
        evidence['data_quality'] = {**row['data_quality'], 'historical_backtest_eligible': False}
    judged = result['judgment']
    found = record(evidence, encoded(judged).decode(), decision_at, judged['invalidation'], output)
    return dict(**found, status='SIMULATED_RECORD' if result['status'] == 'MOCK_VALIDATED' else 'GPT_RECORD',
                actual_model_call=result['status'] == 'LIVE_VALIDATED')


def read_record(path, expected_hash):
    raw = Path(path).read_bytes()
    if sha(raw) != expected_hash:
        raise ValueError('judgment_record_anchor_mismatch')
    payload = json.loads(raw)
    if sha(encoded(payload['evidence'])) != payload['evidence_sha256']:
        raise ValueError('judgment_embedded_evidence_mismatch')
    return payload


def review_outcomes(payload, calendar, prices, market, foreign_flows, cutoff, *, user_trades=(), subsequent_context=None):
    """After-the-fact diagnostics do not mutate the original decision or infer fills."""
    result = evaluate(payload, calendar, prices, market, foreign_flows, cutoff)
    decision, known = timestamp(payload['decision_at']), timestamp(cutoff)
    if known > dt.datetime.now(dt.timezone.utc):
        raise ValueError('outcome_cutoff_in_future')
    trades = []
    trade_ids = set()
    for trade in user_trades:
        if trade.get('user_confirmed') is not True:
            raise ValueError('outcome_trade_not_user_confirmed')
        if trade.get('kind') not in ('BUY', 'SELL') or trade.get('stock_code') != payload['stock_code']:
            continue
        if (not trade.get('id') or trade['id'] in trade_ids or type(trade.get('quantity')) is not int or trade['quantity'] <= 0
                or number(trade.get('price_krw')) <= 0 or number(trade.get('fee_krw')) < 0 or number(trade.get('tax_krw')) < 0
                or trade.get('market') != payload['market']):
            raise ValueError('outcome_invalid_or_duplicate_confirmed_fill')
        trade_ids.add(trade['id'])
        if decision <= timestamp(trade['at']) <= known:
            trades.append(trade)
    context = subsequent_context or dict(facts=[])
    events = [f for f in context['facts'] if f['kind'] == 'event' and f.get('stock_code') in (None, payload['stock_code'])
              and f.get('published_at') and decision < timestamp(f['published_at']) <= known
              and f.get('observed_at') and timestamp(f['observed_at']) <= known]
    axes = dict(selection=dict(status=result['status'], meaning='observed_returns_not_causal_stock_picking_skill'),
                entry_timing=dict(status='NOT_RUN', reason='confirmed_entry_or_valid_future_prices_missing'),
                additional_buy=dict(status='NOT_RUN', reason='explicit_add_intent_or_future_prices_missing'),
                exit_timing=dict(status='NOT_RUN', reason='explicit_exit_intent_or_future_prices_missing'),
                thesis_integrity=dict(status='MANUAL_REVIEW_REQUIRED', reason='price_outcomes_do_not_prove_business_thesis'),
                previously_unknown_events=dict(status='OBSERVED_AFTER_DECISION' if events else 'NOT_RUN',
                                               events=[dict(id=e['id'], published_at=e['published_at'], event_type=e['event_type']) for e in events]))
    if result['status'] == 'evaluated_observationally':
        valid_horizons = {k: v for k, v in result['outcomes'].items() if v['status'] == 'observed_not_causal_strategy_validation'}
        buys = [t for t in trades if t['kind'] == 'BUY']
        exits = [t for t in trades if t['kind'] == 'SELL' and t.get('intent') in ('PARTIAL_PROFIT', 'REDUCE', 'FINAL_DEFENSE', 'THESIS_INVALIDATED')]
        if buys and valid_horizons:
            base = prices[payload['asof']]['close']
            axes['entry_timing'] = dict(status='OBSERVED_FILL_COMPARISON_NOT_OPTIMALITY', fills=[
                dict(event_id=t['id'], price_krw=t['price_krw'], difference_from_decision_close_pct=str((Fraction(str(t['price_krw']))/Fraction(base)-1)*100)) for t in buys])
        adds = [t for t in buys if t.get('intent') == 'ADD']
        if adds and valid_horizons:
            axes['additional_buy'] = dict(status='OBSERVED_USER_ADD_FILLS_REQUIRES_CAUSAL_REVIEW', fills=adds)
        if exits and valid_horizons:
            axes['exit_timing'] = dict(status='OBSERVED_USER_EXIT_FILLS_NOT_INFERRED', fills=exits)
        try:
            judgment = json.loads(payload['judgment'])
        except (ValueError, TypeError):
            judgment = {}
        plan = payload['evidence'].get('investment_assessment', {}).get('strategies', {}).get(judgment.get('strategy'), {})
        stop = plan.get('defense')
        if stop is not None:
            dates = calendar['dates']; start = dates.index(payload['asof'])
            for horizon, outcome in valid_horizons.items():
                observations = dates[start+1:start+int(horizon)+1]
                hit = next((d for d in observations if prices[d]['close'] <= stop), None)
                outcome['fixed_original_defense_close_breach'] = dict(triggered=hit is not None, first_trade_date=hit, price=stop,
                    order_execution_inferred=False, later_rule_change_can_erase=False)
    return {**result, 'review_axes': axes, 'actual_user_trade_count': len(trades),
            'historical_record_modified': False, 'performance_is_observational': True}


def evaluate_saved_record(record_path, record_sha256, input_path, input_sha256, output):
    record_path, input_path = Path(record_path).resolve(), Path(input_path).resolve()
    payload = read_record(record_path, record_sha256)
    raw = input_path.read_bytes()
    if sha(raw) != input_sha256:
        raise ValueError('outcome_input_anchor_mismatch')
    future = json.loads(raw)
    if (future.get('schema_version'), future.get('stock_code'), future.get('asof')) != (
            'investment-outcome-input-v1', payload['stock_code'], payload['asof']):
        raise ValueError('outcome_input_identity_mismatch')
    if (future.get('record_sha256'), future.get('evidence_dataset_id'), future.get('evidence_sha256')) != (
            record_sha256, payload['evidence']['dataset_id'], payload['evidence_sha256']):
        raise ValueError('outcome_decision_dataset_hash_binding_mismatch')
    original_close = payload['evidence'].get('price_trend_metrics', {}).get('close', {}).get('value')
    base = future['prices'].get(payload['asof'])
    if base and original_close is not None and Fraction(str(base['close'])) != Fraction(str(original_close)):
        raise ValueError('outcome_base_price_differs_from_original_decision')
    context = dict(facts=[], input_files={})
    if future.get('context_file'):
        context_path = input_path.parent/future['context_file']
        if context_path.is_symlink() or not context_path.resolve().is_relative_to(input_path.parent):
            raise ValueError('unsafe_outcome_context_path')
        if sha(context_path.read_bytes()) != future.get('context_sha256'):
            raise ValueError('outcome_context_anchor_mismatch')
        context = load_bundle(context_path, timestamp(future['cutoff']).astimezone(ZoneInfo('Asia/Seoul')).date().isoformat(), future['cutoff'])
    result = review_outcomes(payload, future['calendar'], future['prices'], future['market'],
                             future['foreign_flows'], future['cutoff'], user_trades=future.get('user_trades', []), subsequent_context=context)
    document = dict(record_sha256=record_sha256, future_input_sha256=input_sha256, result=result,
                    original_record_unchanged=True, upstream_verified_future_bundle_required=True)
    root = independent(output, (record_path, input_path));root.mkdir(parents=True, exist_ok=True)
    path = root/(sha(encoded(document))+'.json')
    raw = encoded(document)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('outcome_existing_result_changed')
    else:
        with path.open('xb') as handle:
            handle.write(raw)
    if (sha(record_path.read_bytes()) != record_sha256 or sha(input_path.read_bytes()) != input_sha256
            or any(sha(Path(p).read_bytes()) != h for p, h in context['input_files'].items())):
        raise ValueError('outcome_inputs_changed')
    return dict(path=str(path), sha256=sha(raw), status=result['status'])
