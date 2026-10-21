"""Offline research reconciliation; never modifies a publication or registers signals.

Separate observed-version drift from corporate-action basis changes. Original
API fields are replayed, not synthesized from the normalized operating database.
"""
from copy import deepcopy
from pathlib import Path
import datetime as dt
import gzip
import hashlib
import json
import tarfile

from engine import digest, instant
from forward_system import FIELDS, KIS_FIELDS, KRX_FIELDS, load, sha
from price_link import six, valid, action_flag

FLOW_ENDPOINT = 'KIS FHPTJ04160001 / J / million KRW to KRW'


def flow_rows(obj, symbol, captured_at):
    """Return original rows and a conservative knowledge time, or no evidence.

    Legacy history exports retain a projection of the response, not full HTTP
    bodies. Their missing per-response timestamps are NOT inferred from asof:
    captured_at is an upper bound at this offline inspection, preventing PIT use.
    """
    instant(captured_at)
    if obj.get('exchange') != 'KRX':
        return [], None, 'wrong_or_missing_exchange'
    records = obj.get('records')
    if isinstance(records, dict):
        r = records.get(symbol)
        if not r:
            return [], None, 'symbol_missing'
        if obj.get('mode') == 'history':
            # Two preserved exporter schemas: pilot history_metric/request date,
            # full-run metric/received_at_utc (the retained exporter pins Oct13).
            # This is the contract in .github/scripts/kis_enrich_collect.py.
            metric = r.get('history_metric') or r.get('metric') or {}
            if (r.get('symbol') != symbol or r.get('exchange', obj['exchange']) != 'KRX'
                or not obj.get('run_id') or metric.get('http') != 200
                or metric.get('code') != 'MCA00000'):
                return [], None, 'history_identity_or_response_invalid'
            rows = r.get('rows', [])
            end = r.get('history_request_date')
            if not end and r.get('metric') and r.get('received_at_utc'):
                if obj.get('analysis_date') != '20261014':
                    return [], None, 'unknown_history_export_contract'
                end = '20261013'
            if not end or any(x.get('stck_bsop_date', '9') > end for x in rows):
                return [], None, 'history_request_date_mismatch'
            known = r.get('received_at_utc') or captured_at
            kind = 'saved_response_field_projection' if r.get('received_at_utc') else 'saved_response_field_projection_timestamp_upper_bound'
        elif 'flow' in r:
            rows = [r['flow']]
            if rows[0].get('stck_bsop_date') != obj.get('trade_date'):
                return [], None, 'checkpoint_date_mismatch'
            known = r.get('observed_at') or obj.get('updated_at')
            kind = 'checkpoint_original_fields'
        else:
            return [], None, 'unsupported_records'
    elif obj.get('symbol') == symbol:
        e = obj.get('daily_acceptance', {})
        if e.get('flow_endpoint') != FLOW_ENDPOINT:
            return [], None, 'original_flow_endpoint_missing'
        rows = e.get('raw_flow_rows', [])
        known = e.get('observed_at') or obj.get('observed_at')
        kind = 'archived_original_flow_rows'
    else:
        return [], None, 'identity_missing'
    if not known:
        return [], None, 'observation_time_missing'
    instant(known)
    dates = []
    for row in rows:
        d = dt.datetime.strptime(row['stck_bsop_date'], '%Y%m%d').date()
        int(row['frgn_ntby_tr_pbmn'])
        if d > instant(known).date():
            return [], None, 'flow_from_future'
        dates.append(d)
    if len(dates) != len(set(dates)):
        return [], None, 'duplicate_dates'
    return rows, known, kind


def add_evidence(index, symbol, obj, identity, captured_at):
    rows, known, kind = flow_rows(obj, symbol, captured_at)
    for row in rows:
        d = dt.datetime.strptime(row['stck_bsop_date'], '%Y%m%d').date().isoformat()
        amount = int(row['frgn_ntby_tr_pbmn']) * 1_000_000
        item = {'row': deepcopy(row), 'source': {**deepcopy(identity), 'observed_at': known,
                'captured_at': captured_at, 'representation': kind}}
        versions = index.setdefault(symbol, {}).setdefault(d, {}).setdefault(amount, {})
        versions.setdefault(digest(row), item)
    return len(rows), kind


