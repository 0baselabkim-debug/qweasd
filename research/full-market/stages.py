"""Research-only stage EVENTS. Earlier stages do not replace the confirmed rule."""
from strategy import pivots,transition_at

def stages_at(closes,t):
    if t<39:return {}
    values=list(closes[t-39:t+1])
    import math
    if any(x is None or not math.isfinite(x) or x<=0 for x in values):return {}
    ps=pivots(values,t-39);out={}
    if len(ps)>=4:
        h1,l1,h2,l2=ps[-4:]
        if ''.join(p.kind for p in ps[-4:])=='HLHL' and h2.price<h1.price and l2.price<l1.price and l2.confirmed_at==t and closes[t]>l2.price:
            out['조기']={'reference_low_index':l2.index,'reference_low':l2.price,'confirmation_index':t,'confirmation_close':closes[t]}
    if len(ps)>=6:
        h1,l1,h2,l2,h3,l3=ps[-6:]
        if ''.join(p.kind for p in ps[-6:])=='HLHLHL' and h2.price<h1.price and l2.price<l1.price and l2.price<l3.price<h3.price and l3.confirmed_at==t:
            out['중간']={'reference_low_index':l2.index,'reference_low':l2.price,'confirmation_index':t,'confirmation_close':closes[t],
                         'rebound_high_index':h3.index,'rebound_high':h3.price,'higher_low_index':l3.index,'higher_low':l3.price}
    confirmed=transition_at(closes,t)
    if confirmed:out['확정']=confirmed
    for e in out.values():
        e['rise_pct']=(e['confirmation_close']/e['reference_low']-1)*100
        e['within_10']=e['reference_low']<=e['confirmation_close']<=e['reference_low']*1.10+e['reference_low']*1e-12
    return out
