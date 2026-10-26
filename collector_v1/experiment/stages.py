"""Independent KIS observations and offline KRX attestations; no live CLI/scheduler."""
import datetime as dt
import json
import re
import subprocess
import time
from pathlib import Path
from zoneinfo import ZoneInfo
from ..core import Failure, Stop, Store, atomic, encoded, now, sha
from ..providers import Q, task
from ..validation import KRX_FIELDS, integer, parse
from . import engine, policy, runtime
from .storage import ExperimentBudget, append, events


def _inside(root, relative):
    p = (root / relative).resolve()
    if root.resolve() not in p.parents:
        raise Failure('unsafe_stage_path')
    return p


def _immutable(path, raw):
    if path.exists() and path.read_bytes() != raw:
        raise Failure('immutable_stage_file_changed')
    if not path.exists():
        atomic(path, raw)


class SharedBudget(ExperimentBudget):
    """One durable physical journal for four independent rounds, including auth."""
    def __init__(self, store, round_id, metrics, run_id, session):
        super().__init__(store, round_id, metrics, run_id)
        self.session = session
        self.path = session.root / 'http.jsonl'
        all_events = events(self.path)
        self.total_used = sum(e['event'] == 'started' for e in all_events)
        self.entries = [e for e in all_events if e['round'] == round_id]
        self.used = sum(e['event'] == 'started' for e in self.entries)
        self.initial = self.used
        starts = [dt.datetime.fromisoformat(e['started_at']) for e in all_events if e['event']=='started']
        if starts:
            self.last = time.monotonic() - max(0,(dt.datetime.now(dt.timezone.utc)-max(starts)).total_seconds())
        if any(e.get('source') == 'KRX' for e in all_events if e['event'] == 'started'):
            raise Failure('KIS_session_contains_unexpected_provider_calls')

    def check(self):
        super().check()
        if (self.session.root / 'STOP').exists():
            raise Stop('operator_stop')
        if self.total_used >= policy.TOTAL_LIMIT - 2:
            raise Stop('shared_experiment_http_budget_exhausted')

    def before(self, source, endpoint, reason):
        if source != 'KIS' or endpoint not in (Q+'investor-trade-by-stock-daily', Q+'inquire-daily-itemchartprice', Q+'inquire-daily-indexchartprice', '/oauth2/tokenP'):
            raise Failure('non_KIS_request_in_provisional_stage')
        self.check()
        begin = time.monotonic()
        time.sleep(max(0, policy.INTERVAL - (time.monotonic() - self.last)))
        self.metrics.add('rate_wait', time.monotonic() - begin)
        self.check()
        self.last = time.monotonic()
        self.used += 1
        self.total_used += 1
        entry = dict(event='started', sequence=self.total_used, round_sequence=self.used,
                     round=self.round_id, source=source, endpoint=endpoint, reason=reason,
                     source_run=self.run_id, started_at=now())
        append(self.path, entry)
        self.entries.append(entry)
        self.current = entry
        return entry

    def finish(self, entry, status, **metadata):
        outcome = dict(event='finished', sequence=entry['sequence'], round=self.round_id,
                       status=status, completed_at=now(), **metadata)
        append(self.path, outcome)
        self.entries.append(outcome)