def select_flow_replay(index, symbol, dates, normalized_flows):
    """Do not choose a DB-matching observation when another amount conflicts."""
    rows, sources, errors = [], {}, []
    for day in dates:
        versions = index.get(symbol, {}).get(day, {})
        if not versions:
            errors.append({'date': day, 'reason': 'original_fields_missing'})
            continue
        if len(versions) != 1:
            errors.append({'date': day, 'reason': 'original_amount_versions_conflict'})
            continue
        amount = next(iter(versions))
        if normalized_flows.get(day) != (amount, 'KRX'):
            errors.append({'date': day, 'reason': 'snapshot_amount_or_market_mismatch'})
            continue
        # Prefer a preserved row containing quantity too, but never select one
        # of conflicting quantity versions simply because it matches the DB.
        candidates = versions[amount]
        quantities = {int(v['row']['frgn_ntby_qty']) for v in candidates.values() if 'frgn_ntby_qty' in v['row']}
        if len(quantities) > 1:
            errors.append({'date': day, 'reason': 'original_quantity_versions_conflict'})
            continue
        chosen = min(candidates, key=lambda h: ('frgn_ntby_qty' not in candidates[h]['row'], -len(candidates[h]['row']), h))
        item = candidates[chosen]
        rows.append(deepcopy(item['row'])); sources[digest(item['source'])] = deepcopy(item['source'])
    return {'schema': 'research-raw-flow-replay-v2', 'symbol': symbol, 'asof': dates[-1], 'rows': rows,
            'sources': list(sources.values()), 'errors': errors,
            'status': 'replay_ready' if len(dates) == 20 and not errors else 'hold'}


def verify_replay_sources(replay, repo, cache=None):
    """Reopen hash-checked files/members and verify every row verbatim."""
    cache = {} if cache is None else cache
    allowed = set()
    repo = Path(repo).resolve()
    for source in replay['sources']:
        path = (repo / source['file']).resolve()
        if not path.is_relative_to(repo):
            raise ValueError('source_outside_repository')
        filekey = ('file', str(path))
        if filekey not in cache:
            cache[filekey] = sha(path)
        if cache[filekey] != source['sha256']:
            raise ValueError('source_hash_mismatch')
        key = (str(path), source.get('member'))
        if key not in cache:
            if source.get('member'):
                # Cache archive members in one sequential pass, not N decompressions.
                with tarfile.open(path) as tf:
                    for m in tf:
                        if m.isfile() and m.name.endswith(('.json', '.json.gz')):
                            data = tf.extractfile(m).read()
                            # Retain compressed bytes, not every decoded price
                            # history in this archive (several GB for 227 targets).
                            cache[(str(path), m.name)] = (hashlib.sha256(data).hexdigest(), data)
            else:
                cache[key] = (cache[filekey], load(path))
        h, obj = cache[key]
        if source.get('member') and h != source['member_sha256']:
            raise ValueError('member_hash_mismatch')
        if isinstance(obj, bytes):
            obj = json.loads(gzip.decompress(obj) if obj[:2] == b'\x1f\x8b' else obj)
        rows, known, kind = flow_rows(obj, replay['symbol'], source['captured_at'])
        if not rows or kind != source['representation'] or instant(source['observed_at']) < instant(known):
            raise ValueError('source_identity_or_time_mismatch')
        allowed.update(digest(r) for r in rows)
    if any(digest(r) not in allowed for r in replay['rows']):
        raise ValueError('row_not_in_original_source')


