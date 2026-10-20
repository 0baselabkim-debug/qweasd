"""Export verified dashboard flow metrics without fabricating missing observations."""
import argparse,json,math,sys,hashlib,datetime as dt,os,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from screener.store import load
from screener.indicators import universe,stock_activity,sector_indicators
from screener.publication import coherent
from scripts.verify_daily_briefing import verify

def clean(v):
    if pd.isna(v):return None
    if isinstance(v,(int,float)) and not math.isfinite(float(v)):return None
    return float(v) if isinstance(v,(int,float)) else str(v)

def fields(r,names):
    return {k:clean(r[k]) for k in names}

def export(asof,output,snapshot=None,dataset_output=None,metrics_output=None):
    started=time.perf_counter()
    root=Path(__file__).resolve().parents[1]
    snapshot=Path(snapshot or root/'snapshot');output=Path(output)
    if not coherent(snapshot):raise ValueError('Snapshot DB/manifest/validation/ledger hash mismatch')
    manifest=json.loads((snapshot/'manifest.json').read_text())
    validation=json.loads((snapshot/'daily-validation.json').read_text())
    ledger=json.loads((snapshot/'publication-ledger.json').read_text())[manifest['asof']]
    if validation.get('trade_date')!=asof or not validation.get('preservation_verified'):raise ValueError('Snapshot validation date/status mismatch')
    if any(v.get('trade_date')!=asof or not v.get('preservation_verified') for v in [manifest.get('daily_validation',{}),ledger.get('validation',{})]):raise ValueError('Validation metadata disagrees')
    if manifest['asof']!=asof:raise ValueError('Manifest date does not match requested date')
    if not manifest.get('daily_validation',{}).get('preservation_verified'):raise ValueError('Unverified snapshot')
    data=load(snapshot/'dashboard.duckdb')
    stocks,matrix=universe(data,asof)
    stocks=stock_activity(data,stocks,asof)
    sectors,_=sector_indicators(data,stocks,matrix,asof)
    required=['net_1d','net_5d','net_20d','change_5d','intensity_5d','breadth_5d','positive_5d','evaluable_5d','members']
    eligible=sectors[(sectors.net_5d>0)&sectors.net_5d.notna()&(sectors.sector!='업종 미분류')]
    leaders=eligible.sort_values(['net_5d','market','sector'],ascending=[False,True,True]).head(3)
    result={'schema_version':2,'asof':asof,'source_snapshot_sha256':manifest['sha256'],'source_run':manifest.get('daily_run'),'classification':'official_sector','ranking':'5d_net_foreign_buy_krw','sector_composite_score_status':'unavailable','notes':['Current-date sector membership applied to past 5/20 trading days; not historical membership','Missing observations are null, never zero','KRX foreign investor net purchases; provider finality unverified'],'top_sectors':[]}
    for rank,(_,sector) in enumerate(leaders.iterrows(),1):
        members=stocks[(stocks.market==sector.market)&(stocks.sector==sector.sector)]
        members=members[members.net_5d.notna()].sort_values(['net_5d','symbol'],ascending=[False,True]).head(5)
        entry={'rank':rank,'market':sector.market,'sector':sector.sector,**fields(sector,required),'top_stocks':[]}
        for _,s in members.iterrows():
            entry['top_stocks'].append({'symbol':s.symbol,'name':s['name'],**fields(s,['net_1d','net_5d','net_20d','change_5d','intensity_5d','positive_days_20d'])})
        result['top_sectors'].append(entry)
    if len(result['top_sectors'])!=3:raise ValueError('Insufficient eligible positive sectors for TOP 3')
    result['raw_flow_reconciliation']=verify(result,snapshot/'dashboard.duckdb')
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    timestamps={k:validation.get(k) for k in ['collection_started_at','collection_completed_at']}
    timestamps['snapshot_published_at']=ledger.get('published_at_utc')
    for k,value in timestamps.items():
        if not value or dt.datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is None:raise ValueError('Missing/invalid source timestamp: '+k)
    result.update(**timestamps,generated_at=now,published_at=now,
        publication_status='ready_for_briefing',validation_status='passed',provider_finality='UNVERIFIED',
        units={'net_amount':'KRW','change_5d':'KRW','breadth_5d':'percent','intensity_5d':'ratio (multiply by 100 for percent)'},
        freshness_policy={'timezone':'Asia/Seoul','require_asof_equals_requested_trade_date':True,'stale_action':'Do not describe as today; report unavailable or delayed','publication_time_basis':'Commit preparation time; publication is established by this file being readable on main'})
    if hashlib.sha256((snapshot/'dashboard.duckdb').read_bytes()).hexdigest()!=manifest['sha256']:raise ValueError('Snapshot changed during export')
    if dataset_output is not None:
        from scripts.export_market_briefing import export_dataset
        legacy_seconds=time.perf_counter()-started
        metrics=export_dataset(snapshot,dataset_output,stocks,sectors,result)
        metrics.update(legacy_generation_seconds=legacy_seconds,combined_generation_seconds=time.perf_counter()-started)
        if metrics_output:
            Path(metrics_output).parent.mkdir(parents=True,exist_ok=True)
            Path(metrics_output).write_text(json.dumps(metrics,indent=2)+'\n')
        print('Full-market dataset:',json.dumps(metrics))
    if output.exists():
        old=json.loads(output.read_text())
        def stable(v):return {k:x for k,x in v.items() if k not in ['generated_at','published_at']}
        if stable(old)==stable(result):return old
    output.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=output.parent,delete=False) as f:
        temp=Path(f.name);f.write(payload);f.flush();os.fsync(f.fileno())
    temp.replace(output)
    print('Exported',asof,len(result['top_sectors']),'sectors to',output)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--asof',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,default=Path('snapshot'))
    p.add_argument('--dataset-output',type=Path)
    p.add_argument('--metrics-output',type=Path)
    a=p.parse_args()
    try:export(a.asof,a.output,a.snapshot,a.dataset_output,a.metrics_output)
    except Exception as e:
        Path('briefing-failure.json').write_text(json.dumps({'status':'failed','asof':a.asof,'reason':str(e),'previous_json_preserved':True},ensure_ascii=False)+'\n')
        raise
