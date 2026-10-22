"""Official 40-day research preparation and offline coverage, never registration.

prepare reads a pinned normal publication; collect-batch is an explicit one-shot
nighttime action. No cron, delayed job, signal store, production write or KIS API.
"""
import argparse
import collections
import csv
import datetime as dt
import gzip
import io
import json
import subprocess
import time
from pathlib import Path

import duckdb
from engine import digest, instant, decision_gate
from forward_system import load, validate_publication, snapshot_rows, official_next_session
from krx_validation import (Vault, OfficialIndex, canonical, hash_bytes, write_once,
                            official_context, verify_session_sources)
from krx40_collect import (OUT, ROOT, REPO, BRANCH, Journal, utcnow, git,
                           response_envelope, collect_batch, github_preflight, persist_research,
                           require_window)
from conflict_reconciliation import add_evidence, select_flow_replay, verify_replay_sources
from price_link import action_flag, classify_link

PREVIOUS = ROOT/'krx-validation-study'
FULL = ROOT.parent/'full-market'


def csv_data(rows, fields=None):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields or list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return ('\ufeff'+output.getvalue()).encode()


def save_csv(path, rows, fields=None):
    write_once(path, csv_data(rows, fields))


def freeze_main(commit):
    """Read immutable Git objects into a new research-only input, never checkout."""
    if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
        raise ValueError('full_commit_required')
    directory = OUT/'inputs'/commit
    for name, source in [('dashboard.duckdb', 'snapshot/dashboard.duckdb'),
                         ('manifest.json', 'snapshot/manifest.json'),
                         ('daily-validation.json', 'snapshot/daily-validation.json'),
                         ('publication-ledger.json', 'snapshot/publication-ledger.json'),
                         ('index.json', 'snapshot/briefing/index.json')]:
        raw = subprocess.check_output(['git', 'show', commit+':'+source], cwd=REPO)
        write_once(directory/name, raw)
    source = directory/'source.json'
    if not source.exists():
        write_once(source, canonical({'commit': commit, 'captured_at': utcnow().isoformat(),
                                     'commit_time': git('show', '-s', '--format=%cI', commit)}))
    if load(source)['commit'] != commit:
        raise ValueError('frozen_source_commit_mismatch')
    publication = validate_publication(directory)
    return directory, publication


def preserved_index(vault):
    """Reuse hash-addressed official envelopes produced by the verified replay.

    Both identical and conflicting preserved versions enter the index. A source
    manifest binds every envelope to immutable Git bytes; no last-write-wins.
    """
    old = Vault(PREVIOUS/'vault'); index = OfficialIndex(vault); sources = {}
    for path in sorted((old.root/'originals').iterdir()):
        raw = old.original(path.name)
        try:
            obj = json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)
        except (ValueError, UnicodeError, gzip.BadGzipFile):
            continue
        if not isinstance(obj, dict) or not all(k in obj for k in ('rows', 'rows_sha256', 'http', 'url', 'observed_at')):
            continue
        rows = obj['rows']
        if not rows:
            continue
        market = rows[0]['MKT_NM']; day = dt.datetime.strptime(rows[0]['BAS_DD'], '%Y%m%d').date().isoformat()
        h = vault.capture_bytes(raw)
        index.add(obj, market, day, h)
        sources[str(path.relative_to(REPO))] = h
    journal = Journal(OUT/'collection')
    for entry in journal.requests():
        if entry.get('status') != 'validated':
            continue
        raw = journal.checkpoint_response(entry)
        h = vault.capture_bytes(raw)
        env = response_envelope(raw, entry['http'], entry['market'], entry['date'], entry['completed_at'])
        index.add(env, entry['market'], entry['date'], h)
        sources[str((journal.root/entry['file']).relative_to(REPO))] = entry['file_sha256']
    return index, sources


