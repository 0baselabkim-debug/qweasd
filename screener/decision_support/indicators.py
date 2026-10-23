"""Calendar-aligned features; nulls are never interpolated or changed to zero."""
import math


def metric(value, reason=None):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('nonfinite_indicator')
    return {'value': value, 'status': 'valid' if value is not None else 'unavailable',
            'reason': None if value is not None else reason or 'insufficient_verified_history'}


def flow_metrics(days, amounts, quantities=None):
    def total(n, offset=0, source=amounts):
        end=len(days)-offset; dates=days[max(0,end-n):end]
        vals=[source.get(d) for d in dates]
        return sum(vals) if len(vals)==n and all(v is not None for v in vals) else None
    out={f'net_{n}d':metric(total(n),'missing_calendar_aligned_flow') for n in (1,3,5,20)}
    previous=total(5,5);now=total(5)
    out['previous_5d']=metric(previous,'missing_previous_nonoverlapping_5_days')
    out['change_5d']=metric(now-previous if now is not None and previous is not None else None,'missing_10day_flow')
    yesterday=total(1,1);today=total(1)
    out['day_change']=metric(today-yesterday if today is not None and yesterday is not None else None,'missing_previous_day_flow')
    values=[amounts.get(d) for d in days[-20:]]
    out['positive_days_20d']=metric(sum(v>0 for v in values) if len(values)==20 and all(v is not None for v in values) else None,'missing_20day_flow')
    out['short_reversal']=metric(('to_net_selling' if today<0<=yesterday else 'to_net_buying' if today>0>=yesterday else 'no_sign_change') if today is not None and yesterday is not None else None,'missing_previous_day_flow')
    if quantities is not None:
        for n in (1,5,20):out[f'net_quantity_{n}d']=metric(total(n,source=quantities),'missing_quantity_observations')
    return out


def technical(days, observations, benchmarks=None):
    """One series/version. Every input bar must already have passed acceptance.

    SMA slope = today minus previous trading day's SMA (one-day price units).
    ATR = Wilder seed on first 14 true ranges, recursive thereafter. At least
    15 bars are required to seed; initialization window is returned explicitly.
    Ichimoku current cloud uses calculations from 26 sessions ago; projected
    cloud is separately named and never compared with today's price.
    """
    bars=[observations.get(d) for d in days]
    def window(n,end=None):
        end=len(bars) if end is None else end
        if end<0:return None
        w=bars[max(0,end-n):end]
        return w if len(w)==n and all(x is not None for x in w) else None
    def avg(field,n,end=None):
        w=window(n,end);return sum(x[field] for x in w)/n if w else None
    def midpoint(n,end=None):
        w=window(n,end);return (max(x['high'] for x in w)+min(x['low'] for x in w))/2 if w else None
    latest=bars[-1] if bars else None;close=latest['close'] if latest else None
    out={}
    for n in (5,20,60,120):
        v=avg('close',n);p=avg('close',n,len(bars)-1)
        out[f'sma_{n}']=metric(v)
        out[f'sma_{n}_slope_1d']=metric(v-p if v is not None and p is not None else None)
        out[f'close_sma_{n}_deviation_pct']=metric((close/v-1)*100 if close is not None and v else None)
    mas=[out[f'sma_{n}']['value'] for n in (5,20,60,120)]
    out['sma_alignment']=metric(('ascending' if all(a>b for a,b in zip(mas,mas[1:])) else 'descending' if all(a<b for a,b in zip(mas,mas[1:])) else 'mixed') if all(v is not None for v in mas) else None)
    tenkan,kijun=midpoint(9),midpoint(26)
    out['ichimoku_tenkan_9']=metric(tenkan);out['ichimoku_kijun_26']=metric(kijun)
    out['ichimoku_projected_span_a']=metric((tenkan+kijun)/2 if tenkan is not None and kijun is not None else None)
    out['ichimoku_projected_span_b']=metric(midpoint(52))
    t,k=midpoint(9,len(bars)-26),midpoint(26,len(bars)-26)
    out['ichimoku_current_span_a']=metric((t+k)/2 if t is not None and k is not None else None)
    out['ichimoku_current_span_b']=metric(midpoint(52,len(bars)-26))
    out['ichimoku_chikou_value']=metric(close)
    out['ichimoku_chikou_plot_date']=metric(days[-27] if close is not None and len(days)>=27 else None)
    out['close_above_current_cloud']=metric(close>max(out['ichimoku_current_span_a']['value'],out['ichimoku_current_span_b']['value']) if close is not None and out['ichimoku_current_span_a']['value'] is not None and out['ichimoku_current_span_b']['value'] is not None else None)
    for n in (5,20):out[f'volume_mean_{n}']=metric(avg('volume',n))
    a,p=avg('turnover',5),avg('turnover',20,len(bars)-5)
    out['turnover_5_vs_previous_20_ratio']=metric(a/p if a is not None and p and p>0 else None)
    # Use only the contiguous accepted suffix, never jump across an invalid day.
    suffix=[]
    for b in reversed(bars):
        if b is None:break
        suffix.append(b)
    suffix.reverse();tr=[]
    for prev,b in zip(suffix,suffix[1:]):tr.append(max(b['high']-b['low'],abs(b['high']-prev['close']),abs(b['low']-prev['close'])))
    atr=None
    if len(tr)>=14:
        atr=sum(tr[:14])/14
        for v in tr[14:]:atr=(atr*13+v)/14
    out['atr_14']=metric(atr)
    out['atr_seed_start']=metric(days[len(days)-len(suffix)] if atr is not None else None)
    for n in (5,20,60):
        w=window(n);prior=window(n,len(bars)-1)
        high=max(x['high'] for x in w) if w else None;low=min(x['low'] for x in w) if w else None
        ph=max(x['high'] for x in prior) if prior else None;pl=min(x['low'] for x in prior) if prior else None
        for key,v in [('high',high),('low',low),('prior_high',ph),('prior_low',pl)]:out[f'{key}_{n}']=metric(v)
        out[f'close_breakout_{n}']=metric(close>ph if close is not None and ph is not None else None)
        out[f'close_breakdown_{n}']=metric(close<pl if close is not None and pl is not None else None)
    w=window(21);up=[];down=[]
    if w:
        for prev,b in zip(w,w[1:]):
            if b['close']>prev['close']:up.append(b['volume'])
            if b['close']<prev['close']:down.append(b['volume'])
    out['up_day_volume_mean_20']=metric(sum(up)/len(up) if up else None,'no_up_days_or_incomplete_21day_history')
    out['down_day_volume_mean_20']=metric(sum(down)/len(down) if down else None,'no_down_days_or_incomplete_21day_history')
    prev=bars[-2] if len(bars)>1 else None
    out['down_volume_expansion']=metric(close<prev['close'] and latest['volume']>prev['volume'] if latest and prev else None)
    w=window(5);level=out['prior_high_20']['value']
    out['recent_high_breakout_failure']=metric(max(b['high'] for b in w)>level and close<=level if w and close is not None and level is not None else None)
    for n in (5,20):
        w=window(n+1);ds=days[-(n+1):];idx=[(benchmarks or {}).get(d) for d in ds]
        ret=(w[-1]['close']/w[0]['close']-1)*100 if w else None
        out[f'return_{n}d_pct']=metric(ret)
        rs=ret-(idx[-1]/idx[0]-1)*100 if ret is not None and len(idx)==n+1 and all(v is not None and v>0 for v in idx) else None
        out[f'market_excess_{n}d_pp']=metric(rs,'price_or_same_market_benchmark_missing')
    out['close']=metric(close,'latest_price_not_verified')
    out['volume']=metric(latest['volume'] if latest else None,'latest_price_not_verified')
    out['close_change_1d']=metric(close-prev['close'] if latest and prev else None)
    out['close_change_1d_pct']=metric((close/prev['close']-1)*100 if latest and prev else None)
    out['volume_change_1d']=metric(latest['volume']-prev['volume'] if latest and prev else None)
    out['volume_change_1d_pct']=metric((latest['volume']/prev['volume']-1)*100 if latest and prev and prev['volume']>0 else None)
    out['intraday_range']=metric(latest['high']-latest['low'] if latest else None)
    out['intraday_range_pct']=metric((latest['high']-latest['low'])/close*100 if latest else None)
    for n in (5,20,60):
        ph=out[f'prior_high_{n}']['value'];pl=out[f'prior_low_{n}']['value']
        out[f'new_high_{n}']=metric(latest['high']>ph if latest and ph is not None else None)
        out[f'new_low_{n}']=metric(latest['low']<pl if latest and pl is not None else None)
    w=window(40);swings={'high':[],'low':[]}
    if w:
        ds=days[-40:]
        for i in range(2,38):
            neighbors=[w[j]['close'] for j in range(i-2,i+3) if j!=i]
            for kind,confirmed in [('high',all(w[i]['close']>v for v in neighbors)),('low',all(w[i]['close']<v for v in neighbors))]:
                if confirmed:swings[kind].append({'price':w[i]['close'],'occurred_on':ds[i],'confirmed_on':ds[i+2]})
    for kind in ('high','low'):
        out[f'confirmed_swing_{kind}s']=metric(swings[kind][-2:] if w else None,'missing_40_verified_sessions')
        values=swings[kind]
        out[f'swing_{kind}_change']=metric(values[-1]['price']-values[-2]['price'] if len(values)>=2 else None,'two_confirmed_swings_missing')
    return out


