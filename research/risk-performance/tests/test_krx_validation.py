"""Synthetic evidence is isolated to tmp_path; preserved inputs are read-only."""
import copy
import csv
import datetime as dt
import gzip
import json
from pathlib import Path

import pytest

import krx_validation as k
from forward_system import Store, load
from engine import digest

ROOT = Path(__file__).resolve().parents[1]


def bar():
    return dict(zip(k.FIELDS, [100, 110, 90, 105, 10, 1000]))


def reference(day='2026-10-20', symbol='001234', values=None, known='2026-10-20T12:00:00+00:00'):
    values = values or bar()
    row = {'BAS_DD': day.replace('-', ''), 'ISU_CD': symbol, 'MKT_NM': 'KOSPI',
           **{key: str(values[field]) for key, field in zip(k.KRX_FIELDS, k.FIELDS)}}
    rows = [row]
    return {'http': 200, 'url': 'https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd='+day.replace('-', ''),
            'observed_at': known, 'rows': rows, 'rows_sha256': k.rows_digest(rows)}


def observation():
    return {'symbol': '001234', 'trade_date': '2026-10-20', 'bar': bar(), 'action_flag': False,
            'exchange': 'KRX', 'market': 'KOSPI', 'basis': 'adjusted_0'}


@pytest.mark.parametrize('field', k.FIELDS)
def test_every_one_of_six_fields_is_required(field):
    index = k.OfficialIndex(); index.add(reference(), 'KOSPI', '2026-10-20', 'a'*64)
    good = observation()
    assert k.assess(good, 'KOSPI', index)['status'] == 'six_field_pass'
    changed = copy.deepcopy(good); changed['bar'][field] += 1
    result = k.assess(changed, 'KOSPI', index)
    assert result['status'] == 'hold' and result['differences'][field] == 1
    assert not result['signal_eligible']


@pytest.mark.parametrize('bad', [None, 'NaN', 'Infinity', '1.5'])
def test_no_missing_to_zero_or_float_tolerance(bad):
    row = bar(); row['turnover'] = bad
    with pytest.raises(ValueError): k.six(row)


@pytest.mark.parametrize('bad', ['date', 'market', 'duplicate', 'hash', 'http', 'timezone'])
def test_official_scope_and_integrity(bad):
    env = reference()
    if bad == 'date': env['rows'][0]['BAS_DD'] = '20261019'
    if bad == 'market': env['rows'][0]['MKT_NM'] = 'KOSDAQ'
    if bad == 'duplicate': env['rows'] *= 2
    if bad == 'http': env['http'] = 403
    if bad == 'timezone': env['observed_at'] = '2026-10-20T12:00:00'
    env['rows_sha256'] = k.rows_digest(env['rows'])
    if bad == 'hash': env['rows_sha256'] = 'wrong'
    with pytest.raises(ValueError): k.OfficialIndex().add(env, 'KOSPI', '2026-10-20', 'a'*64)


def test_legitimate_alphanumeric_codes_do_not_reject_whole_market():
    index = k.OfficialIndex(); index.add(reference(symbol='00104K'), 'KOSPI', '2026-10-20', 'a'*64)
    assert index.resolve('KOSPI', '00104K', '2026-10-20')[0] == bar()


def test_official_conflict_never_last_write_wins():
    index = k.OfficialIndex()
    index.add(reference(), 'KOSPI', '2026-10-20', 'a'*64)
    index.add(reference(values={**bar(), 'close': 104}, known='2026-10-21T00:00:00+00:00'), 'KOSPI', '2026-10-20', 'b'*64)
    assert index.resolve('KOSPI', '001234', '2026-10-20')[1] == 'official_version_conflict'
    # An explicit historical evidence cutoff cannot see the later revision.
    assert index.resolve('KOSPI', '001234', '2026-10-20', '2026-10-20T13:00:00+00:00')[0] == bar()


def test_vault_never_replaces_original_and_detects_tampering(tmp_path):
    vault = k.Vault(tmp_path)
    first = vault.capture_bytes(b'first'); later = vault.capture_bytes(b'later')
    assert first != later and vault.original(first) == b'first'
    assert vault.capture_bytes(b'first') == first
    with pytest.raises(ValueError): k.write_once(tmp_path/'originals'/first, b'later')
    (tmp_path/'originals'/first).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash_mismatch'): vault.original(first)


def test_partial_40_days_not_filled_from_other_sources():
    sessions = [str(dt.date(2026, 8, 1)+dt.timedelta(days=i)) for i in range(40)]  # TEST ONLY calendar
    index = k.OfficialIndex()
    for d in sessions[-3:]: index.add(reference(d), 'KOSPI', d, 'a'*64)
    result = k.official_context(index, '001234', 'KOSPI', sessions[-1], sessions)
    assert len(result['bars']) == 3 and len(result['missing']) == 37
    assert not result['coverage_complete'] and not result['signal_eligible']


