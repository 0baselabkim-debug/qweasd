"""Produce independent, append-only evidence generations from preserved inputs."""
import csv
import datetime as dt
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import errno
from collections import Counter
import duckdb

from screener.decision_support.evidence import encoded, sha, timestamp
from . import SCHEMA_VERSION
from .metrics import analyze
from .source import load, differences

ROOT = Path(__file__).resolve().parents[2]


def metrics_in(value, prefix=''):
    if isinstance(value,dict):
        if {'value','status'} <= value.keys():yield prefix,value
        else:
            for key,item in value.items():yield from metrics_in(item,prefix+'.'+key if prefix else key)


def candidates(records, priority_symbols, limit=20):
    if not 0 <= limit <= 20:raise ValueError('briefing_limit_must_be_0_to_20')
    order = {code:i for i,code in enumerate(priority_symbols)}
    selected = []
    for position,row in enumerate(records):
        get = lambda group,key:row[group].get(key,{}).get('value')
        if any(get(group,key) is None for group,key in (
                ('foreign_flow_metrics','net_5d'),('foreign_flow_metrics','net_20d'),
                ('price_trend_metrics','return_5d_pct'),('relative_strength_metrics','market_excess_5d_pp'))):continue
        tags = row['observation_tags']; types=[]
        if tags['foreign_buying_price_rising']['value'] is True:types.append('flow_price_coincident')
        if tags['foreign_buying_price_falling']['value'] is True and tags['foreign_flow_strengthening']['value'] is True:types.append('flow_leading_reversal_watch_hypothesis')
        if row['flow_persistence_metrics']['transition']['value'] in ('new_net_buying','new_net_selling'):types.append('flow_sign_change_watch')
        if not types:continue
        pick=lambda group,keys:{k:row[group][k] for k in keys}
        core_missing=[item for item in row['missing_fields'] if item['field'] in {
            'foreign_flow_metrics.net_5d','foreign_flow_metrics.net_20d','institution_flow_metrics.net_quantity_5d',
            'price_trend_metrics.return_5d_pct','price_trend_metrics.return_20d_pct',
            'relative_strength_metrics.market_excess_5d_pp','relative_strength_metrics.market_excess_20d_pp','observation_tags.recent_event_detected'}]
        selected.append((order.get(row['stock_code'],len(order)+position),dict(stock_code=row['stock_code'],stock_name=row['stock_name'],candidate_types=types,
            foreign_flow_metrics=pick('foreign_flow_metrics',('net_1d','net_5d','net_20d','previous_5d','change_5d')),
            flow_persistence_metrics=pick('flow_persistence_metrics',('positive_days_5d','positive_days_20d','consecutive_net_buy_days','consecutive_net_buy_days_lower_bound','transition','max_positive_day_share_5d','net_5d_to_turnover_ratio')),
            institution_flow_metrics=pick('institution_flow_metrics',('net_quantity_1d','net_quantity_5d','net_quantity_20d')),
            institution_amount_status='unavailable_unit_not_verified',price_return_5d_pct=row['price_trend_metrics']['return_5d_pct'],
            price_return_20d_pct=row['price_trend_metrics']['return_20d_pct'],relative_strength_metrics=row['relative_strength_metrics'],
            volume_liquidity_metrics=pick('volume_liquidity_metrics',('current_volume_to_mean_5d_ratio','current_volume_to_mean_20d_ratio','current_turnover_to_mean_5d_ratio','down_volume_expansion')),
            recent_event_detected=row['observation_tags']['recent_event_detected'],
            data_quality=row['data_quality'],missing_fields=core_missing,total_missing_metric_count=len(row['missing_fields']),source_references=row['source_references'],
            full_evidence_lookup=dict(file='market.json.gz',stock_code=row['stock_code']))))
    selected.sort(key=lambda x:x[0])
    return dict(order_basis='EXISTING_PUBLISHED_BRIEFING_THEN_STABLE_INPUT_ORDER_NO_NEW_SCORE',
        eligible_candidates=len(selected),returned=min(limit,len(selected)),maximum=limit,stocks=[r for _,r in selected[:limit]],
        investment_recommendations_generated=False)


def corporate_references(stock):
    return dict(status='preserved_context_only_no_new_external_calls',financial=stock.get('financial',{}),
        events=[],event_feed_status='unavailable',reason='Exact public timestamps, event facts and direct relationships require independently verified event records.',
        exact_publication_time_verified=False,point_in_time_usable=False,
        consensus_surprise=None,consensus_reason='pre_announcement_timestamped_consensus_unavailable')


