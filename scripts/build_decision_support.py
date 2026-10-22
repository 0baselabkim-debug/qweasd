"""Build/verify the optional analytical sidecar. No market API or credentials.

Failure changes only status.json; prior index/data remain intact. Readers MUST
compare source_snapshot_sha256 with the current normal manifest before use.
"""
import argparse
import collections
import datetime as dt
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import duckdb
import pandas as pd
from screener.publication import coherent
from screener.decision_support.evidence import (encoded,sha,read,integer,date,timestamp,
    institution,official_rows,accept_chart,KIS_FIELDS,bar)
from screener.decision_support.indicators import flow_metrics,technical,observations,metric
from screener.decision_support.history import load_objects, candidate, continuation
from scripts.briefing_raw import reference
from scripts.briefing_schema import sector_id

ROOT=Path(__file__).resolve().parents[1]
SCHEMA_VERSION=1


def put(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_bytes()!=raw:raise ValueError('immutable_evidence_changed')
    if not path.exists():path.write_bytes(raw)


def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT).decode().strip()


def source_inventory(previous, out, snapshot, fetch):
    """Only read existing published checkpoint branches, never dispatch collectors."""
    inventory=read(previous/'source-manifest.json') if (previous/'source-manifest.json').exists() else {'checkpoints':{},'references':{}}
    for section in inventory.values():
        for item in section.values():
            p=previous/item['file']
            if sha(p.read_bytes())!=item['sha256']:raise ValueError('previous_evidence_hash_mismatch')
            put(out/item['file'],p.read_bytes())
    manifest=read(snapshot/'manifest.json');asof=manifest['asof']
    if fetch:
        listing=git('ls-remote','--heads','origin','refs/heads/data/daily-*').splitlines()
        for line in listing:
            commit,ref=line.split();compact=ref.rsplit('-',1)[-1]
            if not compact.isdigit() or len(compact)!=8 or compact>asof.replace('-',''):continue
            # Existing daily branches begin in Oct2026. No historical recollection.
            if any(v.get('git_commit')==commit for v in inventory['checkpoints'].values()):continue
            subprocess.run(['git','fetch','origin',ref],cwd=ROOT,check=True,capture_output=True)
            if git('rev-parse','FETCH_HEAD')!=commit:raise ValueError('checkpoint_branch_changed_during_read')
            raw=subprocess.check_output(['git','show',commit+':daily-results/bundle.json.gz'],cwd=ROOT)
            b=json.loads(gzip.decompress(raw))
            if b.get('trade_date')!=compact or b.get('exchange')!='KRX':raise ValueError('checkpoint_identity_mismatch')
            if b.get('status')!='complete' or b.get('failures'):continue
            if compact==asof.replace('-','') and str(b.get('source_run'))!=str(manifest['daily_run']):raise ValueError('checkpoint_publication_run_mismatch')
            h=sha(raw);rel='evidence/checkpoints/'+h+'.json.gz';put(out/rel,raw)
            inventory['checkpoints'][h]={'file':rel,'sha256':h,'git_commit':commit,'git_ref':ref,
                'asof':date(compact),'observed_at':b.get('completed_at') or b.get('updated_at'),
                'source_run':str(b.get('source_run'))}
    inventory.setdefault('auxiliary',{})
    # Only preserved auxiliary responses are fetched. This exporter has no
    # market credentials and never starts a market collector.
    if fetch:
        listing=git('ls-remote','--heads','origin','refs/heads/evidence/decision-price-source')
        if listing:
            commit,ref=listing.split()
            subprocess.run(['git','fetch','origin',ref],cwd=ROOT,check=True,capture_output=True)
            if git('rev-parse','FETCH_HEAD')!=commit:raise ValueError('auxiliary_branch_changed_during_read')
            raw=subprocess.check_output(['git','show',commit+':auxiliary-prices/index.json'],cwd=ROOT)
            auxiliary=json.loads(raw)
            for key,versions in auxiliary['objects'].items():
                for v in versions:
                    h=v['sha256']
                    if h in inventory['auxiliary']:continue
                    source='auxiliary-prices/'+v['file']
                    if '..' in Path(source).parts:raise ValueError('invalid_auxiliary_source_path')
                    raw=subprocess.check_output(['git','show',commit+':'+source],cwd=ROOT)
                    if sha(raw)!=h:raise ValueError('auxiliary_source_hash_mismatch')
                    obj=json.loads(gzip.decompress(raw))
                    if sha(encoded(obj))!=v['payload_sha256']:raise ValueError('auxiliary_payload_hash_mismatch')
                    if obj.get('asof','9999')>asof:continue
                    rel='evidence/auxiliary/'+h+'.json.gz';put(out/rel,raw)
                    inventory['auxiliary'][h]={'file':rel,'sha256':h,'key':key,'git_commit':commit,
                        'asof':obj['asof'],'observed_at':obj['observed_at']}
                    if key.startswith('krx:'):
                        inventory['references'][h]={'file':rel,'sha256':h,'source_file':source,'git_commit':commit,
                            'asof':obj['asof'],'observed_at':obj['observed_at'],'url':obj['url']}
    # Official reference observations are already collected by the operating system.
    paths=list((ROOT/'liquidity/reference').rglob('*.json.gz'))+list((ROOT/'liquidity/reference-observations').rglob('*.json.gz'))+list((ROOT/'auxiliary-reference-imports').glob('*.json.gz'))
    for p in paths:
        raw=p.read_bytes();h=sha(raw)
        if h in inventory['references']:continue
        b=read(p)
        if not isinstance(b,dict) or not b.get('asof') or b['asof']>asof:continue
        rel='evidence/references/'+h+'.json.gz';put(out/rel,raw)
        inventory['references'][h]={'file':rel,'sha256':h,'source_file':str(p.relative_to(ROOT)),
            'asof':b['asof'],'observed_at':b.get('observed_at'),'url':b.get('url')}
    (out/'source-manifest.json').write_bytes(encoded(inventory))
    return inventory


