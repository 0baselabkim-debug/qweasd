"""Anchored evidence, exact numbers and conservative availability contracts."""
import datetime as dt
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re

from screener.decision_support.evidence import encoded, sha, timestamp

ROOT = Path(__file__).resolve().parents[2]


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('explicit_finite_number_required')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('invalid_number') from None
    if not result.is_finite():
        raise ValueError('nonfinite_number')
    return result


def amount(value):
    """JSON-safe exact decimal string. No implicit rounding of money."""
    n = number(value)
    return format(n, 'f')


def symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9A-Z]{6}', value):
        raise ValueError('six_character_official_stock_code_required')
    return value


def value(row, group, key):
    item = row.get(group, {}).get(key, {})
    return item.get('value') if item.get('status') == 'valid' else None


def independent(path, sources=()):
    path = Path(path).resolve()
    for source in (ROOT, *(Path(p).resolve() for p in sources)):
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise ValueError('output_overlaps_repository_or_input')
    return path


def eligible_time(observed_at, cutoff):
    return timestamp(observed_at) <= timestamp(cutoff)


def check_row(row, dataset):
    symbol(row['stock_code'])
    dt.date.fromisoformat(row['asof'])
    if (row['dataset_id'], row['asof'], row['source_manifest_sha256']) != (
            dataset['dataset_id'], dataset['asof'], dataset['source_manifest_sha256']):
        raise ValueError('evidence_dataset_date_or_manifest_mismatch')
    if row.get('market') not in ('KOSPI', 'KOSDAQ') or row.get('exchange') != 'KRX':
        raise ValueError('unsupported_market_scope')
    if row['data_quality'].get('latest_price_verified') is not True:
        for group in ('price_trend_metrics', 'volatility_metrics', 'relative_strength_metrics', 'volume_liquidity_metrics'):
            # Earlier verified prices must not masquerade as the target close.
            if group == 'price_trend_metrics' and value(row, group, 'close') is not None:
                raise ValueError('unverified_current_price_promoted')
    return row


def fact(identifier, val, unit, refs, category='quantitative'):
    return dict(id=identifier, value=val, unit=unit, source_ids=list(refs), category=category)


def facts_for(row, context):
    facts = {}
    groups = ('foreign_flow_metrics', 'institution_flow_metrics', 'flow_persistence_metrics',
              'price_trend_metrics', 'relative_strength_metrics', 'volume_liquidity_metrics', 'volatility_metrics')
    for group in groups:
        for key, item in row.get(group, {}).items():
            if not isinstance(item, dict) or item.get('status') != 'valid' or item.get('value') is None:
                continue
            # Object/array-valued observations stay in the source, not scalar claims.
            val = item['value']
            if not isinstance(val, (int, float, str, bool)):
                continue
            independent_index = group == 'relative_strength_metrics' and key in ('market_return_5d_pct', 'market_return_20d_pct')
            if group not in ('foreign_flow_metrics', 'institution_flow_metrics', 'flow_persistence_metrics') and not independent_index and not row['data_quality']['latest_price_verified']:
                continue
            unit = ('shares' if 'quantity' in key else 'KRW' if group == 'foreign_flow_metrics' and key.startswith(('net_', 'previous_', 'change_', 'day_change')) else
                    'percent' if key.endswith('_pct') else 'percentage_points' if key.endswith('_pp') else 'source_metric')
            fid = group + '.' + key
            facts[fid] = fact(fid, val, unit, [row['source_manifest_sha256']])
    for item in context.get('facts', []):
        if item.get('stock_code') in (None, row['stock_code']):
            fid = 'context.' + item['id']
            facts[fid] = fact(fid, item['value'], item['unit'], [item['source_sha256']], item['kind'])
    return facts


def digest_tree(path):
    path = Path(path)
    result = {}
    for p in sorted(path.rglob('*')):
        if p.is_symlink():
            raise ValueError('input_symlink_not_allowed')
        if p.is_file():
            result[str(p.relative_to(path))] = sha(p.read_bytes())
    return result
