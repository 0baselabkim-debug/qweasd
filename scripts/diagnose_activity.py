"""Read-only exact 25-day gaps; never relax sector completeness or pricing masks."""
import collections,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from screener.indicators import universe,turnover_matrix,activity
from screener.store import load,PACKAGED_DB

def diagnose(data,asof):
    stocks,_=universe(data,asof);sectors=[];details=[];ready=0
    masks={(date,r['symbol']) for date,rows in data['metadata'].get('adjusted_price_conflicts',{}).items() for r in rows}
    prices=data['prices'];raw=prices[prices.exchange=='KRX'].set_index(['symbol','trade_date'])
    for (market,sector),g in stocks.groupby(['market','sector']):
        matrix=turnover_matrix(data,g.symbol.tolist(),[market],asof);missing={};reasons=collections.defaultdict(set);checks=[];complete=0
        for code in g.symbol:
            values=matrix[code];_,ratio=activity(values)
            if pd.notna(ratio):ready+=1;complete+=1;continue
            gaps=[]
            for date in values.index[values.isna()]:
                key=(code,date);day=str(date.date())
                reason='missing_price_row' if key not in raw.index else 'unresolved_price_conflict' if (day,code) in masks else 'invalid_turnover'
                reasons[reason].add(code);gaps.append({'date':day,'reason':reason});checks.append({'symbol':code,'date':day,'reason':reason})
            if len(values)<25:reasons['short_calendar'].add(code)
            if not gaps and len(values)==25:reasons['nonpositive_prior_turnover'].add(code)
            details.append({'symbol':code,'market':market,'sector':sector,'gaps':gaps})
        blocking=sorted({r['date'] for r in checks})
        sectors.append({'market':market,'sector':sector,'members':len(g),'complete_25d_symbols':complete,'unverified_symbols':len(g)-complete,
                        'blocking_dates':blocking,'reason_symbol_counts':{k:len(v) for k,v in reasons.items()},
                        'minimum_additional_date_symbol_checks':len(checks),'remaining_checks':checks,'calendar_days':len(matrix),
                        'basis':'current membership; overlapping reason counts; minimum checks not guaranteed recoveries'})
    sectors.sort(key=lambda r:(r['minimum_additional_date_symbol_checks'],r['unverified_symbols'],r['market'],r['sector']))
    return {'asof':asof,'stock_activity':ready,'sectors':sectors,'stocks':details}
if __name__=='__main__':
    d=load(PACKAGED_DB);day=str(d['flows'].trade_date.max().date());r=diagnose(d,day)
    out=Path('reports/operations-stabilization');out.mkdir(parents=True,exist_ok=True)
    (out/'activity-gaps.json').write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    pd.DataFrame([{k:v for k,v in s.items() if k!='remaining_checks'} for s in r['sectors']]).to_csv(out/'sector-gaps.csv',index=False)
    print(json.dumps({'stock_activity':r['stock_activity'],'smallest_sectors':r['sectors'][:4]},ensure_ascii=False))