def collect_kis(root, round_id, universe, rate_proof, run_id, *, opener=None, config=None):
    """Transport injection is mandatory. No workflow or live transport is enabled here.

    Current tests supply synthetic HTTP. A future approved launcher must bind the
    reviewed transport, master/universe, artifact recovery and dated policy first.
    """
    if opener is None:
        raise Failure('live_execution_requires_separate_approval_and_launcher')
    policy.window(round_id)
    root = Path(root).resolve()
    if (root / 'seed').exists() or (root / 'round').exists():
        raise Failure('cannot_repurpose_legacy_experiment_root')
    session = Store(root)
    with session.lock():
        source_code_sha256 = sha(encoded({name:sha((Path(__file__).parent/name).read_bytes()) for name in ('engine.py','stages.py','storage.py','policy.py','stage_analysis.py','../core.py','../providers.py','../validation.py')}))
        contract = dict(source_code_sha256=source_code_sha256,schema=1, phase='KIS_PROVISIONAL', asof=policy.ASOF,
                        population_hash=sha(encoded(universe)), round_limit=policy.ROUND_LIMIT,
                        total_limit=policy.TOTAL_LIMIT, krx_http_reservation=2, interval=policy.INTERVAL,
                        starts=[x.isoformat() for x in policy.STARTS])
        previous = session.read('session.json')
        if previous is not None and previous != contract:
            raise Failure('stage_session_contract_mismatch')
        if previous is not None and not (root / 'http.jsonl').exists():
            raise Failure('shared_call_journal_missing_no_reset')
        if previous is None:
            if any(p.name != 'writer.lock' for p in root.iterdir()):
                raise Failure('new_session_would_discard_existing_state')
            atomic(root / 'http.jsonl', b'')
            session.write('session.json', contract)
        marker = f'round-{round_id}.json'
        relative = f'kis/R{round_id}'
        if session.read(marker) and not (root / relative).exists():
            raise Failure('round_state_lost_no_fresh_root')
        folder = root / relative
        if (folder / 'manifest.json').exists():
            runtime.verify(folder)
        round_contract = dict(round=round_id,directory=relative,config_sha256=sha(encoded(config or {'gzip_level':9,'profile':'baseline'})))
        if session.read(marker) and session.read(marker)!=round_contract:raise Failure('same_round_configuration_change_requires_review')
        session.write(marker, round_contract)
        report = engine.run_round(folder, round_id, universe, rate_proof, run_id,
                                  opener=opener, config=config, kis_only=True,
                                  budget_factory=lambda s, n, m, r: SharedBudget(s, n, m, r, session))
        report['source_code_sha256'] = source_code_sha256
        report['physical_http_total'] = sum(e['event'] == 'started' for e in events(root/'http.jsonl'))
        report['code_commit'] = subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()
        report['config_sha256'] = sha(encoded(report['config']))
        ledger = [e for e in events(root/'http.jsonl') if e['round']==round_id]
        report['failed_http_requests'] = sum(e['event']=='finished' and e['status']!='received' for e in ledger) + sum(e['event']=='started' for e in ledger) - sum(e['event']=='finished' for e in ledger)
        report['http_failure_rate'] = report['failed_http_requests']/report['physical_http'] if report['physical_http'] else 0
        failures = events(folder/'failures.jsonl')
        report['task_failure_count'] = len(failures)
        cache = engine.replay(Store(folder),round_id)
        recovery_intervals=[]
        for key in {engine.identity(e['task']) for e in failures}:
            failed_at=min(e['observed_at'] for e in failures if engine.identity(e['task'])==key)
            recovered_at=cache[key]['collected_at'] if key in cache else None
            recovery_intervals.append(dict(task_id=key,first_failed_at=failed_at,recovered_at=recovered_at,
                wall_seconds=(dt.datetime.fromisoformat(recovered_at)-dt.datetime.fromisoformat(failed_at)).total_seconds() if recovered_at else None))
        report['recovery_intervals']=recovery_intervals
        report['round_directory'] = relative
        report['source_generation_reuse'] = 'same_round_only'
        Store(folder).write('report.json', report)
        Store(folder).write('manifest.json', dict(files=runtime.inventory(folder)))
        return seal_generation(session, report)


def seal_generation(session, report):
    root = session.root
    raw = encoded(report)
    report_file = 'generations/reports/' + sha(raw) + '.json'
    _immutable(root / report_file, raw)
    folder = _inside(root, report['round_directory'])
    files = {report_file: sha(raw)}
    for group in ('raw', 'observations', 'derived'):
        for p in (folder / group).rglob('*'):
            if p.is_file():
                files[str(p.relative_to(root))] = sha(p.read_bytes())
    journal = (root / 'http.jsonl').read_bytes()
    manifest = dict(schema=1, phase='KIS_PROVISIONAL', asof=report['asof'], round=report['round'],
                    report_file=report_file, files=files, journal_prefix_bytes=len(journal),
                    journal_prefix_sha256=sha(journal))
    generation = sha(encoded(manifest))
    _immutable(root / 'generations' / (generation+'.json'), encoded(manifest))
    return dict(generation=generation, report=report)


def load_generation(root, generation):
    root = Path(root).resolve()
    if not re.fullmatch('[0-9a-f]{64}', generation):
        raise Failure('invalid_generation_id')
    manifest = json.loads((root/'generations'/(generation+'.json')).read_bytes())
    if sha(encoded(manifest)) != generation or manifest['phase'] != 'KIS_PROVISIONAL':
        raise Failure('generation_manifest_mismatch')
    for name, digest in manifest['files'].items():
        if sha(_inside(root, name).read_bytes()) != digest:
            raise Failure('generation_file_hash_mismatch')
    journal = (root/'http.jsonl').read_bytes()[:manifest['journal_prefix_bytes']]
    if len(journal) != manifest['journal_prefix_bytes'] or sha(journal) != manifest['journal_prefix_sha256']:
        raise Failure('generation_call_journal_mismatch')
    report = json.loads(_inside(root, manifest['report_file']).read_bytes())
    if (report['asof'], report['round'], report['phase']) != (manifest['asof'], manifest['round'], manifest['phase']):
        raise Failure('generation_identity_mismatch')
    store = Store(_inside(root, report['round_directory']))
    # Re-derive accepted provisional values from the exact immutable observations.
    cache = {}
    for row in report['rows']:
        for obs in row['observation_ids']:
            if not re.fullmatch('[0-9a-f]{64}', obs):raise Failure('invalid_observation_id')
            entry = store.read('observations/'+obs+'.json')
            if not entry:raise Failure('missing_generation_observation')
            store.raw(entry)
            t = entry['task']
            if t['asof'] != report['asof'] or t['round'] != report['round']:
                raise Failure('cross_generation_evidence_forbidden')
            cache[engine.identity(t)] = entry
        rebuilt = engine.qualify_kis(row, cache, store, report['round'])
        if rebuilt != row:
            raise Failure('provisional_values_do_not_match_raw_sources')
    import duckdb
    with duckdb.connect(str(_inside(store.root, report['database_file'])), read_only=True) as db:
        saved = {k:json.loads(v) for k,v in db.execute('SELECT symbol,payload FROM observations').fetchall()}
    if saved != {r['symbol']:r for r in report['rows']}:
        raise Failure('generation_DB_JSON_mismatch')
    return report


