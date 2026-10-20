"""Offline research primitives. No market API, production writes or scheduler."""
import datetime as dt
import hashlib
import json
import math
import statistics
from pathlib import Path
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo('Asia/Seoul')


def instant(value):
    value = dt.datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.tzinfo is None:
        raise ValueError('timezone_required')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def next_session(day, calendar):
    i = calendar.index(day)
    return calendar[i+1] if i+1 < len(calendar) else None


def decision_gate(day, calendar, observations, known_at, frozen_at=None):
    """Everything must be available AFTER close and strictly BEFORE next open.
    Historical reconstruction does not require a contemporaneous signal freeze;
    prospective classification does. Never backdate a new freeze.
    """
    entry = next_session(day, calendar)
    if entry is None:
        return False, 'next_official_session_missing'
    close = instant(day+'T15:30:00+09:00')
    deadline = instant(entry+'T09:00:00+09:00')
    if not observations:
        return False, 'observation_time_missing'
    available = max([instant(known_at), *[instant(x) for x in observations]])
    if available < close or available >= deadline:
        return False, 'evidence_not_available_before_next_open'
    if frozen_at is not None:
        lock = instant(frozen_at)
        if lock < available or lock >= deadline:
            return False, 'prospective_freeze_outside_available_window'
    return True, ''


def cohort(state, improving, close, low, high):
    if not improving:
        return None
    if low and close >= low:
        rise = close/low-1
        if state == '중간 전환' and high and close <= high:
            if rise <= .10+1e-12: return 'A'
            if rise <= .20+1e-12: return 'B'
        if state == '상승 전환 확정' and rise <= .20+1e-12:
            return 'C'
    return 'D'


def suppress_duplicates(signals, horizon, calendar):
    """Independent group strategies: one entry per symbol/pattern, no overlaps.
    D has no pivot pattern and may re-enter only after its horizon cooldown.
    C is never selected from successful future A/B positions.
    """
    seen = set(); busy = {}; selected = []
    for s in sorted(signals, key=lambda s:(s['signal_date'], s['symbol'], s['group'])):
        key = (s['symbol'], s['group'])
        i = calendar.index(s['signal_date'])
        pattern = (*key, s.get('L2_date'), s.get('L3_date'))
        if i < busy.get(key, -1): continue
        if s['group'] != 'D' and pattern in seen: continue
        if s['group'] == 'C' and s.get('confirmed_date') != s['signal_date']: continue
        seen.add(pattern); busy[key] = i+horizon; selected.append(s)
    return selected


