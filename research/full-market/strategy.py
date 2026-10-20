"""Offline-only experimental rule. No data fetching, trading, or production imports."""
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Pivot:
    kind: str
    index: int
    price: float
    confirmed_at: int

def pivots(values,offset=0):
    """Strict 2-left/2-right closes. Ties are NOT extrema; confirmation at i+2.
    Consecutive extrema of the same kind: keep more extreme, ties keep earlier.
    Called only on an observed prefix, never the full future series for past dates.
    """
    out=[]
    for i in range(2,len(values)-2):
        neighbors=values[i-2:i]+values[i+1:i+3]
        kind='L' if all(values[i]<v for v in neighbors) else 'H' if all(values[i]>v for v in neighbors) else None
        if kind is None:continue
        p=Pivot(kind,i+offset,values[i],i+offset+2)
        if out and out[-1].kind==kind:
            if (kind=='L' and p.price<out[-1].price) or (kind=='H' and p.price>out[-1].price):out[-1]=p
        else:out.append(p)
    return out

def transition_at(closes,t):
    """New close breakout on t with a fully observed trailing 40-session window.
    Alternating H1,L1,H2,L2,H3,L3: H2<H1, L2<L1, L2<L3<H3.
    All pivots confirmed by t; never backdate to the unconfirmed pivot date.
    """
    if t<39 or t>=len(closes):return None
    window=list(closes[t-39:t+1])
    if any(v is None or not math.isfinite(v) or v<=0 for v in window):return None
    ps=pivots(window,t-39)
    if len(ps)<6:return None
    h1,l1,h2,l2,h3,l3=ps[-6:]
    if ''.join(p.kind for p in ps[-6:])!='HLHLHL':return None
    if not(h2.price<h1.price and l2.price<l1.price and l2.price<l3.price<h3.price):return None
    if l3.confirmed_at>t or not(closes[t-1]<=h3.price<closes[t]):return None
    return {'confirmation_index':t,'confirmation_close':closes[t],'reference_low_index':l2.index,'reference_low':l2.price,
            'rebound_high_index':h3.index,'rebound_high':h3.price,'higher_low_index':l3.index,'higher_low':l3.price,
            'higher_low_confirmed_index':l3.confirmed_at,'rise_pct':(closes[t]/l2.price-1)*100}

def signals(closes):
    # First confirmed breakout per swing structure, including after a recross.
    seen=set();events=[]
    for t in range(39,len(closes)):
        event=transition_at(closes[:t+1],t)
        if event is None:continue
        identity=tuple(event[k] for k in ('reference_low_index','rebound_high_index','higher_low_index'))
        if identity not in seen:
            seen.add(identity);events.append(event)
    return events

def classify(*,price_eligible,flow_eligible,net5,previous5,signal):
    if not price_eligible or not flow_eligible:return '판정 불가'
    if net5<=0 or net5<=previous5:return '수급 조건 미충족'
    if signal is None:return 'C'
    # Inclusive 10% rule, avoid round-off at exact 1.10 boundary.
    price=signal['confirmation_close'];low=signal['reference_low']
    return 'A' if low<=price<=low*1.10+abs(low)*1e-12 else 'B'