def main_references(commit, snapshot, index, vault):
    """Also inspect newer main's preserved official archives, without checking it out."""
    names = git('ls-tree', '-r', '--name-only', commit).splitlines()
    paths = [p for p in names if p.startswith(('liquidity/reference/', 'liquidity/reference-observations/'))
             and p.endswith(('.json', '.json.gz'))]
    audit = []; sources = {}
    for rel in paths:
        raw = subprocess.check_output(['git', 'show', commit+':'+rel], cwd=REPO)
        h = vault.capture_bytes(raw)
        stored = snapshot/'official-references'/(h+'.bin')
        write_once(stored, raw); sources[str(stored.relative_to(REPO))] = h
        try:
            obj = json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)
            rows = obj.get('rows', [])
            if not rows:
                raise ValueError('official_market_rows_missing')
            market = rows[0]['MKT_NM']
            day = dt.datetime.strptime(rows[0]['BAS_DD'], '%Y%m%d').date().isoformat()
            index.add(obj, market, day, h)
            status, reason = 'imported', ''
        except (ValueError, KeyError, TypeError) as exc:
            status, reason = 'hold', str(exc) if isinstance(exc, ValueError) else 'unsupported_archive_schema'
        audit.append({'source_commit': commit, 'source_path': rel, 'sha256': h, 'status': status, 'reason': reason})
    write_once(snapshot/'official-reference-inventory.json', canonical(audit))
    return sources


def calendars(day, vault):
    current = load(PREVIOUS/'CURRENT_RUN.json')
    previous_results = load(REPO/current['directory']/'results.json')
    evidence = previous_results['session_evidence_by_market']
    old = Vault(PREVIOUS/'vault'); sessions = {}; calendar = None
    for market, record in evidence.items():
        for h in record['index_sources']+[record['holiday_source']]:
            if vault.capture_bytes(old.original(h)) != h:
                raise ValueError('calendar_hash_mismatch')
        dates, _, calendar = verify_session_sources(market, day, record, vault)
        if dates[-1:] != [day] or len(dates) < 40:
            raise ValueError('latest_day_not_supported_by_preserved_official_calendar')
        # A date omitted by both sources must not be treated as a holiday: the
        # saved index request ends Oct19, the holiday list explicitly covers all
        # subsequent civil days to the target. Never fill a calendar gap.
        index_ends = []
        for h in record['index_sources']:
            raw = vault.original(h)
            obj = json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)
            index_ends.append(dt.datetime.strptime(obj['params']['FID_INPUT_DATE_2'], '%Y%m%d').date())
        last = max(index_ends)
        holiday_dates = {r['bass_dt'] for r in calendar['holiday_response']['output']}
        while last < dt.date.fromisoformat(day):
            last += dt.timedelta(days=1)
            if last.strftime('%Y%m%d') not in holiday_dates:
                raise ValueError('official_calendar_extension_gap')
        sessions[market] = dates
    return sessions, evidence, calendar


def request_plan(commit, publication, index, sessions, calendar_evidence):
    existing = {(r['market'], r['date']) for r in index.sources}
    missing = [{'market': m, 'date': d} for m in sorted(sessions)
               for d in sessions[m][-40:] if (m, d) not in existing]
    return {'schema': 'official-40d-request-plan-v1', 'source_commit': commit,
            'asof': publication['asof'], 'publication': publication,
            'sessions': sessions, 'calendar_evidence': calendar_evidence,
            'existing_market_dates': [{'market': m, 'date': d} for m, d in sorted(existing)],
            'missing_market_dates': missing,
            'minimum_remaining_requests': len(missing), 'maximum_total_requests': 80,
            'batch_limit': 20, 'window_KST': '23:00 <= time or time < 09:00',
            'KIS_requests': 0, 'registration_authorized': False}