def verify_output(directory,expected_manifest_sha256=None):
    directory=Path(directory);raw=(directory/'manifest.json').read_bytes()
    if expected_manifest_sha256 and sha(raw)!=expected_manifest_sha256:raise ValueError('derived_manifest_anchor_mismatch')
    manifest=json.loads(raw)
    actual={str(p.relative_to(directory)):sha(p.read_bytes()) for p in directory.rglob('*') if p.is_file() and p!=directory/'manifest.json'}
    if actual!=manifest['files']:raise ValueError('derived_manifest_file_population_or_hash_mismatch')
    market=json.loads(gzip.decompress((directory/'market.json.gz').read_bytes()))
    if market['dataset_id']!=manifest['dataset_id']:raise ValueError('derived_generation_mismatch')
    for key in ('schema_version','source_generation_id','source_manifest_sha256','input_index_sha256','source_tree_sha256','source_kind','reference_tree_sha256','knowledge_cutoff','code_sha256','briefing_limit'):
        if market.get(key)!=manifest.get(key):raise ValueError('derived_identity_mismatch')
    with duckdb.connect(str(directory/'evidence.duckdb'),read_only=True) as db:
        stored=db.execute('select source_order,stock_code,trade_date,dataset_id,payload from evidence order by source_order').fetchall()
        records=[json.loads(row[-1]) for row in stored]
        if any((ordinal,code,str(day),dataset)!=(i,row['stock_code'],row['asof'],manifest['dataset_id']) for i,((ordinal,code,day,dataset,_),row) in enumerate(zip(stored,records))):
            raise ValueError('derived_DuckDB_columns_payload_mismatch')
        if records!=market['stocks']:raise ValueError('derived_DuckDB_JSON_values_mismatch')
        parquet=db.execute('select source_order,stock_code,trade_date,dataset_id,payload from read_parquet(?) order by source_order',[str(directory/'records.parquet')]).fetchall()
        if parquet!=stored:raise ValueError('derived_Parquet_JSON_values_mismatch')
    if len({r['stock_code'] for r in records})!=len(records):raise ValueError('duplicate_derived_stock')
    return dict(status='PASS',records=len(records),database_json_parquet_match=True,manifest_sha256=sha(raw))