def validate_krx(root, generation, references):
    """Offline only: references maps market -> (evidence Store, metadata).

    Never fetch missing data, retry a provider, or rewrite the KIS generation.
    """
    report = load_generation(root, generation)
    parsed = {}
    reference_status = {}
    provenance = {}
    for market in ('KOSPI', 'KOSDAQ'):
        if market not in references:
            reference_status[market] = 'PENDING_KRX'
            continue
        store, entry = references[market]
        t = entry['task']
        endpoint = '/svc/apis/sto/'+('stk' if market == 'KOSPI' else 'ksq')+'_bydd_trd'
        if entry.get('source') != 'KRX' or entry.get('endpoint') != endpoint or t.get('kind') != 'krx' or t.get('market') != market or t.get('asof') != report['asof'] or entry.get('params') != {'basDd':report['asof'].replace('-','')}:
            raise Failure('KRX_reference_request_identity_mismatch')
        observed = dt.datetime.fromisoformat(entry['collected_at'])
        if observed.tzinfo is None or observed.astimezone(ZoneInfo('Asia/Seoul')).date() < dt.date.fromisoformat(report['asof']):
            raise Failure('invalid_reference_observation_time')
        raw = store.raw(entry)  # Integrity failures stop; never downgrade to pending.
        provenance[market] = dict(source_hash=entry['source_hash'],observation_id=sha(encoded(entry)),observed_at=entry['collected_at'])
        try:
            values = parse(raw, entry)
            # Validate all six numeric fields, even when the KIS row is absent.
            for r in values:
                prices = {k:integer(r[f]) for k,f in KRX_FIELDS.items()}
                if min(prices.values()) < 0 or prices['close'] <= 0 or not prices['low'] <= min(prices['open'],prices['close']) <= max(prices['open'],prices['close']) <= prices['high']:
                    raise Failure('invalid_KRX_OHLCV')
                if integer(r['MKTCAP']) <= 0:raise Failure('invalid_market_cap')
            parsed[market] = {r['ISU_CD']:r for r in values}
            reference_status[market] = 'AVAILABLE'
        except (Failure, ValueError, KeyError, TypeError) as exc:
            code = exc.code if isinstance(exc, Failure) else 'REFERENCE_SCHEMA_ERROR'
            reference_status[market] = 'PENDING_KRX' if code == 'REFERENCE_NOT_AVAILABLE' else 'HOLD_'+code
    rows = []
    available = {r['symbol']:r for r in report['rows']}
    for symbol in report['target_symbols']:
        kis = available.get(symbol)
        if not kis:
            rows.append(dict(symbol=symbol,status='HOLD_KIS_INCOMPLETE',differences={}))
            continue
        market = kis['market']
        record = dict(symbol=symbol,market=market,asof=report['asof'],kis_row_sha256=sha(encoded(kis)),differences={})
        state = reference_status[market]
        if state != 'AVAILABLE':record['status'] = state
        elif symbol not in parsed[market]:record['status'] = 'HOLD_KRX_SYMBOL_MISSING'
        else:
            ref = parsed[market][symbol]
            record['differences'] = {k:dict(kis=kis[k],krx=integer(ref[f]),delta=kis[k]-integer(ref[f])) for k,f in KRX_FIELDS.items() if kis[k] != integer(ref[f])}
            record.update(status='HOLD_PRICE_CONFLICT' if record['differences'] else 'SIX_FIELD_MATCH',market_cap=integer(ref['MKTCAP']))
        rows.append(record)
    matched = sum(r['status'] == 'SIX_FIELD_MATCH' for r in rows)
    payload = dict(schema=1,phase='KRX_POST_VALIDATION',asof=report['asof'],round=report['round'],kis_generation=generation,
                   status='CROSSCHECK_COMPLETE' if rows and matched == len(report['target_symbols']) and report['status']=='PROVISIONAL_COMPLETE' else 'PENDING_OR_HOLD',
                   matched=matched,target=len(report['target_symbols']),rows=rows,references=provenance,reference_status=reference_status,
                   provider_finality='UNVERIFIED',flow_officially_verified=False,physical_http_this_verification=0,checked_at=now())
    digest = sha(encoded(payload))
    _immutable(Path(root)/'attestations'/(digest+'.json'), encoded(payload))
    return dict(attestation=digest,report=payload)
