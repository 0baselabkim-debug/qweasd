"""Deterministic strata selection; not a performance-based stock recommendation."""
from pathlib import Path
import json
import duckdb,pandas as pd
ROOT=Path(__file__).resolve().parent

def main():
    f=pd.read_csv(ROOT/'inputs/selection-universe.csv',dtype={'symbol':str})
    with duckdb.connect('/workspace/offline-flow-reversal-20261019/input/dashboard.duckdb',read_only=True) as c:
        listing=c.execute('select symbol,listing_date from instruments').fetchdf()
    f=f.merge(listing,on='symbol')
    # Exclude obvious pre-listing insufficiency from a 60-session collection experiment.
    f=f[f.listing_date<=pd.Timestamp('2026-07-01')]
    failed='40일 미만·연결 실패';good='장기 연결 대조군';review='40일 이상이나 품질 보류'
    large='대형(상위1/3)';mid='중형(중간1/3)';small='소형(하위1/3)'
    strata=[('KOSPI',large,True,failed),('KOSPI',large,False,failed),('KOSPI',mid,True,failed),
            ('KOSPI',small,False,good),('KOSPI',mid,False,review),('KOSDAQ',large,True,good),
            ('KOSDAQ',mid,False,failed),('KOSDAQ',small,True,failed),('KOSDAQ',small,False,failed),
            ('KOSDAQ',large,True,review)]
    chosen=[]
    for market,cap,improving,state in strata:
        group=f[(f.market==market)&(f.cap_bucket==cap)&(f.improving==improving)&(f.history_state==state)].copy()
        group['distance']=(group.market_cap-group.market_cap.median()).abs()
        chosen.append(group.sort_values(['distance','symbol']).iloc[0])
    table=pd.DataFrame(chosen)
    columns=['symbol','name','market','market_cap','cap_bucket','improving','history_state','prices','net_5d','change_5d']
    table[columns].to_csv(ROOT/'results/pilot-targets.csv',index=False,encoding='utf-8-sig')
    config={'asof':'2026-10-19','start':'2026-06-01','end':'2026-10-19','minimum_sessions':60,
            'max_physical_requests':40,'max_elapsed_seconds':600,'interval_seconds':1.05,
            'selection':'5 per market; 5 improving/5 not; 4 large/3 mid/3 small; 6 short/2 long controls/2 quality holds; median cap within stratum',
            'targets':json.loads(table[columns].to_json(orient='records',force_ascii=False))}
    (ROOT/'pilot-config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    print(table[columns].to_string(index=False))
if __name__=='__main__':main()
