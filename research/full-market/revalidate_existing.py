"""A new observation candidate, never a mutation of the 701-stock baseline."""
import math
FIELDS=('open','high','low','close','volume','turnover')

def candidate(old,new,asof,official_raw,scope,symbol,action_dates=()):
 if scope!={'market':'J','symbol':symbol,'end':'20261020','adjustment':'0'}:return None
 observed={d:dict(b) for d,b in new.items() if d<=asof}
 overlap=set(old)&set(observed)
 changed={d for d in overlap if any(old[d][k]!=observed[d][k] for k in FIELDS)}
 if changed!={asof} or len(overlap-{asof})<20 or action_dates:return None
 if official_raw is None or len(official_raw)!=1:return None
 values=tuple(observed[asof][k] for k in FIELDS)
 if values not in official_raw or any(not math.isfinite(v) for v in values):return None
 b=observed[asof]
 if not (0<b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high'] and b['volume']>=0 and b['turnover']>=0):return None
 # Whole KIS OHLCV observations are used. No KRX price is inserted into KIS bars.
 joined={d:dict(b) for d,b in old.items() if d<=asof}
 joined.update(observed)
 return {'bars':joined,'matched_overlap':len(overlap-{asof}),'changed_dates':[asof],'new_observation_dates':sorted(observed)}