def official_rows(envelope, market, day):
    url = ('https://data-dbg.krx.co.kr/svc/apis/sto/'
           + {'KOSPI': 'stk', 'KOSDAQ': 'ksq'}[market] + '_bydd_trd?basDd=' + day.replace('-', ''))
    if envelope.get('url') != url or envelope.get('http') != 200:
        raise ValueError('official_scope_or_response_invalid')
    rows = envelope.get('rows', [])
    from screener.liquidity_quality import digest as evidence_digest
    if not rows or envelope.get('rows_sha256') != evidence_digest(rows):
        raise ValueError('official_rows_hash_mismatch')
    instant(envelope['observed_at'])
    if any(r.get('BAS_DD') != day.replace('-', '') or r.get('MKT_NM') != market for r in rows):
        raise ValueError('official_date_or_market_mismatch')
    if len({r['ISU_CD'] for r in rows}) != len(rows):
        raise ValueError('official_duplicate_symbol')
    return {r['ISU_CD']: dict(zip(FIELDS, (float(r[k]) for k in KRX_FIELDS))) for r in rows}


def research_snapshot_view(link, snapshot_prices, checkpoint, references, snapshot_sha256):
    """Exact-date research overlay for stale historical volume/turnover only.

    No price-series rewriting, no operating DB mutation, no PIT/signal approval.
    Require: already valid 40-day link, unchanged adjusted close, original dated
    checkpoint equals preserved DB volume/turnover, and ALL SIX candidate fields
    agree with every supplied official observation. Any missing/ambiguous proof
    retains the original comparison and a hold. No partial passing result.
    """
    view = deepcopy(snapshot_prices); audit = []; unresolved = []
    if link.get('schema') != 'research-linked-40-v2' or link.get('status') != 'linked_history_validated':
        raise ValueError('validated_link_required')
    for b in link['bars'][-40:]:
        day = b['trade_date']; q = snapshot_prices.get(day)
        if not q or (q['adjusted_close'] == b['close'] and all(q[k] == b[k] for k in ['volume', 'turnover'])):
            continue
        reason = None
        observed = checkpoint.get(day, {}).get('records', {}).get(link['symbol'], {})
        f = observed.get('flow', {})
        if day >= link['asof'] or q['adjusted_close'] != b['close']:
            reason = 'not_historical_volume_turnover_only'
        elif (checkpoint.get(day, {}).get('exchange') != 'KRX' or
              checkpoint.get(day, {}).get('trade_date') != day.replace('-', '') or
              f.get('stck_bsop_date') != day.replace('-', '') or
              float(f.get('stck_clpr', -1)) != q['close'] or
              float(f.get('acml_vol', -1)) != q['volume'] or
              float(f.get('acml_tr_pbmn', -1)) != q['turnover']):
            reason = 'original_snapshot_checkpoint_not_reproduced'
        proof = []
        for ref in references.get(day, []):
            official = official_rows(ref, link['market'], day)
            if link['symbol'] not in official or six(official[link['symbol']]) != six(b):
                reason = 'official_six_field_disagreement'
            proof.append({'response_sha256': digest(ref), 'url': ref['url'], 'observed_at': ref['observed_at']})
        if not proof:
            reason = 'exact_date_official_evidence_missing'
        if reason:
            unresolved.append({'date': day, 'reason': reason}); continue
        audit.append({'date': day, 'symbol': link['symbol'], 'snapshot_sha256': snapshot_sha256,
                      'original': deepcopy(q), 'candidate': deepcopy(b), 'official_proofs': proof,
                      'checkpoint_observed_at': observed['observed_at'],
                      'reason': 'historical_volume_turnover_versions_officially_reconciled'})
        view[day] = {**q, 'volume': b['volume'], 'turnover': b['turnover']}
    return {'status': 'research_comparison_passed' if not unresolved else 'hold',
            'prices': view if not unresolved else deepcopy(snapshot_prices),
            'audit': audit, 'unresolved': unresolved,
            'prospective_signal_eligible': False, 'provider_finality': 'UNVERIFIED'}