def flow_audit(snapshot, universe, flows, sessions, captured_at):
    """Independently replay original amount AND quantity; no new API."""
    source_paths = [REPO/'staging/kis_history_20261014.json.gz', REPO/'research/daily-replay.json.gz',
                    ROOT/'inputs/checkpoint-20261015.json.gz', ROOT/'inputs/checkpoint-20261016.json.gz',
                    FULL/'inputs/checkpoint-20261019.json.gz', FULL/'inputs/checkpoint-20261020.json.gz',
                    ROOT/'kis-price-version-study/inputs/checkpoint-20261021.json.gz']
    from krx40_checkpoint import captured_paths
    source_paths += captured_paths()
    index = {}; sources = {}
    for path in dict.fromkeys(source_paths):
        if not path.exists():
            continue
        h = hash_bytes(path.read_bytes()); sources[str(path.relative_to(REPO))] = h
        obj = load(path)
        for symbol, _, _, _ in universe:
            add_evidence(index, symbol, obj, {'file': str(path.relative_to(REPO)), 'sha256': h}, captured_at)
    con = duckdb.connect(str(snapshot/'dashboard.duckdb'), read_only=True)
    try:
        quantities = {(s, str(d)): q for s, d, q in con.execute('select symbol,trade_date,net_quantity from flows').fetchall()}
    finally:
        con.close()
    checked = {}; cache = {}; replay_blobs = {}
    for symbol, _, market, _ in universe:
        replay = select_flow_replay(index, symbol, sessions[market][-20:], flows.get(symbol, {}))
        verify_replay_sources(replay, REPO, cache)
        errors = list(replay['errors'])
        for row in replay['rows']:
            d = dt.datetime.strptime(row['stck_bsop_date'], '%Y%m%d').date().isoformat()
            quantity = row.get('frgn_ntby_qty')
            if quantity is None or quantities.get((symbol, d)) != int(quantity):
                errors.append({'date': d, 'reason': 'original_quantity_missing_or_snapshot_mismatch'})
        replay['errors'] = errors
        replay['status'] = 'replay_ready' if not errors and len(replay['rows']) == 20 else 'hold'
        h = digest(replay); replay_blobs[h] = replay
        checked[symbol] = {'status': replay['status'], 'replay_sha256': h,
                           'checked_days': len(replay['rows']), 'errors': errors}
    return checked, replay_blobs, sources


def classify_official(context, flow_check, flow_rows, extra_reasons, prefix_required=40):
    reasons = list(context['reasons'])+list(extra_reasons)
    if flow_check['status'] != 'replay_ready':
        reasons.append('original_20_day_amount_quantity_replay_hold')
    # Earlier swing state must not silently disappear when replacing 60–94
    # preserved sessions with exactly 40 official rows. Keep context as a gate.
    if len(context['bars']) < prefix_required:
        reasons.append('earlier_swing_context_not_yet_officially_validated')
    if reasons:
        return {'status': 'unclassifiable', 'group': None, 'reason': ';'.join(sorted(set(reasons)))}
    link = {**context, 'status': 'linked_history_validated'}
    return classify_link(link, flow_rows)  # unchanged research cohort/event rules


