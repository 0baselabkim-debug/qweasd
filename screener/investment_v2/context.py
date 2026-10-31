"""Local, hash-bound financial/macro/event adapters. No external requests.

Validation_status is an upstream assertion, not proof of provider authenticity.
The local original JSON, exact value, publication and observation are checked.
"""
import datetime as dt
import json
from pathlib import Path
from fractions import Fraction

from screener.decision_support.evidence import encoded, sha, timestamp
from .contracts import number, symbol

KINDS = {'financial', 'macro', 'event', 'industry'}
EVENT_TYPES = {'earnings_schedule', 'earnings_result', 'supply_contract', 'order_backlog',
               'contract_delay', 'contract_cancellation', 'filing', 'news', 'policy', 'regulation',
               'industry_change', 'macro_release', 'scheduled_event'}


def pointer(obj, path):
    if not isinstance(path, list):
        raise ValueError('explicit_source_pointer_required')
    for key in path:
        obj = obj[key]
    return obj


def load_bundle(path, asof, cutoff):
    if path is None:
        return dict(status='NOT_RUN', facts=[], held=[], sources={}, input_files={}, reason='no_external_context_bundle')
    path = Path(path).resolve()
    raw = path.read_bytes()
    doc = json.loads(raw)
    if doc.get('schema_version') != 'investment-context-v1' or doc.get('asof') != asof:
        raise ValueError('context_schema_or_asof_mismatch')
    if timestamp(doc['generated_at']) > timestamp(cutoff):
        raise ValueError('context_generation_after_cutoff')
    originals, sources, files = {}, {}, {str(path): sha(raw)}
    for sid, ref in doc.get('sources', {}).items():
        p = path.parent / ref['file']
        if p.is_symlink() or not p.resolve().is_relative_to(path.parent) or p.resolve() == path:
            raise ValueError('unsafe_context_original_path')
        data = p.read_bytes()
        if sha(data) != ref['sha256']:
            raise ValueError('context_original_hash_mismatch')
        originals[sid] = json.loads(data)
        sources[sid] = ref
        files[str(p.resolve())] = sha(data)
    facts, held, seen = [], [], set()
    for item in doc.get('items', []):
        if not isinstance(item.get('id'), str) or not item['id'] or item['id'] in seen:
            raise ValueError('context_duplicate_or_missing_id')
        seen.add(item['id'])
        if item.get('kind') not in KINDS or not item.get('unit') or not item.get('metric'):
            raise ValueError('context_kind_metric_unit_required')
        if item.get('stock_code') is not None:
            symbol(item['stock_code'])
        sid = item['source_id']
        if sid not in sources or encoded(pointer(originals[sid], item['pointer'])) != encoded(item['value']):
            raise ValueError('context_claim_not_equal_to_original')
        if type(item['value']) in (int, float):
            number(item['value'])
        if item['kind'] == 'financial' and not all(item.get(k) for k in ('period_start', 'period_end', 'accounting_scope', 'currency')):
            raise ValueError('financial_period_scope_currency_required')
        if item['kind'] == 'financial':
            if item['accounting_scope'] not in ('CFS', 'OFS') or item['period_start'] > item['period_end'] or item['period_end'] > asof:
                raise ValueError('financial_basis_or_period_invalid')
        if item['kind'] == 'event':
            if item.get('event_type') not in EVENT_TYPES:
                raise ValueError('unknown_event_type')
            dt.date.fromisoformat(item['occurred_on'])
            if item['occurred_on'] > asof and item['event_type'] not in ('scheduled_event', 'earnings_schedule'):
                raise ValueError('future_event_not_an_announced_schedule')
        reason = None
        if item.get('validation_status') != 'verified' or sources[sid].get('validation_status') != 'verified':
            reason = 'UNVERIFIED_CONTEXT'
        elif not item.get('published_at') or not item.get('observed_at'):
            reason = 'EXACT_AVAILABILITY_UNKNOWN'
        elif not sources[sid].get('published_at') or not sources[sid].get('observed_at'):
            reason = 'SOURCE_AVAILABILITY_UNKNOWN'
        else:
            pub, obs = timestamp(item['published_at']), timestamp(item['observed_at'])
            if pub > obs or max(pub, obs, timestamp(sources[sid]['published_at']), timestamp(sources[sid]['observed_at'])) > timestamp(cutoff):
                reason = 'NOT_AVAILABLE_AT_CUTOFF'
        record = {**item, 'source_sha256': sources[sid]['sha256']}
        if reason:
            held.append(dict(id=item['id'], reason=reason))
        else:
            facts.append(record)
    identities = {}
    for f in facts:
        key = (f['kind'], f.get('stock_code'), f['metric'], f.get('period_start'), f.get('period_end'),
               f.get('accounting_scope'), f.get('currency'), f.get('occurred_on'), f.get('reference_date'))
        identities.setdefault(key, []).append(f)
    conflicted = {f['id'] for versions in identities.values() if len(versions) > 1 for f in versions}
    held.extend(dict(id=fid, reason='MULTIPLE_CONTEXT_VERSIONS_REQUIRE_EXPLICIT_SELECTION') for fid in sorted(conflicted))
    facts = [f for f in facts if f['id'] not in conflicted]
    return dict(status='LOCAL_VERIFIED' if facts else 'UNAVAILABLE', facts=facts, held=held,
                sources=sources, input_files=files, adapter_network_calls=0)