def trade(signal, bars, calendar, horizon, policy='fixed', roundtrip_bps=0):
    """Entry day counts as day 1; fixed exit is its (horizon-1)th following close.
    Prices are raw execution observations. L3 stop needs a comparable raw L3 and
    verified no-action interval. An unknown adjustment/corporate action blocks it.
    Missing intervals never shorten the holding period. No quote means no fill.
    """
    if horizon<1 or policy not in ('fixed','L3') or not 0<=roundtrip_bps<20000:
        raise ValueError('invalid_policy_or_cost')
    result = {'status':'관측 대기', 'entry_date':None, 'exit_date':None,
              'entry_price':None, 'exit_price':None, 'gross_return':None,
              'net_return':None, 'mae':None, 'entry_gap':None, 'reason':''}
    if not signal.get('pit_eligible') or signal.get('record_kind') == 'retrospective_reference':
        result.update(status='진입 불가', reason='signal_not_point_in_time'); return result
    day = next_session(signal['signal_date'], calendar)
    result['entry_date'] = day
    if day is None: result['reason']='next_official_session_missing'; return result
    start=calendar.index(day)
    entry=bars.get(day)
    if entry is None: result['reason']='entry_OHLC_missing'; return result
    if not entry.get('execution_verified') or entry.get('basis') != 'raw':
        result['reason']='entry_price_basis_unverified'; return result
    if entry.get('tradable') is not True or entry.get('volume', 0) <= 0:
        result.update(status='진입 실패', reason='not_tradable'); return result
    result['entry_price']=entry['open']
    if signal.get('raw_signal_close'):
        result['entry_gap']=entry['open']/signal['raw_signal_close']-1
    lows=[]; exit_price=None; exit_day=None
    for step in range(horizon):
        if start+step >= len(calendar): result['reason']='horizon_not_elapsed'; return result
        d=calendar[start+step]; b=bars.get(d)
        if b is None: result['reason']='holding_day_missing'; return result
        if not b.get('execution_verified') or b.get('basis')!='raw' or not b.get('action_interval_verified'):
            result['reason']='holding_price_or_action_unverified'; return result
        if b.get('tradable') is not True or b.get('volume',0)<=0:
            result['reason']='holding_suspension_unresolved'; return result
        lows.append(b['low'])
        if step == horizon-1: exit_day=d; exit_price=b['close']; break
        if policy=='L3' and signal.get('L3_raw') is None:
            result['reason']='L3_raw_basis_unverified'; return result
        if policy=='L3' and b['close'] < signal['L3_raw']:
            exit_day=next_session(d,calendar); nxt=bars.get(exit_day)
            if not nxt or not nxt.get('execution_verified') or nxt.get('basis')!='raw' or not nxt.get('action_interval_verified'):
                result['reason']='stop_next_open_unverified'; return result
            if nxt.get('tradable') is not True or nxt.get('volume',0)<=0:
                result['reason']='stop_exit_not_tradable'; return result
            exit_price=nxt['open']; lows.append(exit_price); break
    if exit_price is None: return result
    gross=exit_price/entry['open']-1
    # Explicit hypothetical all-in round-trip friction, half allocated per side.
    cost=roundtrip_bps/20000
    net=exit_price*(1-cost)/(entry['open']*(1+cost))-1
    result.update(status='관측 완료', exit_date=exit_day, exit_price=exit_price,
                  gross_return=gross, net_return=net,
                  mae=min(0,min(lows)/entry['open']-1),reason='')
    return result


def summary(completed):
    """Cross-sectional trade statistics only; never pretend these form an NAV."""
    if not completed:
        return {k:None for k in ['mean_net_return','median_net_return','win_rate','mean_mae','p10_net_return']}
    values=sorted(r['net_return'] for r in completed)
    i=(len(values)-1)*.1; lo=math.floor(i); hi=math.ceil(i)
    return {'mean_net_return':statistics.mean(values),'median_net_return':statistics.median(values),
            'win_rate':sum(x>0 for x in values)/len(values),
            'mean_mae':statistics.mean(r['mae'] for r in completed),
            'p10_net_return':values[lo]+(values[hi]-values[lo])*(i-lo)}


def nav_statistics(dated_nav, annual_risk_free=None, calendar=None):
    """Requires an actual dated, capital-constrained marked-to-market NAV series."""
    if len(dated_nav)<2: return {'mdd':None,'sharpe':None,'sortino':None}
    dates=[x[0] for x in dated_nav]; values=[float(x[1]) for x in dated_nav]
    if dates != sorted(set(dates)) or any(not math.isfinite(x) or x<=0 for x in values):
        raise ValueError('invalid_NAV')
    peak=values[0];mdd=0
    for x in values: peak=max(peak,x);mdd=min(mdd,x/peak-1)
    result={'mdd':mdd,'sharpe':None,'sortino':None}
    # Annualization needs consecutive verified sessions, not irregular marks.
    if annual_risk_free is None or len(values)<61 or calendar is None:return result
    if dates != [d for d in calendar if dates[0]<=d<=dates[-1]]:
        raise ValueError('NAV_session_gap')
    rets=[b/a-1-annual_risk_free/252 for a,b in zip(values,values[1:])]
    vol=statistics.stdev(rets);down=(sum(min(0,x)**2 for x in rets)/len(rets))**.5
    if vol:result['sharpe']=statistics.mean(rets)/vol*252**.5
    if down:result['sortino']=statistics.mean(rets)/down*252**.5
    return result
