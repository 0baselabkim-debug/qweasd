"""Read-only browser check of the existing public app; never uses API credentials."""
import json,os,time,traceback
from pathlib import Path
from playwright.sync_api import sync_playwright

URL=os.environ.get('DASHBOARD_CHECK_URL','https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/')
out=Path(os.environ.get('DASHBOARD_CHECK_OUTPUT','reports/live-dashboard'));out.mkdir(parents=True,exist_ok=True)
report={'url':URL,'success':False,'checks':[]}
root=Path(__file__).resolve().parents[1]
expected=json.loads((root/'reports/enrichment/coverage.json').read_text())
financial_count=expected['metrics']['ocf']['count']
full_history_count=expected['complete_20d_symbols']
complete_scores=expected['complete_stock_scores']
theme_count=len({r['symbol'] for r in json.loads((root/'theme_mapping.json').read_text())['memberships'] if r['status']=='confirmed'})
publication=json.loads((root/'snapshot/manifest.json').read_text())
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['BROWSER_EXECUTABLE'],'args':['--no-sandbox','--disable-dev-shm-usage']} if os.environ.get('BROWSER_EXECUTABLE') else {}))
 page=browser.new_page(viewport={'width':1540,'height':2100},device_scale_factor=1)
 ui=page
 def choose(label,value):
  page.wait_for_timeout(1500)
  box=ui.get_by_role('combobox',name=label,exact=True)
  box.click();box.fill(value);page.wait_for_timeout(700)
  ui.get_by_role('option',name=value,exact=True).click(force=True)
  page.wait_for_timeout(2000)
 try:
  response=page.goto(URL,wait_until='domcontentloaded',timeout=60000)
  report['http_status']=response.status if response else None
  # A sleeping Community Cloud app exposes this public wake-up button.
  for attempt in range(24):
   found=next((frame for frame in page.frames if frame.get_by_text('오늘의 자금 흐름, 다음 관찰 후보',exact=True).count()),None)
   if found:ui=found;break
   wake=page.get_by_role('button',name='Yes, get this app back up!')
   if wake.count():wake.click()
   page.wait_for_timeout(5000)
  ui.get_by_text('오늘의 자금 흐름, 다음 관찰 후보',exact=True).wait_for(timeout=90000)
  ui.get_by_text('분류체계',exact=True).wait_for(timeout=60000)
  ui.get_by_text('공시일 확인된 연간 재무',exact=True).wait_for()
  choose('데이터 기준일',expected['asof'])
  body=ui.locator('body').inner_text()
  assert f'{financial_count:,}종목' in body,'updated_financial_count_not_visible'
  assert f'{full_history_count:,} / 2,430종목' in body,'updated_20day_coverage_not_visible'
  assert f'종목 종합 평가 가능 {complete_scores:,}개' in body,'updated_score_coverage_not_visible'
  report['checks'].append(f'Financial snapshot: {financial_count}; 20-day flow: {full_history_count}; complete scores: {complete_scores}')
  if publication.get('automatic_snapshot_sync'):
   assert '웹 자동 반영' in body,'automatic_publication_status_not_visible'
   report['checks'].append('Validated automatic publication enabled; existing date is explicit')
  page.screenshot(path=str(out/'official.png'),full_page=True)
  ui.get_by_text('투자 테마',exact=True).click()
  ui.get_by_text('① 투자 테마별 자금 흐름',exact=True).wait_for(timeout=30000)
  assert '부분 검증 테마' in ui.locator('body').inner_text()
  assert f'사업 근거 확인 종목 {theme_count}개' in ui.locator('body').inner_text()
  choose('테마 선택','원전')
  choose('종목 상세정보','두산에너빌리티 · 034020')
  ui.get_by_text('② 선택한 테마의 종목',exact=True).wait_for()
  page.wait_for_timeout(2000)
  assert '두산에너빌리티' in ui.locator('body').inner_text(),'nuclear_theme_member_missing'
  report['checks'].append('Theme selector: nuclear and Doosan Enerbility')
  choose('종목 상세정보','현대건설 · 000720')
  assert '신한울원자력' in ui.locator('body').inner_text() or '현대건설' in ui.locator('body').inner_text()
  report['checks'].append(f'Expanded partial themes: {theme_count} verified companies; Hyundai Engineering & Construction detail')
  page.screenshot(path=str(out/'theme.png'),full_page=True)
  ui.get_by_text('공식 업종',exact=True).click()
  ui.get_by_role('combobox',name='섹터 선택').wait_for()
  choose('섹터 선택','KOSPI · IT 서비스')
  choose('종목 정렬','종목명')
  choose('종목 상세정보','NAVER · 035420')
  page.wait_for_timeout(2000)
  assert 'NAVER' in ui.locator('body').inner_text()
  report['checks'].append('Official industry, sorting and NAVER details')
  with page.expect_download(timeout=30000) as download:
   ui.get_by_role('button',name='선택 종목 CSV',exact=True).click()
  saved=out/'selected-stocks.csv';download.value.save_as(str(saved))
  assert '035420' in saved.read_text(encoding='utf-8-sig')
  report['checks'].append('CSV download includes selected real company')
  page.screenshot(path=str(out/'detail.png'),full_page=True)
  report['success']=True
 except Exception as e:
  report['error_type']=type(e).__name__;report['error']=str(e)[:1800]
  page.screenshot(path=str(out/'failure.png'),full_page=True)
 finally:
  report['final_url']=page.url
  report['app_frame_url']=ui.url
  report['visible_text']=ui.locator('body').inner_text()[:18000]
  (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
  browser.close()
print(json.dumps({k:v for k,v in report.items() if k!='visible_text'},ensure_ascii=False))
if not report['success']:raise SystemExit(1)
