"""Read-only reproduction of the extra regular-collection observation (no API)."""
import csv
import gzip
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KIS = ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_clpr', 'acml_vol', 'acml_tr_pbmn')
KRX = ('TDD_OPNPRC', 'TDD_HGPRC', 'TDD_LWPRC', 'TDD_CLSPRC', 'ACC_TRDVOL', 'ACC_TRDVAL')
FIELDS = ('open', 'high', 'low', 'close', 'volume', 'turnover')

def load(p):
    raw = p.read_bytes()
    return json.loads(gzip.decompress(raw) if p.suffix == '.gz' else raw)

def main():
    provenance = load(HERE / 'REGULAR_COLLECTION_INTERMEDIATE.json')
    path = HERE / 'inputs/checkpoint-20261021.json.gz'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == provenance['source_sha256']
    cp = load(path)
    assert (cp['trade_date'], cp['exchange']) == ('20261021', 'KRX')
    old = load(ROOT.parent / 'full-market/inputs/checkpoint-20261020.json.gz')
    official = load(ROOT / 'official-crosscheck/references/KOSPI-2026-10-20.json')
    expected_rows = list(csv.DictReader((HERE / 'REGULAR_COLLECTION_INTERMEDIATE.csv').open(encoding='utf-8-sig')))
    assert len(expected_rows) == 12
    actual = []
    for symbol in ('095570', '006840'):
        record = cp['records'][symbol]
        assert record['request_scope'] == {'market': 'J', 'symbol': symbol, 'end': '20261021', 'adjustment': '0'}
        latest = [r for r in record['adjusted_prices'] if r['stck_bsop_date'] == '20261020']
        first = [r for r in old['records'][symbol]['adjusted_prices'] if r['stck_bsop_date'] == '20261020']
        ref = [r for r in official['rows'] if r['ISU_CD'] == symbol and r['BAS_DD'] == '20261020' and r['MKT_NM'] == 'KOSPI']
        assert len(latest) == len(first) == len(ref) == 1
        for field, kis_key, krx_key in zip(FIELDS, KIS, KRX):
            before, after, krx = int(first[0][kis_key]), int(latest[0][kis_key]), int(ref[0][krx_key])
            assert after == krx
            row = next(r for r in expected_rows if r['symbol'] == symbol and r['field'] == field)
            assert (int(row['initial']), int(row['regular_oct21_history']), int(row['official_KRX'])) == (before, after, krx)
            assert row['observed_at'] == record['observed_at']
            assert row['source_sha256'] == provenance['source_sha256']
            actual.append({'symbol': symbol, 'field': field, 'difference': after-before})
    assert len(actual) == 12
    print(json.dumps({'verified_fields': len(actual), 'new_market_requests': 0, 'new_requery_C': False, 'differences': actual}))

if __name__ == '__main__':
    main()
