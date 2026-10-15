"""Read-only desktop/mobile checks of the deployed UI against preserved actual data."""
import csv,json,os,time,traceback
from pathlib import Path
from playwright.sync_api import sync_playwright
URL=os.environ.get('DASHBOARD_CHECK_URL','https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/')
out=Path(os.environ.get('DASHBOARD_CHECK_OUTPUT','reports/live-dashboard'));out.mkdir(parents=True,exist_ok=True)
root=Path(__file__).resolve().parents[1]
report={'url':URL,'success':False,'checks':[]}
expected={name:list(csv.DictReader((root/f'reports/flow-trends/{name}-flows.csv').read_text().splitlines())) for name in ['sector','stock']}
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['BROWSER_EXECUTABLE'],'args':['--no-sandbox','--disable-dev-shm-usage']} if os.environ.get('BROWSER_EXECUTABLE') else {}))
 page=browser.new_page(viewport={'width':1500,'height':1100},device_scale_factor=1)
 ui=page
 def pause():
  page.wait_for_timeout(700)
  ui.locator('.stApp[data-test-script-state="notRunning"]').wait_for(timeout=90000)
  page.wait_for_timeout(500)
 def main(section):
  label='강세 섹터' if section=='sector' else '강세 섹터 내 우수 종목'
  ui.get_by_role('tab',name=label,exact=True).click();pause()
 def sidebar(show):
  button=ui.locator('[data-testid="stExpandSidebarButton"]' if show else '[data-testid="stSidebarCollapseButton"] button')
  if button.count() and button.first.is_visible():button.first.click();pause()
 def choose(label,value):
  if label in ['종목 범위','종목 정렬']:main('stock')
  if label in ['섹터 선택','테마 선택','섹터 수급 정렬']:main('sector')
  if label in ['데이터 기준일','시장']:sidebar(True)
  box=ui.get_by_role('combobox',name=label,exact=True)
  box.click();box.fill(value);page.wait_for_timeout(300)
  ui.get_by_role('option',name=value,exact=True).click(force=True);pause()
 def tab(section,label):
  main(section);ui.locator('.st-key-'+section+'_period').get_by_role('tab',name=label,exact=True).click();pause()
 def download(button,name,section=None):
  if section:main(section)
  with page.expect_download(timeout=30000) as event:ui.get_by_role('button',name=button,exact=True).click()
  target=out/name;event.value.save_as(str(target))
  return list(csv.DictReader(target.read_text(encoding='utf-8-sig').splitlines()))
 def mode(section,value):
  main(section);ui.locator('.st-key-'+section+'_view').get_by_role('radio',name=value,exact=True).check(force=True);pause()
 try:
  response=page.goto(URL,wait_until='domcontentloaded',timeout=60000)
  report['http_status']=response.status if response else None
  for _ in range(30):
   found=next((f for f in page.frames if f.get_by_text('한국 증시 수급 필터',exact=True).count()),None)
   if found:ui=found;break
   wake=page.get_by_role('button',name='Yes, get this app back up!')
   if wake.count():wake.click()
   page.wait_for_timeout(4000)
  ui.get_by_text('선택 종목 상세 분석',exact=True).wait_for(timeout=120000);pause()
  choose('데이터 기준일','2026-10-14');sidebar(False)
  assert not ui.locator('[data-testid="stException"]').count()
  for removed in ['수급 변화 해석 · 집계 기준','섹터 상세 지표 · 5일/20일 비교','KOREA EQUITY · DAILY FLOW MONITOR']:
   assert removed not in ui.locator('body').inner_text()
  left=ui.locator('.st-key-sector_panel').bounding_box();right=ui.locator('.st-key-sector_context').bounding_box()
  assert right['x']>left['x']+left['width']-2 and abs(right['y']-left['y'])<5
  report['checks'].append('Dark desktop: two side-by-side panels, two main tabs, removed prose/expanders')
  choose('종목 범위','전체 시장')
  assert ui.get_by_role('combobox',name='종목 범위',exact=True).input_value()=='전체 시장'
  score_baseline=None
  for label,days in [('오늘',1),('지난 5일',5),('지난 20일',20)]:
   for section in ['sector','stock']:
    tab(section,label)
    button='섹터 현황 CSV' if section=='sector' else '선택 종목 CSV'
    rows=download(button,f'{section}-{days}d.csv')
    originals=expected[section]
    if section=='sector':originals=[r for r in originals if r[f'net_{days}d'] and float(r[f'net_{days}d'])>0]
    keys=['market','sector'] if section=='sector' else ['symbol'];actual=['시장','섹터'] if section=='sector' else ['코드']
    lookup={tuple(r[k] for k in keys):r[f'net_{days}d'] for r in originals}
    assert len(rows)==len(lookup),f'{section} {days}d: actual {len(rows)}, expected {len(lookup)}'
    amounts=[]
    for r in rows:
     source=lookup[tuple(r[k] for k in actual)];value=r['선택 기간 순매수(억원)']
     assert bool(value)==bool(source)
     if value:assert abs(float(value)*1e8-float(source))<1;amounts.append(float(value))
    assert amounts==sorted(amounts,reverse=True)
    if section=='stock':
     scores={r['코드']:(r['종합 점수'],r['업종 내 평가 순위']) for r in rows}
     if score_baseline is None:score_baseline=scores
     else:assert scores==score_baseline
   report['checks'].append(f'{days}d: all sector/2,430 stock CSV amounts, missing values and sorting match source; scores unchanged')
  for section in ['sector','stock']:
   for value in ['매수세 강화','자금 이탈']:
    mode(section,value);rows=download('섹터 현황 CSV' if section=='sector' else '선택 종목 CSV',f'{section}-{value}.csv')
    field='5일 증감(억원)' if value=='매수세 강화' else '선택 기간 순매수(억원)'
    amounts=[float(r[field]) for r in rows]
    assert amounts and all(v>0 if value=='매수세 강화' else v<0 for v in amounts)
    assert amounts==sorted(amounts,reverse=value=='매수세 강화')
  mode('stock','꾸준한 순매수');rows=download('선택 종목 CSV','steady.csv')
  assert len(rows)==61 and all(float(r['20일 순매수 일수'])>=15 for r in rows)
  mode('stock','전체 종목');mode('sector','자금 유입 상위')
  choose('섹터 선택','KOSPI · IT 서비스');choose('종목 정렬','종목명');choose('종목 상세정보','NAVER · 035420')
  rows=download('선택 종목 CSV','selected-sector.csv','stock')
  assert [r['종목명'] for r in rows]==sorted(r['종목명'] for r in rows) and any(r['코드']=='035420' for r in rows)
  report['checks'].append('Flow views, steady buying, sector-linked stock scope, stock sorting and NAVER detail')
  sidebar(True);ui.get_by_role('radio',name='투자 테마',exact=True).check(force=True);pause();sidebar(False)
  choose('테마 선택','원전');choose('종목 상세정보','두산에너빌리티 · 034020')
  assert '검증된 일부 테마' in ui.locator('body').inner_text()
  page.screenshot(path=str(out/'theme.png'),full_page=True)
  sidebar(True);ui.get_by_role('radio',name='공식 업종',exact=True).check(force=True);pause();sidebar(False)
  choose('섹터 선택','KOSPI · 전기·전자');choose('종목 상세정보','삼성전자 · 005930');tab('sector','지난 20일')
  # Actual chart data remain in raw won in downloaded CSV, including missing values.
  charts=ui.locator('[data-testid="stVegaLiteChart"]:visible');assert charts.count()==2
  with page.expect_download(timeout=30000) as event:
   ui.locator('.st-key-stock_flow_chart_csv').get_by_role('button').click()
  event.value.save_as(str(out/'stock-chart.csv'))
  chartrows=list(csv.DictReader((out/'stock-chart.csv').read_text(encoding='utf-8-sig').splitlines()))
  samsung=next(r for r in expected['stock'] if r['symbol']=='005930')
  assert len(chartrows)==20 and abs(sum(float(r['net_amount']) for r in chartrows)-float(samsung['net_20d']))<1
  assert float(chartrows[-1]['cumulative_amount'])==sum(float(r['net_amount']) for r in chartrows)
  bars=charts.first.locator('svg g.mark-rect.role-mark path');assert bars.count()==20
  tooltip=False
  for i in range(bars.count()):
   box=bars.nth(i).bounding_box()
   if box and box['width'] and box['height']:
    bars.nth(i).hover(force=True);page.wait_for_timeout(200)
    if ui.locator('#vg-tooltip-element').is_visible():tooltip=True;break
  assert tooltip
  for key in ['sector_flow_chart','stock_flow_chart']:
   radio=ui.locator('.st-key-'+key+'_mode')
   radio.get_by_role('radio',name='누적 순매수',exact=True).check(force=True);pause()
   assert ui.locator('[data-testid="stVegaLiteChart"]:visible svg g.mark-line.role-mark').count()>0
   radio.get_by_role('radio',name='일별 순매수',exact=True).check(force=True);pause()
  report['checks'].append('Verified theme filter; two 20-day charts, hover tooltip, daily/cumulative toggles')
  ui.locator('h1').scroll_into_view_if_needed();page.screenshot(path=str(out/'desktop.png'),full_page=True)
  ui.locator('.st-key-detail_panel').screenshot(path=str(out/'stock-detail.png'))
  # Narrow screens retain native table horizontal scrolling, with no page overflow.
  for width in [768,390]:
   page.set_viewport_size({'width':width,'height':844});pause()
   ui.locator('h1').scroll_into_view_if_needed()
   size=ui.locator('body').evaluate('e => ({width:innerWidth,scroll:document.documentElement.scrollWidth})')
   assert size['scroll']<=size['width']+2,size
   if width==390:
    left=ui.locator('.st-key-sector_panel').bounding_box();right=ui.locator('.st-key-sector_context').bounding_box()
    assert right['y']>left['y']+left['height']-2
    tab('sector','지난 5일');choose('섹터 수급 정렬','시가총액 대비 순매수 강도')
    main('stock');choose('종목 정렬','종합 점수');main('sector')
   ui.locator('h1').scroll_into_view_if_needed();page.screenshot(path=str(out/f'width-{width}.png'),full_page=True)
   report['checks'].append(f'{width}px: no page overflow; '+('stacked panels, period/sort/main-tab interaction passed' if width==390 else 'tablet layout checked'))
  assert not ui.locator('[data-testid="stException"]').count()
  report['success']=True
 except Exception as e:
  report['error_type']=type(e).__name__;report['error']=str(e)[:1800];report['traceback']=traceback.format_exc()[-4000:]
  page.screenshot(path=str(out/'failure.png'),full_page=True)
 finally:
  report['final_url']=page.url;report['app_frame_url']=ui.url
  (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');browser.close()
print(json.dumps(report,ensure_ascii=False))
if not report['success']:raise SystemExit(1)
