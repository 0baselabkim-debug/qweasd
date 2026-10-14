"""Validate daily observations in a disposable DB before an atomic Git snapshot commit."""
import datetime as dt
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
import duckdb
from .market_quality import quote_cap
from .store import load
from .scoring import evaluate

def integer(value):
    if not re.fullmatch(r'-?\d+',str(value)):raise ValueError('Missing or invalid integer observation')
    return int(value)

def validate_bundle(b):
    if b.get('schema')!=1 or b.get('exchange')!='KRX' or b.get('status')!='complete':raise ValueError('Incomplete daily bundle')
    day=dt.datetime.strptime(b['trade_date'],'%Y%m%d').date()
    if b.get('finality')!='UNVERIFIED':raise ValueError('Unproven finality claim')
    universe=b['universe'];u={r['symbol']:r for r in universe};records=b['records'];excluded=b['excluded']
    if len(u)!=len(universe) or not u:raise ValueError('Duplicate or empty universe')
    if set(records)&set(excluded) or set(records)|set(excluded)!=set(u) or b.get('failures'):raise ValueError('Unaccounted stock or collection failure')
    for code,row in u.items():
        if not re.fullmatch(r'\d{6}',code) or row['market'] not in ['KOSPI','KOSDAQ']:raise ValueError('Invalid instrument')
        if dt.datetime.strptime(row['listing_date'],'%Y%m%d').date()>day:raise ValueError('Future listing')
    if not b.get('master_sources'):raise ValueError('No observed official master')
    normalized=[]
    for code,r in records.items():
        f=r['flow'];q=r['quote'];instrument=u[code]
        if f['stck_bsop_date']!=b['trade_date'] or q['stck_shrn_iscd']!=code:raise ValueError('Source date/symbol mismatch')
        fields={k:integer(f.get(k)) for k in ['frgn_ntby_tr_pbmn','frgn_ntby_qty','frgn_shnu_vol','frgn_seln_vol','frgn_shnu_tr_pbmn','frgn_seln_tr_pbmn','stck_clpr','acml_vol','acml_tr_pbmn']}
        if fields['frgn_shnu_vol']-fields['frgn_seln_vol']!=fields['frgn_ntby_qty']:raise ValueError('Quantity identity mismatch')
        if any(fields[k]<0 for k in ['frgn_shnu_vol','frgn_seln_vol','frgn_shnu_tr_pbmn','frgn_seln_tr_pbmn']):raise ValueError('Negative gross flow')
        if abs(fields['frgn_shnu_tr_pbmn']-fields['frgn_seln_tr_pbmn']-fields['frgn_ntby_tr_pbmn'])>1:raise ValueError('Amount unit mismatch')
        if any(fields[k]<=0 for k in ['stck_clpr','acml_vol','acml_tr_pbmn']):raise ValueError('Non-trading stock in evaluated set')
        archived={'close':fields['stck_clpr'],'volume':fields['acml_vol']}
        cap,errors=quote_cap(q,archived,instrument['shares_thousands'],str(day),r['observed_at'])
        if errors:raise ValueError('Market-cap checks failed '+code+' '+','.join(errors))
        dates=[v['stck_bsop_date'] for v in r['adjusted_prices']]
        if len(dates)!=len(set(dates)) or b['trade_date'] not in dates:raise ValueError('Adjusted series duplicate/missing day')
        for v in r['adjusted_prices']:
            d=dt.datetime.strptime(v['stck_bsop_date'],'%Y%m%d').date()
            if d>day or not math.isfinite(float(v['stck_clpr'])) or float(v['stck_clpr'])<=0:raise ValueError('Adjusted series invalid date/value')
        latest=next(v for v in r['adjusted_prices'] if v['stck_bsop_date']==b['trade_date'])
        if integer(latest['stck_clpr'])!=fields['stck_clpr'] or integer(latest['acml_vol'])!=fields['acml_vol']:raise ValueError('Daily adjusted endpoint does not match close/volume')
        sector=instrument.get('sector') or q.get('bstp_kor_isnm') or b.get('kind_industries',{}).get(code) or '업종 미분류'
        normalized.append({'symbol':code,'day':day,'instrument':instrument,'record':r,'fields':fields,'cap':cap,'sector':sector})
    for code,e in excluded.items():
        if e['reason']=='official_delisting':
            field='kosdaq_mket_lstg_abol_dt' if u[code]['market']=='KOSDAQ' else 'scts_mket_lstg_abol_dt'
            date=e.get('fields',{}).get('lstg_abol_dt','')
            if re.fullmatch(r'\d{8}',date) and u[code]['listing_date']<=date<=b['trade_date'] and e['fields'].get(field)==date and e.get('market')==u[code]['market']:continue
            raise ValueError('Unverified delisting or relisted symbol '+code)
        if e['reason']=='official_master_halt' and u[code].get('halt')=='Y':continue
        if e['reason']=='dated_zero_volume' and e.get('evidence',{}).get('stck_bsop_date')==b['trade_date'] and integer(e['evidence'].get('acml_vol'))==0:continue
        raise ValueError('Unsupported exclusion '+code)
    for market in ['KOSPI','KOSDAQ']:
        rows=b['benchmarks'][market]['output2'];dates=[r['stck_bsop_date'] for r in rows]
        if len(dates)!=len(set(dates)) or b['trade_date'] not in dates:raise ValueError('Benchmark missing target/duplicate')
        for row in rows:
            value=float(row['bstp_nmix_prpr'])
            if not math.isfinite(value) or value<=0 or row['stck_bsop_date']>b['trade_date']:raise ValueError('Invalid benchmark')
    return day,normalized

