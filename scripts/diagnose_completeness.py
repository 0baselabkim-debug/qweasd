"""Read-only operation backlog; overlapping categories are explicitly not additive."""
import collections,json,sys,gzip,io,tarfile,subprocess
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
    for s in gaps['sectors']:
        checks=s['remaining_checks'];codes=sorted({r['symbol'] for r in checks})
        for r in checks:
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
                        'request_estimate_note':'Lower bound excludes authentication, history rebuild, flow verification and retries; not a completion promise'})
    active=[]
    for p in Path('technical/market').glob('*.json.gz'):
        b=read(p);code=b['symbol'];key=(b['asof'],code)
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
    result={'asof':day,'stock_activity':gaps['stock_activity'],'categories':groups,'category_overlap_date_symbol_counts':overlap,
            'category_note':'overlapping observations, not mutually exclusive causes; exchange mismatch and suspension require source evidence, not inference from price conflict',
            'sectors':sorted(sectors,key=lambda s:-(s['constituents_per_remaining_check'] or 0))}
    out.mkdir(parents=True,exist_ok=True);(out/'completeness.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'stock_activity':gaps['stock_activity'],'categories':{k:{a:b for a,b in v.items() if a!='tasks'} for k,v in groups.items()}},ensure_ascii=False))
if __name__=='__main__':run(Path(sys.argv[1] if len(sys.argv)>1 else 'reports/completeness-phase2'))
