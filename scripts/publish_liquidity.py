"""Prepare date-specific turnover evidence in a coherent, recoverable publication.

Raw DB rows are immutable. A full validated KIS/KRX bundle can release ONLY an
identical original turnover/volume observation, never a different price series.
"""
import copy,datetime as dt,hashlib,json,shutil,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import duckdb,pandas as pd
from scripts import market_io as batch
from screener.store import load
from screener.liquidity_quality import daily_accepted,digest
from screener.market_validation import validate
from screener.liquidity_evidence import permits,price_identity

def build(snapshot=Path('snapshot'),root=Path('liquidity'),out=Path('recovery-publication')):
    source=snapshot/'dashboard.duckdb';old_hash=hashlib.sha256(source.read_bytes()).hexdigest();data=load(source)
    manifest=json.loads((snapshot/'manifest.json').read_text());assert manifest['sha256']==old_hash
    day=manifest['asof'];proofs=copy.deepcopy(data['metadata'].get('liquidity_price_evidence',{}));added=[]
    for date,rows in sorted(data['metadata'].get('adjusted_price_conflicts',{}).items()):
        for item in rows:
            code=item['symbol'];b=batch.read(root/'accepted'/date/(code+'.json.gz'))
            if not b or not daily_accepted(b) or validate(b,date):continue
            found=data['prices'][(data['prices'].symbol==code)&(data['prices'].trade_date==pd.Timestamp(date))]
            if len(found)!=1:continue
            row=found.iloc[0].to_dict()
            old=proofs.get(date,{}).get(code)
            if old and permits(row,old,day):continue
            proof={'bundle':b,'bundle_sha256':digest(b),'original_price_sha256':digest(price_identity(row)),
                   'scope':'same-date identical turnover and volume only; original closes preserved'}
            if not permits(row,proof,day):continue
            proofs.setdefault(date,{})[code]=proof;added.append({'asof':date,'symbol':code,'bundle_sha256':digest(b)})
    if not added:return {'added':[],'baseline_sha256':old_hash}
    out.mkdir(parents=True,exist_ok=True);dest=out/'dashboard.duckdb'
    (out/'manifest.json').unlink(missing_ok=True) # prior candidate is not a published generation
    shutil.copy2(source,dest)
    with duckdb.connect(str(dest)) as c:
        c.execute('insert or replace into metadata values (?,?)',['liquidity_price_evidence',json.dumps(proofs,allow_nan=False)])
        c.execute('CHECKPOINT')
    new=load(dest)
    for table in ['instruments','memberships','flows','prices','financials','benchmarks','calendar','exclusions']:
        pd.testing.assert_frame_equal(data[table],new[table])
    from screener.indicators import universe,stock_activity,sector_indicators
    def counts(d):
        x,m=universe(d,day);x=stock_activity(d,x,day);s,_=sector_indicators(d,x,m,day)
        return {'stocks':len(x),'stock_activity':int(x.activity_ratio.notna().sum()),'sectors':len(s),'sector_activity':int(s.activity_ratio.notna().sum())}
    new_hash=hashlib.sha256(dest.read_bytes()).hexdigest()
    audit={'baseline_sha256':old_hash,'sha256':new_hash,'added':added,'before':counts(data),'after':counts(new),'raw_tables_preserved':True,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    # Keep the original collection validation and all prior publication hashes.
    validation=json.loads((snapshot/'daily-validation.json').read_text());ledger=json.loads((snapshot/'publication-ledger.json').read_text())
    for target in [validation,manifest['daily_validation'],ledger[day]]:
        target.setdefault('recovery_revisions',[]).append({'previous_sha256':target['sha256'],'sha256':new_hash,'audit':audit})
        target['sha256']=new_hash
    ledger[day]['validation']=copy.deepcopy(manifest['daily_validation']);manifest.update(sha256=new_hash,recovery_validation=audit)
    for name,value in [('manifest.json',manifest),('daily-validation.json',validation),('publication-ledger.json',ledger),('recovery-validation.json',audit)]:
        (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    return audit

def install(snapshot,out,audit):
    if not audit['added']:return
    if hashlib.sha256((snapshot/'dashboard.duckdb').read_bytes()).hexdigest()!=audit['baseline_sha256']:raise ValueError('Publication base changed; rebuild required')
    archive=snapshot/'publication-history'/audit['baseline_sha256'];archive.mkdir(parents=True,exist_ok=True)
    for name in ['dashboard.duckdb','manifest.json','daily-validation.json','publication-ledger.json']:
        if not (archive/name).exists():shutil.copy2(snapshot/name,archive/name)
    # All files are staged in ONE git commit; app additionally rejects a mixed generation.
    for name in ['dashboard.duckdb','manifest.json','daily-validation.json','publication-ledger.json','recovery-validation.json']:shutil.copy2(out/name,snapshot/name)

if __name__=='__main__':
    snapshot=Path('snapshot');out=Path('recovery-publication');audit=build(snapshot,out=out)
    install(snapshot,out,audit)
    batch.write(batch.ROOT/'recovery-publication-report.json',{
        'run_id':__import__('os').environ.get('GITHUB_RUN_ID'),
        'prepared_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'audit':audit,'stock_activity_gain':audit.get('after',{}).get('stock_activity',0)-audit.get('before',{}).get('stock_activity',0),
        'sector_activity_gain':audit.get('after',{}).get('sector_activity',0)-audit.get('before',{}).get('sector_activity',0),
        'status':'PREPARED_NOT_YET_PUBLISHED'})
    print(json.dumps(audit,ensure_ascii=False))