def build_candidate(b,baseline,destination):
    day,rows=validate_bundle(b);baseline=Path(baseline);destination=Path(destination)
    if baseline.resolve()==destination.resolve():raise ValueError('Candidate must be separate from published DB')
    destination.parent.mkdir(parents=True,exist_ok=True);temp=destination.with_suffix('.building.duckdb')
    shutil.copy2(baseline,temp)
    audit={'trade_date':str(day),'records':len(rows),'excluded':len(b['excluded']),'adjusted_updates':0,'source_run':b.get('source_run'),
           'planned_start_at':b.get('planned_start_at'),'collection_started_at':b.get('started_at'),'collection_completed_at':b.get('completed_at'),
           'input_mode':b.get('input_mode','live_daily_collection')}
    try:
        with duckdb.connect(str(temp)) as c:
            previous=c.execute('select max(trade_date) from flows').fetchone()[0]
            if day<previous:raise ValueError('Refuse stale snapshot rollback')
            previous_count=c.execute('select count(*) from flows where trade_date=?',[previous]).fetchone()[0]
            if len(rows)<previous_count*.9:raise ValueError('Evaluated coverage dropped by more than 10%')
            c.execute('BEGIN')
            for r in rows:
                code=r['symbol'];i=r['instrument'];f=r['fields'];record=r['record'];sector=r['sector']
                old=c.execute('select net_amount,net_quantity from flows where symbol=? and trade_date=?',[code,day]).fetchone()
                values=(f['frgn_ntby_tr_pbmn']*1000000,f['frgn_ntby_qty'])
                if old and old!=values:raise ValueError('Existing flow revision requires review '+code)
                special=i.get('spac',False) or any(w in sector for w in ['금융','보험','증권','부동산','리츠']) or '리츠' in i['name']
                existing=c.execute('select market,special from instruments where symbol=?',[code]).fetchone()
                if existing and existing!=(i['market'],special):raise ValueError('Instrument classification change requires review '+code)
                prior_sector=c.execute('select sector from memberships where symbol=? and valid_from<? order by valid_from desc limit 1',[code,day]).fetchone()
                if sector=='업종 미분류' and prior_sector and prior_sector[0]!='업종 미분류':raise ValueError('Current official industry missing '+code)
                c.execute('insert or ignore into instruments values (?,?,?,?,?)',[code,i['name'],i['market'],dt.datetime.strptime(i['listing_date'],'%Y%m%d').date(),special])
                old_map=c.execute('select sector from memberships where symbol=? and valid_from<=? and (valid_to is null or valid_to>=?)',[code,day,day]).fetchall()
                if old_map and (len(old_map)!=1 or old_map[0][0]!=sector):raise ValueError('Conflicting dated industry '+code)
                if not old_map:c.execute('insert into memberships values (?,?,?,?,?,?)',[code,i['market'],sector,day,day,'Official KIS/KRX industry observed on target day; no backfill'])
                c.execute('insert or ignore into flows values (?,?,?,?,?,?,?)',[code,day,*values,'KRX','KIS FHPTJ04160001','UNVERIFIED'])
                old_price=c.execute('select close,volume,turnover from prices where symbol=? and trade_date=?',[code,day]).fetchone()
                if old_price and old_price!=(f['stck_clpr'],f['acml_vol'],f['acml_tr_pbmn']):raise ValueError('Existing price revision requires review '+code)
                c.execute('insert or ignore into prices values (?,?,?,?,?,?,?,?,?,?,?,?)',[code,day,f['stck_clpr'],None,f['acml_tr_pbmn'],f['acml_vol'],r['cap'],'KIS dated J daily flow + adjusted chart','KRX','KIS J hts_avls × 100 million KRW','CLOSE_VOLUME_DATED_MASTER_SHARES_MATCH',record['observed_at']])
                for v in record['adjusted_prices']:
                    d=dt.datetime.strptime(v['stck_bsop_date'],'%Y%m%d').date()
                    c.execute('update prices set adjusted_close=? where symbol=? and trade_date=?',[float(v['stck_clpr']),code,d]);audit['adjusted_updates']+=1
                c.execute('insert or ignore into calendar values (?,?)',[i['market'],day])
            for code,r in b['excluded'].items():
                c.execute('insert or ignore into exclusions values (?,?,?,?)',[code,r['name'],day,r['reason']])
                observations=r.get('adjusted_prices',[]);seen=set()
                for v in observations:
                    d=dt.datetime.strptime(v['stck_bsop_date'],'%Y%m%d').date();adj=float(v['stck_clpr'])
                    if d in seen or d>day or not math.isfinite(adj) or adj<=0:raise ValueError('Invalid excluded price series')
                    seen.add(d)
                    if d==day:
                        # Do not invent a raw close/cap for an excluded company.
                        c.execute('insert or ignore into prices(symbol,trade_date,adjusted_close,volume,turnover,source,exchange) values (?,?,?,?,?,?,?)',[code,day,adj,integer(v['acml_vol']),integer(v['acml_tr_pbmn']),'KIS J adjusted chart; excluded from stock evaluation','KRX'])
                    c.execute('update prices set adjusted_close=? where symbol=? and trade_date=?',[adj,code,d])
            for market,response in b['benchmarks'].items():
                for r in response['output2']:
                    d=dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date();value=float(r['bstp_nmix_prpr'])
                    old=c.execute('select close from benchmarks where market=? and trade_date=?',[market,d]).fetchone()
                    if old and old[0]!=value:raise ValueError('Historical benchmark revision requires review')
                    c.execute('insert or ignore into benchmarks values (?,?,?)',[market,d,value])
                    c.execute('insert or ignore into calendar values (?,?)',[market,d])
            c.execute('insert or replace into metadata values (?,?)',['daily_'+str(day),json.dumps(audit)])
            registry=c.execute("select value from metadata where key='verified_delistings'").fetchone()
            registry=json.loads(registry[0]) if registry else {}
            registry.update({code:r for code,r in b['excluded'].items() if r['reason']=='official_delisting'})
            c.execute('insert or replace into metadata values (?,?)',['verified_delistings',json.dumps(registry,ensure_ascii=False)])
            c.execute('insert or replace into metadata values (?,?)',['adjusted_price_provenance',json.dumps({'source':'KIS J FHKST03010100','parameter':'FID_ORG_ADJ_PRC=0','requested_end':str(day),'quote_observation_rule':'after close, before next calendar-day open','adjustment_vintage':'provider response at collection time; not a historical adjustment-vintage archive','run':b.get('source_run')})])
            c.execute('COMMIT');c.execute('CHECKPOINT')
        # Independent preservation comparison: no old flows, financials or unadjusted prices disappear/change.
        with duckdb.connect(str(temp),read_only=True) as c:
            escaped=str(baseline.resolve()).replace("'","''")
            c.execute(f"attach '{escaped}' as original (read_only)")
            for table,cols in [('flows','*'),('financials','*'),('memberships','*'),('instruments','*'),('exclusions','*'),('prices','symbol,trade_date,close,turnover,volume,market_cap,source,exchange,market_cap_source,market_cap_quality,market_cap_observed_at')]:
                n=c.execute(f'select count(*) from (select {cols} from original.{table} except select {cols} from main.{table})').fetchone()[0]
                if n:raise ValueError('Preservation check failed '+table)
        x,s,_=evaluate(load(temp),day)
        if len(x)!=len(rows) or int(x.net_amount.sum())!=sum(r['fields']['frgn_ntby_tr_pbmn']*1000000 for r in rows):raise ValueError('Evaluation lost input stocks')
        if int(s.net_amount.sum())!=int(x.net_amount.sum()):raise ValueError('Industry aggregate double counted')
        audit.update(complete_stock_scores=int(x.total_score.notna().sum()),complete_sector_scores=int(s.total_score.notna().sum()),full_20d_symbols=int((x.flow_observations>=20).sum()),preservation_verified=True)
        temp.replace(destination);audit['sha256']=hashlib.sha256(destination.read_bytes()).hexdigest();return audit
    except Exception:
        temp.unlink(missing_ok=True);raise

def candidate_manifest(candidate,previous,audit):
    d=load(candidate);result=dict(previous)
    result.update(asof=audit['trade_date'],flow_rows=len(d['flows']),financial_rows=len(d['financials']),price_rows=len(d['prices']),
                  sha256=audit['sha256'],current_day_flow_rows=audit['records'],automatic_snapshot_sync=True,
                  daily_run=audit['source_run'],daily_validation=audit,finality='UNVERIFIED',secrets_required=False)
    return result
