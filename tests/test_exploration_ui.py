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
 assert len(rows)==3 and all('bp (' in r['전일 대비'] and '%p)' in r['전일 대비'] for r in rows)
 assert rows[0]['전일 대비']==f"{state['indicators']['us2y']['change']:+.2f}bp ({state['indicators']['us2y']['change']/100:+.3f}%p)"
 other=macro_rows(state,catalog(),'환율·원자재');assert any('달러/배럴' in r['최신 데이터 값'] and r['항목명']=='WTI 현물' for r in other)
 assert state==original
 missing=macro_rows({},catalog(),'한국 증시');assert all(r['최신 데이터 값']=='미확보' and r['전일 대비']=='미확보' for r in missing)
