"""Conservative performance/stability conclusions from actually completed rounds."""

def aggregate(reports):
    rounds=sorted(reports,key=lambda r:r['round'])
    performance=[{k:r.get(k) for k in ('round','status','scheduled_at','actual_started_at','completed_at','elapsed_seconds','success_rate','physical_http','retries','metrics')} for r in rounds]
    complete=[r for r in rounds if r['status']=='COMPLETE']
    equal=[r for r in rounds if r.get('comparison',{}).get('classification')=='COMPLETE_MATCH']
    first=equal[0] if equal else None
    maintained=bool(first and all(r.get('comparison',{}).get('classification')=='COMPLETE_MATCH' for r in rounds if r['round']>=first['round']))
    first_duration=rounds[0].get('elapsed_seconds') if rounds[0]['status']=='COMPLETE' else None
    last_duration=rounds[-1].get('elapsed_seconds') if rounds[-1]['status']=='COMPLETE' else None
    categories={}
    for cat in ('foreign','institution','price'):
        candidates=[r for r in rounds[1:] if r['status']=='COMPLETE' and rounds[r['round']-2]['status']=='COMPLETE' and not r.get('comparison',{}).get('unavailable',['unknown']) and not r.get('comparison',{}).get('changed_by_category',{}).get(cat,['unknown'])]
        initial=candidates[0] if candidates else None
        categories[cat]={'first_equal_pair':[initial['round']-1,initial['round']] if initial else None,'observation_windows':initial['comparison'].get('observation_windows') if initial else None,'finality':'UNVERIFIED'}
    best=min(complete,key=lambda r:(r.get('retries',0),r.get('elapsed_seconds',float('inf')))) if complete else None
    return dict(category_stability=categories,best_verified_configuration=best.get('config') if best else None,api_interval_seconds=1.05,performance=performance,first_complete_equal_pair=[first['round']-1,first['round']] if first else None,
                observed_equal_windows=first['comparison']['observation_windows'] if first else None,
                later_equality_maintained=maintained,provider_finality='UNVERIFIED',
                first_full_collection_seconds=first_duration,last_full_collection_seconds=last_duration,
                elapsed_improvement_percent=(first_duration-last_duration)*100/first_duration if first_duration and last_duration else None,
                elapsed_change_is_not_causal_optimization_proof=True,
                recommendation='Use actual observation interval only; retain 1.05s and independent validation' if maintained and len(complete)==4 else 'Insufficient complete comparable observations; no stable operating time established')
