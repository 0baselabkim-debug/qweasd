"""Offline research evidence layer; no provider clients or signal writer.

Six-field reconciliation is NOT corporate-action approval or provider finality.
Existing cohort/swing/registration rules remain in their original modules.
"""
import datetime as dt
import gzip
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_FLOOR
from pathlib import Path
from urllib.parse import urlsplit

from engine import digest, instant, decision_gate
from forward_system import (Store, FIELDS, KIS_FIELDS, KRX_FIELDS, load,
                            validate_publication, snapshot_rows, official_next_session, build_signal)
from screener.liquidity_quality import digest as rows_digest


def hash_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(raw):
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def write_once(path, raw):
    """Atomic, idempotent bytes; a new revision must have a new identity."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('immutable_revision_conflict')
    finally:
        os.unlink(temporary)
    return path


class Vault:
    """Content-addressed originals plus separate, immutable derived observations.

    Hashes detect modification; ordinary files are not physical WORM storage.
    """
    def __init__(self, root):
        self.root = Path(root)

    def capture(self, path, expected=None):
        raw = Path(path).read_bytes()
        h = hash_bytes(raw)
        if expected and h != expected:
            raise ValueError('original_source_hash_mismatch')
        return self.capture_bytes(raw)

    def capture_bytes(self, raw):
        h = hash_bytes(raw)
        write_once(self.root/'originals'/h, raw)
        return h

    def original(self, h):
        if len(h) != 64 or any(c not in '0123456789abcdef' for c in h):
            raise ValueError('unsafe_hash')
        raw = (self.root/'originals'/h).read_bytes()
        if hash_bytes(raw) != h:
            raise ValueError('stored_original_hash_mismatch')
        return raw

    def record(self, kind, payload):
        if kind not in ('observations', 'assessments', 'errors', 'runs', 'contexts'):
            raise ValueError('research_record_kind_required')
        h = digest(payload)
        write_once(self.root/kind/(h+'.json'), canonical({'sha256': h, 'payload': payload}))
        return h


def six(row, keys=FIELDS):
    """Integer KRW/share fields: no float tolerance, rounding or missing-to-zero."""
    values = []
    for key in keys:
        try:
            value = Decimal(str(row[key]))
        except (KeyError, InvalidOperation):
            raise ValueError('missing_or_invalid_6field') from None
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError('nonfinite_or_fractional_6field')
        values.append(int(value))
    o, h, l, c, volume, turnover = values
    if not (0 < l <= min(o, c) <= max(o, c) <= h and volume >= 0 and turnover >= 0):
        raise ValueError('invalid_OHLCV')
    return dict(zip(FIELDS, values))


def iso(day):
    return dt.datetime.strptime(day, '%Y%m%d').date().isoformat()


class OfficialIndex:
    """Do not silently prefer a newer, conflicting official version."""
    def __init__(self, vault=None):
        self.rows = defaultdict(list)
        self.sources = []
        self.vault = vault

    def add(self, envelope, market, day, source_hash):
        part = {'KOSPI': 'stk', 'KOSDAQ': 'ksq'}[market]
        expected = f'https://data-dbg.krx.co.kr/svc/apis/sto/{part}_bydd_trd?basDd={day.replace("-", "")}'
        if envelope.get('http') != 200 or envelope.get('url') != expected:
            raise ValueError('official_date_market_or_http_mismatch')
        rows = envelope.get('rows', [])
        if not rows or envelope.get('rows_sha256') != rows_digest(rows):
            raise ValueError('official_rows_hash_mismatch')
        instant(envelope['observed_at'])
        codes = [r.get('ISU_CD') for r in rows]
        if len(codes) != len(set(codes)) or any(not s or len(s) != 6 or not s.isascii() or not s.isalnum() for s in codes):
            raise ValueError('official_duplicate_or_invalid_symbol')
        if any(r.get('BAS_DD') != day.replace('-', '') or r.get('MKT_NM') != market for r in rows):
            raise ValueError('official_row_date_market_mismatch')
        envelope_hash = self.vault.capture_bytes(gzip.compress(canonical(envelope), mtime=0)) if self.vault else None
        for row in rows:
            try:
                bar, error = six(row, KRX_FIELDS), None
            except ValueError as exc:
                bar, error = None, str(exc)
            self.rows[market, row['ISU_CD'], day].append({
                'bar': bar, 'error': error, 'observed_at': envelope['observed_at'],
                'source_sha256': source_hash, 'row_sha256': digest(row),
                'envelope_sha256': envelope_hash})
        self.sources.append({'market': market, 'date': day, 'source_sha256': source_hash,
                             'rows': len(rows), 'observed_at': envelope['observed_at']})

    def resolve(self, market, symbol, day, known_by=None):
        candidates = self.rows.get((market, symbol, day), [])
        if known_by is not None:
            candidates = [r for r in candidates if instant(r['observed_at']) <= instant(known_by)]
        if not candidates:
            return None, 'official_same_day_missing', []
        if any(r['error'] for r in candidates):
            return None, 'official_invalid_observation', candidates
        distinct = {digest(r['bar']) for r in candidates}
        if len(distinct) != 1:
            return None, 'official_version_conflict', candidates
        return candidates[0]['bar'].copy(), None, candidates


def checkpoint_observation(cp, symbol, day, source_hash):
    """Read the complete adjusted chart bar; never use investor close as raw1."""
    r = cp['records'][symbol]
    if cp.get('exchange') != 'KRX' or r.get('request_scope') != {
        'market': 'J', 'symbol': symbol, 'end': cp['trade_date'], 'adjustment': '0'
    } or r.get('chart_header', {}).get('stck_shrn_iscd') != symbol:
        raise ValueError('checkpoint_identity_or_scope_mismatch')
    instant(r['observed_at'])
    rows = r.get('adjusted_prices', [])
    dates = [x.get('stck_bsop_date') for x in rows]
    if len(dates) != len(set(dates)) or any(not d or d > cp['trade_date'] for d in dates):
        raise ValueError('duplicate_or_future_price_date')
    selected = [x for x in rows if x['stck_bsop_date'] == day.replace('-', '')]
    if len(selected) != 1:
        raise ValueError('exact_day_missing')
    row = selected[0]
    from price_link import action_flag
    return {'symbol': symbol, 'trade_date': day, 'exchange': 'KRX', 'basis': 'adjusted_0',
            'bar': six(row, KIS_FIELDS), 'observed_at': r['observed_at'],
            'request_scope': r['request_scope'], 'source_sha256': source_hash,
            'row_sha256': digest(row), 'action_flag': action_flag(row)}


def assess(observation, market, official, adjustment_pending=False):
    if observation.get('exchange') != 'KRX' or observation.get('market') != market or observation.get('basis') not in ('adjusted_0', 'raw_1'):
        raise ValueError('observation_market_or_basis_unverified')
    six(observation['bar'])
    bar, error, proofs = official.resolve(market, observation['symbol'], observation['trade_date'])
    reasons = [error] if error else []
    delta = {k: observation['bar'][k]-bar[k] for k in FIELDS} if bar else {}
    if any(delta.values()):
        reasons.append('price_observation_conflict')
    if observation.get('action_flag') or adjustment_pending:
        reasons.append('corporate_action_or_adjustment_review_required')
    return {'status': 'hold' if reasons else 'six_field_pass',
            'six_field_equal': bool(bar) and not any(delta.values()),
            'reasons': reasons, 'differences': delta, 'official_proofs': proofs,
            'observation_sha256': digest(observation), 'provider_finality': 'UNVERIFIED',
            'signal_eligible': False}


def select_preserved_version(first, later, later_reason=None):
    """Reuse a whole already validated first bar only when later is absent.

    A malformed newer response, new conflict or action hold never falls back.
    """
    if later_reason == 'checkpoint_symbol_absent' and first['assessment']['status'] == 'six_field_pass':
        return first, 'first_exact_official_match_reused'
    return later, 'later'


def official_context(index, symbol, market, day, sessions, listing=None, excluded=False):
    """Uniform raw KRX series only. Full bars carry individual source versions.

    Missing official days are NOT filled with KIS. Historical prefix is retained
    where continuous, as required by the unchanged swing event implementation.
    """
    if sessions != sorted(set(sessions)):
        raise ValueError('calendar_duplicate_or_unsorted')
    required = [d for d in sessions if d <= day][-40:]
    reasons = []
    if len(required) != 40 or required[-1:] != [day]:
        reasons.append('official_40_session_calendar_missing')
    if listing and any(d < listing for d in required):
        reasons.append('listing_history_short_40')
    if excluded:
        reasons.append('excluded_or_suspended')
    bars, evidence, missing = [], {}, []
    for d in required:
        bar, error, proofs = index.resolve(market, symbol, d)
        if error:
            missing.append({'date': d, 'reason': error})
            continue
        bars.append({'trade_date': d, **bar})
        evidence[d] = proofs
        if bar['volume'] == 0 or bar['turnover'] == 0:
            reasons.append('zero_trading_in_40_window')
    if missing:
        reasons.append('official_40_bars_incomplete_or_conflicting')
    # Keep a continuous prefix, preventing old confirmed states becoming new D.
    if not missing and len(required) == 40:
        prefix = []
        for d in reversed([d for d in sessions if d < required[0]]):
            bar, error, proofs = index.resolve(market, symbol, d)
            if error or (listing and d < listing):
                break
            prefix.append({'trade_date': d, **bar})
            evidence[d] = proofs
        bars = list(reversed(prefix)) + bars
    return {'schema': 'official-raw-context-v1', 'symbol': symbol, 'market': market,
            'asof': day, 'basis': 'KRX_raw', 'bars': bars, 'validated_window': required,
            'calendar': [d for d in sessions if d <= day], 'evidence': evidence,
            'missing': missing, 'reasons': sorted(set(reasons)),
            'coverage_complete': not reasons, 'provider_finality': 'UNVERIFIED',
            'corporate_action_interval': 'not_automatically_verified', 'signal_eligible': False}


def review_adjustment(raw_bar, adjusted_bar, rule, document):
    """Verify cited arithmetic separately; never infer a factor from prices.

    This cannot establish that the citation semantically authorizes a rule.
    Consequently even an arithmetic match requires independent review and does
    not lift corporate-action holds or enable registration.
    """
    reasons = []
    host = urlsplit(rule.get('source_url', '')).hostname or ''
    if not any(host == h or host.endswith('.'+h) for h in ('krx.co.kr', 'dart.fss.or.kr')):
        reasons.append('official_action_document_required')
    if hash_bytes(document) != rule.get('document_sha256'):
        reasons.append('action_document_hash_mismatch')
    quote = rule.get('quoted_rule', '')
    if not quote or quote.encode() not in document:
        reasons.append('adjustment_rule_citation_missing')
    calculated = {}
    try:
        six(raw_bar); six(adjusted_bar)
        rounding = {'half_up': ROUND_HALF_UP, 'floor': ROUND_FLOOR}[rule['rounding']]
        for field in FIELDS:
            # Explicit field-by-field multipliers, including volume/turnover.
            n, d = rule['multipliers'][field]
            if isinstance(n, bool) or isinstance(d, bool) or int(n) != n or int(d) != d or n <= 0 or d <= 0:
                raise ValueError('invalid_explicit_factor')
            calculated[field] = int((Decimal(raw_bar[field])*Decimal(n)/Decimal(d)).quantize(Decimal('1'), rounding=rounding))
        if calculated != adjusted_bar:
            reasons.append('declared_adjustment_arithmetic_mismatch')
    except (KeyError, ValueError, TypeError, InvalidOperation):
        reasons.append('complete_explicit_adjustment_rule_required')
    return {'arithmetic_matches': not reasons, 'reasons': reasons,
            'computed': calculated, 'status': 'independent_official_rule_review_required',
            'automatic_release': False}


def verify_context_sources(context, vault):
    """Rehash source envelopes and bind each selected bar and observation time."""
    loaded = {}
    for day, proofs in context['evidence'].items():
        for proof in proofs:
            h = proof.get('envelope_sha256')
            if not h:
                raise ValueError('official_envelope_identity_missing')
            if h not in loaded:
                loaded[h] = json.loads(gzip.decompress(vault.original(h)))
            envelope = loaded[h]
            index = OfficialIndex()
            index.add(envelope, context['market'], day, proof['source_sha256'])
            bar, reason, _ = index.resolve(context['market'], context['symbol'], day)
            matches = [r for r in envelope['rows'] if r['ISU_CD'] == context['symbol']]
            if reason or bar != proof['bar'] or envelope['observed_at'] != proof['observed_at'] or digest(matches[0]) != proof['row_sha256']:
                raise ValueError('official_proof_does_not_match_original')


def verify_session_sources(market, day, evidence, vault):
    """Reproduce historical sessions from preserved official index responses.

    Extend only with the saved official holiday response, never weekdays.
    Source observation times also enter the prospective registration gate.
    """
    if not evidence.get('index_sources') or not evidence.get('holiday_source'):
        raise ValueError('official_historical_calendar_sources_missing')
    sessions, known = set(), []
    def decode(h):
        raw = vault.original(h)
        return json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)
    for h in evidence['index_sources']:
        obj = decode(h); p = obj['params']; response = obj['response']
        if obj['market'] != market or p['FID_COND_MRKT_DIV_CODE'] != 'U' or p['FID_INPUT_ISCD'] != {'KOSPI':'0001','KOSDAQ':'1001'}[market] or p.get('FID_PERIOD_DIV_CODE') != 'D' or response.get('rt_cd') != '0':
            raise ValueError('official_index_calendar_identity_mismatch')
        instant(obj['observed_at']); known.append(obj['observed_at'])
        dates = [r['stck_bsop_date'] for r in response['output2']]
        if len(dates) != len(set(dates)) or any(not p['FID_INPUT_DATE_1'] <= d <= p['FID_INPUT_DATE_2'] for d in dates):
            raise ValueError('official_index_calendar_dates_invalid')
        sessions.update(iso(d) for d in dates if iso(d) <= day)
    calendar = decode(evidence['holiday_source'])
    holiday = calendar.get('holiday_response', {})
    if holiday.get('endpoint') != 'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/chk-holiday' or holiday.get('tr_id') != 'CTCA0903R' or holiday.get('rt_cd') != '0':
        raise ValueError('official_holiday_source_invalid')
    instant(holiday['observed_at']); known.append(holiday['observed_at'])
    rows = holiday.get('output', [])
    if len(rows) != len({r['bass_dt'] for r in rows}):
        raise ValueError('official_holiday_duplicate_date')
    for r in rows:
        d = iso(r['bass_dt'])
        if r.get('opnd_yn') not in ('Y','N') or r.get('tr_day_yn') not in ('Y','N'):
            raise ValueError('official_holiday_status_unknown')
        is_open = r['opnd_yn'] == r['tr_day_yn'] == 'Y'
        if d in sessions and not is_open:
            raise ValueError('official_calendar_sources_conflict')
        if d <= day and is_open:
            sessions.add(d)
    return sorted(sessions), known, calendar


def prepare_signal(context, bundle, publication, prices, flows, sessions, calendar, vault, now=None):
    """Pure preparation gate. It never stores a live signal or changes an old one.

    An existing accepted adjusted bundle must match the complete official raw
    context. Action-adjusted contexts need a separately reviewed future adapter;
    they cannot pass merely by supplying an approval boolean here.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    if not context.get('coverage_complete') or context.get('basis') != 'KRX_raw':
        raise ValueError('official_40_context_not_ready')
    verify_context_sources(context, vault)
    official_sessions, calendar_times, saved_calendar = verify_session_sources(
        context['market'], context['asof'], context.get('session_evidence', {}), vault)
    if official_sessions != context['calendar'] or saved_calendar != calendar:
        raise ValueError('calendar_not_reproduced_from_original')
    if (context['symbol'], context['market'], context['asof']) != (bundle['symbol'], bundle['market'], publication['asof']):
        raise ValueError('context_bundle_identity_mismatch')
    if bundle.get('corporate_actions'):
        raise ValueError('corporate_action_rule_adapter_not_approved')
    dates = context['validated_window']
    mapping = {r['trade_date']: r for r in context['bars']}
    if len(dates) != 40 or dates != sessions[-40:]:
        raise ValueError('official_context_calendar_mismatch')
    if bundle['bars'] != context['bars']:
        raise ValueError('bundle_official_whole_bar_or_prefix_mismatch')
    known = []
    for d in mapping:
        proofs = context['evidence'].get(d, [])
        if not proofs or any(p.get('error') or p.get('bar') != six(mapping[d]) for p in proofs):
            raise ValueError('official_context_evidence_mismatch')
        known.extend(p['observed_at'] for p in proofs)
    next_day, observed = official_next_session(publication['asof'], calendar, now)
    times = known+calendar_times+[publication['known_at'], observed]
    allowed, reason = decision_gate(publication['asof'], sorted(set(sessions+[next_day])), times,
                                    max(times, key=instant), now)
    if not allowed:
        raise ValueError(reason)
    # Existing flow replay, acceptance, snapshot overlap and cohort rules retained.
    candidate = build_signal(bundle, publication, prices, flows, sessions, calendar, now)
    candidate.update(record_kind='prospective_preparation', pit_eligible=False,
                     known_at=max([candidate['known_at']]+times, key=instant),
                     official_context_sha256=digest(context))
    return {'status': 'registration_ready_requires_separate_execution',
            'candidate': candidate, 'official_context_sha256': digest(context),
            'registered': False}