def observations(flow, price):
    """Descriptive flags with exact sign rules, never buy/sell orders."""
    get=lambda name:flow.get(name,{}).get('value')
    n,p,c=get('net_5d'),get('previous_5d'),get('change_5d');n20=get('net_20d')
    state=None
    if None not in (n,p,c):
        state=('new_inflow' if n>0 and p<=0 else 'strengthening' if n>0 and c>0 else 'stable' if n>0 and c==0 else 'slowing' if n>0 else 'outflow_warning' if n<0 else 'zero_net_flow')
    def pv(k):return price.get(k,{}).get('value')
    ret=pv('return_5d_pct')
    divergence=None if c is None or ret is None else ('improving' if c>0 else 'deteriorating' if c<0 else 'unchanged')+'_flow_'+('rising' if ret>0 else 'falling' if ret<0 else 'unchanged')+'_price'
    return {'flow_state':metric(state,'missing_10day_flow'),
            'flow_price_divergence':metric(divergence,'price_or_flow_unavailable'),
            'possible_trend_damage':metric(n20<0 and pv('close_sma_20_deviation_pct')<0 and pv('sma_20_slope_1d')<0 if n20 is not None and pv('close_sma_20_deviation_pct') is not None and pv('sma_20_slope_1d') is not None else None),
            'entry_evidence':{'resistance_breakout':price.get('close_breakout_20',metric(None)),
                'trend_confirmation':metric(pv('close_sma_20_deviation_pct')>0 and pv('sma_20_slope_1d')>0 if pv('close_sma_20_deviation_pct') is not None and pv('sma_20_slope_1d') is not None else None),
                'pullback_support_level':price.get('sma_20',metric(None)),
                'support_breakdown':price.get('close_breakdown_20',metric(None)),
                'atr_risk_unit':price.get('atr_14',metric(None))},
            'order_signal':None,'decision':'not_generated; objective evidence only; no personal entry date stored'}
