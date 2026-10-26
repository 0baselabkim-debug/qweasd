from ..core import encoded,sha
FIELDS=('foreign_net_quantity','foreign_net_amount','institution_net_quantity','institution_amount_raw','open','high','low','close','volume','turnover','market_cap')


def values(row):return {k:row.get(k) for k in FIELDS}

def compare(a,b):
    if not a.get('population_hash') or not a.get('target_symbols') or set(a['target_symbols'])!=set(b['target_symbols']):
        return {'classification':'NOT_COMPARABLE','reason':'empty_or_different_required_population'}
    if a['asof']!=b['asof'] or a['population_hash']!=b['population_hash'] or b['round']!=a['round']+1:
        return {'classification':'NOT_COMPARABLE','reason':'date_population_or_round_mismatch'}
    left={r['symbol']:r for r in a['rows']};right={r['symbol']:r for r in b['rows']}
    changed=[];same=[];unavailable=[];status_changed=[];categories={'foreign':[],'institution':[],'price':[]}
    expected=set(a['target_symbols'])
    for code in sorted(expected):
        x=left.get(code);y=right.get(code)
        if not x or not y or x['market']!=y['market'] or x['asof']!=y['asof']:
            unavailable.append(code);continue
        diffs={f:{'previous':x.get(f),'current':y.get(f)} for f in FIELDS if x.get(f)!=y.get(f)}
        if x['validation_status']!=y['validation_status']:status_changed.append(code)
        if x['validation_status']!='PASS' or y['validation_status']!='PASS':unavailable.append(code)
        if diffs:
            changed.append({'symbol':code,'fields':diffs})
            for cat,prefix in [('foreign',('foreign_',)),('institution',('institution_',)),('price',('open','high','low','close','volume','turnover','market_cap'))]:
                if any(f.startswith(prefix) for f in diffs):categories[cat].append(code)
        else:same.append(code)
    full=a.get('status')=='COMPLETE' and b.get('status')=='COMPLETE' and not changed and not unavailable and len(same)==len(expected) and len(left)==len(right)==len(expected)
    classification='COMPLETE_MATCH' if full else 'VALUES_CHANGED' if changed else 'PARTIAL_MATCH'
    return dict(classification=classification,same_symbols=len(same),changed=changed,unavailable=unavailable,status_changed=status_changed,changed_by_category=categories,
                provider_finality='UNVERIFIED',observation_windows=[a['observation_window'],b['observation_window']])


def value_hash(rows):return sha(encoded([dict(symbol=r['symbol'],market=r['market'],asof=r['asof'],**values(r)) for r in sorted(rows,key=lambda r:r['symbol'])]))