def build(snapshot, output_root, knowledge_cutoff, limit=20, *, source_kind='published', reference_directory=None):
    start=time.monotonic();snapshot=Path(snapshot).resolve();output_root=Path(output_root).resolve()
    if (output_root.is_relative_to(ROOT) or ROOT.is_relative_to(output_root) or output_root.is_relative_to(snapshot) or snapshot.is_relative_to(output_root)
            or any(output_root.is_relative_to(ROOT/p) for p in ('.github','collector_v1','screener','data','tests'))):
        raise ValueError('output_must_be_independent_of_protected_operating_and_source_paths')
    if timestamp(knowledge_cutoff)>dt.datetime.now(dt.timezone.utc):raise ValueError('knowledge_cutoff_in_future')
    if source_kind=='published':source=load(snapshot,knowledge_cutoff)
    elif source_kind=='collector-artifact':
        from .collector_source import load as load_collector
        source=load_collector(snapshot,knowledge_cutoff,reference_directory)
    else:raise ValueError('unsupported_evidence_source_kind')
    index=source['index']
    if not 0 <= limit <= 20:raise ValueError('briefing_limit_must_be_0_to_20')
    if not source['rows']:raise ValueError('empty_source_population')
    code={str(p.relative_to(ROOT)):sha(p.read_bytes()) for folder in ('decision_evidence','decision_support') for p in sorted((ROOT/'screener'/folder).glob('*.py'))}
    identity=dict(schema_version=SCHEMA_VERSION,source_generation_id=index['dataset_id'],source_manifest_sha256=source['source_manifest_sha256'],
        input_index_sha256=source['input_index_sha256'],source_tree_sha256=sha(encoded(source['input_file_hashes'])),source_kind=source_kind,
        reference_tree_sha256=sha(encoded(source.get('reference_file_hashes',{}))),knowledge_cutoff=knowledge_cutoff,code_sha256=code,briefing_limit=limit)
    dataset_id='decision-evidence-v2-'+index['asof']+'-'+sha(encoded(identity))
    final=output_root/dataset_id
    if final.exists():
        saved=json.loads((final/'manifest.json').read_bytes())
        if any(saved.get(k)!=v for k,v in identity.items()):raise ValueError('existing_derived_identity_mismatch')
        return dict(dataset_id=dataset_id,directory=str(final),idempotent=True,validation=verify_output(final))
    output_root.mkdir(parents=True,exist_ok=True)
    generated=dt.datetime.now(dt.timezone.utc).isoformat();records=[];conflicts=[]
    available=Counter();missing=Counter();holds=Counter();hold_stocks=Counter();transitions=Counter();price_counts=Counter();suffixes=Counter();bases=Counter()
    for stock in source['rows']:
        features=analyze(stock,source['calendars'][stock['market']],stock['_price_calendar'],stock['_benchmarks'])
        # A row flag cannot promote a generation with unproven historical availability.
        features['data_quality']['historical_backtest_eligible']=(
            features['data_quality']['historical_backtest_eligible'] is True and source['historical_backtest_eligible'] is True)
        row=dict(schema_version=SCHEMA_VERSION,asof=index['asof'],generated_at=generated,dataset_id=dataset_id,
            source_generation_id=index['dataset_id'],source_manifest_sha256=source['source_manifest_sha256'],
            validation_status='DERIVED_WITH_METRIC_HOLDS',source_validation_status=index['validation_status'],
            provider_finality=stock['provider_finality'],stock_code=stock['symbol'],stock_name=stock['name'],market=stock['market'],exchange='KRX',
            knowledge_cutoff=knowledge_cutoff,**features,corporate_event_evidence=corporate_references(stock),
            macro_context_reference=dict(source_file=source.get('macro_source_file','snapshot/macro.json'),source_sha256=source['input_file_hashes'].get('macro.json'),observations=source['macro']),
            source_references=dict(source_index_sha256=source['input_index_sha256'],source_stock_payload_sha256=sha(encoded({k:v for k,v in stock.items() if not k.startswith('_')})),
                foreign_source_snapshot_sha256=index['source_snapshot_sha256'],price_source_ids=stock.get('metric_provenance',{}).get('price_source_ids',[]),
                price_observed_at=stock['price_series'].get('observed_at'),source_batch_collected_at=index.get('source_collected_at'),
                exact_foreign_request_observed_at=stock.get('metric_provenance',{}).get('foreign_observed_at'),source_publication_time=source['source_publication_time'],
                known_latest_source_observation=source['known_source_observation_latest'],point_in_time_reproducible=False),
            automatic_order=None,investment_recommendation=None)
        row['missing_fields']=[dict(field=path,reason=value.get('reason')) for path,value in metrics_in(features) if value['value'] is None]
        for path,value in metrics_in(features):
            if value['value'] is None:missing[path+'|'+str(value.get('reason'))]+=1
            else:available[path]+=1
        holds.update(row['data_quality']['price_hold_reasons'])
        hold_stocks.update(row['data_quality']['price_hold_reasons'].keys())
        suffixes[str(row['data_quality']['verified_contiguous_price_days'])]+=1
        bases[str(row['data_quality']['original_adjusted_basis'])]+=1
        transitions[str(features['flow_persistence_metrics']['transition']['value'])]+=1
        price_counts['stocks']+=1;price_counts['latest_price_verified']+=row['data_quality']['latest_price_verified']
        for key in ('sma_5','sma_20','sma_60','sma_120','atr_14','return_5d_pct','return_20d_pct'):
            price_counts[key]+=features['price_trend_metrics'][key]['value'] is not None
        conflicts.extend(differences(stock,source['official']));records.append(row)
    quality=dict(schema_version=SCHEMA_VERSION,asof=index['asof'],dataset_id=dataset_id,source_generation_id=index['dataset_id'],
        source_coverage=index['coverage'],derived_price_coverage=dict(price_counts),computable_metric_counts=dict(available),
        unavailable_field_reason_counts=dict(missing),price_hold_reason_observations=dict(holds),flow_transition_counts=dict(transitions),
        price_hold_reason_stocks=dict(hold_stocks),verified_contiguous_suffix_stock_counts=dict(suffixes),price_basis_stock_counts=dict(bases),
        conflict_field_counts=dict(Counter(r.get('field',r.get('reason')) for r in conflicts)),
        official_current_stock_coverage=sum(bool(source['official'].get((s['symbol'],index['asof'],s['market']))) for s in source['rows']),
        official_comparison_status='NOT_RUN_MISSING_SAME_DAY_KRX' if not any(source['official'].get((s['symbol'],index['asof'],s['market'])) for s in source['rows']) else 'PRESERVED_SAME_DAY_REFERENCE_COMPARED_WITH_HOLDS',
        official_price_cause_policy='Exact differences preserved; NXT, timing and adjustment causes are not inferred.',
        p0_price_coverage_improved=False,p0_limitation='No missing or conflicting price was replaced or newly approved. Additional verified history requires preserved official six-field and adjustment evidence.',
        source_known_latest_observation=source['known_source_observation_latest'],source_publication_time=source['source_publication_time'],
        point_in_time_reproducible=False,historical_backtest_eligible=False,source_verification=source['source_verification'],
        additional_KIS_calls=0,additional_external_calls=0,operating_publication='NOT_CHANGED')
    briefing=dict(schema_version=SCHEMA_VERSION,dataset_id=dataset_id,asof=index['asof'],**candidates(records,source['priority_symbols'],limit))
    market=dict(**identity,dataset_id=dataset_id,asof=index['asof'],generated_at=generated,stocks=records,
        provider_finality=index['provider_finality'],validation_status='DERIVED_WITH_METRIC_HOLDS',historical_backtest_eligible=False,
        units={**index.get('units',{}),'concentration_and_activity':'ratio','close_location':'ratio_0_to_1','pivot_distance':'percent'},
        calendar_provenance=source.get('calendar_provenance','PRESERVED_OPERATING_CALENDAR_AND_QUALIFIED_KIS_INDEX_SESSIONS; no synthetic weekdays or new calendar lookup'))
    staging=Path(tempfile.mkdtemp(prefix='.candidate-',dir=output_root))
    try:
        (staging/'market.json.gz').write_bytes(gzip.compress(encoded(market),mtime=0))
        (staging/'briefing.json').write_bytes(encoded(briefing))
        (staging/'quality.json').write_bytes(encoded(quality))
        buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=['stock_code','trade_date','status','field','kis_value','krx_value','delta','cause','reason','official_source_ids'])
        writer.writeheader()
        for conflict in conflicts:writer.writerow(conflict)
        (staging/'price-conflicts.csv.gz').write_bytes(gzip.compress(buffer.getvalue().encode(),mtime=0))
        with duckdb.connect(str(staging/'evidence.duckdb')) as db:
            db.execute('create table evidence(source_order integer, stock_code varchar primary key, trade_date date, dataset_id varchar, payload json)')
            db.executemany('insert into evidence values (?,?,?,?,?)',[(i,r['stock_code'],r['asof'],dataset_id,encoded(r).decode()) for i,r in enumerate(records)])
            db.execute('copy evidence to ? (format parquet, compression zstd)',[str(staging/'records.parquet')])
        after={str(p.relative_to(snapshot)):sha(p.read_bytes()) for p in sorted(snapshot.rglob('*')) if p.is_file()}
        if after!=source['input_file_hashes']:raise ValueError('source_changed_during_read_only_build')
        if any(sha(Path(p).read_bytes())!=h for p,h in source.get('reference_file_hashes',{}).items()):raise ValueError('official_reference_changed_during_build')
        files={p.name:sha(p.read_bytes()) for p in staging.iterdir() if p.is_file()}
        (staging/'manifest.json').write_bytes(encoded(dict(**identity,dataset_id=dataset_id,files=files,
            source_files_verified_unchanged=len(after),source_tree_unchanged=True)))
        verification=verify_output(staging)
        try:os.rename(staging,final)
        except OSError as error:
            if error.errno not in (errno.EEXIST,errno.ENOTEMPTY):raise
            saved=json.loads((final/'manifest.json').read_bytes())
            if any(saved.get(k)!=v for k,v in identity.items()):raise ValueError('concurrent_derived_identity_mismatch')
            verify_output(final);shutil.rmtree(staging)
        return dict(dataset_id=dataset_id,directory=str(final),idempotent=False,validation=verification,elapsed_seconds=time.monotonic()-start,
            bytes=sum(p.stat().st_size for p in final.iterdir() if p.is_file()),coverage=quality['derived_price_coverage'],briefing_candidates=briefing['returned'])
    except Exception:
        # Only the incomplete derived candidate is removed; no source path is written.
        if staging.exists():shutil.rmtree(staging)
        raise