def financial_metrics(context, stock_code):
    items = [f for f in context['facts'] if f['kind'] == 'financial' and f.get('stock_code') == stock_code]
    results = {}
    def same_basis(a, b):
        return all(a.get(k) == b.get(k) for k in ('unit', 'accounting_scope', 'currency', 'period_start', 'period_end'))
    for metric in ('revenue', 'operating_profit', 'net_income', 'order_backlog'):
        values = sorted((f for f in items if f['metric'] == metric), key=lambda f: (f['period_end'], f['period_start']))
        growth = None
        if len(values) >= 2:
            b = values[-1]
            pb, sb = dt.date.fromisoformat(b['period_end']), dt.date.fromisoformat(b['period_start'])
            matches = [a for a in values[:-1] if
                (dt.date.fromisoformat(a['period_end']).month, dt.date.fromisoformat(a['period_end']).day,
                 dt.date.fromisoformat(a['period_start']).month, dt.date.fromisoformat(a['period_start']).day) == (pb.month, pb.day, sb.month, sb.day)
                and dt.date.fromisoformat(a['period_end']).year == pb.year-1 and dt.date.fromisoformat(a['period_start']).year == sb.year-1
                and all(a.get(k) == b.get(k) for k in ('unit', 'accounting_scope', 'currency'))]
            if len(matches) == 1 and number(matches[0]['value']) > 0:
                a = matches[0]
                growth = str((Fraction(str(b['value'])) / Fraction(str(a['value'])) - 1) * 100)
        refs = [a['id'], b['id']] if growth is not None else []
        results[metric+'_growth_pct'] = dict(value=growth, status='valid' if growth is not None else 'UNAVAILABLE',
                                           source_ids=refs)
    def division(top, bottom):
        aa = [f for f in items if f['metric'] == top]
        bb = [f for f in items if f['metric'] == bottom]
        pairs = [(a, b) for a in aa for b in bb if same_basis(a, b)]
        pairs.sort(key=lambda p: p[0]['period_end'])
        if not pairs or number(pairs[-1][1]['value']) <= 0:
            return dict(value=None, status='UNAVAILABLE', source_ids=[])
        a, b = pairs[-1]
        return dict(value=str(Fraction(str(a['value']))/Fraction(str(b['value']))), status='valid', source_ids=[a['id'], b['id']])
    results['operating_margin_ratio'] = division('operating_profit', 'revenue')
    results['debt_to_equity_ratio'] = division('total_liabilities', 'total_equity')
    return results