def test_complete_context_carries_whole_bars_and_prefix(tmp_path):
    sessions = [str(dt.date(2026, 8, 1)+dt.timedelta(days=i)) for i in range(42)]  # TEST ONLY
    index = k.OfficialIndex(k.Vault(tmp_path))
    for d in sessions: index.add(reference(d), 'KOSPI', d, 'a'*64)
    context = k.official_context(index, '001234', 'KOSPI', sessions[-1], sessions)
    assert context['coverage_complete'] and len(context['bars']) == 42
    assert not context['signal_eligible']
    k.verify_context_sources(context, index.vault)
    context['evidence'][sessions[0]][0]['observed_at'] = '2026-01-01T00:00:00+00:00'
    with pytest.raises(ValueError, match='does_not_match_original'): k.verify_context_sources(context, index.vault)


def test_actions_require_cited_rules_and_no_automatic_release():
    document = b'TEST ONLY explicit rule: prices half, volume double, turnover unchanged.'
    raw = {**bar(), 'close': 104}
    adjusted = dict(zip(k.FIELDS, [50, 55, 45, 52, 20, 1000]))
    rule = {'source_url': 'https://kind.krx.co.kr/test-only', 'document_sha256': k.hash_bytes(document),
            'quoted_rule': document.decode(), 'rounding': 'half_up',
            'multipliers': {**{f: [1,2] for f in k.FIELDS[:4]}, 'volume': [2,1], 'turnover': [1,1]}}
    result = k.review_adjustment(raw, adjusted, rule, document)
    assert result['arithmetic_matches'] and not result['automatic_release']
    rule['multipliers']['close'] = [1,1]
    assert not k.review_adjustment(raw, adjusted, rule, document)['arithmetic_matches']
    rule['document_sha256'] = 'wrong'
    assert 'action_document_hash_mismatch' in k.review_adjustment(raw, adjusted, rule, document)['reasons']


def test_matching_prices_do_not_release_pending_action():
    index = k.OfficialIndex(); index.add(reference(), 'KOSPI', '2026-10-20', 'a'*64)
    result = k.assess(observation(), 'KOSPI', index, True)
    assert result['six_field_equal'] and result['status'] == 'hold'


def test_failure_does_not_touch_existing_signals(tmp_path, monkeypatch):
    registry = Store(tmp_path/'registry')
    path = registry.put('signals', 'a'*64, {'original_signal': True})
    before = path.read_bytes()
    def broken(_): raise ValueError('publication_hash_mismatch')
    monkeypatch.setattr(k, 'validate_publication', broken)
    result = k.safe_publication_check(tmp_path/'bad', k.Vault(tmp_path/'errors'))
    assert result['status'] == 'hold' and not result['signals_touched']
    assert path.read_bytes() == before and len(list((tmp_path/'errors/errors').glob('*.json'))) == 1


def test_database_exception_is_also_isolated(tmp_path, monkeypatch):
    import duckdb
    def broken(_): raise duckdb.IOException('test broken database')
    monkeypatch.setattr(k, 'validate_publication', broken)
    assert k.safe_publication_check(tmp_path, k.Vault(tmp_path/'errors'))['status'] == 'hold'


def test_oct22_manual_validation_cannot_run_through_historical_cli(tmp_path, monkeypatch):
    import krx_validation_trial as trial
    (tmp_path/'manifest.json').write_text(json.dumps({'asof':'2026-10-22'}))
    monkeypatch.setattr(trial, 'SNAP', tmp_path)
    with pytest.raises(ValueError, match='not_oct22_manual_trial'): trial.run()


@pytest.fixture
def signal_inputs(tmp_path):
    # Reuse a real archived bundle but ALL official/calendar wrappers below are
    # synthetic tests. None are written into research results or live registries.
    with (ROOT/'signals.csv').open(encoding='utf-8-sig') as f:
        row = next(r for r in csv.DictReader(f) if r['record_kind']=='historical_partial_universe' and r['group']=='A')
    b = load(ROOT/row['source'])
    vault = k.Vault(tmp_path/'evidence'); index = k.OfficialIndex(vault)
    for r in b['bars']:
        env = reference(r['trade_date'], b['symbol'], k.six(r), '2026-10-15T23:40:03+00:00')
        index.add(env, b['market'], r['trade_date'], 'a'*64)
    sessions = [r['trade_date'] for r in b['bars']]
    context = k.official_context(index, b['symbol'], b['market'], b['asof'], sessions)
    args = dict(context=context, bundle=b,
                publication={'asof':b['asof'], 'sha256':'test-only', 'dataset_id':'test-only', 'known_at':'2026-10-15T23:40:03+00:00'},
                prices={r['trade_date']:{**r, 'adjusted_close':r['close']} for r in b['bars']},
                flows={r['trade_date']:(r['net_amount'],'KRX') for r in b['flows']},
                sessions=sessions, vault=vault,
                calendar={'holiday_response':{'endpoint':'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/chk-holiday',
                  'tr_id':'CTCA0903R','rt_cd':'0','observed_at':'2026-10-15T12:00:00+00:00',
                  'output':[{'bass_dt':'20261016','opnd_yn':'Y','tr_day_yn':'Y'}]}},
                now=dt.datetime.fromisoformat('2026-10-15T23:50:00+00:00'))
    index_source = {'market':b['market'], 'observed_at':'2026-10-15T23:40:03+00:00',
                    'params':{'FID_COND_MRKT_DIV_CODE':'U','FID_INPUT_ISCD':'0001','FID_PERIOD_DIV_CODE':'D',
                              'FID_INPUT_DATE_1':sessions[0].replace('-',''), 'FID_INPUT_DATE_2':sessions[-1].replace('-','')},
                    'response':{'rt_cd':'0','output2':[{'stck_bsop_date':d.replace('-','')} for d in sessions]}}
    context['session_evidence'] = {'index_sources':[vault.capture_bytes(k.canonical(index_source))],
                                   'holiday_source':vault.capture_bytes(k.canonical(args['calendar']))}
    return args


