"""Export verified dashboard flow metrics without fabricating missing observations."""
import argparse,json,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from screener.store import load
from screener.indicators import universe,stock_activity,sector_indicators

def clean(v):
    if pd.isna(v):return None
    if isinstance(v,(int,float)) and not math.isfinite(float(v)):return None
    return float(v) if isinstance(v,(int,float)) else str(v)

def fields(r,names):
    return {k:clean(r[k]) for k in names}

def export(asof,output):
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads((root/'snapshot/manifest.json').read_text())
    if manifest['asof']!=asof:raise ValueError('Manifest date does not match requested date')
    if not manifest.get('daily_validation',{}).get('preservation_verified'):raise ValueError('Unverified snapshot')
    data=load(root/'snapshot/dashboard.duckdb')
    stocks,matrix=universe(data,asof)
    stocks=stock_activity(data,stocks,asof)
    sectors,_=sector_indicators(data,stocks,matrix,asof)
    required=['net_1d','net_5d','net_20d','change_5d','intensity_5d','breadth_5d','positive_5d','evaluable_5d','members']
    eligible=sectors[(sectors.net_5d>0)&sectors.net_5d.notna()&(sectors.sector!='업종 미분류')]
    leaders=eligible.sort_values(['net_5d','market','sector'],ascending=[False,True,True]).head(3)
    result={'schema_version':1,'asof':asof,'source_snapshot_sha256':manifest['sha256'],'source_run':manifest.get('daily_run'),'classification':'official_sector','ranking':'5d_net_foreign_buy_krw','sector_composite_score_status':'unavailable','notes':['Current-date sector membership applied to past 5/20 trading days; not historical membership','Missing observations are null, never zero','KRX foreign investor net purchases; provider finality unverified'],'top_sectors':[]}
    for rank,(_,sector) in enumerate(leaders.iterrows(),1):
        members=stocks[(stocks.market==sector.market)&(stocks.sector==sector.sector)]
        members=members[members.net_5d.notna()].sort_values(['net_5d','symbol'],ascending=[False,True]).head(5)
        entry={'rank':rank,'market':sector.market,'sector':sector.sector,**fields(sector,required),'top_stocks':[]}
        for _,s in members.iterrows():
            entry['top_stocks'].append({'symbol':s.symbol,'name':s['name'],**fields(s,['net_1d','net_5d','net_20d','change_5d','intensity_5d','positive_days_20d'])})
        result['top_sectors'].append(entry)
    if not result['top_sectors']:raise ValueError('No eligible sectors; refusing empty success')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print('Exported',asof,len(result['top_sectors']),'sectors to',output)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--asof',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();export(a.asof,a.output)
