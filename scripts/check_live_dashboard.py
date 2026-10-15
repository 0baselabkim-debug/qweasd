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
  # Flow feature checks use only the existing web snapshot and downloads, never provider APIs.
  assert '현재 구성 기준 수급 추이' in ui.locator('body').inner_text()
  import csv as csv_module
  def tab(section,label):
   ui.locator('.st-key-'+section+'_period').get_by_role('tab',name=label,exact=True).click()
   page.wait_for_timeout(2500)
   assert ui.locator('.st-key-'+section+'_period').get_by_role('tab',name=label,exact=True).get_attribute('aria-selected')=='true'
  def download_csv(button,name):
   with page.expect_download(timeout=30000) as download:
    ui.get_by_role('button',name=button,exact=True).click()
   destination=out/name;download.value.save_as(str(destination))
   return list(csv_module.DictReader(destination.read_text(encoding='utf-8-sig').splitlines()))
  expected_sectors=list(csv_module.DictReader((root/'reports/flow-trends/sector-flows.csv').read_text().splitlines()))
  expected_stocks=list(csv_module.DictReader((root/'reports/flow-trends/stock-flows.csv').read_text().splitlines()))
  choose('종목 범위','전체 시장');choose('종목 정렬','누적 순매수 금액')
  for label,days in [('오늘',1),('지난 5일',5),('지난 20일',20)]:
   tab('sector',label)
   if days==5:
    assert ui.locator('.st-key-stock_period').get_by_role('tab',name='오늘',exact=True).get_attribute('aria-selected')=='true','stock_period_must_be_independent'
   tab('stock',label)
   assert ui.get_by_role('combobox',name='종목 정렬',exact=True).input_value()=='누적 순매수 금액', 'stock_sort_reset_missing'
   assert ui.get_by_role('combobox',name='섹터 수급 정렬',exact=True).input_value()=='누적 순매수 금액', 'sector_sort_reset_missing'
   for button,name,original,keys in [('섹터 현황 CSV','sectors',expected_sectors,['market','sector']),('선택 종목 CSV','stocks',expected_stocks,['symbol'])]:
    rows=download_csv(button,f'flow-{name}-{days}d.csv')
    if name=='sectors':original=[r for r in original if r[f'net_{days}d'] and float(r[f'net_{days}d'])>0]
    assert len(rows)==len(original)
    actual_keys=['시장','섹터'] if name=='sectors' else ['코드']
    lookup={tuple(r[k] for k in keys):r[f'net_{days}d'] for r in original}
    amounts=[]
    for row in rows:
     expected_value=lookup[tuple(row[k] for k in actual_keys)]
     value=row['선택 기간 순매수(억원)']
     assert bool(value)==bool(expected_value),'missing_value_mismatch'
     if value:
      assert abs(float(value)*1e8-float(expected_value))<1
      amounts.append(float(value))
    assert amounts==sorted(amounts,reverse=True),'selected_period_rank_not_descending'
   report['checks'].append(f'{label}: real sector and stock CSV amounts all match preserved source; descending selected-period ranking')
   if days<20:
    choose('섹터 수급 정렬','시가총액 대비 순매수 강도');choose('종목 정렬','종합 점수')
  report['checks'].append('Period tabs independently switch and reset custom sorting to selected-period flow amount')
  # Verify new descriptive views against independently reconciled stored data.
  changed_sectors=list(csv_module.DictReader((root/'reports/flow-change/sectors.csv').read_text().splitlines()))
  changed_stocks=list(csv_module.DictReader((root/'reports/flow-change/stocks.csv').read_text().splitlines()))
  def mode(section,value):
   ui.locator('.st-key-'+section+'_view').get_by_role('radio',name=value,exact=True).check(force=True)
   page.wait_for_timeout(2500)
  def verify_view(section,value):
   mode(section,value)
   original=changed_sectors if section=='sector' else changed_stocks
   button='섹터 현황 CSV' if section=='sector' else '선택 종목 CSV'
   rows=download_csv(button,f'{section}-{value}.csv')
   if value=='매수세 강화':expected=[r for r in original if r['change_5d'] and float(r['change_5d'])>0]
   elif value=='자금 이탈':expected=[r for r in original if r['net_20d'] and float(r['net_20d'])<0]
   else:expected=[r for r in original if r['buying_pattern']=='꾸준한 순매수']
   keys=['market','sector'] if section=='sector' else ['symbol']
   actual_keys=['시장','섹터'] if section=='sector' else ['코드']
   lookup={tuple(r[k] for k in keys):r for r in expected}
   assert len(rows)==len(expected)
   for row in rows:
    e=lookup[tuple(row[k] for k in actual_keys)]
    assert row['수급 변화']==e['change_direction']+' · '+e['change_state']
    assert row['매수 지속']==e['buying_pattern']
    if e['change_5d']:assert abs(float(row['5일 증감(억원)'])*1e8-float(e['change_5d']))<1
   field='5일 증감(억원)' if value=='매수세 강화' else '선택 기간 순매수(억원)'
   amounts=[float(r[field]) for r in rows]
   assert amounts==sorted(amounts,reverse=value!='자금 이탈')
   report['checks'].append(f'{section} {value}: {len(rows)} real rows, state/amount/filter/order match')
  for value in ['매수세 강화','자금 이탈']:verify_view('sector',value)
  for value in ['매수세 강화','꾸준한 순매수','자금 이탈']:verify_view('stock',value)
  choose('섹터 선택','KOSPI · 건설')
  assert ui.get_by_role('combobox',name='종목 범위',exact=True).input_value()=='선택 섹터'
  rows=download_csv('선택 종목 CSV','selected-sector-after-change.csv')
  expected_codes={r['symbol'] for r in changed_stocks if r['market']=='KOSPI' and r['sector']=='건설'}
  assert {r['코드'] for r in rows}==expected_codes
  report['checks'].append('Sector change automatically resets stock scope and shows that sector’s stocks')
  mode('sector','자금 유입 상위');mode('stock','전체 종목')
  choose('섹터 선택','KOSPI · IT 서비스');choose('종목 범위','전체 시장')
  choose('종목 상세정보','삼성전자 · 005930')
  assert '선택 종목 20거래일 일별·누적 순매수' in ui.locator('body').inner_text()
  charts=ui.locator('[data-testid="stVegaLiteChart"]')
  assert charts.count()==2,'two_flow_charts_missing'
  assert charts.first.get_by_text('일별 순매수(억원)',exact=True).count()==1,'duplicate_daily_axis'
  assert charts.first.get_by_text('누적 순매수(억원)',exact=True).count()==1,'missing_cumulative_axis'
  plot=charts.first.locator('svg').first
  plot.scroll_into_view_if_needed()
  bars=plot.locator('g.mark-rect.role-mark path')
  assert bars.count()==20,'expected_twenty_actual_daily_bars'
  tooltip=False
  for i in range(min(bars.count(),20)):
   box=bars.nth(i).bounding_box()
   if box and box['width']>0 and box['height']>0:
    bars.nth(i).hover(force=True);page.wait_for_timeout(200)
    if ui.locator('#vg-tooltip-element').is_visible():tooltip=True;break
  assert tooltip,'flow_chart_hover_tooltip_missing'
  charts.first.screenshot(path=str(out/'flow-sector.png'))
  charts.nth(1).screenshot(path=str(out/'flow-stock.png'))
  page.screenshot(path=str(out/'flow-trends.png'),full_page=True)
  report['checks'].append('Today/5/20-day tabs, all-stock ranking, two real charts and hover tooltip')
  report['success']=True
 except Exception as e:
  report['error_type']=type(e).__name__;report['error']=str(e)[:1800];report['traceback']=traceback.format_exc()[-4000:]
  page.screenshot(path=str(out/'failure.png'),full_page=True)
 finally:
  report['final_url']=page.url
  report['app_frame_url']=ui.url
  report['visible_text']=ui.locator('body').inner_text()[:18000]
  (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
  browser.close()
print(json.dumps({k:v for k,v in report.items() if k!='visible_text'},ensure_ascii=False))
if not report['success']:raise SystemExit(1)