def prepare(commit):
    started = time.monotonic()
    snapshot, publication = freeze_main(commit)
    vault = Vault(OUT/'vault'); index, sources = preserved_index(vault)
    sources.update(main_references(commit, snapshot, index, vault))
    day = publication['asof']; sessions, evidence, calendar = calendars(day, vault)
    universe, prices, flows, db_sessions, excluded = snapshot_rows(snapshot)
    if len({r[0] for r in universe}) != len(universe):
        raise ValueError('duplicate_population')
    if any(db_sessions[m][-40:] != sessions[m][-40:] for m in sessions):
        raise ValueError('official_and_snapshot_calendar_disagreement')
    plan = request_plan(commit, publication, index, sessions, evidence)
    write_once(OUT/'plans'/(digest(plan)+'.json'), canonical(plan))
    baseline_path = FULL/'results/all-stocks.csv'
    with baseline_path.open(encoding='utf-8-sig') as f:
        baseline = {r['symbol'] for r in csv.DictReader(f)}
    explicit_path = ROOT/'conflict-diagnosis/explicit-adjustment-differences.csv'
    with explicit_path.open(encoding='utf-8-sig') as f:
        explicit = {r['symbol'] for r in csv.DictReader(f)}
    cp_path = ROOT/'kis-price-version-study/inputs/checkpoint-20261021.json.gz'
    if (snapshot/'checkpoint-source.json').exists():
        saved = load(snapshot/'checkpoint-source.json')
        cp_path = (REPO/saved['file']).resolve()
        if not cp_path.is_relative_to((OUT/'daily-checkpoints').resolve()) or hash_bytes(cp_path.read_bytes()) != saved['sha256']:
            raise ValueError('captured_checkpoint_hash_mismatch')
        sources[str((snapshot/'checkpoint-source.json').relative_to(REPO))] = hash_bytes((snapshot/'checkpoint-source.json').read_bytes())
    checkpoint = load(cp_path)
    # The independent current-checkpoint gate never silently uses Oct21 as a
    # newer date. A future plan without its matching original checkpoint holds.
    matching_checkpoint = checkpoint['trade_date'] == day.replace('-', '')
    checked, replay_blobs, flow_sources = flow_audit(snapshot, universe, flows, sessions,
                                                   load(snapshot/'source.json')['captured_at'])
    sources.update(flow_sources)
    for p in [baseline_path, explicit_path, cp_path]:
        sources[str(p.relative_to(REPO))] = hash_bytes(p.read_bytes())
    import pandas as pd
    prior = pd.read_parquet(FULL/'results/research-prices.parquet')
    prefix_starts = prior[prior.trade_date <= day].groupby('symbol').trade_date.min().to_dict()
    sources[str((FULL/'results/research-prices.parquet').relative_to(REPO))] = hash_bytes((FULL/'results/research-prices.parquet').read_bytes())
    coverage = []; classifications = []; conflicts = []; contexts = {}
    for symbol, name, market, listing in universe:
        context = official_context(index, symbol, market, day, sessions[market], str(listing) if listing else None, symbol in excluded)
        context['session_evidence'] = evidence[market]
        context['prefix_required_from_preserved_research'] = max(40, sum(d >= str(prefix_starts.get(symbol, day)) for d in sessions[market]))
        extra = []
        if symbol in explicit:
            extra.append('explicit_adjustment_51_requires_official_action_rule')
        if symbol == '117670':
            extra.append('alphachips_unresolved_price_action_basis')
        record = checkpoint['records'].get(symbol) if matching_checkpoint else None
        if not record:
            extra.append('matching_latest_original_checkpoint_missing')
        else:
            required = set(sessions[market][-40:])
            if any(action_flag(r) and dt.datetime.strptime(r['stck_bsop_date'], '%Y%m%d').date().isoformat() in required
                   for r in record.get('adjusted_prices', [])):
                extra.append('checkpoint_adjustment_flag_requires_review')
        # No mixed provider bars: snapshot disagreements stay separately visible.
        overlap = []
        for b in context['bars']:
            q = prices.get(symbol, {}).get(b['trade_date'])
            if q and any(q[k] != b[k] for k in ('close', 'volume', 'turnover')):
                overlap.append(b['trade_date'])
        if overlap:
            extra.append('operating_snapshot_official_price_overlap_conflict')
        result = classify_official(context, checked[symbol], flows.get(symbol, {}), extra,
                                   context['prefix_required_from_preserved_research'])
        h = digest(context); contexts[h] = context
        invalid = [r['reason'] for r in context['missing']]
        status = 'official_40_validated'
        if not context['coverage_complete']:
            status = 'official_price_conflict' if 'official_version_conflict' in invalid else 'official_history_incomplete_or_untradable'
        elif extra:
            status = 'official_40_acquired_validation_hold'
        cov = {'asof': day, 'symbol': symbol, 'name': name, 'market': market,
               'baseline_2429_member': symbol in baseline, 'status': status,
               'official_days': sum(b['trade_date'] in context['validated_window'] for b in context['bars']),
               'official_40_complete': context['coverage_complete'],
               'price_validation_pass': context['coverage_complete'] and not extra,
               'adjustment_hold': symbol in explicit, 'alphachips_hold': symbol == '117670',
               'original_flow_status': checked[symbol]['status'],
               'missing_dates': ';'.join(r['date'] for r in context['missing']),
               'reasons': ';'.join(sorted(set(context['reasons']+extra))),
               'context_sha256': h, 'flow_replay_sha256': checked[symbol]['replay_sha256'],
               'snapshot_conflicting_dates': ';'.join(overlap)}
        coverage.append(cov)
        classifications.append({'asof': day, 'symbol': symbol, 'name': name, 'market': market,
                                'status': result['status'], 'group': result['group'],
                                'reason': result['reason'], 'context_sha256': h,
                                'structure': json.dumps(result.get('structure'), ensure_ascii=False),
                                'rise_pct': result.get('rise_pct'), 'net_5d_krw': result.get('net_5d_krw'),
                                'previous_5d_krw': result.get('previous_5d_krw'),
                                'change_5d_krw': result.get('change_5d_krw'),
                                'net_20d_krw': result.get('net_20d_krw'),
                                'record_kind': 'research_preparation_only', 'registered': False})
        if extra or any('conflict' in x for x in invalid):
            conflicts.append(cov)
    next_day, _ = official_next_session(day, calendar, utcnow())
    gate, gate_reason = decision_gate(day, sessions['KOSPI']+[next_day],
                                     [publication['known_at']], publication['known_at'], utcnow().isoformat())
    groups = collections.Counter(r['group'] for r in classifications if r['group'])
    stats = {'asof': day, 'population': len(universe), 'baseline_population': len(baseline),
             'added_symbols': sorted({r[0] for r in universe}-baseline),
             'absent_baseline_symbols': sorted(baseline-{r[0] for r in universe}),
             'official_market_dates': sorted({s['date'] for s in index.sources}),
             'official_days_distribution': dict(collections.Counter(r['official_days'] for r in coverage)),
             'official_40_complete': sum(r['official_40_complete'] for r in coverage),
             'price_validation_pass': sum(r['price_validation_pass'] for r in coverage),
             'known_adjustment_holds': sum(r['adjustment_hold'] for r in coverage),
             'known_alphachips_hold': sum(r['alphachips_hold'] for r in coverage),
             'original_20day_flow_pass': sum(v['status'] == 'replay_ready' for v in checked.values()),
             'original_flow_hold': sum(v['status'] != 'replay_ready' for v in checked.values()),
             'groups': {g: groups[g] for g in 'ABCD'},
             'condition_not_met': sum(r['status'] == 'condition_not_met' for r in classifications),
             'unclassifiable': sum(r['status'] == 'unclassifiable' for r in classifications),
             'no_new_event': sum(r['status'] == 'no_new_event' for r in classifications),
             'next_official_session': next_day, 'timing_gate': gate, 'timing_reason': gate_reason,
             'minimum_missing_requests': len(plan['missing_market_dates']),
             'market_API_requests': Journal(OUT/'collection').summary()['confirmed_physical_requests'],
             'registration_ready': 0, 'registered_signals': 0,
             'registration_readiness_status': 'blocked_missing_official_context_or_deadline',
             'source_commit': commit, 'publication': publication,
             'plan_sha256': digest(plan), 'source_sha256': sources}
    # Do not claim a complete future adapter from this incomplete offline run.
    # Existing prepare_snapshot/build_signal must be executed on validated
    # bundles after complete context and all existing acceptance gates pass.
    if any(r['group'] for r in classifications):
        stats['registration_readiness_status'] = 'requires_existing_prepare_snapshot_bundle_acceptance'
    assert len(coverage) == len(classifications) == len({r['symbol'] for r in coverage})
    assert sum(groups.values())+stats['condition_not_met']+stats['unclassifiable']+stats['no_new_event'] == len(universe)
    run_id = digest(stats); directory = OUT/'runs'/run_id
    write_once(directory/'SUMMARY.json', canonical(stats))
    save_csv(directory/'OFFICIAL_40D_COVERAGE.csv', coverage)
    save_csv(directory/'ALL_STOCKS_CLASSIFICATION.csv', classifications)
    save_csv(directory/'PRICE_CONFLICTS.csv', conflicts, list(coverage[0]))
    save_csv(directory/'RESEARCH_CANDIDATES.csv', [r for r in classifications if r['group']], list(classifications[0]))
    write_once(directory/'FLOW_REPLAY.json.gz', gzip.compress(canonical({'checks': checked, 'replays': replay_blobs}), mtime=0))
    write_once(directory/'CONTEXTS.json.gz', gzip.compress(canonical(contexts), mtime=0))
    write_once(directory/'API_EXECUTION.json', canonical(Journal(OUT/'collection').summary()))
    validate_publication(snapshot)
    if any(hash_bytes((REPO/p).read_bytes()) != h for p, h in sources.items()):
        raise ValueError('preserved_input_changed')
    execution = {'run_id': run_id, 'directory': str(directory.relative_to(REPO)),
                 'finished_at': utcnow().isoformat(), 'elapsed_seconds': round(time.monotonic()-started, 3),
                 'plan': str((OUT/'plans'/(digest(plan)+'.json')).relative_to(REPO))}
    write_once(OUT/'executions'/(digest(execution)+'.json'), canonical(execution))
    return execution, stats


