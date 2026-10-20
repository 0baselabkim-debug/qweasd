import importlib.util
from pathlib import Path
import pytest
from engine import nav_statistics

def test_actual_artifacts_preserve_vintages_and_missing_outcomes():
    path=Path(__file__).resolve().parents[1]/'verify.py'
    spec=importlib.util.spec_from_file_location('risk_verify',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.verify()['independent_flow_checks']==220

def test_irregular_nav_cannot_be_annualized():
    from datetime import date,timedelta
    dates=[str(date(2026,1,1)+timedelta(days=i)) for i in range(62)]
    nav=[(d,100+i) for i,d in enumerate(dates) if i!=10]
    assert nav_statistics(nav,0)['sharpe'] is None
    with pytest.raises(ValueError,match='NAV_session_gap'):nav_statistics(nav,0,dates)

def test_original_signal_prefix_does_not_use_future_bars():
    import csv,gzip,json
    import forward
    root=Path(__file__).resolve().parents[1]
    with (root/'signals.csv').open(encoding='utf-8-sig') as f:
        s=next(r for r in csv.DictReader(f) if r['record_kind']=='historical_partial_universe' and r['group']=='A')
    p=root/s['source'];raw=p.read_bytes();b=json.loads(gzip.decompress(raw) if p.suffix=='.gz' else raw)
    dates=[r['trade_date'] for r in b['bars']];bars={r['trade_date']:r for r in b['bars']}
    events=forward.audit.census.price_events(dates,bars)
    for length in [len(dates)-10,len(dates)-5,len(dates)-1]:
        cutoff=dates[length-1]
        before=forward.audit.census.price_events(dates[:length],{d:bars[d] for d in dates[:length]})
        assert before==[e for e in events if e['confirmed_date']<=cutoff]

def test_old_real_bundle_cannot_be_frozen_now(tmp_path):
    import csv
    from datetime import datetime,timezone
    import forward
    root=Path(__file__).resolve().parents[1]
    with (root/'signals.csv').open(encoding='utf-8-sig') as f:
        s=next(r for r in csv.DictReader(f) if r['record_kind']=='historical_partial_universe' and r['group']=='A')
    calendar=forward.audit.load(forward.audit.FULL/'collection/calendar.json')['sessions']
    with pytest.raises(ValueError,match='prospective_freeze_outside_available_window'):
        forward.freeze(root/s['source'],calendar,tmp_path/'forward',datetime(2026,10,20,tzinfo=timezone.utc))
    assert not (tmp_path/'forward').exists()