def test_registration_preparation_reuses_existing_cohort_without_writing(signal_inputs):
    result = k.prepare_signal(**signal_inputs)
    assert result['candidate']['group']=='A' and not result['registered']
    assert result['candidate']['record_kind']=='prospective_preparation' and not result['candidate']['pit_eligible']
    assert result['candidate']['L2_price']==155300
    assert not list(signal_inputs['vault'].root.glob('signals/*'))


def test_late_registration_blocked_even_if_all_prices_match(signal_inputs):
    signal_inputs['now'] = dt.datetime.fromisoformat('2026-10-16T00:00:00+00:00')
    with pytest.raises(ValueError, match='freeze_outside'): k.prepare_signal(**signal_inputs)


def test_new_official_version_time_cannot_be_backdated(signal_inputs):
    signal_inputs['context']['evidence'][signal_inputs['sessions'][-1]][0]['observed_at'] = '2026-10-16T00:01:00+00:00'
    with pytest.raises(ValueError, match='does_not_match_original'): k.prepare_signal(**signal_inputs)


def test_partial_or_mixed_bars_never_create_signal(signal_inputs):
    signal_inputs['bundle']['bars'][-2]['volume'] += 1
    with pytest.raises(ValueError, match='whole_bar'): k.prepare_signal(**signal_inputs)
    signal_inputs['context']['coverage_complete'] = False
    with pytest.raises(ValueError, match='not_ready'): k.prepare_signal(**signal_inputs)


def test_preparation_failure_does_not_open_or_change_registry(tmp_path, monkeypatch):
    def broken(_): raise ValueError('publication_hash_mismatch')
    monkeypatch.setattr(k, 'validate_publication', broken)
    result = k.prepare_snapshot(tmp_path, {}, {}, {}, k.Vault(tmp_path/'errors'))
    assert result['status'] == 'hold' and not result['signals_touched']
    assert not list(tmp_path.rglob('signals'))


def test_zero_candidates_distinct_from_unclassifiable(tmp_path, monkeypatch):
    pub = {'records': 2, 'asof': '2026-10-20'}
    monkeypatch.setattr(k, 'validate_publication', lambda _:pub)
    monkeypatch.setattr(k, 'snapshot_rows', lambda _:(
        [('001234','TEST1','KOSPI',None),('005678','TEST2','KOSPI',None)], {}, {}, {'KOSPI':[]}, set()))
    def no_candidate(*args, **kwargs): raise ValueError('flow_condition_not_met')
    monkeypatch.setattr(k, 'prepare_signal', no_candidate)
    result = k.prepare_snapshot(tmp_path, {'001234':{}}, {'001234':{}}, {}, k.Vault(tmp_path/'records'))
    assert [r['status'] for r in result['results']] == ['condition_not_met', 'hold']
    assert not result['registered']


def test_market_scope_not_assumed_from_close_match():
    index = k.OfficialIndex(); index.add(reference(), 'KOSPI', '2026-10-20', 'a'*64)
    obs = observation(); obs['exchange']='NXT'
    with pytest.raises(ValueError, match='market_or_basis'): k.assess(obs, 'KOSPI', index)


def test_only_absent_later_record_can_reuse_verified_whole_first_bar():
    first = {'assessment': {'status':'six_field_pass'}, 'observation': {'bar': bar()}}
    later = {'assessment': {'status':'hold'}}
    selected, version = k.select_preserved_version(first, later, 'checkpoint_symbol_absent')
    assert selected is first and version == 'first_exact_official_match_reused'
    for bad in ['checkpoint_fields_missing', 'price_observation_conflict', 'corporate_action_or_adjustment_review_required']:
        assert k.select_preserved_version(first, later, bad)[0] is later


def test_first_action_hold_or_conflict_never_reused():
    first = {'assessment': {'status':'hold'}}; later = {'assessment': {'status':'hold'}}
    assert k.select_preserved_version(first, later, 'checkpoint_symbol_absent')[0] is later


def test_unproven_historical_calendar_blocks_preparation(signal_inputs):
    signal_inputs['context'].pop('session_evidence')
    with pytest.raises(ValueError, match='calendar_sources_missing'): k.prepare_signal(**signal_inputs)


def test_official_calendar_change_is_not_silently_applied(signal_inputs):
    signal_inputs['calendar']['holiday_response']['output'][0]['opnd_yn']='N'
    with pytest.raises(ValueError, match='calendar_not_reproduced'): k.prepare_signal(**signal_inputs)
