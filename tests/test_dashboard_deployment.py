"""Exercise the public deployment package without credentials or local raw files."""
import hashlib
import json
from pathlib import Path

from screener.store import DB, load
from screener.scoring import evaluate

ROOT = Path(__file__).resolve().parents[1]


def test_published_snapshot_is_unchanged_and_date_consistent():
    manifest = json.loads((ROOT / 'snapshot/manifest.json').read_text())
    packaged = ROOT / 'snapshot/dashboard.duckdb'
    assert hashlib.sha256(packaged.read_bytes()).hexdigest() == manifest['sha256']
    data = load(packaged)
    assert len(data['flows']) == manifest['flow_rows'] == 2430
    assert len(data['financials']) == manifest['financial_rows'] == 2388
    for table in ['flows', 'prices']:
        assert set(data[table].trade_date.dt.strftime('%Y-%m-%d')) == {manifest['asof']}
        assert set(data[table].exchange) == {'KRX'}
    assert not data['flows'][['symbol', 'trade_date']].duplicated().any()
    assert data['flows'].net_amount.notna().all()
    assert set(data['flows'].finality) == {'UNVERIFIED'}


def test_three_stocks_and_two_sectors_match_verified_real_values():
    expected = json.loads((ROOT / 'snapshot/verification.json').read_text())
    stocks, sectors, _ = evaluate(load(ROOT / 'snapshot/dashboard.duckdb'), expected['asof'])
    assert len(stocks) == 2430
    assert stocks.total_score.isna().all()
    assert sectors.total_score.isna().all()
    for row in expected['three_stock_checks']:
        actual = stocks.set_index('symbol').loc[row['symbol']]
        assert actual.net_amount == row['net_amount_krw']
    for row in expected['two_sector_checks']:
        actual = sectors[(sectors.market == row['market']) & (sectors.sector == row['sector'])].iloc[0]
        assert actual.net_amount == row['net_amount_krw']
        assert actual.members == row['members']


def test_clean_checkout_uses_bundled_data_without_credentials():
    if not (ROOT / 'data/dashboard.duckdb').exists():
        assert DB == ROOT / 'snapshot/dashboard.duckdb'
    assert not (ROOT / '.env').exists()
    assert not (ROOT / '.streamlit/secrets.toml').exists()


def test_sector_sort_and_details_are_interactive():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=90).run()
    assert not app.exception
    next(w for w in app.selectbox if w.label == '섹터 선택').select('KOSPI · IT 서비스')
    app.run()
    next(w for w in app.selectbox if w.label == '종목 정렬').select('종목명')
    app.run()
    next(w for w in app.selectbox if w.label == '종목 상세정보').select('035420')
    app.run()
    assert not app.exception
    assert any('NAVER' in w.value for w in app.markdown)
    stock_table = app.dataframe[1].value
    assert stock_table['종목명'].tolist() == sorted(stock_table['종목명'].tolist())
    assert stock_table['코드'].astype(str).str.zfill(6).eq('035420').any()
