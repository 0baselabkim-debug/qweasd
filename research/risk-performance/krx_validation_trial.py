"""Fixed historical replay only. Never runs the unapproved 2026-10-22 trial.

No CLI date override, network clients, registry writes or workflow changes.
"""
import collections
import csv
import datetime as dt
import gzip
import json
import socket
import time
from pathlib import Path

from krx_validation import (Vault, OfficialIndex, assess, checkpoint_observation,
                            official_context, hash_bytes, canonical, write_once, iso, select_preserved_version,
                            verify_session_sources)
from forward_system import load, snapshot_rows, validate_publication, official_next_session
from engine import digest, instant
from screener.liquidity_quality import digest as rows_digest

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
FULL = ROOT.parent/'full-market'
OUT = ROOT/'krx-validation-study'
SNAP = ROOT/'conflict-diagnosis/inputs/latest-snapshot'
DAY = '2026-10-20'


def csv_bytes(rows):
    import io
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return ('\ufeff'+output.getvalue()).encode()


def run():
    started = time.monotonic()
    vault = Vault(OUT/'vault')
    sources = {}

    def capture(path, expected=None):
        path = Path(path)
        if not path.resolve().is_relative_to(REPO):
            raise ValueError('source_outside_research_repository')
        h = vault.capture(path, expected)
        sources[str(path.relative_to(REPO))] = h
        return h

    # The current task authorizes a replay of already preserved historical data.
    if load(SNAP/'manifest.json')['asof'] != DAY:
        raise ValueError('fixed_historical_replay_only_not_oct22_manual_trial')
    publication = validate_publication(SNAP)
    universe, _, _, db_sessions, excluded = snapshot_rows(SNAP)
    if publication['asof'] != DAY or len(universe) != 2429 or len({r[0] for r in universe}) != 2429:
        raise ValueError('historical_universe_or_publication_changed')
    for p in SNAP.glob('*'):
        if p.is_file():
            sources[str(p.relative_to(REPO))] = hash_bytes(p.read_bytes())

    official = OfficialIndex(vault)
    import_audit = []
    # Canonical raw HTTP responses and their independent, preserved request ledger.
    ledger_path = ROOT/'official-crosscheck/API_EXECUTION.json'
    capture(ledger_path)
    for entry in load(ledger_path)['requests']:
        if not entry.get('key', '').startswith(('KOSPI-', 'KOSDAQ-')):
            continue
        p = ledger_path.parent/entry['file']
        h = capture(p, entry['file_sha256'])
        raw = gzip.decompress(p.read_bytes())
        if hash_bytes(raw) != entry['response_sha256']:
            raise ValueError('official_raw_HTTP_hash_mismatch')
        market, compact_day = entry['key'].split('-')
        rows = json.loads(raw).get('OutBlock_1', [])
        env = {'http': entry['http'], 'url': entry['url']+'?basDd='+entry['params']['basDd'],
               'observed_at': entry['completed_at'], 'rows': rows,
               'rows_sha256': rows_digest(rows), 'raw_response_sha256': entry['response_sha256']}
        official.add(env, market, iso(compact_day), h)
        import_audit.append({'source': str(p.relative_to(REPO)), 'status': 'accepted', 'reason': ''})

    # Keep ALL saved official versions, including older 10/14 and 10/15 archives.
    catalog_path = ROOT/'forward-study/inputs/reference-audit/catalog.json'
    capture(catalog_path)
    for entry in load(catalog_path):
        p = catalog_path.parent/entry['saved_file']
        h = capture(p, entry['sha256'])
        obj = load(p)
        try:
            if not isinstance(obj, dict):
                raise ValueError('not_a_full_official_market_response')
            if 'response' in obj and 'OutBlock_1' in obj['response']:
                rows = obj['response']['OutBlock_1']
                obj = {'http': 200, 'url': obj['url'], 'observed_at': obj['observed_at'],
                       'rows': rows, 'rows_sha256': rows_digest(rows)}
            rows = obj.get('rows', [])
            if not rows:
                raise ValueError('not_a_full_official_market_response')
            market = rows[0]['MKT_NM']
            day = iso(rows[0]['BAS_DD'])
            official.add(obj, market, day, h)
            import_audit.append({'source': str(p.relative_to(REPO)), 'status': 'accepted', 'reason': ''})
        except (ValueError, KeyError, TypeError) as exc:
            import_audit.append({'source': str(p.relative_to(REPO)), 'status': 'hold',
                                 'reason': str(exc) if isinstance(exc, ValueError) else 'unsupported_official_archive_schema'})
    # Include the original 10/19 collection (the archived catalog can be partial).
    p = FULL/'collection/official-reference-probe.json'
    capture(p)
    for entry in load(p)['records']:
        if entry.get('http') != 200:
            continue
        path = p.parent/entry['file']
        h = capture(path, entry['sha256'])
        obj = load(path); rows = obj['response']['OutBlock_1']
        env = {'http': 200, 'url': obj['url'], 'observed_at': obj['observed_at'],
               'rows': rows, 'rows_sha256': rows_digest(rows)}
        official.add(env, obj['market'], '2026-10-19', h)

    # Reproduce the actual official index sessions, never generate weekdays.
    calendar_path = FULL/'collection/calendar.json'
    capture(calendar_path)
    saved_calendar = load(calendar_path)
    sessions = {'KOSPI': set(), 'KOSDAQ': set()}
    session_sources = {'KOSPI': [], 'KOSDAQ': []}
    for entry in saved_calendar['observations']:
        p = calendar_path.parent/entry['file']
        h = capture(p, entry['sha256'])
        obj = load(p); market = entry['market']; params = obj['params']
        session_sources[market].append(h)
        if obj['market'] != market or params['FID_COND_MRKT_DIV_CODE'] != 'U' or params['FID_INPUT_ISCD'] != {'KOSPI':'0001','KOSDAQ':'1001'}[market] or obj['response']['rt_cd'] != '0':
            raise ValueError('official_index_calendar_identity_mismatch')
        dates = [r['stck_bsop_date'] for r in obj['response']['output2']]
        if len(dates) != len(set(dates)) or any(not params['FID_INPUT_DATE_1'] <= d <= params['FID_INPUT_DATE_2'] for d in dates):
            raise ValueError('official_index_calendar_dates_invalid')
        sessions[market].update(iso(d) for d in dates)
    for market in sessions:
        if sessions[market] != set(saved_calendar['market_sessions'][market]):
            raise ValueError('calendar_not_reproducible_from_original')
    holiday_path = ROOT/'official-crosscheck/official-calendar.json'
    holiday_hash = capture(holiday_path)
    holiday = load(holiday_path)
    # Existing ledger already supplies this official calendar raw response.
    entry = next(r for r in load(ledger_path)['requests'] if r['key'] == 'holiday')
    raw_path = ledger_path.parent/entry['file']
    capture(raw_path, entry['file_sha256'])
    raw = gzip.decompress(raw_path.read_bytes())
    if hash_bytes(raw) != entry['response_sha256'] or json.loads(raw)['output'] != holiday['holiday_response']['output']:
        raise ValueError('holiday_original_hash_or_rows_mismatch')
    for r in holiday['holiday_response']['output']:
        if r['opnd_yn'] == r['tr_day_yn'] == 'Y' and iso(r['bass_dt']) <= DAY:
            for market in sessions:
                sessions[market].add(iso(r['bass_dt']))
    sessions = {m: sorted(d for d in ds if d <= DAY) for m, ds in sessions.items()}
    session_evidence = {m: {'index_sources': hs, 'holiday_source': holiday_hash} for m, hs in session_sources.items()}
    for m in sessions:
        if verify_session_sources(m, DAY, session_evidence[m], vault)[0] != sessions[m]:
            raise ValueError('calendar_independent_reproduction_mismatch')
    if any(sessions[m][-40:] != db_sessions[m][-40:] for m in sessions):
        raise ValueError('snapshot_official_calendar_window_disagreement')

    first_path = FULL/'inputs/checkpoint-20261020.json.gz'
    later_path = ROOT/'kis-price-version-study/inputs/checkpoint-20261021.json.gz'
    cp_first, cp_later = load(first_path), load(later_path)
    first_hash, later_hash = capture(first_path), capture(later_path)
    if (cp_first['trade_date'], cp_later['trade_date']) != ('20261020', '20261021'):
        raise ValueError('wrong_preserved_checkpoints')
    explicit_path = ROOT/'conflict-diagnosis/explicit-adjustment-differences.csv'
    capture(explicit_path)
    with explicit_path.open(encoding='utf-8-sig') as f:
        explicit = {r['symbol'] for r in csv.DictReader(f)}
    population, details, context_coverage = [], [], []
    for symbol, name, market, listing in universe:
        views = {}
        for label, cp, source in [('first', cp_first, first_hash), ('later', cp_later, later_hash)]:
            try:
                if symbol not in cp['records']:
                    raise ValueError('checkpoint_symbol_absent')
                observation = checkpoint_observation(cp, symbol, DAY, source)
                observation['market'] = market
                observation_id = vault.record('observations', observation)
                result = assess(observation, market, official, symbol in explicit)
                assessment_id = vault.record('assessments', result)
                views[label] = {'observation': observation, 'assessment': result}
                details.append({'symbol': symbol, 'market': market, 'date': DAY, 'version': label,
                                'status': result['status'], 'six_field_equal': result['six_field_equal'],
                                'reasons': ';'.join(result['reasons']), 'observation_sha256': observation_id,
                                'assessment_sha256': assessment_id, 'source_sha256': source,
                                **{k: v for k, v in observation['bar'].items()},
                                **{'delta_'+k: result['differences'].get(k) for k in observation['bar']}})
            except (ValueError, KeyError, TypeError) as exc:
                views[label] = {'assessment': {'status': 'hold', 'six_field_equal': False,
                                             'reasons': [str(exc) if isinstance(exc, ValueError) else 'checkpoint_fields_missing']}}
        context = official_context(official, symbol, market, DAY, sessions[market],
                                   str(listing) if listing else None, symbol in excluded)
        context['session_evidence'] = session_evidence[market]
        context_coverage.append({'symbol': symbol, 'official_valid_days_in_window': len(context['bars']),
                                 'complete_40': context['coverage_complete'],
                                 'reasons': ';'.join(context['reasons']),
                                 'missing_dates': ';'.join(r['date'] for r in context['missing'])})
        a, b = views['first']['assessment'], views['later']['assessment']
        chosen, chosen_version = select_preserved_version(views['first'], views['later'],
                                                          b['reasons'][0] if b['reasons'] else None)
        selected = chosen['assessment']
        population.append({'symbol': symbol, 'name': name, 'market': market, 'asof': DAY,
                           'first_six_equal': a['six_field_equal'], 'later_six_equal': b['six_field_equal'],
                           'latest_status': b['status'], 'latest_reasons': ';'.join(b['reasons']),
                           'selected_version': chosen_version, 'selected_status': selected['status'],
                           'selected_reasons': ';'.join(selected['reasons']),
                           'official_40_complete': context['coverage_complete'],
                           'ABCD_status': 'unclassifiable' if not context['coverage_complete'] else 'requires_existing_bundle_validation',
                           'group': None, 'registered': False})

    # The already approved four price responses are only read and hash-checked.
    requery_path = ROOT/'kis-price-version-study/API_EXECUTION.json'
    capture(requery_path)
    from kis_version_analysis import validate_new
    for entry in load(requery_path)['requests']:
        if entry['authentication']:
            continue
        p = requery_path.parent/entry['file']; capture(p, entry['file_sha256'])
        raw = gzip.decompress(p.read_bytes())
        if hash_bytes(raw) != entry['response_sha256']:
            raise ValueError('preserved_requery_response_hash_mismatch')
        symbol, kind = entry['key'].split('-')
        bar, flags = validate_new(entry, json.loads(raw), symbol, kind)
        ref, error, _ = official.resolve('KOSPI', symbol, DAY)
        if error or bar != ref:
            raise ValueError('two_stock_requery_regression')

    next_session, _ = official_next_session('2026-10-22', holiday, dt.datetime.now(dt.timezone.utc))
    missing_market_days = {(m, d) for m in sessions for d in sessions[m][-40:]
                           if not any(s['market'] == m and s['date'] == d for s in official.sources)}
    result = {
        'schema': 'offline-official-validation-trial-v1', 'asof': DAY, 'population': len(population),
        'new_market_API_requests': 0, 'oct22_manual_validation_executed': False, 'signals_registered': 0,
        'first_six_field_equal': sum(r['first_six_equal'] for r in population),
        'later_six_field_equal': sum(r['later_six_equal'] for r in population),
        'later_operational_pass': sum(r['latest_status'] == 'six_field_pass' for r in population),
        'later_hold': sum(r['latest_status'] == 'hold' for r in population),
        'selected_operational_pass': sum(r['selected_status'] == 'six_field_pass' for r in population),
        'selected_hold': sum(r['selected_status'] == 'hold' for r in population),
        'whole_first_observation_reused': sum(r['selected_version'] == 'first_exact_official_match_reused' for r in population),
        'later_hold_reasons_overlapping': dict(collections.Counter(reason for r in population for reason in r['latest_reasons'].split(';') if reason)),
        'official_40_complete': sum(r['official_40_complete'] for r in population),
        'new_policy_ABCD_classifiable': 0, 'legacy_189_classification_not_changed': True,
        'official_market_dates': sorted({(s['market'], s['date']) for s in official.sources}),
        'valid_official_days_per_symbol': dict(collections.Counter(r['official_valid_days_in_window'] for r in context_coverage)),
        'additional_full_market_date_requests_for_40_window': len(missing_market_days),
        'missing_market_dates': sorted(missing_market_days),
        'next_session_after_oct22_from_saved_calendar': next_session,
        'condition_not_met_count': None,
        'elapsed_seconds': round(time.monotonic()-started, 3),
        'source_sha256': sources,
        'session_evidence_by_market': session_evidence,
    }
    for path, h in sources.items():
        if hash_bytes((REPO/path).read_bytes()) != h:
            raise ValueError('original_input_changed_during_replay')
    # Content-addressed run directory; never edit a previous study or registry.
    run_id = digest({'inputs': sources, 'code': {p.name: hash_bytes(p.read_bytes()) for p in [Path(__file__), ROOT/'krx_validation.py']}})
    destination = OUT/'runs'/run_id
    for name, rows in [('population.csv', population), ('observations.csv', details),
                       ('official-coverage.csv', context_coverage), ('reference-import-audit.csv', import_audit)]:
        write_once(destination/name, csv_bytes(rows))
    # Timing varies; store it separately under its own immutable identity.
    runtime = {'run_id': run_id, 'elapsed_seconds': result.pop('elapsed_seconds'),
               'executed_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    write_once(destination/'results.json', canonical(result))
    write_once(OUT/'executions'/(digest(runtime)+'.json'), canonical(runtime))
    print(json.dumps({'run_directory': str(destination.relative_to(REPO)), **{k:v for k,v in result.items() if k not in ('source_sha256','missing_market_dates','official_market_dates')}, **runtime}, ensure_ascii=False))


if __name__ == '__main__':
    def deny(*args, **kwargs):
        raise RuntimeError('offline_trial_network_forbidden')
    socket.socket.connect = deny
    socket.create_connection = deny
    run()
