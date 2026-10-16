"""Small KRX adjusted OHLCV pilot or private authenticated work queue. No orders."""
import argparse,datetime as dt,gzip,json,os,sys,time
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.kis_public_client import KIS
from screener.technical import validate,indicators,rules,sell_guide
from screener.store import load

PILOT=['005930','000660','035420']

def paged(api,symbol,end,index=False,count=320):
    earliest=(dt.date.fromisoformat(end)-dt.timedelta(days=max(900,count*2+90))).strftime('%Y%m%d');cursor=end.replace('-','');records={}
    for _ in range(min(60, (count+99)//100+3)):
        d=api.benchmark('0001',earliest,cursor) if index else api.prices(symbol,earliest,cursor)
        rows=d.get('output2',[]);rows=[r for r in rows if r.get('stck_bsop_date')]
        if not rows:break
        before=len(records)
        if len({r['stck_bsop_date'] for r in rows})!=len(rows):raise ValueError('Duplicate dates within provider page')
        for r in rows:
            day=r['stck_bsop_date'];iso=dt.datetime.strptime(day,'%Y%m%d').date().isoformat()
            if iso<=end:
                if iso in records and records[iso]!=r:raise ValueError('Conflicting pagination observations')
                records[iso]=r
        if len(records)>=count:break
        if len(records)==before:break
        cursor=(dt.datetime.strptime(min(r['stck_bsop_date'] for r in rows),'%Y%m%d').date()-dt.timedelta(days=1)).strftime('%Y%m%d')
    return sorted(records.items())[-count:]

def fetch(api,symbol,asof,calendar,original=None):
    raw=paged(api,symbol,asof);bars=[]
    for day,r in raw:
        bars.append({'trade_date':day,**{col:float(r[field]) for col,field in [('open','stck_oprc'),('high','stck_hgpr'),('low','stck_lwpr'),('close','stck_clpr'),('volume','acml_vol'),('turnover','acml_tr_pbmn')]}})
    response=api.request('/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily',{'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':symbol,'FID_INPUT_DATE_1':asof.replace('-',''),'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''},'FHPTJ04160001')
    flows=[];latest_raw=None
    for r in response.get('output2',[]):
        if not r.get('stck_bsop_date'):continue
        day=dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat()
        if day<=asof:
            flows.append({'trade_date':day,'net_amount':int(r['frgn_ntby_tr_pbmn'])*1_000_000})
            if day==asof:latest_raw=float(r['stck_clpr'])
    flows=sorted(flows,key=lambda r:r['trade_date'])[-20:]
    conflicts=[]
    if not bars or latest_raw is None or bars[-1]['close']!=latest_raw:conflicts.append('Adjusted chart / same-date KRX investor close mismatch')
    if original is not None and bars and bars[-1]['close']!=original:conflicts.append('Preserved dashboard close differs; original untouched')
    bundle={'schema':1,'symbol':symbol,'asof':asof,'exchange':'KRX','adjustment':'adjusted','source':'KIS FHKST03010100 / J / FID_ORG_ADJ_PRC=0','flow_source':'KIS FHPTJ04160001 / J / million KRW multiplied by 1000000','calendar_source':'KIS KOSPI daily index observations','bars':bars,'flows':flows,'calendar':calendar,'finality':'UNVERIFIED','finality_evidence':None,'price_conflicts':conflicts,'corporate_actions_verified':all(r.get('prtt_rate') not in [None,''] and 'revl_issu_reas' in r for d,r in raw),'corporate_actions':[{'trade_date':d,'rate':r.get('prtt_rate'),'reason':r.get('revl_issu_reas')} for d,r in raw if float(r.get('prtt_rate') or 0)!=0 or r.get('revl_issu_reas')],'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    bundle['validation']=validate(bundle,asof)
    structural=validate({**bundle,'price_conflicts':[]},asof,require_final=False)
    bundle['structure_valid']=not structural;bundle['structure_errors']=structural
    if not structural:
        f=indicators(bars);last=f.iloc[-1]
        bundle['computed']={k:float(last[k]) for k in ['ma5','ma20','ma60','ma120','tenkan','kijun','cloud_a','cloud_b','future_span_a','future_span_b','atr','prior_turnover20']}
    return bundle

def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['pilot','private','public','evidence','verify'],default='public');p.add_argument('--limit',type=int,default=150);p.add_argument('--budget',type=int,default=900);p.add_argument('--probe',action='store_true');args=p.parse_args()
    started=time.monotonic();data=load();asof=str(data['flows'].trade_date.max().date())
    if args.mode=='verify':
        from scripts.technical_acceptance import run,LazyAPI
        run(LazyAPI(KIS),data,max(1,min(600,args.limit)),max(60,min(1500,args.budget)),probe=args.probe);return
    if args.mode=='evidence':
        from scripts.technical_evidence import run as probe
        probe(KIS(),data);return
    if args.mode=='public':
        from scripts.technical_batch import run,read,STATE,select_targets,refresh_summary,CACHE
        from screener.technical_quality import daily_accepted
        state=read(STATE,{'symbols':{}})
        exclusions=data.get('exclusions')
        excluded=set(exclusions.loc[exclusions.trade_date.astype(str)==asof,'symbol']) if exclusions is not None else set()
        pending=select_targets(sorted(set(data['instruments'].symbol)-excluded),state['symbols'],asof,1)
        # Completed histories need no token and no historical API request.
        if not pending:
            from scripts.technical_acceptance import run as review,LazyAPI
            needs_review=any((b:=read(p)) and b['asof']==asof and not daily_accepted(b) for p in CACHE.glob('*.json.gz'))
            if needs_review:review(LazyAPI(KIS),data,max(1,min(600,args.limit)),max(60,min(1500,args.budget)))
            else:refresh_summary(asof);print('No pending public histories or evidence checks; no API calls')
            return
        api=KIS();run(api,data,paged,max(1,min(600,args.limit)),max(60,min(1500,args.budget)))
        from scripts.technical_batch import read
        previous_evidence=read(Path('reports/technical-expansion/provider-evidence.json'),{})
        if args.probe or previous_evidence.get('asof')!=asof:
            from scripts.technical_evidence import run as probe
            probe(api,data)
        return
    if args.mode=='private':
        from screener.journal_store import ServiceStore
        store=ServiceStore.from_env()
        if store is None:print('Private worker not configured; no private data accessed');return
        tasks=store.targets(asof);targets=[r['symbol'] for r in tasks][:30]
        if not targets:print('No pending private market-data tasks');return
    else:targets=PILOT
    if args.mode=='private':
        from scripts.private_technical import run
        from scripts.technical_acceptance import LazyAPI
        run(store,LazyAPI(KIS),data,asof,targets,paged);return
    api=KIS();calendar=[d for d,r in paged(api,'0001',asof,index=True)];bundles={};failed=0
    for code in targets:
        try:
            p=data['prices'];old=p[(p.symbol==code)&(p.trade_date.astype(str)==asof)]
            original=float(old.close.iloc[0]) if len(old)==1 else None
            bundle=fetch(api,code,asof,calendar,original)
            bundles[code]=bundle
        except Exception:
            failed+=1
            if args.mode=='private':store.fail(code,'Upstream collection or schema validation failed; previous data retained')
    if args.mode=='pilot':
        result={'asof':asof,'run_id':os.environ.get('GITHUB_RUN_ID'),'bundles':bundles,'failed':failed,'elapsed_seconds':round(time.monotonic()-started,2),'requests':len(api.metrics),'rate_limit_errors':sum(m['code']=='EGW00201' for m in api.metrics)}
        serialized=json.dumps(result,ensure_ascii=False,allow_nan=False)
        assert all(s not in serialized for s in [api.key,api.secret,api.token])
        Path('technical').mkdir(exist_ok=True);Path('technical/pilot.json.gz').write_bytes(gzip.compress(serialized.encode(),mtime=0))
        print(json.dumps({'public_pilot_symbols':len(bundles),'bars':sum(len(b['bars']) for b in bundles.values()),'structure_valid':sum(b['structure_valid'] for b in bundles.values()),'signal_ready':sum(not b['validation'] for b in bundles.values()),'requests':len(api.metrics),'failed':failed}))
    else:print('Private worker finished; no identifiers or positions written to logs')
    if failed:raise SystemExit(1)

if __name__=='__main__':main()
