"""Read-only operation backlog; overlapping categories are explicitly not additive."""
import collections,json,sys,gzip,io,tarfile,subprocess,math
import pandas as pd
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.diagnose_activity import diagnose
from scripts.technical_batch import read
from screener.store import load,PACKAGED_DB
from screener.technical_quality import blocking_conflicts,daily_accepted
from screener.technical import rules,validate

def run(out):
    data=load(PACKAGED_DB);day=str(data['flows'].trade_date.max().date());gaps=diagnose(data,day)
    original_blocked=set()
    with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git','archive','c834c172b6fe733add5d35e35c22ffc8f5b467d6','technical/market']))) as archive:
        for member in archive:
            if member.name.endswith('.json.gz'):
                b=json.loads(gzip.decompress(archive.extractfile(member).read()))
                if blocking_conflicts(b):original_blocked.add(b['symbol'])
    # Persist explicit current codes in categories; retain the original cohort evidence unchanged.
    categories=collections.defaultdict(set);sectors=[]
    raw=data['prices'][data['prices'].exchange=='KRX'].set_index(['symbol','trade_date'])
    observations=collections.defaultdict(set)
    bundle_cache={p.name.split('.')[0]:read(p) for p in Path('technical/market').glob('*.json.gz')}
    for s in gaps['sectors']:
        checks=s['remaining_checks'];codes=sorted({r['symbol'] for r in checks})
        for r in checks:
            key=(r['date'],r['symbol']);idx=(r['symbol'],pd.Timestamp(r['date']))
            b=bundle_cache.get(r['symbol'],{});bars=[v for v in b.get('bars',[]) if v['trade_date']==r['date']]
            if idx not in raw.index:observations['missing_krx_price_row'].add(key)
            else:
                price=raw.loc[idx]
                if not math.isfinite(float(price.turnover)) or price.turnover<0:observations['invalid_or_missing_turnover'].add(key)
                if price.turnover==0:observations['observed_zero_turnover'].add(key)
                if price.volume==0:observations['observed_zero_volume_not_proof_of_suspension'].add(key)
                if len(bars)==1 and b.get('exchange')=='KRX':
                    match=all(float(price[k])==bars[0][k] for k in ['volume','turnover'])
                    observations['saved_volume_turnover_equal_not_acceptance' if match else 'saved_volume_or_turnover_mismatch'].add(key)
                else:observations['no_comparable_saved_krx_bar'].add(key)
            categories['activity_25d_incomplete'].add((r['date'],r['symbol']))
            if r['reason']=='unresolved_price_conflict':categories['historical_date_evidence_missing'].add((r['date'],r['symbol']))
        cached=0;latest_ok=0
        for code in codes:
            b=read(Path('technical/market')/(code+'.json.gz'),{})
            if daily_accepted(b) and not validate(b,day):latest_ok+=1
        for r in checks:
            b=read(Path('technical/history')/r['date']/(r['symbol']+'.json.gz'),{})
            if daily_accepted(b) and not validate(b,r['date']):cached+=1
        sectors.append({**s,'latest_accepted_among_missing':latest_ok,'saved_date_bundles_accepted':cached,
                        'potential_stock_gain':len(codes),'constituents_per_remaining_check':s['members']/len(checks) if checks else None,
                        'minimum_fresh_kis_anchors':2*max(0,len(checks)-cached),
                        'latest_date_prerequisites':len(codes)-latest_ok,
                        'planning_requests_no_retry':{'kis_low':3*max(0,len(checks)-cached)+(len(codes)-latest_ok),'kis_high':7*(max(0,len(checks)-cached)+len(codes)-latest_ok),'authentication_per_run':1,'krx_per_distinct_date_per_run':2},
                        'request_estimate_note':'Anchors are lower bound. Planning range assumes 3 requests per unchanged historical candidate, up to 7 with history rebuild; latest prerequisite 1-7. Saved proof may reduce calls; retries increase them. Not a completion promise'})
    active=[];accepted_codes=set()
    for p in Path('technical/market').glob('*.json.gz'):
        b=read(p);code=b['symbol'];key=(b['asof'],code)
        if daily_accepted(b) and not validate(b,day):accepted_codes.add(code)
        if blocking_conflicts(b):
            active.append(code);categories['active_price_conflict'].add(key)
            categories['original_blocked_unresolved' if code in original_blocked else 'newly_discovered_conflict_same_date'].add(key)
        if len(b.get('bars',[]))<rules()['min_bars']:categories['technical_history_short'].add(key)
        if len(b.get('flows',[]))<20:categories['flow_history_short'].add(key)
        if not daily_accepted(b):categories['external_or_incomplete_verification'].add(key)
    ledger=read(Path('technical/recovery-state.json'),{})
    for key,j in ledger.get('jobs',{}).items():
        result=j.get('last_result',{})
        if result.get('promoted'):continue
        if result.get('error_type')=='RecoveryBudget':categories['budget_paused'].add(tuple(key.split('/')))
    groups={k:{'symbols':len({c for d,c in v}),'date_symbol_tasks':len(v),'by_date':dict(collections.Counter(d for d,c in v)),'tasks':[{'date':d,'symbol':c} for d,c in sorted(v)]} for k,v in categories.items()}
    overlap={f'{a} & {b}':len(categories[a]&categories[b]) for i,a in enumerate(groups) for b in list(groups)[i+1:]}
    missing_codes={r['symbol'] for sec in sectors for r in sec['remaining_checks']}
    result={'accepted_with_activity_gap':sorted(accepted_codes & missing_codes),'accepted_without_activity_gap':len(accepted_codes-missing_codes),'asof':day,'stock_activity':gaps['stock_activity'],'categories':groups,'category_overlap_date_symbol_counts':overlap,
            'gap_observation_counts':{k:{'symbols':len({c for d,c in v}),'date_symbol_tasks':len(v)} for k,v in observations.items()},
            'category_note':'overlapping observations, not mutually exclusive causes; exchange mismatch and suspension require source evidence, not inference from price conflict',
            'sectors':sorted(sectors,key=lambda s:-(s['constituents_per_remaining_check'] or 0))}
    out.mkdir(parents=True,exist_ok=True);(out/'completeness.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'stock_activity':gaps['stock_activity'],'categories':{k:{a:b for a,b in v.items() if a!='tasks'} for k,v in groups.items()}},ensure_ascii=False))
if __name__=='__main__':run(Path(sys.argv[1] if len(sys.argv)>1 else 'reports/completeness-phase2'))
