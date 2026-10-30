"""Explicit immutable judgments and strictly future, calendar-aligned outcome evaluation."""
import datetime as dt
from zoneinfo import ZoneInfo
from pathlib import Path
from fractions import Fraction
from screener.decision_support.evidence import encoded, sha, timestamp
from .metrics import metric, FIELDS
from screener.decision_support.evidence import bar

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parents[2]


def record(evidence, judgment, decision_at, invalidation_conditions, output):
    if not isinstance(judgment,str) or not judgment.strip():raise ValueError('explicit_GPT_judgment_required')
    at=timestamp(decision_at)
    if at>dt.datetime.now(dt.timezone.utc):raise ValueError('judgment_time_in_future')
    if not evidence.get('stock_code') or len(evidence['stock_code'])!=6 or not evidence['stock_code'].isdigit():raise ValueError('invalid_judgment_stock_code')
    dt.date.fromisoformat(evidence['asof'])
    root=Path(output).resolve()
    if root.is_relative_to(ROOT) or ROOT.is_relative_to(root):raise ValueError('judgments_must_be_independent_of_operating_repository')
    if at<timestamp(evidence['generated_at']):raise ValueError('judgment_cannot_use_a_later_generated_dataset')
    if evidence.get('knowledge_cutoff') and at<timestamp(evidence['knowledge_cutoff']):raise ValueError('judgment_knowledge_cutoff_violation')
    if not isinstance(invalidation_conditions,list):raise ValueError('explicit_invalidation_conditions_required')
    payload=dict(schema_version='decision-judgment-v1',stock_code=evidence['stock_code'],asof=evidence['asof'],
        market=evidence.get('market'),
        decision_at=decision_at,judgment=judgment,invalidation_conditions=invalidation_conditions,
        evidence_sha256=sha(encoded(evidence)),evidence=evidence,
        historical_backtest_eligible=evidence['data_quality'].get('historical_backtest_eligible') is True)
    identity=sha(encoded(payload));root.mkdir(parents=True,exist_ok=True);path=root/(identity+'.json')
    raw=encoded(payload)
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('judgment_record_hash_mismatch')
    else:
        with path.open('xb') as handle:handle.write(raw)
    return dict(record_id=identity,path=str(path),historical_backtest_eligible=payload['historical_backtest_eligible'])


def evaluate(payload, calendar, prices, market, foreign_flows, cutoff):
    if not isinstance(payload.get('judgment'),str) or not payload['judgment'].strip():
        return dict(status='unavailable',reason='explicit_GPT_judgment_missing')
    if payload.get('historical_backtest_eligible') is not True:
        return dict(status='unavailable',reason='point_in_time_or_historical_eligibility_not_verified')
    if payload.get('market') not in ('KOSPI','KOSDAQ') or not payload.get('stock_code'):
        return dict(status='unavailable',reason='judgment_stock_market_identity_missing')
    asof=payload['asof'];decision=timestamp(payload['decision_at']);known=timestamp(cutoff)
    if known<decision:raise ValueError('evaluation_precedes_judgment')
    if (not isinstance(calendar,dict) or calendar.get('validation_status')!='verified' or not calendar.get('source_id')
            or not calendar.get('observed_at') or timestamp(calendar['observed_at'])>known):
        return dict(status='unavailable',reason='verified_calendar_provenance_missing')
    calendar=calendar['dates']
    if calendar!=sorted(set(calendar)):raise ValueError('evaluation_calendar_duplicate_or_unsorted')
    for date in calendar:dt.date.fromisoformat(date)
    if asof not in calendar:return dict(status='unavailable',reason='reference_session_missing')
    if decision.astimezone(KST).date().isoformat()!=asof:
        return dict(status='unavailable',reason='reference_price_is_not_from_decision_day')
    start=calendar.index(asof);base=prices.get(asof)
    def identity(value,day,stock=True):
        return (value and value.get('trade_date')==day and value.get('market')==payload['market']
            and (not stock or value.get('stock_code')==payload['stock_code']) and value.get('source_id'))
    def closed_observation(value,day):
        if not value or not value.get('observed_at'):return False
        observed=timestamp(value['observed_at']).astimezone(KST)
        close=dt.datetime.combine(dt.date.fromisoformat(day),dt.time(15,30),KST)
        return close<=observed<=known
    if (not base or base.get('validation_status')!='operational_crosscheck_pass' or base.get('is_closed') is not True
            or not identity(base,asof) or not closed_observation(base,asof) or timestamp(base['observed_at'])>decision or not base.get('basis')):
        return dict(status='unavailable',reason='reference_price_unverified')
    try:bar(base,dict(zip(FIELDS,FIELDS)))
    except ValueError:return dict(status='unavailable',reason='reference_price_invalid')
    outcomes={}
    for n in (5,10,20):
        dates=calendar[start:start+n+1]
        if len(dates)!=n+1 or dates[-1]>known.astimezone(KST).date().isoformat():
            outcomes[str(n)]=dict(status='unavailable',reason='future_horizon_not_elapsed');continue
        bars=[prices.get(d) for d in dates]
        valid=all(b and b.get('validation_status')=='operational_crosscheck_pass' and b.get('is_closed') is True
            and b.get('basis')==base.get('basis') and not any(b.get(k,False) for k in ('adjustment_pending','halted','delisted'))
            and identity(b,d) and closed_observation(b,d) for d,b in zip(dates,bars))
        if valid:
            try:
                for b in bars:bar(b,dict(zip(FIELDS,FIELDS)))
            except ValueError:valid=False
        if not valid:
            outcomes[str(n)]=dict(status='unavailable',reason='missing_halted_delisted_adjusted_or_unverified_price');continue
        entry=base['close'];ret=(Fraction(bars[-1]['close'],entry)-1)*100
        idx=[market.get(d) for d in dates]
        index_known=all(v and v.get('validation_status')=='verified' and identity(v,d,stock=False) and closed_observation(v,d)
            and v.get('close') is not None and v['close']>0 for d,v in zip(dates,idx))
        excess=float(ret-(Fraction(str(idx[-1]['close']))/Fraction(str(idx[0]['close']))-1)*100) if index_known else None
        path=[Fraction(b['close']) for b in bars];peak=path[0];drawdown=Fraction()
        for close in path:
            peak=max(peak,close);drawdown=min(drawdown,(close/peak-1)*100)
        flows=[foreign_flows.get(d) for d in dates[1:]]
        flow_known=all(v and v.get('validation_status')=='verified' and identity(v,d) and closed_observation(v,d)
            and type(v.get('net_amount_krw')) is int for d,v in zip(dates[1:],flows))
        outcomes[str(n)]=dict(status='observed_not_causal_strategy_validation',end_trade_date=dates[-1],return_pct=float(ret),
            market_excess_pp=metric(excess,'matching_benchmark_sessions_missing'),max_close_to_close_drawdown_pct=float(drawdown),
            maximum_intraday_adverse_excursion_pct=float(min(Fraction(),(Fraction(min(b['low'] for b in bars[1:]),entry)-1)*100)),
            maximum_intraday_favorable_excursion_pct=float(max(Fraction(),(Fraction(max(b['high'] for b in bars[1:]),entry)-1)*100)),
            foreign_net_amount_after_judgment=metric(sum(v['net_amount_krw'] for v in flows) if flow_known else None,'verified_future_flow_missing'),
            invalidation_condition_result=None,invalidation_reason='free_text_conditions_require_explicit_review_or_machine_readable_rule')
    return dict(status='evaluated_observationally',outcomes=outcomes)
