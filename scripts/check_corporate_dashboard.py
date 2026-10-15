"""Read-only proof that an Actions-published corporate snapshot reaches the public app."""
import csv,json,time,traceback
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];out=ROOT/'reports/live-dashboard';out.mkdir(exist_ok=True)
manifest=json.loads((ROOT/'snapshot/corporate-manifest.json').read_text())
url='https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/'
report={'url':url,'success':False,'checks':[],'expected_business_companies':manifest['business_companies'],'published_corporate_run':manifest['last_run']['id']}
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1540,'height':1400});ui=page
 try:
  response=page.goto(url,wait_until='domcontentloaded',timeout=60000);report['http_status']=response.status if response else None
  for _ in range(24):
   frame=next((f for f in page.frames if f.get_by_text('공시 자동화 · 운영 검증',exact=True).count()),None)
   if frame:ui=frame;break
   wake=page.get_by_role('button',name='Yes, get this app back up!')
   if wake.count():wake.click()
   page.wait_for_timeout(5000)
  ui.get_by_text('공시 자동화 · 운영 검증',exact=True).click();page.wait_for_timeout(1500)
  text=ui.locator('body').inner_text()
  assert f'사업 내용 조사 {manifest["business_companies"]:,} / 2,430기업' in text,'Actions snapshot not yet visible'
  assert f'재무 버전 {manifest["financial_versions"]:,}건' in text
  assert str(manifest['last_run']['completed_at']) in text
  report['checks'].append('Exact Actions-published company count, financial versions and actual collection timestamp visible')
  assert '신규 거래일 실제 수집·게시 검증 0/3거래일' in text
  report['checks'].append('Replay is displayed separately and does not count as a new trading day')
  def choose(label,value):
   box=ui.get_by_role('combobox',name=label,exact=True);box.click();box.fill(value)
   ui.get_by_role('option',name=value,exact=True).click();page.wait_for_timeout(2500)
   ui.locator('body').press('Escape')
  ui.get_by_role('button',name='수급 필터',exact=True).click();page.wait_for_timeout(6000)
  choose('시장','KOSDAQ')
  ui.get_by_role('radio',name='전체 섹터',exact=True).check(force=True);page.wait_for_timeout(2000)
  def double(name):
   frame=next(f for f in page.frames if 'exploration_ui.flow_explorer' in f.url)
   frame.get_by_role('cell',name=name,exact=True).dblclick();page.wait_for_timeout(2500)
  double('기계·장비');double('원익IPS')
  ui.get_by_text('최신 공시 재무 · 사업 테마 검토',exact=True).click();page.wait_for_timeout(1000)
  panel=ui.locator('[data-testid="stExpander"]').filter(has=ui.get_by_text('최신 공시 재무 · 사업 테마 검토',exact=True))
  assert '2026-06-30' in panel.inner_text() and '2026-08-14' in panel.inner_text()
  assert '사업 테마 검토 기준일' in panel.inner_text() and '추가 사업보고서 조사 대기' not in panel.inner_text()
  panel.screenshot(path=str(out/'corporate-detail.png'))
  report['checks'].append('Actual half-year statement, disclosure date and held interim valuation displayed')
  page.screenshot(path=str(out/'automation.png'),full_page=True);report['success']=True
 except Exception as e:
  report.update(error=str(e)[:1800],traceback=traceback.format_exc()[-3500:]);page.screenshot(path=str(out/'failure.png'),full_page=True)
 finally:
  report['visible_text']=ui.locator('body').inner_text()[-18000:];(out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');browser.close()
print(json.dumps({k:v for k,v in report.items() if k!='visible_text'},ensure_ascii=False))
if not report['success']:raise SystemExit(1)
