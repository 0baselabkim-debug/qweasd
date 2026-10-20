from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]

def test_diverse_strata():
    f=pd.read_csv(ROOT/'results/pilot-targets.csv')
    assert f.market.value_counts().to_dict()=={'KOSPI':5,'KOSDAQ':5}
    assert f.improving.value_counts().to_dict()=={True:5,False:5}
    assert sorted(f.cap_bucket.value_counts().tolist())==[3,3,4]
    assert sorted(f.history_state.value_counts().tolist())==[2,2,6]

def test_actual_budget_and_response_validation():
    s=json.loads((ROOT/'results/pilot-summary.json').read_text())
    assert s['stocks']==10 and s['physical_requests']==21 and s['elapsed_seconds']<600
    assert s['authentication_requests']==1 and s['market_requests']==20
    assert s['date_continuity_60_pass']==10
    assert s['stocks_changed_from_old_observation']==8

def test_new_prices_not_backdated():
    f=pd.read_parquet(ROOT/'results/pilot-prices.parquet')
    assert (f.date<='2026-10-19').all()
    assert (pd.to_datetime(f.observed_at,utc=True)>pd.Timestamp('2026-10-19T23:59:59+09:00')).all()
    q=pd.read_csv(ROOT/'results/pilot-validation.csv')
    assert (~q.historical_701_updated).all()
    assert q.provider_finality.eq('UNVERIFIED').all()
