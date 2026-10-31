"""Explicit profile adapter for preserved collector generations, including failures.

The original October-29 adapter remains pinned by default. This adapter requires
an external acquisition proof, verifies the entire manifest, replays raw tasks,
and compares the saved database. Partial generations stay partial.
"""
import datetime as dt
import json
from pathlib import Path
from collections import Counter
from zoneinfo import ZoneInfo
import duckdb

from collector_v1.core import Store
from collector_v1.experiment.engine import replay,identity,qualify_kis
from collector_v1.experiment.storage import events
from collector_v1.providers import task
from screener.decision_evidence.collector_source import saved_rows,flow_observation,references
from screener.decision_support.evidence import read,sha,encoded,timestamp,accept_chart,corporate_adjustment_pending,KIS_FIELDS
from . import MODE,VERSION


def inspect(root, proof):
    root=Path(root).resolve()
    if not proof or not {'zip_sha256','manifest_sha256','artifact_id','run_id','report_path','universe_path'}<=proof.keys():
        raise ValueError('external_acquisition_proof_required')
    raw=(root/'manifest.json').read_bytes()
    if sha(raw)!=proof['manifest_sha256']: raise ValueError('historical_manifest_anchor_mismatch')
    manifest=json.loads(raw)
    files={str(p.relative_to(root)):sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    if any(p.is_symlink() for p in root.rglob('*')): raise ValueError('source_symlink')
    # Legacy writers deliberately excluded empty process-lock files. They are
    # still included in our before/after tree digest, never accepted as data.
    covered={k:v for k,v in files.items() if not (Path(k).name=='writer.lock' and (root/k).stat().st_size==0 and k not in manifest['files'])}
    if covered!={**manifest['files'],'manifest.json':proof['manifest_sha256']}: raise ValueError('historical_manifest_population_or_hash_mismatch')
    report=read(root/proof['report_path']);folder=(root/proof['report_path']).parent
    if not folder.is_relative_to(root): raise ValueError('unsafe_report_path')
    asof=report['asof']
    if str(report['run_id'])!=str(proof['run_id']) or manifest['asof']!=asof: raise ValueError('historical_run_or_date_mismatch')
    universe=read(root/proof['universe_path']);selected={r['symbol']:r for r in universe if r['eligible']}
    if len({r['symbol'] for r in universe})!=len(universe) or report['population_sha256']!=sha(encoded(universe)):
        raise ValueError('historical_master_duplicate_or_hash_mismatch')
    if report['target']!=len(selected) or report['target_symbols']!=sorted(selected): raise ValueError('historical_population_mismatch')
    journal=events(folder/'tasks.jsonl')
    if not journal: raise ValueError('historical_tasks_missing')
    first=journal[0]['task'];generation=first.get('generation_id');round_id=first['round']
    store=Store(folder);cache=replay(store,round_id,asof=asof,generation_id=generation)
    rows=report['rows']
    if len({r['symbol'] for r in rows})!=len(rows): raise ValueError('historical_report_duplicate_symbol')
    for row in rows:
        if selected.get(row['symbol'],{}).get('market')!=row['market']: raise ValueError('historical_symbol_market_mismatch')
        if qualify_kis(row,cache,store,round_id,asof=asof,generation_id=generation)!=row: raise ValueError('historical_raw_report_mismatch')
    path=(folder/report['database_file']).resolve()
    if not path.is_relative_to(folder): raise ValueError('unsafe_database_path')
    with duckdb.connect(str(path),read_only=True) as db:
        actual=db.execute('select symbol,payload from observations').fetchall()
    if len(actual)!=len(rows) or {s:json.loads(v) for s,v in actual}!={r['symbol']:r for r in rows}: raise ValueError('historical_DB_JSON_mismatch')
    http=events(folder/'http.jsonl');starts=[e for e in http if e['event']=='started'];ends=[e for e in http if e['event']=='finished']
    if len(starts)!=report['physical_http'] or [e['sequence'] for e in starts]!=list(range(1,len(starts)+1)):
        raise ValueError('historical_http_journal_mismatch')
    if len({e['sequence'] for e in ends})!=len(ends) or not {e['sequence'] for e in ends}<={e['sequence'] for e in starts}: raise ValueError('historical_http_end_mismatch')
    complete=len(rows)==len(selected) and len(starts)==len(ends) and len(report.get('indices',[]))==2
    if report['status']=='PASS' and not complete: raise ValueError('incomplete_generation_marked_PASS')
    return dict(report=report,selected=selected,store=store,cache=cache,round_id=round_id,generation_id=generation,
                files=files,complete=complete,physical_http=len(starts),unresolved_http=len(starts)-len(ends))


def load(root,knowledge_cutoff,reference_directory=None,*,proof):
    root=Path(root).resolve();v=inspect(root,proof);r=v['report'];asof=r['asof'];cutoff=timestamp(knowledge_cutoff)
    if timestamp(r['completed_at'])>cutoff: raise ValueError('source_completed_after_cutoff')
    official,sources,errors,ref_hashes=references(reference_directory,cutoff,asof=asof)
    store=v['store'];cache=v['cache'];g=v['generation_id'];round_id=v['round_id']
    def get(kind,market,code=None):
        t=task(kind,asof,round=round_id,market=market,**({'symbol':code} if code else {}),**({'generation_id':g} if g else {}))
        e=cache.get(identity(t))
        if e is None:return None,{}
        if timestamp(e['collected_at'])>cutoff: raise ValueError('source_after_cutoff')
        return e,saved_rows(json.loads(store.raw(e)),e,kind,code,market,asof=asof,generation_id=g,round_id=round_id)
    calendars={};benchmarks={}
    for m in ('KOSPI','KOSDAQ'):
        e,raw=get('index',m)
        if not raw: raise ValueError('preserved_session_calendar_missing')
        calendars[m]=sorted(raw);benchmarks[m]={d:float(x['bstp_nmix_prpr']) for d,x in raw.items()}
        if any(x<=0 for x in benchmarks[m].values()): raise ValueError('index_value_invalid')
    rows=[];holds=Counter();approved=Counter();failures={x['symbol']:x for x in r.get('errors',[]) if 'symbol' in x}
    for code,instrument in sorted(v['selected'].items()):
        market=instrument['market'];fe,flow=get('flow',market,code);pe,price=get('price',market,code)
        days=calendars[market]
        if any(d not in days for d in set(price)|set(flow)): raise ValueError('source_date_outside_preserved_index_calendar')
        fcols=['trade_date','net_amount_krw','net_quantity_shares','institution_net_quantity_shares','institution_net_amount_krw','institution_net_amount_provider_units']
        fseries=[]
        for d,raw in sorted(flow.items()):
            norm=flow_observation(raw,price.get(d));holds.update(norm['reasons']);fseries.append([d,*[norm[k] for k in fcols[1:]]])
        barrier=max((d for d,x in price.items() if corporate_adjustment_pending(x)),default=None)
        pcols=['trade_date','open','high','low','close','volume','turnover','validation_status','reasons'];pseries=[]
        for d,raw in sorted(price.items()):
            q=accept_chart(raw,code,d,market,pe['collected_at'],official,instrument['listing_date'],blocked=bool(barrier and d<=barrier))
            if q['accepted']: approved[d]+=1
            values=q['value'] or dict.fromkeys(KIS_FIELDS)
            pseries.append([d,*[values[k] for k in pcols[1:-2]],q['status'],q['reasons']])
        rows.append(dict(symbol=code,name=instrument['name'],market=market,asof=asof,exchange='KRX',provider_finality='UNVERIFIED',
            validity=dict(historical_backtest_eligible=False),flow_series=dict(columns=fcols,rows=fseries),
            price_series=dict(columns=pcols,rows=pseries,basis='KIS_original_option_1_no_adjustment_bridge',observed_at=pe['collected_at'] if pe else None),
            metric_provenance=dict(price_source_ids=[sha(encoded(pe))] if pe else [],foreign_observed_at=fe['collected_at'] if fe else None),
            _closed_price_days=[d for d in days if pe and dt.datetime.combine(dt.date.fromisoformat(d),dt.time(15,30),ZoneInfo('Asia/Seoul'))<=timestamp(pe['collected_at'])],
            _price_calendar=days,_benchmarks=benchmarks[market],recovery_status=failures.get(code)))
    generation='artifact-'+str(proof['artifact_id'])+'-'+(g or 'round-'+str(round_id))
    index=dict(asof=asof,dataset_id=generation,source_snapshot_sha256=proof['zip_sha256'],source_collected_at=r['completed_at'],
        provider_finality='UNVERIFIED',validation_status='INTERNAL_PASS_COMPLETE_PROVISIONAL' if v['complete'] else 'PARTIAL_FAILED_GENERATION',
        coverage=dict(stocks=len(rows),KIS_internal_pass=len(r['rows']),provider_final_confirmed=0),units=dict(price='KRW',volume='shares',turnover='KRW',quantity='shares',foreign_amount='KRW; provider million x 1000000',institution_amount='UNVERIFIED'))
    return dict(index=index,rows=rows,calendars=calendars,benchmarks=benchmarks,official=official,official_sources=sources,official_errors=errors,
        priority_symbols=[],macro=[],macro_source_file=None,input_file_hashes=v['files'],reference_file_hashes=ref_hashes,
        source_manifest_sha256=proof['manifest_sha256'],input_index_sha256=sha(encoded(r)),known_source_observation_latest=r['completed_at'],
        source_publication_time=None,historical_backtest_eligible=False,
        source_verification=dict(status='PASS_RAW_MASTER_MANIFEST_DB_REPLAY',collection_complete=v['complete'],source_status=r['status'],
            target=len(v['selected']),validated=len(r['rows']),unresolved_http=v['unresolved_http'],original_physical_HTTP=v['physical_http'],
            raw_flow_holds=dict(holds),approved_price_bars_by_trade_date=dict(approved),acquisition_proof=proof,analysis_mode=MODE,analysis_version=VERSION,
            official_holiday_calendar='NOT_FOUND; no statutory holiday completeness claim'),
        calendar_provenance='PRESERVED_KIS_U_INDEX_RESPONSE_SESSION_DATES; not official KRX holiday calendar; no generated weekdays')
