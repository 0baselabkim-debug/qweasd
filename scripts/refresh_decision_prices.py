"""Incremental, bounded priority evidence. No operating price replacements."""
import datetime as dt
import json
from scripts.collect_decision_prices import cached,cache,krx,kis_series,read,now
from scripts.kis_public_client import KIS,REQUEST_BUDGET

PRIORITY=('000720','285130','017670','018260','064400')

def plan(root,asof):
    index=read(root/'index.json')['objects'] if (root/'index.json').exists() else {}
    bootstrap=[];recent=[]
    for code in PRIORITY:
        bases=sorted({k.split(':')[1] for k in index if k.startswith('kis:') and k.split(':')[2]==code and k.split(':')[1]<=asof})
        full=[d for d in bases if all(cached(root,f'kis:{d}:{code}:{o}:1') for o in ('0','1'))]
        # Partial current bootstrap must resume, not get reclassified as recent.
        later_sessions=sum(k.startswith('krx:KOSPI:') and bool(full) and full[-1]<k.split(':')[-1]<=asof for k in index)
        if asof in bases or not full or later_sessions>=80:bootstrap.append(code)
        else:
            for option in ('0','1'):
                if not cached(root,f'recent:{asof}:{code}:{option}'):recent.append((code,option))
    return bootstrap,recent

def reusable(root,asof):
    """Skip calls when saved daily observations already prove a long suffix."""
    from pathlib import Path
    import duckdb
    from screener.decision_support.evidence import official_rows,date
    from screener.decision_support.history import candidate,continuation
    manifest=read(Path('snapshot/manifest.json'))
    inventory=read(Path('snapshot/decision-support/source-manifest.json'))
    current=[v for v in inventory['checkpoints'].values() if v['asof']==asof and str(v['source_run'])==str(manifest['daily_run'])]
    if len(current)!=1:return set()
    b=read(Path('snapshot/decision-support')/current[0]['file'])
    objects={k:[(r['payload_sha256'],obj) for r,obj in zip(read(root/'index.json')['objects'][k],cached(root,k))] for k in read(root/'index.json')['objects']}
    official,_,_=official_rows(list((root/'objects').glob('*.json.gz')),asof)
    healthy=set();instruments={x['symbol']:x for x in b['universe']}
    with duckdb.connect('snapshot/dashboard.duckdb',read_only=True) as c:
        for code in PRIORITY:
            if code not in b['records']:continue
            market=c.execute('select market from instruments where symbol=?',[code]).fetchone()[0]
            days=[str(x[0]) for x in c.execute('select trade_date from calendar where market=? and trade_date<=? order by trade_date',[market,asof]).fetchall()]
            listing=date(instruments[code]['listing_date'])
            h=candidate(objects,code,market,asof,listing,official,{},days,{})
            if h is None:h=continuation(objects,code,market,asof,listing,official,{},days,{},b['records'][code],current[0]['sha256'])
            if h and not h.get('error') and len(h['days'])>=121 and all(d in h['accepted'] for d in h['days'][-121:]):healthy.add(code)
    return healthy


def refresh(root,budget,asof):
    for market in ('KOSPI','KOSDAQ'):krx(asof,market,root,budget)
    bootstrap,recent=plan(root,asof)
    healthy=reusable(root,asof)
    bootstrap=[c for c in bootstrap if c not in healthy]
    recent=[(c,o) for c,o in recent if c not in healthy]
    pending_boot=any(not cached(root,f'kis:{asof}:{c}:{o}:1') for c in bootstrap for o in ('0','1'))
    pending_index=bool(bootstrap) and not cached(root,f'kis:{asof}:0001:index:2')
    if not recent and not pending_boot and not pending_index:return
    token=REQUEST_BUDGET.set(budget)
    try:
        api=KIS(interval=1.05);budget.finish('authenticated',http=200)
        if bootstrap:kis_series(root,budget,asof,symbols=bootstrap,index_codes=('0001',),api=api)
        for code,option in recent:
            endpoint='/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice'
            params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':(dt.date.fromisoformat(asof)-dt.timedelta(days=160)).strftime('%Y%m%d'),
                    'FID_INPUT_DATE_2':asof.replace('-',''),'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':option}
            response=api.request(endpoint,params,'FHKST03010100');budget.finish('received',http=200,symbol=code,option=option,params=params)
            if api.token and api.token in json.dumps(response):raise ValueError('token_echo')
            rows=response.get('output2',[]);dates=[x.get('stck_bsop_date','') for x in rows]
            if response.get('rt_cd')!='0' or response.get('output1',{}).get('stck_shrn_iscd')!=code:raise ValueError('recent_identity_mismatch')
            if not rows or len(dates)!=len(set(dates)) or max(dates)!=params['FID_INPUT_DATE_2'] or any(not params['FID_INPUT_DATE_1']<=d<=params['FID_INPUT_DATE_2'] for d in dates):raise ValueError('recent_date_mismatch')
            cache(root,f'recent:{asof}:{code}:{option}',{'endpoint':endpoint,'params':params,'response':response,'observed_at':now(),'asof':asof,'symbol':code})
    finally:
        if budget.state['requests'] and budget.state['requests'][-1]['status']=='attempted':budget.finish('KIS_failed_or_interrupted')
        REQUEST_BUDGET.reset(token)
