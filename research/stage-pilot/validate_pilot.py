"""Validate new observations without replacing old observations or historical signals."""
from pathlib import Path
import gzip,hashlib,json
import duckdb,pandas as pd
ROOT=Path(__file__).resolve().parent
OLD=Path('/workspace/research-price-recovery-20261019')
FIELDS={'open':'stck_oprc','high':'stck_hgpr','low':'stck_lwpr','close':'stck_clpr','volume':'acml_vol','turnover':'acml_tr_pbmn'}

def main():
    source=ROOT/'inputs/pilot-response';run=json.loads((source/'run.json').read_text())
    config=json.loads((ROOT/'pilot-config.json').read_text());targets={x['symbol']:x for x in config['targets']}
    assert run['complete'] and run['physical_requests']<=40 and run['elapsed_seconds']<=600
    assert len(run['metrics'])==run['physical_requests'] and set(run['records'])==set(targets)
    old=json.loads(gzip.decompress((OLD/'inputs/checkpoint-20261019.json.gz').read_bytes()))['records']
    calendar=pd.read_csv(OLD/'results/research-calendar.csv').trade_date
    expected=set(calendar[(calendar>=config['start'])&(calendar<=config['end'])])
    latest60=set(calendar[calendar<=config['end']].tail(60))
    with duckdb.connect('/workspace/offline-flow-reversal-20261019/input/dashboard.duckdb',read_only=True) as c:
        listing={code:str(date) for code,date in c.execute('select symbol,listing_date from instruments').fetchall()}
    rows=[];differences=[];prices=[];action_rows=[]
    for code,files in run['records'].items():
        versions={}
        for kind,e in files.items():
            raw=(source/e['file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==e['sha256']
            x=json.loads(raw);p=x['params']
            assert p['FID_INPUT_ISCD']==code and p['FID_COND_MRKT_DIV_CODE']=='J' and p['FID_INPUT_DATE_2']=='20261019'
            assert p['FID_ORG_ADJ_PRC']==('0' if kind=='adjusted' else '1')
            df=pd.DataFrame(x['output2']);df['date']=pd.to_datetime(df.stck_bsop_date).dt.strftime('%Y-%m-%d')
            assert not df.date.duplicated().any()
            dates=set(df.date);assert dates==expected and latest60<=dates
            assert df.date.min()>=listing[code] and df.date.max()=='2026-10-19'
            for field,col in FIELDS.items():df[field]=pd.to_numeric(df[col],errors='raise')
            assert (df[['open','high','low','close']]>0).all().all()
            assert (df.low<=df[['open','close']].min(axis=1)).all()
            assert (df.high>=df[['open','close']].max(axis=1)).all()
            assert (df[['volume','turnover']]>=0).all().all()
            df=df.set_index('date')
            versions[kind]=(df,x['observed_at'])
            for date,r in df.iterrows():
                prices.append({'symbol':code,'date':date,'kind':kind,'observed_at':x['observed_at'],**{k:r[k] for k in FIELDS}})
                if r.get('prtt_rate','0.00') not in ['0','0.0','0.00',''] or r.get('revl_issu_reas',''):
                    action_rows.append({'symbol':code,'date':date,'kind':kind,'prtt_rate':r.get('prtt_rate'),'flng_cls_code':r.get('flng_cls_code'),'revl_issu_reas':r.get('revl_issu_reas')})
        a,observed=versions['adjusted'];b,_=versions['raw'];assert set(a.index)==set(b.index)
        adjustment_differences=[d for d in a.index if any(a.loc[d,k]!=b.loc[d,k] for k in FIELDS)]
        oldrows={str(pd.Timestamp(v['stck_bsop_date']).date()):v for v in old[code]['adjusted_prices']}
        changed_dates=[]
        for d,v in oldrows.items():
            changed=[k for k,col in FIELDS.items() if a.loc[d,k]!=float(v[col])]
            if changed:
                changed_dates.append(d)
                differences.append({'symbol':code,'name':targets[code]['name'],'date':d,'fields':','.join(changed),
                    **{'old_'+k:float(v[col]) for k,col in FIELDS.items()},
                    **{'new_'+k:a.loc[d,k] for k in FIELDS}})
        rows.append({**targets[code],'adjusted_rows':len(a),'raw_rows':len(b),'first':a.index.min(),'last':a.index.max(),
                     'continuous_60':True,'duplicate_dates':0,'invalid_ohlcv':0,'prelisting_rows':0,
                     'zero_volume_rows':int((a.volume==0).sum()),'adjusted_raw_different_days':len(adjustment_differences),
                     'adjusted_raw_difference_first':min(adjustment_differences) if adjustment_differences else None,
                     'adjusted_raw_difference_last':max(adjustment_differences) if adjustment_differences else None,
                     'old_overlap_days':len(oldrows),'old_changed_days':len(changed_dates),'old_changed_dates':','.join(changed_dates),
                     'observed_at':observed,'status':'별도 후보·이전 관측 불일치' if changed_dates else '별도 후보·겹침 일치',
                     'provider_finality':'UNVERIFIED','historical_701_updated':False})
    out=ROOT/'results';f=pd.DataFrame(rows)
    f.to_csv(out/'pilot-validation.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(differences).to_csv(out/'pilot-observation-differences.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(action_rows).to_csv(out/'pilot-corporate-action-flags.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(prices).to_parquet(out/'pilot-prices.parquet',index=False)
    summary={'stocks':len(f),'physical_requests':run['physical_requests'],
             'authentication_requests':sum(m['kind']=='authentication' for m in run['metrics']),
             'market_requests':sum(m['kind']=='market' for m in run['metrics']),
             'retry_requests':sum(m['attempt']>1 for m in run['metrics']),
             'rate_limit_responses':sum(m['http']==429 or m['code']=='EGW00201' for m in run['metrics']),
             'elapsed_seconds':run['elapsed_seconds'],'median_market_response_seconds':pd.Series([m['seconds'] for m in run['metrics'] if m['kind']=='market']).median(),
             'adjusted_price_rows':int(f.adjusted_rows.sum()),'raw_price_rows':int(f.raw_rows.sum()),'date_continuity_60_pass':int(f.continuous_60.sum()),
             'stocks_changed_from_old_observation':int((f.old_changed_days>0).sum()),'stocks_with_adjustment_difference':int((f.adjusted_raw_different_days>0).sum()),
             'corporate_action_flag_stocks':len({r['symbol'] for r in action_rows}),'new_observations_not_asof19_knowledge':True,
             'operational_changes':0,'stage_cohort_changes':0,'new_collection_success_not_finality':True}
    (out/'pilot-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