def financial_data(snapshot,asof):
    annual=collections.defaultdict(list);disclosures=collections.defaultdict(list);quarter=collections.defaultdict(list)
    with duckdb.connect(str(snapshot/'dashboard.duckdb'),read_only=True) as c:
        frame=c.execute('select * from financials where disclosed_on<=? and period_end<=? and disclosure_verified=true order by period_end desc,disclosed_on desc,receipt desc',[asof,asof]).df()
    # JSON numeric nulls are explicit; annual and YTD/quarter remain separate.
    for r in json.loads(frame.to_json(orient='records',date_format='iso')):
        annual[r['symbol']].append(r)
    digest=None;state_file=snapshot/'corporate-state.json.gz'
    if state_file.exists():
        digest=sha(state_file.read_bytes());cm=read(snapshot/'corporate-manifest.json')
        if digest!=cm['state_sha256']:raise ValueError('corporate_source_hash_mismatch')
        state=read(state_file)
        for r in state.get('financials',{}).values():
            if r.get('status')=='verified_period' and r['disclosed_on']<=asof and r['period_end']<=asof:
                quarter[r['symbol']].append({k:r.get(k) for k in ('symbol','receipt','disclosed_on','period_end','scope','basis','form','currency','values','prior_values','standalone_quarter','source','response_sha256','url')})
        for r in state.get('filings',{}).values():
            if r['disclosed_on']<=asof:disclosures[r['symbol']].append(r)
    for group in (quarter,disclosures):
        for code in group:group[code].sort(key=lambda r:(r['disclosed_on'],r.get('receipt','')),reverse=True)
    return annual,quarter,disclosures,digest


def flow_maps(c,asof):
    vals={};quantities={}
    for code,day,amount,qty in c.execute("select symbol,trade_date,net_amount,net_quantity from flows where trade_date<=? and exchange='KRX'",[asof]).fetchall():
        key=(code,str(day))
        if key in vals:raise ValueError('duplicate_foreign_observation')
        vals[key]=amount;quantities[key]=qty
    return vals,quantities


