"""Compare provisional KIS values without representing official price acceptance."""
from ..core import encoded, sha
from .compare import FIELDS
KIS_FIELDS = tuple(k for k in FIELDS if k != 'market_cap')


def compare_kis(a, b):
    expected = a.get('target_symbols', [])
    if (not expected or len(set(expected)) != len(expected) or set(expected) != set(b.get('target_symbols', []))
            or not a.get('population_hash') or a['population_hash'] != b.get('population_hash')
            or a['asof'] != b['asof'] or b['round'] != a['round'] + 1
            or a.get('phase') != 'KIS_PROVISIONAL' or b.get('phase') != 'KIS_PROVISIONAL'):
        return dict(classification='NOT_COMPARABLE',basis='KIS_PROVISIONAL')
    left = {r['symbol']:r for r in a['rows']};right = {r['symbol']:r for r in b['rows']}
    changed=[];same=[];unavailable=[];categories=dict(foreign=[],institution=[],price=[])
    for code in sorted(expected):
        x=left.get(code);y=right.get(code)
        if not x or not y or x['market']!=y['market'] or x['asof']!=y['asof'] or x.get('kis_validation_status')!='PASS' or y.get('kis_validation_status')!='PASS':
            unavailable.append(code);continue
        if any(x.get(k) is None or y.get(k) is None for k in KIS_FIELDS):unavailable.append(code)
        diffs={k:dict(previous=x.get(k),current=y.get(k)) for k in KIS_FIELDS if x.get(k)!=y.get(k)}
        if diffs:
            changed.append(dict(symbol=code,fields=diffs))
            for category,prefix in [('foreign',('foreign_',)),('institution',('institution_',)),('price',('open','high','low','close','volume','turnover'))]:
                if any(k.startswith(prefix) for k in diffs):categories[category].append(code)
        else:same.append(code)
    full = (a['status']==b['status']=='PROVISIONAL_COMPLETE' and not unavailable and not changed
            and len(a['rows'])==len(b['rows'])==len(left)==len(right)==len(expected))
    return dict(classification='PROVISIONAL_COMPLETE_MATCH' if full else 'VALUES_CHANGED' if changed else 'PARTIAL_MATCH',
                basis='KIS_PROVISIONAL',same_symbols=len(same),changed=changed,unavailable=unavailable,changed_by_category=categories,
                provider_finality='UNVERIFIED',official_price_acceptance=False,
                observation_windows=[a['observation_window'],b['observation_window']])


def provisional_value_hash(rows):
    return sha(encoded([dict(symbol=r['symbol'],market=r['market'],asof=r['asof'],**{k:r.get(k) for k in KIS_FIELDS}) for r in sorted(rows,key=lambda r:r['symbol'])]))


def summarize_four(reports, attestations=()):
    ordered=sorted(reports,key=lambda r:r['round'])
    comparisons=[compare_kis(a,b) for a,b in zip(ordered,ordered[1:])]
    complete=(len(ordered)==4 and [r['round'] for r in ordered]==[1,2,3,4]
              and all(r['status']=='PROVISIONAL_COMPLETE' for r in ordered)
              and all(c['classification'] in ('PROVISIONAL_COMPLETE_MATCH','VALUES_CHANGED') and not c.get('unavailable') for c in comparisons))
    first=ordered[0]['elapsed_seconds'] if complete else None
    last=ordered[-1]['elapsed_seconds'] if complete else None
    keys=('round','asof','scheduled_at','actual_started_at','completed_at','elapsed_seconds','physical_http','physical_http_total','failed_http_requests','http_failure_rate','task_failure_count','retries','kis_success_rate','recovery','recovery_intervals','metrics','code_commit','source_code_sha256','config','config_sha256')
    return dict(basis='KIS_PROVISIONAL',performance=[{k:r.get(k) for k in keys} for r in ordered],comparisons=comparisons,
                all_four_provisional_complete=complete,provider_finality='UNVERIFIED',
                elapsed_improvement_percent=(first-last)*100/first if first and last is not None else None,
                measured_change_is_not_causal_optimization_proof=True,
                krx_results=[{k:r.get(k) for k in ('kis_generation','round','status','matched','target','references')} for r in attestations])
