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
   ui.get_by_role('option',name=value,exact=True).click(force=True);page.wait_for_timeout(2200)
  choose('섹터 수급 정렬','종합 강세 점수')
  with page.expect_download() as d:ui.get_by_role('button',name='섹터 현황 CSV',exact=True).click()
  dest=out/'formal-sectors.csv';d.value.save_as(str(dest));rows=list(csv.DictReader(dest.read_text(encoding='utf-8-sig').splitlines()))
  assert len(rows)==50 and all(r['종합 강세 점수 /100']=='평가 보류' and not r['종합 점수 정렬 순위'] for r in rows)
  report['checks'].append('Formal sector control shows all 50 held groups without invented scores or ranks')
  choose('종목 범위','전체 시장');choose('종목 상세정보','원익IPS · 240810')
  ui.get_by_text('최신 공시 재무 · 사업 테마 검토',exact=True).click();page.wait_for_timeout(1000)
  panel=ui.locator('[data-testid="stExpander"]').filter(has=ui.get_by_text('최신 공시 재무 · 사업 테마 검토',exact=True))
  assert '2026-06-30' in panel.inner_text() and '2026-08-14' in panel.inner_text()
  panel.screenshot(path=str(out/'corporate-detail.png'))
  report['checks'].append('Actual half-year statement, disclosure date and held interim valuation displayed')
  page.screenshot(path=str(out/'automation.png'),full_page=True);report['success']=True
 except Exception as e:
  report.update(error=str(e)[:1800],traceback=traceback.format_exc()[-3500:]);page.screenshot(path=str(out/'failure.png'),full_page=True)
 finally:
  report['visible_text']=ui.locator('body').inner_text()[-18000:];(out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');browser.close()
print(json.dumps({k:v for k,v in report.items() if k!='visible_text'},ensure_ascii=False))
if not report['success']:raise SystemExit(1)
