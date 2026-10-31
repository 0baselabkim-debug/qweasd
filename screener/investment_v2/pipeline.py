"""Read-only evidence -> assessment -> guarded GPT -> briefing -> immutable records."""
import datetime as dt
import fcntl
import gzip
import json
import os
from pathlib import Path
import shutil
import tempfile

import duckdb

from screener.decision_evidence.build import verify_output, candidates
from screener.decision_support.evidence import encoded, sha, timestamp
from . import SCHEMA
from .contracts import independent, check_row, digest_tree
from .contracts import value
from .context import load_bundle
from .portfolio import PortfolioLedger, review
from .assessment import assess
from .engine import JudgmentEngine
from .briefing import render
from .records import record_judgment


def verify_bundle(path, expected_manifest_sha256=None):
    path = Path(path)
    raw = (path/'manifest.json').read_bytes()
    if expected_manifest_sha256 and sha(raw) != expected_manifest_sha256:
        raise ValueError('investment_manifest_anchor_mismatch')
    m = json.loads(raw)
    actual = digest_tree(path)
    actual.pop('manifest.json')
    if actual != m['files']:
        raise ValueError('investment_output_file_or_hash_mismatch')
    market = json.loads(gzip.decompress((path/'market.json.gz').read_bytes()))
    if (market['dataset_id'], market['asof'], market['source_evidence_dataset_id']) != (m['dataset_id'], m['asof'], m['source_evidence_dataset_id']):
        raise ValueError('investment_output_identity_mismatch')
    rows = market['stocks']
    if len(rows) != m['population'] or len({r['stock_code'] for r in rows}) != len(rows):
        raise ValueError('investment_output_population_mismatch')
    if any((r['asof'], r['dataset_id'], r['source_evidence_dataset_id']) !=
           (m['asof'], m['dataset_id'], m['source_evidence_dataset_id']) for r in rows):
        raise ValueError('investment_row_identity_mismatch')
    with duckdb.connect(str(path/'investment.duckdb'), read_only=True) as db:
        data = db.execute('select source_order,stock_code,trade_date,dataset_id,payload from assessments order by source_order').fetchall()
        parquet = db.execute('select source_order,stock_code,trade_date,dataset_id,payload from read_parquet(?) order by source_order', [str(path/'records.parquet')]).fetchall()
    if len(data) != len(rows) or data != parquet:
        raise ValueError('investment_DuckDB_Parquet_mismatch')
    for index, (record, row) in enumerate(zip(data, rows)):
        ordinal, code, day, dataset, payload = record
        if (ordinal, code, str(day), dataset) != (index, row['stock_code'], row['asof'], m['dataset_id']) or json.loads(payload) != row:
            raise ValueError('investment_DuckDB_JSON_mismatch')
    return dict(status='PASS', rows=len(rows), database_json_parquet_match=True, manifest_sha256=sha(raw))