def build(snapshot=Path('snapshot'), previous=Path('snapshot/decision-support'), out=Path('decision-build'), fetch=False):
    start=time.monotonic();snapshot=Path(snapshot);previous=Path(previous);out=Path(out)
    if out.resolve()==previous.resolve() or out.resolve()==snapshot.resolve():raise ValueError('separate_candidate_required')
    if out.exists() and any(out.iterdir()):raise ValueError('candidate_must_be_empty')
    out.mkdir(parents=True,exist_ok=True)
    if not coherent(snapshot):raise ValueError('normal_publication_incoherent')
    manifest=read(snapshot/'manifest.json');asof=manifest['asof'];source_hash=manifest['sha256']
    if not manifest['daily_validation'].get('preservation_verified'):raise ValueError('normal_validation_missing')
    v3=read(snapshot/'briefing/index.json')
    if v3['asof']!=asof or v3['source_snapshot_sha256']!=source_hash:raise ValueError('v3_generation_mismatch')
    protected={p:sha((snapshot/p).read_bytes()) for p in ('dashboard.duckdb','manifest.json','daily_briefing.json','briefing/index.json')}
    inventory=source_inventory(previous,out,snapshot,fetch)
    current=[x for x in inventory['checkpoints'].values() if x['asof']==asof and str(x['source_run'])==str(manifest['daily_run'])]
    if not current:raise ValueError('published_day_original_checkpoint_missing')
    if len(current)!=1:raise ValueError('published_day_checkpoint_versions_ambiguous')
    latest=read(out/current[0]['file'])
    stocks,_,groups,coverage=reference(snapshot/'dashboard.duckdb',asof)
    if set(latest['records'])!=set(stocks):raise ValueError('checkpoint_universe_differs_from_publication')
    instruments={r['symbol']:r for r in latest['universe']}
    with duckdb.connect(str(snapshot/'dashboard.duckdb'),read_only=True) as c:
        calendars={m:[str(r[0]) for r in c.execute('select trade_date from calendar where market=? and trade_date<=? order by trade_date',[m,asof]).fetchall()] for m in ('KOSPI','KOSDAQ')}
        fvalues,fquantities=flow_maps(c,asof)
        db_prices={(s,str(d)):(cl,vol,turn) for s,d,cl,vol,turn in c.execute("select symbol,trade_date,close,volume,turnover from prices where exchange='KRX' and trade_date<=?",[asof]).fetchall()}
        benchmarks={m:{str(d):v for d,v in c.execute('select trade_date,close from benchmarks where market=? and trade_date<=?',[m,asof]).fetchall()} for m in calendars}
    if any(not ds or ds[-1]!=asof or len(ds)!=len(set(ds)) for ds in calendars.values()):raise ValueError('calendar_identity_mismatch')
    refs,ref_sources,ref_errors=official_rows([out/x['file'] for x in inventory['references'].values()],asof)
    aux_objects=load_objects(out,inventory)
    inst_versions=collections.defaultdict(list);inst_rows=[];obs_rows=[];errors=[]
    # No endpoint-specific official amount multiplier was obtained. Do not
    # silently borrow the foreign amount unit for institutional amount fields.
    amount_multiplier=None
    for source_id,src in sorted(inventory['checkpoints'].items()):
        b=read(out/src['file']);day=src['asof']
        if day>asof:continue
        for code,r in b['records'].items():
            if code not in stocks:continue
            market=stocks[code]['market'];scope=r.get('request_scope')
            if scope!={'market':'J','symbol':code,'end':b['trade_date'],'adjustment':'0'}:
                errors.append({'symbol':code,'asof':day,'reason':'saved_request_scope_missing','source_id':source_id});continue
            extra=r.get('analysis_source',{});rows=extra.get('flow_rows',[r['flow']])
            if extra and (extra.get('flow_request')!={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_1':b['trade_date'],'FID_ORG_ADJ_PRC':'','FID_ETC_CLS_CODE':''} or extra.get('flow_response_code')!='0'):
                errors.append({'symbol':code,'asof':day,'reason':'flow_request_provenance_invalid','source_id':source_id});continue
            seen=set();duplicates={x.get('stck_bsop_date') for x in rows if sum(y.get('stck_bsop_date')==x.get('stck_bsop_date') for y in rows)>1}
            for raw in rows:
                reasons=[]
                try:
                    d=date(raw['stck_bsop_date']);timestamp(extra.get('flow_observed_at',r['observed_at']))
                    if d>day or d not in calendars[market] or raw['stck_bsop_date'] in duplicates:raise ValueError('flow_date_duplicate_or_calendar_mismatch')
                    if (code,d) not in fvalues:raise ValueError('foreign_source_missing_for_same_generation')
                    if integer(raw['frgn_ntby_tr_pbmn'])*1000000!=fvalues[code,d] or integer(raw['frgn_ntby_qty'])!=fquantities[code,d]:raise ValueError('foreign_original_conflict')
                    vals=institution(raw,amount_multiplier)
                except (ValueError,KeyError) as e:
                    errors.append({'symbol':code,'asof':raw.get('stck_bsop_date'),'source_id':source_id,'reason':str(e) if isinstance(e,ValueError) else 'missing_flow_field'});continue
                identity=sha(encoded(raw));item={'symbol':code,'trade_date':d,'market':market,'exchange':'KRX','source_id':source_id,'raw_row_sha256':identity,'observed_at':extra.get('flow_observed_at',r['observed_at']),**vals}
                inst_versions[code,d].append(item)
                inst_rows.append({**item,'reasons':json.dumps(item['reasons']),'subgroups':json.dumps(item['subgroups'])})
    imaps=collections.defaultdict(dict);iqmaps=collections.defaultdict(dict);rawmaps=collections.defaultdict(dict)
    for (code,day),variants in inst_versions.items():
        values={(r['net_quantity'],r['net_amount_provider_units']) for r in variants}
        if len(values)!=1:
            errors.append({'symbol':code,'asof':day,'reason':'institution_observation_conflict'});continue
        q,a=next(iter(values));iqmaps[code][day]=q;rawmaps[code][day]=a
        imaps[code][day]=variants[0]['net_amount_krw']
    annual,financials,filings,corp_hash=financial_data(snapshot,asof)
    rows=[];tables=collections.defaultdict(list);counts=collections.Counter()
    institution_proof=sha((ROOT/'reports/decision-support/official/chk_investor_trade_by_stock_daily.py.txt').read_bytes())
    for code,stock in sorted(stocks.items()):
        r=latest['records'][code];market=stock['market'];days=calendars[market]
        rawbars=r['adjusted_prices'];seen=collections.Counter(x.get('stck_bsop_date') for x in rawbars)
        accepted={};series=[];listing=date(instruments[code]['listing_date'])
        observed=r.get('analysis_source',{}).get('chart_observed_at',r['observed_at'])
        if r.get('request_scope')!={'market':'J','symbol':code,'end':latest['trade_date'],'adjustment':'0'} or r.get('chart_header',{}).get('stck_shrn_iscd')!=code:raise ValueError('latest_chart_identity_mismatch')
        analysis=r.get('analysis_source')
        if analysis:
            request=analysis.get('chart_request',{})
            expected={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,'FID_INPUT_DATE_2':latest['trade_date'],'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0'}
            if any(request.get(k)!=v for k,v in expected.items()) or analysis.get('chart_response_code')!='0':raise ValueError('chart_request_provenance_invalid')
        # Keep one complete response vintage per stock; do not splice closes.
        for raw in sorted(rawbars,key=lambda x:x.get('stck_bsop_date','')):
            try:d=date(raw['stck_bsop_date'])
            except (ValueError,KeyError):errors.append({'symbol':code,'reason':'invalid_chart_date'});continue
            if d>asof or d not in days or seen[raw['stck_bsop_date']]!=1:
                errors.append({'symbol':code,'asof':d,'reason':'chart_calendar_duplicate_or_future'});continue
            qualified=accept_chart(raw,code,d,market,observed,refs,listing)
            # Exact raw close/volume/turnover comparisons are diagnostic, never
            # used to insert a different source's close into this six-field bar.
            same= db_prices.get((code,d))==tuple(qualified['value'][k] for k in ('close','volume','turnover')) if qualified['value'] else False
            if qualified['value'] and d==asof:
                try:
                    if tuple(integer(r['flow'][KIS_FIELDS[k]]) for k in ('close','volume','turnover'))!=tuple(qualified['value'][k] for k in ('close','volume','turnover')):
                        qualified['reasons'].append('current_chart_flow_conflict');qualified['accepted']=None;qualified['status']='hold'
                except (KeyError,ValueError):qualified['reasons'].append('current_flow_price_missing');qualified['accepted']=None;qualified['status']='hold'
            if not same:
                qualified['reasons'].append('operating_snapshot_price_version_conflict');qualified['accepted']=None;qualified['status']='hold'
            if qualified['accepted']:accepted[d]=qualified['accepted']
            payload={'symbol':code,'trade_date':d,'market':market,'exchange':'KRX','basis':'KIS_adjusted_option_0',
                     'observed_at':observed,'source_id':current[0]['sha256'],'raw_row_sha256':sha(encoded(raw)),
                     'validation_status':qualified['status'],'provider_finality':'UNVERIFIED',
                     'reasons':qualified['reasons'],'official_source_ids':qualified['official_source_ids'],
                     'adjustment_fields':{k:raw.get(k) for k in ('flng_cls_code','prtt_rate','mod_yn','revl_issu_reas')},
                     **(qualified['value'] or {k:None for k in KIS_FIELDS})}
            series.append(payload);obs_rows.append({**payload,'reasons':json.dumps(payload['reasons']),
                'official_source_ids':json.dumps(payload['official_source_ids']),'adjustment_fields':json.dumps(payload['adjustment_fields'])})
        # A newly qualified whole vintage can replace the analytical input,
        # never the original operating DB or its financial/flow history.
        history=candidate(aux_objects,code,market,asof,listing,refs,db_prices,days,benchmarks[market])
        if history is None:
            history=continuation(aux_objects,code,market,asof,listing,refs,db_prices,days,benchmarks[market],r,current[0]['sha256'])
        price_days=days;price_benchmarks=benchmarks[market];history_selection=None
        if history and history.get('error'):
            errors.append({'symbol':code,'asof':asof,'reason':history['error']})
        elif history and asof in history['accepted'] and len(history['accepted'])>=len(accepted):
            accepted=history['accepted'];series=history['series'];price_days=history['days'];price_benchmarks=history['benchmarks']
            observed=history['observed_at']
            history_selection={'source_ids':history['source_ids'],'operating_version_comparison':history['operating_version_comparison'],
                'validated_continuation':history.get('link_proof'),
                'basis':'both KIS bases + official KRX exact six fields; operating originals preserved',
                'calendar':'preserved official market index sessions, exact operating calendar overlap'}
            obs_rows[:]=[x for x in obs_rows if x['symbol']!=code]
            obs_rows.extend({**x,'reasons':json.dumps(x['reasons']),'official_source_ids':json.dumps(x['official_source_ids']),
                'adjustment_fields':json.dumps(x['adjustment_fields'])} for x in series)
        f=flow_metrics(days,{d:fvalues.get((code,d)) for d in days},{d:fquantities.get((code,d)) for d in days})
        inst=flow_metrics(days,imaps[code],iqmaps[code])
        for k in inst:
            if k.startswith('net_quantity'):continue
            inst[k]=metric(None,'institution_amount_unit_not_officially_confirmed')
        px=technical(price_days,accepted,price_benchmarks);extra=observations(f,px)
        foreign_series=[{'trade_date':d,'net_amount_krw':fvalues.get((code,d)),'net_quantity_shares':fquantities.get((code,d)),
            'institution_net_quantity_shares':iqmaps[code].get(d),'institution_net_amount_krw':imaps[code].get(d),
            'institution_net_amount_provider_units':rawmaps[code].get(d)} for d in days[-60:]]
        item={'symbol':code,'name':stock['name'],'market':market,'sector':stock['sector'],'sector_id':stock['sector_id'],
            'asof':asof,'exchange':'KRX','provider_finality':'UNVERIFIED','foreign':f,'institution':inst,
            'institution_amount_status':'unit_pending','foreign_minus_institution_net_1d':metric(None,'institution_amount_unit_not_officially_confirmed'),
            'foreign_minus_institution_quantity_1d':metric(f['net_quantity_1d']['value']-inst['net_quantity_1d']['value'] if f['net_quantity_1d']['value'] is not None and inst['net_quantity_1d']['value'] is not None else None,'missing_quantity'),
            'foreign_buy_institution_sell_quantity_conflict':metric(f['net_quantity_1d']['value']>0 and inst['net_quantity_1d']['value']<0 if f['net_quantity_1d']['value'] is not None and inst['net_quantity_1d']['value'] is not None else None,'missing_quantity'),
            'ohlcv':next((b for b in series if b['trade_date']==asof),None),
            'price_series':{'basis':series[-1]['basis'] if series else 'KIS_adjusted_option_0','observed_at':observed,'source_id':current[0]['sha256'] if history_selection is None else sha(encoded(history_selection['source_ids'])),
                'qualified_auxiliary_history':history_selection,
                'columns':['trade_date','open','high','low','close','volume','turnover','validation_status','reasons'],
                'rows':[[v[k] for k in ('trade_date','open','high','low','close','volume','turnover','validation_status','reasons')] for v in series]},
            'flow_series':{'columns':['trade_date','net_amount_krw','net_quantity_shares','institution_net_quantity_shares','institution_net_amount_krw','institution_net_amount_provider_units'],
                'rows':[[v[k] for k in ('trade_date','net_amount_krw','net_quantity_shares','institution_net_quantity_shares','institution_net_amount_krw','institution_net_amount_provider_units')] for v in foreign_series]},
            'technical':px,'observations':extra,
            'financial':{'annual':annual[code][:1],'verified_period_reports':financials[code][:4],
                'recent_disclosures':filings[code][:5],'borrowing_details':None,'contracts_funding_shareholder_return':'not_inferred_from_periodic_filings',
                'TTM_PER_ROE':metric(None,'comparable_TTM_and_parent_basis_not_verified'),'source_state_sha256':corp_hash},
            'validity':{'verified_contiguous_price_days':next((n for n,d in enumerate(reversed(price_days)) if d not in accepted),len(price_days)),
                'available_adjusted_rows':len(series),'official_six_field_pass_rows':len(accepted),
                'price_policy':('whole auxiliary vintage; both bases and exact KRX six fields; operating version differences audited' if history_selection else 'same response vintage; same-day six-field KRX match; unchanged source DB comparison; no unverified adjustments'),
                'data_vintage':observed,'historical_backtest_eligible':False,'no_personal_data':True}}
        tables[stock['sector_id']].append(item);rows.append(item)
        counts['stocks']+=1
        counts['institution_quantity_1d']+=inst['net_quantity_1d']['value'] is not None
        counts['institution_quantity_5d']+=inst['net_quantity_5d']['value'] is not None
        counts['institution_quantity_20d']+=inst['net_quantity_20d']['value'] is not None
        counts['foreign_20d']+=f['net_20d']['value'] is not None
        counts['raw_ohlcv_asof']+=item['ohlcv'] is not None
        counts['accepted_ohlcv_asof']+=asof in accepted
        counts['annual_financial']+=bool(annual[code]);counts['period_financial']+=bool(financials[code])
        for key in ('sma_5','sma_20','sma_60','sma_120','atr_14','ichimoku_current_span_b','market_excess_5d_pp'):
            counts[key]+=px[key]['value'] is not None
    source_files={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [ROOT/'screener/decision_support/indicators.py',ROOT/'screener/decision_support/evidence.py',ROOT/'screener/decision_support/history.py',Path(__file__).resolve()]}
    identity={'schema_version':SCHEMA_VERSION,'asof':asof,'source_snapshot_sha256':source_hash,'source_inventory_sha256':sha(encoded(inventory)),
              'corporate_state_sha256':corp_hash,'code_sha256':source_files,'institution_spec_sha256':institution_proof}
    dataset_id='decision-v1-'+asof+'-'+sha(encoded(identity))
    db=out/'analysis.duckdb'
    with duckdb.connect(str(db)) as c:
        def table(name, values, columns):
            frame=pd.DataFrame(values,columns=columns) if not values else pd.DataFrame(values)
            c.register('_input',frame);c.execute('create table '+name+' as select * from _input');c.unregister('_input')
        table('institution_flows_daily',inst_rows,['symbol','trade_date','source_id','net_quantity','net_amount_krw'])
        table('stock_ohlcv_daily',obs_rows,['symbol','trade_date','source_id','open','high','low','close','volume','turnover','validation_status'])
        table('source_files',[{'source_id':k,**v,'kind':kind} for kind,group in inventory.items() for k,v in group.items()],['source_id','file','sha256','kind'])
        table('indicator_snapshots',[{'symbol':r['symbol'],'asof':asof,'dataset_id':dataset_id,'payload':encoded(r).decode()} for r in rows],['symbol','asof','dataset_id','payload'])
        c.execute('CHECKPOINT')
    rawdb=db.read_bytes();compressed=gzip.compress(rawdb,mtime=0);(out/'analysis.duckdb.gz').write_bytes(compressed);db.unlink()
    index={**identity,'dataset_id':dataset_id,'validation_status':'passed_with_metric_holds','publication_status':'validated_auxiliary',
        'provider_finality':'UNVERIFIED','generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'source_dataset_id':v3['dataset_id'],'source_collected_at':manifest['daily_validation'].get('collection_completed_at'),
        'coverage':dict(counts),'source_population':coverage,'sectors':[],
        'price_hold_reasons_asof':dict(collections.Counter(reason for r in rows for reason in (r['ohlcv']['reasons'] if r['ohlcv'] else ['missing_ohlcv']))),
        'database':{'file':'analysis.duckdb.gz','sha256':sha(compressed),'uncompressed_sha256':sha(rawdb)},
        'units':{'foreign_amount':'KRW','institution_amount':'unit_pending; normalized amounts null','quantity':'shares','price':'KRW','volume':'shares','turnover':'KRW','returns':'percent','excess_return':'percentage_points'},
        'freshness_policy':'Require index.source_snapshot_sha256 == snapshot/manifest.json.sha256 and asof match before use; old successful files are not today data',
        'price_policy':'operational crosscheck is separate from provider finality; rejected values preserved as observations',
        'api_calls_added':0,'order_signals_generated':False,
        'definitions':{'sma_slope':'one trading day difference, KRW','atr':'Wilder 14; seed needs 15 consecutive valid OHLC bars; seed start explicit',
            'ichimoku_current_cloud':'span calculations from 26 sessions earlier; span B needs 78 valid sessions',
            'ichimoku_projected_cloud':'today calculation, displayed 26 sessions ahead, never today cloud',
            'price_flat':'exact 5-day return of zero; no arbitrary range threshold','institution_amount_unit':'not borrowed from foreign flow fields',
            'missing_values':'null; actual zero remains zero','thresholds':'sign/zero comparisons only; no buy/sell decision',
            'calendar':'operating official market calendar; no business-day synthesis','relative_strength':'stock same-period percent return minus own listed market index percent return',
            'sector_relative_strength':'held: comparable historical official sector price index unavailable'}}
    shards=[]
    for sid,items in sorted(tables.items()):
        shards.extend((sid,part//60+1,items[part:part+60]) for part in range(0,len(items),60))
    for sid,part,items in shards:
        doc={'schema_version':SCHEMA_VERSION,'asof':asof,'dataset_id':dataset_id,'source_snapshot_sha256':source_hash,'stocks':items}
        rel='sectors/'+sid+'-'+str(part)+'.json';raw=encoded(doc);put(out/rel,raw)
        index['sectors'].append({'sector_id':sid,'part':part,'market':items[0]['market'],'sector':items[0]['sector'],'symbols':len(items),'file':rel,'sha256':sha(raw)})
    (out/'index.json').write_bytes(encoded(index))
    (out/'validation.json').write_bytes(encoded({'asof':asof,'dataset_id':dataset_id,'source_snapshot_sha256':source_hash,'status':'passed_with_metric_holds','issues':errors,'official_row_issues':ref_errors,'elapsed_seconds':round(time.monotonic()-start,3)}))
    validation_start=time.monotonic()
    verify(out,snapshot)
    validation=read(out/'validation.json')
    validation['independent_validation_seconds']=round(time.monotonic()-validation_start,3)
    validation['total_build_and_validation_seconds']=round(time.monotonic()-start,3)
    (out/'validation.json').write_bytes(encoded(validation))
    if any(sha((snapshot/p).read_bytes())!=h for p,h in protected.items()):raise ValueError('operating_source_changed_during_build')
    return index


def verify(directory,snapshot):
    directory=Path(directory);snapshot=Path(snapshot);index=read(directory/'index.json');manifest=read(snapshot/'manifest.json')
    if not coherent(snapshot) or index['source_snapshot_sha256']!=manifest['sha256'] or index['asof']!=manifest['asof']:raise ValueError('generation_mismatch')
    inventory=read(directory/'source-manifest.json')
    if sha(encoded(inventory))!=index['source_inventory_sha256']:raise ValueError('source_inventory_hash_mismatch')
    for group in inventory.values():
        for source in group.values():
            path=directory/source['file']
            if not path.resolve().is_relative_to(directory.resolve()) or sha(path.read_bytes())!=source['sha256']:raise ValueError('evidence_hash_mismatch')
    validation=read(directory/'validation.json')
    if any(validation[k]!=index[k] for k in ('asof','dataset_id','source_snapshot_sha256')):raise ValueError('mixed_validation_generation')
    allrows=[]
    for shard in index['sectors']:
        p=directory/shard['file']
        if not p.resolve().is_relative_to(directory.resolve()) or sha(p.read_bytes())!=shard['sha256']:raise ValueError('shard_hash_mismatch')
        x=read(p)
        if any(x[k]!=index[k] for k in ('asof','dataset_id','source_snapshot_sha256')):raise ValueError('mixed_shard_generation')
        allrows+=x['stocks']
    if len({r['symbol'] for r in allrows})!=len(allrows) or len(allrows)!=index['coverage']['stocks']:raise ValueError('duplicate_or_missing_symbols')
    dbpath=directory/index['database']['file']
    if not dbpath.resolve().is_relative_to(directory.resolve()):raise ValueError('invalid_database_path')
    rawdb=dbpath.read_bytes()
    if sha(rawdb)!=index['database']['sha256'] or sha(gzip.decompress(rawdb))!=index['database']['uncompressed_sha256']:raise ValueError('sidecar_db_hash_mismatch')
    with tempfile.TemporaryDirectory() as temp:
        db=Path(temp)/'sidecar.duckdb';db.write_bytes(gzip.decompress(rawdb))
        with duckdb.connect(str(db),read_only=True) as c:
            saved={code:json.loads(payload) for code,payload in c.execute('select symbol,payload from indicator_snapshots').fetchall()}
            if saved!={r['symbol']:r for r in allrows}:raise ValueError('normalized_database_shard_mismatch')
            iq={}
            for code,day,n,q in c.execute('select symbol,trade_date,count(distinct struct_pack(q:=net_quantity,a:=net_amount_provider_units)),min(net_quantity) from institution_flows_daily group by symbol,trade_date').fetchall():
                if n==1:iq[code,day]=q
    # Independent SQL/raw sums, including the nonoverlapping previous window.
    with duckdb.connect(str(snapshot/'dashboard.duckdb'),read_only=True) as c:
        sums={}; calendars={}
        for market in ('KOSPI','KOSDAQ'):
            dates=[str(r[0]) for r in c.execute('select trade_date from calendar where market=? and trade_date<=? order by trade_date desc limit 20',[market,index['asof']]).fetchall()][::-1]
            calendars[market]=dates
            for n,offset,label in [(1,0,'net_1d'),(3,0,'net_3d'),(5,0,'net_5d'),(20,0,'net_20d'),(5,5,'previous_5d')]:
                end=len(dates)-offset;window=dates[max(0,end-n):end]
                values=c.execute('select symbol,count(net_amount),sum(net_amount) from flows where exchange=\'KRX\' and trade_date in (select unnest(?::DATE[])) group by symbol',[window]).fetchall() if window else []
                sums[market,label]={code:int(total) if count==n and len(window)==n else None for code,count,total in values}
    for r in allrows:
        for label in ('net_1d','net_3d','net_5d','net_20d','previous_5d'):
            if r['foreign'][label]['value']!=sums[r['market'],label].get(r['symbol']):raise ValueError('foreign_raw_sum_mismatch')
        a,b=r['foreign']['net_5d']['value'],r['foreign']['previous_5d']['value']
        if r['foreign']['change_5d']['value']!=(a-b if a is not None and b is not None else None):raise ValueError('foreign_change_mismatch')
        for n in (1,5,20):
            days=calendars[r['market']][-n:];vals=[iq.get((r['symbol'],d)) for d in days]
            expected=sum(vals) if len(vals)==n and all(v is not None for v in vals) else None
            if r['institution'][f'net_quantity_{n}d']['value']!=expected:raise ValueError('institution_quantity_sum_mismatch')
        if r['institution_amount_status']=='unit_pending' and any(r['institution'][f'net_{n}d']['value'] is not None for n in (1,5,20)):raise ValueError('unproven_institution_unit')
    return {'symbols':len(allrows),'foreign_value_checks':len(allrows)*6,'status':'passed'}


def install(out,destination,snapshot):
    verify(out,snapshot)
    destination.parent.mkdir(parents=True,exist_ok=True)
    index=read(out/'index.json')
    # Build the whole replacement before changing any last-good file. Both
    # renames are on one filesystem; a failure restores the original directory.
    with tempfile.TemporaryDirectory(prefix='.decision-install-',dir=destination.parent) as temp:
        ready=Path(temp)/'ready';backup=Path(temp)/'previous'
        shutil.copytree(out,ready)
        (ready/'status.json').write_bytes(encoded({'status':'success','asof':index['asof'],
            'source_snapshot_sha256':index['source_snapshot_sha256'],'dataset_id':index['dataset_id'],'market_api_calls':0}))
        had_previous=destination.exists()
        if had_previous:os.replace(destination,backup)
        try:os.replace(ready,destination)
        except BaseException:
            if had_previous:os.replace(backup,destination)
            raise
    # Remote visibility changes only through a single Git commit. All immutable
    # source files were carried into the candidate; obsolete derived shards are
    # retired, while earlier derived generations remain in Git history.


def main():
    p=argparse.ArgumentParser();p.add_argument('--snapshot',type=Path,default=ROOT/'snapshot');p.add_argument('--fetch-checkpoints',action='store_true');p.add_argument('--install',action='store_true');p.add_argument('--verify-only',action='store_true');a=p.parse_args()
    destination=a.snapshot/'decision-support'
    if a.verify_only:print(json.dumps(verify(destination,a.snapshot)));return
    before=read(destination/'index.json') if (destination/'index.json').exists() else None
    try:
        with tempfile.TemporaryDirectory(prefix='decision-build-',dir=ROOT) as temp:
            out=Path(temp);index=build(a.snapshot,destination,out,a.fetch_checkpoints)
            if before and before['dataset_id']==index['dataset_id']:
                verify(destination,a.snapshot);print(json.dumps({'status':'unchanged','dataset_id':index['dataset_id']}));return
            if a.install:install(out,destination,a.snapshot)
            print(json.dumps({'status':'published_locally' if a.install else 'validated','asof':index['asof'],'coverage':index['coverage'],'api_calls_added':0},ensure_ascii=False))
    except Exception as e:
        if a.install:
            destination.mkdir(parents=True,exist_ok=True)
            (destination/'status.json').write_bytes(encoded({'status':'failed','error_type':type(e).__name__,'reason':str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalpha() else 'see_failed_build_log','previous_good_dataset_preserved':True,'attempted_asof':read(a.snapshot/'manifest.json')['asof'],'source_snapshot_sha256':read(a.snapshot/'manifest.json')['sha256'],'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}))
        raise

if __name__=='__main__':main()
