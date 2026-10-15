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
    public=public_bundles().get(symbol)
    if public:candidates.append(public)
    if store:
        record=store.cache(symbol);error=record.get('last_error')
        if record.get('bundle'):candidates.append(record['bundle'])
    candidates=[b for b in candidates if not asof or b.get('asof','')<=str(asof)]
    result=max(candidates,key=lambda b:b['asof']) if candidates else None
    return result,error

def verified_close(bundle):
    if not bundle or validate(bundle):return None
    return bundle['bars'][-1]['close']