def run(evidence_directory, expected_manifest_sha256, output_root, *, decision_at=None,
        context_path=None, portfolio_directory=None, engine=None, limit=20):
    evidence_directory = Path(evidence_directory).resolve()
    if not expected_manifest_sha256:
        raise ValueError('external_evidence_manifest_anchor_required')
    verification = verify_output(evidence_directory, expected_manifest_sha256)
    before = digest_tree(evidence_directory)
    source = json.loads(gzip.decompress((evidence_directory/'market.json.gz').read_bytes()))
    at = decision_at or dt.datetime.now(dt.timezone.utc).isoformat()
    if timestamp(at) > dt.datetime.now(dt.timezone.utc):
        raise ValueError('decision_in_future')
    if timestamp(at) < max(timestamp(source['generated_at']), timestamp(source['knowledge_cutoff'])):
        raise ValueError('decision_precedes_evidence_generation_or_cutoff')
    if not 0 <= limit <= 20:
        raise ValueError('maximum_twenty_candidates')
    if not source['stocks'] or len({r['stock_code'] for r in source['stocks']}) != len(source['stocks']):
        raise ValueError('empty_or_duplicate_evidence_population')
    for row in source['stocks']:
        check_row(row, source)
        if timestamp(row['generated_at']) > timestamp(at) or timestamp(row['knowledge_cutoff']) > timestamp(at):
            raise ValueError('row_not_available_at_decision')
    context = load_bundle(context_path, source['asof'], at)
    ledger = PortfolioLedger(portfolio_directory) if portfolio_directory else None
    snapshot = ledger.snapshot(at) if ledger else None
    by_code = {r['stock_code']: r for r in source['stocks']}
    account = review(snapshot, by_code)
    inputs = [evidence_directory, *context['input_files']]
    if portfolio_directory:
        inputs.append(portfolio_directory)
    output_root = independent(output_root, inputs)
    output_root.mkdir(parents=True, exist_ok=True)
    engine = engine or JudgmentEngine()
    code_root = Path(__file__).parent
    identity = dict(schema_version=SCHEMA, source_evidence_dataset_id=source['dataset_id'],
        source_evidence_manifest_sha256=expected_manifest_sha256, asof=source['asof'], decision_at=at,
        source_manifest_sha256=source['source_manifest_sha256'], source_tree_sha256=sha(encoded(before)),
        context_inputs=context['input_files'], portfolio_head=snapshot['head_hash'] if snapshot else None,
        code_sha256={p.name: sha(p.read_bytes()) for p in sorted(code_root.iterdir()) if p.suffix in ('.py', '.md')},
        limit=limit, engine_mode='MOCK' if engine.transport else 'LIVE' if engine.allow_live else 'NOT_RUN',
        model=engine.model, max_model_calls=engine.max_calls)
    dataset_id = 'investment-v2-'+source['asof']+'-'+sha(encoded(identity))
    final = output_root/dataset_id
    with (output_root/'writer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if final.exists():
            m = json.loads((final/'manifest.json').read_bytes())
            if any(m.get(k) != v for k, v in identity.items()):
                raise ValueError('investment_existing_identity_mismatch')
            return dict(dataset_id=dataset_id, directory=str(final), idempotent=True, validation=verify_bundle(final))
        assessments = {code: assess(row, context, account) for code, row in by_code.items()}
        candidate_rows = candidates(source['stocks'], [], limit)['stocks']
        candidate_codes = [r['stock_code'] for r in candidate_rows if r['stock_code'] not in account['holdings']]
        account['analysis_comparisons'] = {code: dict(dataset_id=row['dataset_id'], asof=row['asof'],
            foreign_net_5d=value(row, 'foreign_flow_metrics', 'net_5d'),
            foreign_net_20d=value(row, 'foreign_flow_metrics', 'net_20d'),
            return_5d_pct=value(row, 'price_trend_metrics', 'return_5d_pct'),
            market_excess_5d_pp=value(row, 'relative_strength_metrics', 'market_excess_5d_pp'),
            total_score=assessments[code]['scorecard']['total_score']) for code, row in by_code.items() if code in account['holdings']}
        review_codes = list(dict.fromkeys([*(code for code in account['holdings'] if code in by_code), *candidate_codes]))
        judgments, records, failed = {}, [], False
        staging = Path(tempfile.mkdtemp(prefix='.investment-', dir=output_root))
        try:
            selection = dict(status='NOT_RUN', selected_stock_codes=[])
            try:
                selection = engine.judge_batch([by_code[c] for c in review_codes], assessments, context, account, at, candidate_codes)
                judgments = selection['judgments']
            except ValueError:
                failed = True
                judgments = {code: dict(status='REJECTED', reason='model_or_grounding_failed_no_retry', judgment=None) for code in review_codes}
            for code, result in judgments.items():
                try:
                    if result.get('judgment'):
                        assessments[code]['final_grade'] = result['judgment']['grade']
                        assessments[code]['interpretation_status'] = result['status']
                        rec = record_judgment(by_code[code], assessments[code], result, at, staging/'decisions', account, context)
                        # Persist portable relative locations, not temporary absolute paths.
                        rec['path'] = str(Path(rec['path']).relative_to(staging))
                        records.append(rec)
                except ValueError:
                    failed = True
                    assessments[code]['final_grade'] = None
                    assessments[code]['interpretation_status'] = 'REJECTED'
                    judgments[code] = dict(status='REJECTED', reason='model_or_grounding_failed_no_retry', judgment=None)
            rows = [dict(stock_code=code, asof=source['asof'], dataset_id=dataset_id,
                         source_evidence_dataset_id=source['dataset_id'], source_evidence_sha256=sha(encoded(row)),
                         preserved_context=dict(corporate=row.get('corporate_event_evidence'), macro=row.get('macro_context_reference'),
                                                usage='CONTEXT_ONLY_NOT_PROMOTED_TO_VERIFIED_FACTS_OR_SCORES'),
                         assessment=assessments[code], gpt=judgments.get(code, dict(status='NOT_RUN', reason='not_selected_for_model_review', judgment=None)))
                    for code, row in by_code.items()]
            market = dict(**identity, dataset_id=dataset_id, stocks=rows, provider_finality=source['provider_finality'],
                          status='GPT_REJECTED_WITH_DIAGNOSTICS' if failed else 'OFFLINE_INTEGRATED_WITH_DATA_HOLDS',
                          historical_backtest_eligible=False, operating_publication='NOT_CHANGED')
            (staging/'market.json.gz').write_bytes(gzip.compress(encoded(market), mtime=0))
            (staging/'briefing.html').write_text(render(source, assessments, judgments, context, account, candidate_codes, selection['selected_stock_codes']))
            (staging/'briefing.json').write_bytes(encoded(dict(dataset_id=dataset_id, asof=source['asof'],
                candidates=candidate_codes, maximum_candidates=20,
                selected_by_GPT=selection['selected_stock_codes'], selection_status=selection['status'],
                judgments=judgments, portfolio=account, decision_records=records, context_status=context['status'])))
            quality = dict(dataset_id=dataset_id, asof=source['asof'], source_population=len(rows), analysis_population=len(rows),
                source_verification=verification, source_unchanged=True, candidates=len(candidate_codes),
                latest_price_verified=sum(r['data_quality']['latest_price_verified'] for r in source['stocks']),
                complete_scorecards=sum(a['scorecard']['total_score'] is not None for a in assessments.values()),
                partial_scorecards=sum(a['scorecard']['total_score'] is None for a in assessments.values()),
                context_status=context['status'], context_holds=context['held'],
                OpenAI_live_HTTP=sum(j['mode'] == 'LIVE' for j in engine.journal),
                OpenAI_mock_requests=sum(j['mode'] == 'MOCK' for j in engine.journal),
                market_HTTP=0, engine_failed=failed, provider_finality=source['provider_finality'],
                official_price_status='PENDING_KRX' if not all(r['data_quality']['latest_price_verified'] for r in source['stocks']) else 'PRESERVED_CROSSCHECK_NOT_PROVIDER_FINALITY',
                future_performance='NOT_RUN', unverified_context_not_sent_as_facts=True)
            (staging/'quality.json').write_bytes(encoded(quality))
            (staging/'context-at-decision.json').write_bytes(encoded(context))
            (staging/'model-journal.json').write_bytes(encoded(engine.journal))
            with duckdb.connect(str(staging/'investment.duckdb')) as db:
                db.execute('create table assessments(source_order integer,stock_code varchar primary key,trade_date date,dataset_id varchar,payload json)')
                db.executemany('insert into assessments values(?,?,?,?,?)', [(i, r['stock_code'], r['asof'], dataset_id, encoded(r).decode()) for i, r in enumerate(rows)])
                db.execute('copy assessments to ? (format parquet, compression zstd)', [str(staging/'records.parquet')])
            if digest_tree(evidence_directory) != before:
                raise ValueError('read_only_evidence_changed')
            if any(sha(Path(p).read_bytes()) != h for p, h in context['input_files'].items()):
                raise ValueError('context_original_changed_during_analysis')
            if ledger and ledger.snapshot(at) != snapshot:
                raise ValueError('portfolio_changed_during_analysis')
            (staging/'manifest.json').write_bytes(encoded(dict(**identity, dataset_id=dataset_id, population=len(rows),
                files=digest_tree(staging), all_input_sources_unchanged=True)))
            checked = verify_bundle(staging)
            os.rename(staging, final)
            return dict(status=market['status'], dataset_id=dataset_id, directory=str(final), idempotent=False,
                        validation=checked, quality=quality, bytes=sum(p.stat().st_size for p in final.rglob('*') if p.is_file()))
        except Exception:
            # Isolated diagnostics are retained; never remove input or operating data.
            (staging/'FAILURE.json').write_bytes(encoded(dict(status='FAILED_NOT_PUBLISHED', dataset_id=dataset_id, model_journal=engine.journal)))
            raise