def safe_publication_check(directory, error_vault):
    """Validate before opening any signal store. Failure records are isolated."""
    try:
        publication = validate_publication(directory)
        if validate_publication(directory) != publication:
            raise ValueError('publication_changed_during_check')
        return {'status': 'passed', 'publication': publication}
    except Exception as exc:
        # Never persist arbitrary exception bodies (paths/credentials may appear).
        error = {'kind': 'publication_validation_failed', 'error_type': type(exc).__name__,
                 'reason': str(exc) if isinstance(exc, ValueError) and len(str(exc)) < 100 else 'input_or_publication_invalid',
                 'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'signals_touched': False}
        h = error_vault.record('errors', error)
        return {'status': 'hold', 'error_sha256': h, 'reason': error['reason'], 'signals_touched': False}


def prepare_snapshot(directory, contexts, bundles, calendar, vault, now=None):
    """Manual validation adapter, to be invoked only after separate approval.

    Read-only publication and supplied evidence. Does not register any signals;
    each input has to pass the unchanged forward_system rules as well as the new
    official-history gate. No live CLI is exposed in this research stage.
    """
    checked = safe_publication_check(directory, vault)
    if checked['status'] != 'passed':
        return checked
    now = now or dt.datetime.now(dt.timezone.utc)
    publication = checked['publication']
    try:
        universe, prices, flows, sessions, excluded = snapshot_rows(directory)
        if len(universe) != publication['records'] or validate_publication(directory) != publication:
            raise ValueError('publication_changed_or_instrument_join_loss')
        results = []
        for symbol, name, market, listing in universe:
            try:
                if symbol in excluded:
                    raise ValueError('excluded_or_suspended')
                if listing and str(listing) > publication['asof']:
                    raise ValueError('pre_listing')
                if symbol not in contexts or symbol not in bundles:
                    raise ValueError('validated_official_context_or_bundle_missing')
                prepared = prepare_signal(contexts[symbol], bundles[symbol], publication,
                                          prices.get(symbol, {}), flows.get(symbol, {}),
                                          sessions[market], calendar, vault, now)
                results.append({'symbol': symbol, 'status': prepared['status'],
                                'group': prepared['candidate']['group'], 'prepared': prepared,
                                'registered': False})
            except (ValueError, KeyError, TypeError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else 'incomplete_evidence'
                status = 'condition_not_met' if reason == 'flow_condition_not_met' else ('no_new_event' if reason == 'C_requires_new_confirmed_event' else 'hold')
                results.append({'symbol': symbol, 'status': status, 'group': None,
                                'reason': reason, 'registered': False})
        if validate_publication(directory) != publication:
            raise ValueError('publication_changed_during_preparation')
        payload = {'publication': publication, 'results': results,
                   'prepared_at': instant(now).isoformat(), 'registered': False}
        identity = vault.record('runs', payload)
        return {'status': 'prepared_not_registered', 'run_sha256': identity,
                'results': results, 'registered': False}
    except Exception as exc:
        error = {'kind': 'snapshot_preparation_failed', 'error_type': type(exc).__name__,
                 'reason': str(exc) if isinstance(exc, ValueError) and len(str(exc)) < 100 else 'input_or_publication_invalid',
                 'recorded_at': instant(now).isoformat(), 'signals_touched': False}
        identity = vault.record('errors', error)
        return {'status': 'hold', 'error_sha256': identity, 'reason': error['reason'], 'signals_touched': False}
