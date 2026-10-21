"""Actual manual trial and retained-source audit; no market HTTP allowed."""
from pathlib import Path
import collections,csv,gzip,hashlib,json,subprocess,tarfile,time
import pandas as pd
from forward_system import Store,load,sha,utcnow,generate,observe,compare_krx,FIELDS,KIS_FIELDS
import forward
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1];OUT=ROOT/'forward-study'

def write(name,x):(OUT/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def table(name,rows,columns=None):pd.DataFrame(rows,columns=columns).to_csv(OUT/name,index=False,encoding='utf-8-sig')
def decode(raw):return json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
def all_dates(x):
    dates=set()
    if isinstance(x,dict):
        if x.get('BAS_DD'):dates.add(x['BAS_DD'])
        for v in x.values():dates.update(all_dates(v))
    elif isinstance(x,list):
        for v in x:dates.update(all_dates(v))
    return dates

def references():
    source=load(OUT/'inputs/source.json');commit=source['main_commit'];inventory=[];found=[];seen=set()
    reference_cache=OUT/'inputs/reference-audit';reference_cache.mkdir(exist_ok=True)
    catalog=reference_cache/'catalog.json'
    if catalog.exists():
        inventory=load(catalog)
        for entry in inventory:
            raw=(reference_cache/entry['saved_file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==entry['sha256']
            x=decode(raw)
            if isinstance(x,dict) and x.get('asof')=='2026-10-16' and x.get('rows') and x.get('url','').startswith('https://data-dbg.krx.co.kr/'):
                found.append(x)
        table('reference-inventory.csv',inventory)
        return found,inventory
    def inspect(raw,name):
        h=hashlib.sha256(raw).hexdigest()
        if h in seen:return
        seen.add(h)
        try:x=decode(raw)
        except (ValueError,UnicodeDecodeError,gzip.BadGzipFile):return
        saved=h+('.json.gz' if raw[:2]==b'\x1f\x8b' else '.json')
        target=reference_cache/saved
        if not target.exists():target.write_bytes(raw)
        assert sha(target)==h
        dates=sorted(all_dates(x));inventory.append({'path':name,'sha256':h,'KRX_dates':','.join(dates),'saved_file':saved})
        if isinstance(x,dict) and x.get('asof')=='2026-10-16' and x.get('rows') and x.get('url','').startswith('https://data-dbg.krx.co.kr/'):
            found.append(x)
    paths=subprocess.check_output(['git','ls-tree','-r','--name-only',commit],cwd=REPO,text=True).splitlines()
    for p in paths:
        if any(s in p.lower() for s in ['reference','krx']) and p.endswith(('.json','.gz')):
            inspect(subprocess.check_output(['git','show',commit+':'+p],cwd=REPO),commit+':'+p)
    inv=load(REPO/'audit-archive/inventory.json')['files'];groups={}
    for x in inv:
        if any(s in x['path'].lower() for s in ['reference','krx']):groups.setdefault(x['archive'],{})[x['path']]=x
    for archive,members in groups.items():
        with tarfile.open(REPO/archive) as tf:
            for m in tf:
                if m.name not in members:continue
                raw=tf.extractfile(m).read();assert hashlib.sha256(raw).hexdigest()==members[m.name]['sha256'];inspect(raw,archive+':'+m.name)
    # Other preserved workspaces, explicitly excluding synthetic tests and packages.
    paths=subprocess.check_output(['rg','--files','/workspace','-g','!**/site-packages/**','-g','!**/tests/**','-g','!**/node_modules/**'],text=True).splitlines()
    for p in paths:
        if any(s in p.lower() for s in ['reference','krx']) and p.endswith(('.json','.gz')):
            inspect(Path(p).read_bytes(),p)
    table('reference-inventory.csv',inventory)
    catalog.write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n')
    return found,inventory

def main():
    started=time.monotonic();source=load(OUT/'inputs/source.json')
    if 'captured_at' not in source:
        source['captured_at']=utcnow().isoformat();write('inputs/source.json',source)
    refs,inventory=references();store=Store(OUT/'store');prior=pd.read_csv(ROOT/'entry-observation-audit.csv',dtype={'symbol':str}).fillna('')
    symbols=prior[prior.changed_fields!=''].symbol.tolist();assert len(symbols)==37
    original=load(ROOT/'inputs/checkpoint-20261016.json.gz');later=load(ROOT.parent/'full-market/inputs/checkpoint-20261020.json.gz')
    markets={r['symbol']:r['market'] for r in original['universe']};rows=[]
    for code in symbols:
        old=original['records'][code]['flow'];new=next(x for x in later['records'][code]['adjusted_prices'] if x['stck_bsop_date']=='20261016')
        observed=[]
        for label,value,basis,sourcefile,known in [('original_raw',old,'raw',ROOT/'inputs/checkpoint-20261016.json.gz',original['updated_at']),('later_adjusted',new,'adjusted',ROOT.parent/'full-market/inputs/checkpoint-20261020.json.gz',later['updated_at'])]:
            observation={'symbol':code,'market':markets[code],'trade_date':'2026-10-16','exchange':'KRX','basis':basis,
              'observed_at':known,'source_sha256':sha(sourcefile),'kind':label,**{k:float(value[f]) for k,f in zip(FIELDS,KIS_FIELDS)}}
            ref=next((r for r in refs if any(x.get('ISU_CD')==code for x in r['rows'])),None)
            assessment=observe(store,observation,ref);observed.append((observation,assessment))
        raw,assessment=observed[0];adj,_=observed[1]
        rows.append({'symbol':code,'name':next(r['name'] for r in original['universe'] if r['symbol']==code),'date':'2026-10-16',
          **{'original_'+k:raw[k] for k in FIELDS},**{'later_adjusted_'+k:adj[k] for k in FIELDS},
          'original_observed_at':raw['observed_at'],'later_observed_at':adj['observed_at'],
          'changed_fields':','.join(k for k in FIELDS if raw[k]!=adj[k]),'official_KRX_available':bool(ref),
          'raw_matches_KRX':assessment['comparison']['matched'] if ref else None,
          'status':'검증 보류','reason':';'.join(assessment['comparison']['reasons'])+';corporate_action_interval_unverified',
          'original_source_sha256':raw['source_sha256'],'later_source_sha256':adj['source_sha256']})
    table('krx-crosscheck-37.csv',rows)
    calendar=load(ROOT.parent/'full-market/collection/calendar.json')
    # There is no preserved next-session holiday response. Do not manufacture one.
    write('inputs/calendar.json',calendar)
    trial_start=time.monotonic();trial=generate(OUT/'inputs',OUT/'inputs/current-bundles',calendar,store)
    table('latest-screening.csv',trial['results']);trial_seconds=time.monotonic()-trial_start
    # Second execution checks identical decisions, not identical wall-clock timestamps.
    second=generate(OUT/'inputs',OUT/'inputs/current-bundles',calendar,store)
    assert trial['results']==second['results']
    assert len(store.records('signals'))==sum(r['status'] in ['frozen','already_frozen'] for r in trial['results'])
    reason_counts=collections.Counter(reason for r in trial['results'] for reason in r['reason'].split(';') if reason)
    table('performance.csv',[],columns=['signal_id','symbol','group','horizon','exit_policy','status','net_return','mae','mdd','market_excess'])
    result={'latest_asof':trial['publication']['asof'],'dataset_id':trial['publication']['dataset_id'],
       'main_read_only_commit':source['main_commit'],'reference_files_audited':len(inventory),'official_KRX_20261016_payloads':len(refs),
       'price_conflict_stocks':37,'price_conflict_resolved':sum(r['status']!='검증 보류' for r in rows),
       'normal_snapshot_stocks':len(trial['results']),'frozen_signals':len(store.records('signals')),
       'group_counts':{g:sum(r['group']==g for r in trial['results']) for g in 'ABCD'},'holds':sum(r['status']=='hold' for r in trial['results']),
       'hold_reasons':dict(reason_counts),'max_snapshot_price_sessions':max(r['snapshot_price_days'] for r in trial['results']),
       'observations_saved':len([x for x in store.records('observations') if x['kind']=='outcome']),
       'matching_repeated_trial':True,'trial_seconds':trial_seconds,'total_seconds':time.monotonic()-started,
       'completed_performance_rows':0,'new_market_API_calls':0,'new_schedules':0,'provider_finality_promoted':False}
    write('SUMMARY.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    import socket
    def denied(*a,**k):raise RuntimeError('No market/network requests permitted in research trial')
    socket.socket.connect=denied
    main()