def prepare_latest():
    """Fresh normal publication + matching saved source; no market calls."""
    from krx40_checkpoint import capture_latest
    subprocess.run(['git', 'fetch', 'origin', 'main'], cwd=REPO, check=True)
    commit = git('rev-parse', 'origin/main')
    snapshot, publication = freeze_main(commit)
    universe = snapshot_rows(snapshot)[0]
    capture_latest(snapshot, {r[0] for r in universe})
    return prepare(commit)


def execute_batch(plan_path, retry_failed=False):
    """The same path used by the CLI and the manual Actions entry point."""
    require_window(utcnow())
    plan_path = Path(plan_path)
    if not plan_path.resolve().is_relative_to((OUT/'plans').resolve()):
        raise ValueError('pinned_research_plan_required')
    plan = load(plan_path)
    if plan_path.stem != digest(plan):
        raise ValueError('plan_hash_mismatch')
    github_preflight(plan)
    _, current = prepare(plan['source_commit'])
    if current['plan_sha256'] != digest(plan):
        raise ValueError('stale_plan_refresh_required')
    import os
    return collect_batch(plan, OUT/'collection', os.environ.get('KRX_API_KEY'),
                         lambda: github_preflight(plan), persist_research, retry_failed=retry_failed)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'collect-batch'])
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    if git('branch', '--show-current') != BRANCH:
        raise ValueError('research_branch_required')
    if args.mode == 'prepare':
        run, stats = prepare_latest()
        print(json.dumps({'execution': run, 'summary': {k: v for k, v in stats.items() if k not in ('source_sha256', 'publication')}}, ensure_ascii=False))
    else:
        if not args.plan:
            require_window(utcnow())
            raise ValueError('pinned_research_plan_required')
        result = execute_batch(args.plan, args.retry_failed)
        print(json.dumps({'batch_requests': result['batch_requests'], 'remaining_budget': result['remaining_budget']}))
        recent = result['requests'][-result['batch_requests']:] if result['batch_requests'] else []
        if any(r.get('status') != 'validated' for r in recent):
            raise SystemExit(1)



if __name__ == '__main__':
    main()
