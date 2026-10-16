"""UI-only contracts. Actual mouse/touch events are tested separately in the browser."""
import copy
from screener.exploration_ui import navigate,macro_rows
from screener.macro import read,catalog

def test_hierarchical_tabs_close_and_preferences_are_independent():
 s={'stage':'macro','nav_epoch':0,'preferences':{'sector_period':'지난 5일','market':'KOSPI','stock_sort':'매수세 개선액'},'global_search':'NAVER'}
 original=copy.deepcopy(s['preferences'])
 navigate(s,'sector',('KOSPI','IT 서비스'));navigate(s,'stock','035420');navigate(s,'sectors')
 assert s['sector_open'] and s['stock_open']
 navigate(s,'detail');navigate(s,'close_stock')
 assert s['stage']=='stocks' and s['sector_open'] and not s['stock_open'] and s['symbol'] is None
 navigate(s,'stock','035420');navigate(s,'close_sector')
 assert s['stage']=='sectors' and not s['sector_open'] and not s['stock_open']
 navigate(s,'sector',('KOSDAQ','IT 서비스'));assert s['group']==('KOSDAQ','IT 서비스')
 assert s['preferences']==original and s['global_search']=='NAVER'

def test_macro_presentation_does_not_mutate_values_and_rates_use_bp():
 state=read();original=copy.deepcopy(state);rows=macro_rows(state,catalog(),'금리')
 assert len(rows)==3 and all('bp (' in r['전일 대비'] and '%p' not in r['전일 대비'] for r in rows)
 assert rows[0]['전일 대비']==f"{state['indicators']['us2y']['change']:+.2f}bp ({state['indicators']['us2y']['change']/100:+.3f})"
 other=macro_rows(state,catalog(),'환율·원자재');assert any(r['최신 데이터 값']==f"{state['indicators']['wti']['value']:,.2f}" and r['항목명']=='WTI' for r in other)
 assert state==original
 missing=macro_rows({},catalog(),'한국 증시');assert all(r['최신 데이터 값']=='미확보' and r['전일 대비']=='미확보' for r in missing)

def test_warm_process_reloads_public_identity_module(monkeypatch):
    """Reproduce Cloud retaining the old identity callable across app-only reload."""
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from screener import public_build
    monkeypatch.setattr(public_build,'identity',lambda *a,**k:{'source_sha256':'old','revision':'old'})
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=90).run()
    assert not app.exception
    assert 'data_sha256' in public_build.identity()
