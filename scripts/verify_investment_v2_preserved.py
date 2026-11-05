"""Independent sums and identity checks against the pinned preserved original.

This script never downloads or calls a market provider. It reads an existing ZIP
extraction and separately anchored evidence/investment generations.
"""
import argparse
import gzip
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screener.decision_evidence.collector_artifact import verify_directory, ASOF, GENERATION
from screener.decision_evidence.build import verify_output
from screener.investment_v2.pipeline import verify_bundle
from screener.decision_support.evidence import date
from collector_v1.core import Store
from collector_v1.experiment.engine import replay, identity
from collector_v1.providers import task
from scripts.decision_v2_native import validate_analysis


def verify(source, evidence, evidence_hash, investment, investment_hash):
    source, evidence, investment = map(Path, (source, evidence, investment))
    original = verify_directory(source)
    evidence_check = verify_output(evidence, evidence_hash)
    investment_check = verify_bundle(investment, investment_hash)
    market = json.loads(gzip.decompress((evidence/'market.json.gz').read_bytes()))
    derived = json.loads(gzip.decompress((investment/'market.json.gz').read_bytes()))
    report = validate_analysis(source, market)
    assert set(r['stock_code'] for r in market['stocks']) == set(r['stock_code'] for r in derived['stocks'])
    if derived['source_evidence_dataset_id'] != market['dataset_id']:
        raise ValueError('investment_evidence_generation_mismatch')
    store = Store(source/GENERATION/'collection')
    cache = replay(store, 1, asof=ASOF, generation_id=GENERATION)
    calendars = {}
    for m in ('KOSPI', 'KOSDAQ'):
        event = cache[identity(task('index', ASOF, round=1, market=m, generation_id=GENERATION))]
        calendars[m] = sorted(date(x['stck_bsop_date']) for x in json.loads(store.raw(event))['output2'])
    extra = 0
    for row in market['stocks']:
        symbol, m = row['stock_code'], row['market']
        fe = cache[identity(task('flow', ASOF, round=1, symbol=symbol, market=m, generation_id=GENERATION))]
        pe = cache[identity(task('price', ASOF, round=1, symbol=symbol, market=m, generation_id=GENERATION))]
        flows = {date(r['stck_bsop_date']): r for r in json.loads(store.raw(fe))['output2']}
        prices = {date(r['stck_bsop_date']): r for r in json.loads(store.raw(pe))['output2']}
        fqty, iqty = {}, {}
        for day, r in flows.items():
            if min(int(r['orgn_shnu_vol']), int(r['orgn_seln_vol'])) >= 0 and int(r['orgn_shnu_vol'])-int(r['orgn_seln_vol']) == int(r['orgn_ntby_qty']):
                iqty[day] = int(r['orgn_ntby_qty'])
            if (day in prices and all(int(r[k]) == int(prices[day][k]) for k in ('stck_clpr', 'acml_vol', 'acml_tr_pbmn'))
                    and min(int(r[k]) for k in ('stck_clpr', 'acml_vol', 'acml_tr_pbmn')) > 0
                    and min(int(r[k]) for k in ('frgn_shnu_vol', 'frgn_seln_vol', 'frgn_shnu_tr_pbmn', 'frgn_seln_tr_pbmn')) >= 0
                    and int(r['frgn_shnu_vol'])-int(r['frgn_seln_vol']) == int(r['frgn_ntby_qty'])
                    and abs(int(r['frgn_shnu_tr_pbmn'])-int(r['frgn_seln_tr_pbmn'])-int(r['frgn_ntby_tr_pbmn'])) <= 1):
                fqty[day] = int(r['frgn_ntby_qty'])
        def total(values, count, offset=0):
            end = len(calendars[m])-offset
            days = calendars[m][max(0, end-count):end]
            return sum(values[d] for d in days) if len(days) == count and all(d in values for d in days) else None
        previous, now = total(iqty, 5, 5), total(iqty, 5)
        expectations = [('foreign_flow_metrics', 'net_quantity_3d', total(fqty, 3)),
                        ('institution_flow_metrics', 'net_quantity_3d', total(iqty, 3)),
                        ('institution_flow_metrics', 'net_quantity_previous_5d', previous),
                        ('institution_flow_metrics', 'net_quantity_change_5d', now-previous if now is not None and previous is not None else None)]
        for group, key, expected in expectations:
            if row[group][key]['value'] != expected:
                raise ValueError('new_quantity_window_independent_raw_sum_mismatch')
            extra += 1
    if verify_directory(source) != original:
        raise ValueError('preserved_original_changed')
    return dict(status='PASS_RAW_RECALCULATION_AND_INTEGRATION', source=report,
                extra_quantity_and_change_checks=extra, original_files_unchanged=len(original),
                evidence=evidence_check, investment=investment_check, market_API_calls=0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--evidence', required=True, type=Path)
    p.add_argument('--evidence-sha256', required=True)
    p.add_argument('--investment', required=True, type=Path)
    p.add_argument('--investment-sha256', required=True)
    args = p.parse_args()
    print(json.dumps(verify(args.source, args.evidence, args.evidence_sha256, args.investment, args.investment_sha256), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
