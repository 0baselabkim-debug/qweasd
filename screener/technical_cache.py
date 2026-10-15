"""Public pilot observations + private cache access. No API calls on page navigation."""
import gzip,json
from pathlib import Path
from .technical import validate
ROOT=Path(__file__).resolve().parents[1]

def public_bundles():
    path=ROOT/'technical/pilot.json.gz'
    try:return json.loads(gzip.decompress(path.read_bytes())).get('bundles',{})
    except (OSError,ValueError,EOFError):return {}

def get_bundle(symbol,asof=None,store=None):
    candidates=[];error=None
    if len(str(symbol))!=6 or not str(symbol).isascii() or not str(symbol).isdigit():return None,None
    public=public_bundles().get(symbol)
    if len(str(symbol))==6 and str(symbol).isdigit():
        path=ROOT/'technical/market'/(str(symbol)+'.json.gz')
        try:
            market=json.loads(gzip.decompress(path.read_bytes()))
            if not asof or market.get('asof','')<=str(asof):public=market
        except (OSError,ValueError,EOFError):pass
    if public:candidates.append(public)
    if store:
        record=store.cache(symbol);error=record.get('last_error')
        if record.get('bundle'):candidates.append(record['bundle'])
    candidates=[b for b in candidates if isinstance(b,dict) and b.get('symbol')==symbol
                and isinstance(b.get('asof'),str) and (not asof or b['asof']<=str(asof))]
    if candidates:
        latest=max(b['asof'] for b in candidates)
        current=[b for b in candidates if b['asof']==latest]
        for key in ['bars','flows']:
            seen={}
            for b in current:
                for row in b.get(key,[]):
                    day=row.get('trade_date')
                    if day in seen and seen[day]!=row:
                        return None,'공개·비공개 동일 거래일 관측값 불일치 · 판정 보류'
                    seen[day]=row
    # Never fall back to an older verified date as if it were current. On the
    # same date prefer usable evidence, then the history needed for sell anchors.
    result=max(candidates,key=lambda b:(b['asof'],not validate(b),len(b.get('bars',[])))) if candidates else None
    return result,error

def verified_close(bundle,asof=None):
    if not bundle or validate(bundle,asof):return None
    return bundle['bars'][-1]['close']
