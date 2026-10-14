"""Build a reviewable snapshot from preserved public responses; no live API calls."""
import argparse,datetime as dt,gzip,hashlib,json,shutil,sys
from pathlib import Path
import duckdb
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.financials import parse_full_response,number
from screener.store import load
from screener.scoring import evaluate
from screener.themes import aggregate
from screener.market_quality import quote_cap

ROOT=Path(__file__).resolve().parents[1]
ASOF='2026-10-14'

def read(path):
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix=='.gz' else path.read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-dir',type=Path,required=True)
    ap.add_argument('--baseline',type=Path,default=ROOT/'snapshot/dashboard.duckdb')
    ap.add_argument('--output',type=Path,default=ROOT/'data/dashboard.duckdb');args=ap.parse_args()
    raw=args.source_dir;dest=args.output;dest.parent.mkdir(parents=True,exist_ok=True)
    if args.baseline.resolve()==dest.resolve():raise ValueError('Use a separate candidate DB')
    temp=dest.with_suffix('.building.duckdb');shutil.copy2(args.baseline,temp)
    audit={'asof':ASOF,'financial_errors':[],'financial_changes':[],'accounts':[], 'cap_rejected':[], 'mapping_changes':[], 'price_conflicts':[], 'prelisting_flow_rows_skipped':[]}
    reference=read(raw/'kis_reference.json');master={r['symbol']:r for r in reference['records']}
    if not all(s['matches_original_20261014'] for s in reference['sources']):raise ValueError('Original dated master mismatch')
    kind={r['symbol']:r for r in reference.get('kind',{}).get('records',[])}
    sample=read(raw/'kis_history_sample.json')
    quote_file=ROOT/'staging/kis_quotes_20261014.json.gz'
    quotes=read(quote_file)['records'] if quote_file.exists() else {r['symbol']:{'quote':r['quote'],'source':'probe '+sample['run_id']} for r in sample['records']}
    history_file=ROOT/'staging/kis_history_20261014.json.gz'
    histories=read(history_file)['records'].values() if history_file.exists() else sample['records']
    # The exact 19 preceding trading dates are observed in the 10-symbol control.
    previous_days=sorted({r['stck_bsop_date'] for s in sample['records'] for r in s['rows']},reverse=True)[:19]
    with duckdb.connect(str(temp)) as c:
        c.execute('BEGIN TRANSACTION')
        original=c.execute("SELECT * FROM flows WHERE trade_date=? ORDER BY symbol",[ASOF]).fetchall()
        instruments={r[0]:r[1:] for r in c.execute('SELECT symbol,name,market FROM instruments').fetchall()}
        listing_dates=dict(c.execute('SELECT symbol,listing_date FROM instruments').fetchall())
        for field in ['parent_net_income','parent_equity']:
            c.execute(f'ALTER TABLE financials ADD COLUMN IF NOT EXISTS {field} DOUBLE')
        for field in ['market_cap_source','market_cap_quality','market_cap_observed_at']:
            c.execute(f'ALTER TABLE prices ADD COLUMN IF NOT EXISTS {field} VARCHAR')
        financials=c.execute('SELECT * FROM financials').df().to_dict('records')
        if (raw/'financial_additions.json').exists():
            known={r['symbol'] for r in financials}
            financials.extend(r for r in read(raw/'financial_additions.json') if r['symbol'] not in known)
        for f in financials:
            p=raw/'dart_full'/f'{f["symbol"]}_{f["scope"]}_2025.json.gz'
            if not p.exists():audit['financial_errors'].append({'symbol':f['symbol'],'error':'response_missing'});continue
            try:accounts=parse_full_response(read(p),f['receipt'],f['scope'])
            except ValueError as e:audit['financial_errors'].append({'symbol':f['symbol'],'error':str(e)});continue
            for field,account in accounts.items():
                audit['accounts'].append({'symbol':f['symbol'],'scope':f['scope'],'receipt':f['receipt'],'field':field,**account})
                values=[(field,account['value'])]
                if field in ['revenue','operating_profit','equity']:values.append(('previous_'+field,account['previous']))
                for column,value in values:
                    if value is None:continue
                    old=f.get(column)
                    if pd.notna(old) and old!=value:
                        audit['financial_changes'].append({'symbol':f['symbol'],'field':column,'before':old,'after':value,'scope':f['scope'],'receipt':f['receipt']})
                    f[column]=value
            f['source']='OpenDART full accounts / reviewed original filing; receipt/scope/currency matched'
        financial_frame=pd.DataFrame(financials)[[r[0] for r in c.execute('DESCRIBE financials').fetchall()]]
        for col in ['period_end','disclosed_on']:financial_frame[col]=pd.to_datetime(financial_frame[col])
        c.register('_financials',financial_frame)
        c.execute('INSERT OR REPLACE INTO financials SELECT * FROM _financials');c.unregister('_financials')
        prices={(r['symbol'],r['trade_date'].date()):r for r in c.execute('SELECT * FROM prices').df().to_dict('records')}
        calendar=set()
        # Historical prices are date-keyed KRX official responses. No forward filling.
        for p in sorted(raw.glob('krx_*.json')):
            if not (p.name.startswith('krx_KOSPI_') or p.name.startswith('krx_KOSDAQ_')):continue
            market=p.name.split('_')[1];date=p.stem.split('_')[-1]
            if len(date)!=8:continue
            for r in read(p).get('OutBlock_1',[]):
                code=r.get('ISU_CD')
                if code not in instruments or r.get('BAS_DD')!=date:continue
                day=dt.datetime.strptime(date,'%Y%m%d').date()
                if day>dt.date.fromisoformat(ASOF):continue
                close,volume,turnover,cap=(number(r.get(k)) for k in ['TDD_CLSPRC','ACC_TRDVOL','ACC_TRDVAL','MKTCAP'])
                if any(v is None for v in [close,volume,turnover,cap]):continue
                prior=prices.get((code,day));old=(prior['close'],prior['volume'],prior['turnover']) if prior else None
                if old and tuple(old)!=(close,volume,turnover):
                    audit['price_conflicts'].append({'symbol':code,'date':str(day),'old':old,'official':[close,volume,turnover]});continue
                if prior:
                    prior.update(market_cap=cap,market_cap_source='KRX official daily',market_cap_quality='EXPLICIT_DATE')
                else:
                    prices[(code,day)]={'symbol':code,'trade_date':day,'close':close,'adjusted_close':None,'turnover':turnover,'volume':volume,'market_cap':cap,'source':'KRX 공식','exchange':'KRX','market_cap_source':'KRX official daily','market_cap_quality':'EXPLICIT_DATE','market_cap_observed_at':None}
                calendar.add((market,day))
        # Official quote is accepted only against the archived KRX close/volume and dated master.
        for code,record in quotes.items():
            if code not in instruments:continue
            q=record.get('quote',{});r=prices.get((code,dt.date.fromisoformat(ASOF)))
            if not r:continue
            observed=record.get('received_at_utc')
            if not observed and code in {s['symbol'] for s in sample['records']}:observed=sample.get('observed_at_upper_bound_utc')
            cap,failures=quote_cap(q,r,master.get(code,{}).get('listed_shares_raw'),ASOF,observed)
            if failures:audit['cap_rejected'].append({'symbol':code,'reasons':failures});continue
            if pd.isna(r['market_cap']):
                r.update(market_cap=cap,market_cap_source='KIS J inquire-price hts_avls (100 million KRW)',market_cap_quality='LAST_CLOSE_VOLUME_AND_DATED_SHARES_MATCH; received before next-day open; quote itself has no trade_date',market_cap_observed_at=observed)
        price_columns=[r[0] for r in c.execute('DESCRIBE prices').fetchall()]
        c.register('_prices',pd.DataFrame(prices.values())[price_columns]);c.execute('INSERT OR REPLACE INTO prices SELECT * FROM _prices');c.unregister('_prices')
        # Preserve every existing nonempty official industry; enrich only unknown mappings.
        unmapped=c.execute("SELECT symbol,market FROM memberships WHERE sector='업종 미분류' AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?)",[ASOF,ASOF]).fetchall()
        for code,market in unmapped:
            q=quotes.get(code,{}).get('quote',{});name=q.get('bstp_kor_isnm','').strip();source='KIS official J quotation industry; observed current classification'
            if not name:
                entry=kind.get(code,{})
                if entry.get('industry'):
                    name='표준산업 · '+entry['industry'];source='KRX KIND official listed-company industry; separate taxonomy'
            if name:
                c.execute('UPDATE memberships SET sector=?,source=? WHERE symbol=? AND valid_from=?',[name,source,code,ASOF])
                audit['mapping_changes'].append({'symbol':code,'sector':name,'source':source})
                if any(word in name for word in ['금융','보험','증권','부동산','리츠']):c.execute('UPDATE instruments SET special=true WHERE symbol=?',[code])
        # Append only new historical flow keys. Original 2026-10-14 values are immutable.
        prior_flows={(r[0],r[1]):(r[2],r[3]) for r in c.execute('SELECT symbol,trade_date,net_amount,net_quantity FROM flows').fetchall()}
        new_flows=[]
        for record in histories:
            code=record['symbol']
            if code not in instruments:continue
            for r in record.get('rows',[]):
                date=str(r['stck_bsop_date'])
                if date not in previous_days:continue
                amount,qty=number(r.get('frgn_ntby_tr_pbmn')),number(r.get('frgn_ntby_qty'))
                if amount is None or qty is None:raise ValueError('Invalid historic amount or quantity')
                day=dt.datetime.strptime(date,'%Y%m%d').date()
                if listing_dates.get(code) and day<listing_dates[code]:
                    audit['prelisting_flow_rows_skipped'].append({'symbol':code,'date':str(day)});continue
                existing=prior_flows.get((code,day))
                if existing and existing!=(amount*1000000,qty):raise ValueError('Historic flow revision requires review')
                if not existing:new_flows.append([code,day,amount*1000000,qty,'KRX','KIS FHPTJ04160001','UNVERIFIED'])
                prior_flows[(code,day)]=(amount*1000000,qty)
                calendar.add((instruments[code][1],day))
        if new_flows:
            c.register('_flows',pd.DataFrame(new_flows,columns=[r[0] for r in c.execute('DESCRIBE flows').fetchall()]))
            c.execute('INSERT OR IGNORE INTO flows SELECT * FROM _flows');c.unregister('_flows')
        c.executemany('INSERT OR IGNORE INTO calendar VALUES (?,?)',sorted(calendar))
        assert original==c.execute('SELECT * FROM flows WHERE trade_date=? ORDER BY symbol',[ASOF]).fetchall()
        audit['original_2430_flow_rows_unchanged']=True
        audit['history_days_requested']=previous_days
        info={'version':1,'baseline_date':ASOF,'scope':'KRX','financial_basis':'2025 annual / 2024 comparison; same receipt and CFS/OFS scope; KRW',
              'valuation_basis':'CFS owners-of-parent net income/equity; OFS own net income/equity; not TTM or provider PER',
              'roe_basis':'annual total net income / mean opening and closing total equity',
              'historical_flow_mode':'full' if history_file.exists() else '10-symbol verified sample; user-approved full collection pending completion',
              'sector_mapping_policy':'Existing industries retained; unknowns use KIS industry or separately labelled KRX KIND taxonomy; never backdated before 2026-10-14',
              'cap_policy':'KRX explicit-date preferred. KIS latest quote only with exact archived KRX close/volume and same dated-master share-thousands plus cap unit check; KIS quote lacks explicit trade date.',
              'sources':{'kis_sample_run':sample['run_id'],'reference_master':reference['sources']},'built_at_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
        c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',['enrichment',json.dumps(info,ensure_ascii=False)])
        c.execute('COMMIT')
        c.execute('CHECKPOINT')
    temp.replace(dest)
    data=load(dest);stocks,sectors,meta=evaluate(data,ASOF);themes,_,theme_stats=aggregate(stocks,ASOF)
    metrics=['revenue','operating_profit','net_income','equity','liabilities','ocf','market_cap','revenue_growth','op_growth','roe','debt_ratio','per','pbr','flow_intensity','flow_persistence','activity_ratio']
    audit['metrics']={k:{'count':int(stocks[k].notna().sum()),'denominator':len(stocks),'percent':round(100*stocks[k].notna().sum()/len(stocks),2)} for k in metrics}
    audit['unmapped_before']=len(unmapped);audit['unmapped_after']=int((stocks.sector=='업종 미분류').sum())
    audit['complete_20d_symbols']=int((stocks.flow_observations>=20).sum())
    audit['stock_components']={k:int(stocks['score_'+k].notna().sum()) for k in meta['config']['stock_weights']}
    audit['sector_components']={k:int(sectors['score_'+k].notna().sum()) for k in meta['config']['sector_weights']}
    audit['complete_stock_scores']=int(stocks.total_score.notna().sum());audit['complete_sector_scores']=int(sectors.total_score.notna().sum())
    audit['turnaround_symbols']=int(stocks.turnaround.sum())
    audit['financial_hold_reasons']=stocks.financial_hold.replace('','기초 재무 조건 충족').value_counts().to_dict()
    audit['themes']=themes[['sector','members']].to_dict('records');audit['theme_overlap']=theme_stats
    report=ROOT/'data/enrichment-audit.json';report.write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in audit.items() if k not in ['accounts','financial_changes','mapping_changes','price_conflicts']},ensure_ascii=False,indent=2,default=str))

if __name__=='__main__':main()
