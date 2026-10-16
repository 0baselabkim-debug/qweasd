"""Read-only counts from published data and observed operations, no network calls."""
import collections,copy,datetime as dt,gzip,hashlib,json,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.technical_batch import read
from screener.store import load,PACKAGED_DB
from screener.technical import validate,buy_guide,rules
from screener.technical_quality import daily_accepted,blocking_conflicts,digest
from screener.indicators import universe,stock_activity,sector_indicators
from screener.corporate import load_state

BASE='736a282'
def at(name):
    raw=subprocess.check_output(['git','show',BASE+':'+name]);return json.loads(gzip.decompress(raw) if name.endswith('.gz') else raw)
def run():
    data=load(PACKAGED_DB);asof=str(data['flows'].trade_date.max().date());x,m=universe(data,asof);x=stock_activity(data,x,asof);s,_=sector_indicators(data,x,m,asof)
    ledger=read(Path('technical/recovery-state.json'));categories=collections.Counter();guides=collections.Counter();accepted=0
    before=at('technical/summary.json');changed=[]
    for path in sorted(Path('technical/market').glob('*.json.gz')):
        b=read(path);code=b['symbol'];key=b['asof']+'/'+code;j=ledger['jobs'].get(key,{})
        if daily_accepted(b) and not validate(b,asof):category='verified_current_date';accepted+=1
        elif len(b['bars'])<rules()['min_bars']:category='short_history'
        elif len(b['flows'])<20:category='short_flow_history'
        elif j.get('last_result',{}).get('error_type')=='RecoveryBudget':category='budget_paused'
        elif blocking_conflicts(b):category='stored_supplier_conflict'
        else:category='not_yet_verified_or_other_evidence_gap'
        categories[category]+=1;guides[buy_guide(b,asof)['status']]+=1
    runs=[r for r in ledger['runs'] if r.get('metering')=='physical_boundary']
    state=load_state();old=at('snapshot/corporate-state.json.gz');failed={k:j for k,j in state['jobs'].items() if j.get('business_status')=='failed' or j.get('financial_status')=='failed'}
    original_preserved={name:all(state[name].get(k)==v for k,v in old[name].items()) for name in ['filings','financials','reviews']}
    original_preserved['events_prefix']=state['events'][:len(old['events'])]==old['events']
    metrics={'asof':asof,'before':{'accepted':before['daily_accepted'],'guides':before['guide_status_counts'],'stock_activity':387,'sector_activity':0},
        'after':{'accepted':accepted,'guides':guides,'stock_activity':int(x.activity_ratio.notna().sum()),'sector_activity':int(s.activity_ratio.notna().sum())},
        'exclusive_current_queue_categories':categories,'physical_runs':runs,'corporate':{'unique_failed_filings':len(failed),'error_combinations':dict(collections.Counter('|'.join(sorted(j.get(c+'_error','') for c in ['business','financial'] if j.get(c+'_status')=='failed')) for j in failed.values())),
          'business_success_before':sum(j.get('business_status')=='success' for j in old['jobs'].values()),'business_success_after':sum(j.get('business_status')=='success' for j in state['jobs'].values()),'new_financial_records':len(state['financials'])-len(old['financials']),'preservation':original_preserved},
        'liquidity_evidence_dates':{d:sorted(v) for d,v in data['metadata'].get('liquidity_price_evidence',{}).items()},
        'raw_conflict_mask_counts':{d:len(v) for d,v in data['metadata'].get('adjusted_price_conflicts',{}).items()},
        'data_sha256':hashlib.sha256(PACKAGED_DB.read_bytes()).hexdigest(),'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
    proof_codes={c for v in data['metadata'].get('liquidity_price_evidence',{}).values() for c in v}
    metrics['liquidity_trace']=[]
    for code in sorted(proof_codes):
        row=x.set_index('symbol').loc[code];sector=s[(s.sector==row.sector)&(s.market==row.market)].iloc[0]
        metrics['liquidity_trace'].append({'symbol':code,'name':row['name'],'market':row.market,'sector':row.sector,'activity_ratio':None if __import__('pandas').isna(row.activity_ratio) else row.activity_ratio,
            'average_turnover':None if __import__('pandas').isna(row.average_turnover) else row.average_turnover,'sector_activity':None if __import__('pandas').isna(sector.activity_ratio) else sector.activity_ratio})
    out=Path('reports/recovery-operations');out.mkdir(exist_ok=True)
    (out/'verification.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in metrics.items() if k!='physical_runs'},ensure_ascii=False))
if __name__=='__main__':run()
