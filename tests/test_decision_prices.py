import json
import pytest
from scripts import collect_decision_prices as c


def test_physical_budget_persists_and_retry_never_sends(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'guard',lambda:None)
    monkeypatch.setattr(c.time,'sleep',lambda _:None)
    b=c.Budget(tmp_path,limit=2)
    b.before_http('/oauth2/tokenP');b.finish('authenticated')
    b.before_http('/price');b.finish('failed')
    with pytest.raises(ValueError,match='exhausted'):b.before_http('/price')
    with pytest.raises(ValueError,match='retry_disabled'):c.Budget(tmp_path).before_http('/price',1)
    assert len(c.read(tmp_path/'requests.json')['requests'])==2
    monkeypatch.setattr(c,'TOTAL_LIMIT',2)
    with pytest.raises(ValueError,match='exhausted'):c.Budget(tmp_path).before_http('/price')


def test_preserved_success_reused_without_api(tmp_path,monkeypatch):
    obj={'asof':'2026-10-21','rows':[{'BAS_DD':'20261021'}]}
    c.cache(tmp_path,'krx:KOSPI:2026-10-21',obj)
    monkeypatch.setattr(c.urllib.request,'urlopen',lambda *a,**k:pytest.fail('duplicate API'))
    c.krx('2026-10-21','KOSPI',tmp_path,None)
    c.cache(tmp_path,'krx:KOSPI:2026-10-21',{'asof':'2026-10-21','rows':[]})
    assert len(c.cached(tmp_path,'krx:KOSPI:2026-10-21'))==2


def test_tampered_cache_blocked(tmp_path):
    c.cache(tmp_path,'key',{'rows':[]})
    p=next((tmp_path/'objects').iterdir());p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash_mismatch'):c.cached(tmp_path,'key')


def test_missing_calendar_not_weekday_synthesized(tmp_path):
    with pytest.raises(ValueError,match='missing'):c.index_days(tmp_path,'2026-10-21','0001')


def test_manual_collector_keeps_shared_lock_and_no_cron():
    from pathlib import Path
    s=Path('.github/workflows/decision-price-evidence.yml').read_text()
    assert 'kis-validation-auth' in s and 'schedule:' not in s
    assert 'git push origin HEAD:refs/heads/evidence/decision-price-source' in s
    assert 'HEAD:main' not in s
