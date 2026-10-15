"""Native row-click navigation and real-data reconciliation for the indicator dashboard."""
import csv,json,os,sys,traceback
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from screener.store import load
from screener.indicators import universe,stock_activity,sector_indicators
from screener.macro import read as macro_read,catalog
URL=os.environ.get('DASHBOARD_CHECK_URL','https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/')
out=Path(os.environ.get('DASHBOARD_CHECK_OUTPUT','reports/live-dashboard'));out.mkdir(parents=True,exist_ok=True)
data=load();asof=str(data['flows'].trade_date.max().date());stocks,matrix=universe(data,asof);stocks=stock_activity(data,stocks,asof);sectors,_=sector_indicators(data,stocks,matrix,asof)
report={'url':URL,'success':False,'checks':[],'asof':asof}
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['BROWSER_EXECUTABLE'],'args':['--no-sandbox','--disable-dev-shm-usage']} if os.environ.get('BROWSER_EXECUTABLE') else {}))
 page=browser.new_page(viewport={'width':1500,'height':1100},device_scale_factor=1);ui=page
 def pause():
  page.wait_for_timeout(700);ui.locator('.stApp[data-test-script-state="notRunning"]').wait_for(timeout=90000);page.wait_for_timeout(400)
 def click(label):ui.get_by_role('button',name=label,exact=True).click();pause()
 def choose(label,value):
  box=ui.get_by_role('combobox',name=label,exact=True);box.click();box.fill(value);page.wait_for_timeout(300)
  ui.get_by_role('option',name=value,exact=True).click();pause();ui.locator('body').press('Escape')
 def radio(value):ui.get_by_role('radio',name=value,exact=True).check(force=True);pause()
 def download(label,name):
  with page.expect_download(timeout=30000) as event:ui.get_by_role('button',name=label,exact=True).click()
  dest=out/name;event.value.save_as(str(dest));return list(csv.DictReader(dest.read_text(encoding='utf-8-sig').splitlines()))
 def row_click():
  grid=ui.locator('[data-testid="stMain"] [data-testid="stDataFrame"]').first
  grid.locator('.dvn-scroller').click(position={'x':18,'y':54});pause()
 def no_scores():
  body=ui.locator('[data-testid="stMain"]').inner_text()
  for term in ['종합 점수','수급 부분 점수','항목별 평가 점수','/70','/100']:assert term not in body,term
  assert not ui.locator('[data-testid="stException"]').count()
 def match(value,expected):
  import pandas as pd
  if pd.isna(expected):assert value in ['',None]
  else:assert value not in ['',None] and abs(float(value)-float(expected))<1e-6,(value,expected)
 try:
  response=page.goto(URL,wait_until='domcontentloaded',timeout=60000);report['http_status']=response.status if response else None
  for _ in range(30):
   found=next((f for f in page.frames if f.get_by_text('한국 증시 수급 필터',exact=True).count()),None)
   if found:ui=found;break
   wake=page.get_by_role('button',name='Yes, get this app back up!')
   if wake.count():wake.click()
   page.wait_for_timeout(4000)
  ui.get_by_role('button',name='매크로 현황',exact=True).wait_for(timeout=90000);pause()
  rows=download('매크로 현황 CSV','macro.csv');assert len(rows)==12
  state=macro_read();byname={meta['name']:key for key,meta in catalog().items()}
  for row in rows:
   r=state['indicators'].get(byname[row['지표']])
   if r:
    match(row['최신 공표값'],r['value']);match(row['전일 대비'],r['change']);assert row['기준일']==r['date']
  no_scores();page.screenshot(path=str(out/'macro.png'),full_page=True)
  report['checks'].append('12 macro rows match independent published snapshot, dates, values and changes; no trend scoring')
  click('전체 섹터');radio('지난 5일');radio('전체 섹터');choose('섹터 정렬','시총 대비 순매수 강도')
  rows=download('섹터 현황 CSV','sectors.csv');assert len(rows)==len(sectors)
  lookup=sectors.set_index(['market','sector'])
  for row in rows:
   r=lookup.loc[row['시장'],row['섹터']]
   for field,key,scale in [('선택 기간 순매수(억원)','net_5d',1e8),('확산율(%)','breadth_5d',1),('거래 활성도(배)','activity_ratio',1)]:match(row[field],r[key]/scale)
   assert int(row['전체 대상'])==r.members and int(row['평가 가능 종목'])==r.evaluable_5d
  assert all('점수' not in c for c in rows[0]);no_scores();page.screenshot(path=str(out/'sectors.png'),full_page=True)
  group=(rows[0]['시장'],rows[0]['섹터']);row_click()
  assert group[1]+' · 종목 수급' in ui.locator('body').inner_text()
  radio('지난 20일');choose('종목 정렬','매수세 개선액')
  rows=download('선택 종목 CSV','stocks.csv');expected=stocks[(stocks.market==group[0])&(stocks.sector==group[1])].set_index('symbol')
  assert len(rows)==len(expected)
  for row in rows:
   r=expected.loc[row['코드']]
   for field,key,scale in [('선택 기간 순매수(억원)','net_20d',1e8),('5일 변화(억원)','change_5d',1e8),('거래 활성도(배)','activity_ratio',1)]:match(row[field],r[key]/scale)
   assert row['투자 적격성']=='판정 보류'
  no_scores();page.screenshot(path=str(out/'stocks.png'),full_page=True)
  code=rows[0]['코드'];name=rows[0]['종목명'];row_click()
  assert name+' · '+code in ui.locator('body').inner_text()
  assert '투자 적격성 · 재무 판단 근거' in ui.locator('body').inner_text()
  assert '감사의견' in ui.locator('body').inner_text() and '판정 보류' in ui.locator('body').inner_text()
  rows=download('일별 수급 CSV','chart.csv');assert len(rows)==20
  actual=matrix[code]
  for row in rows:match(row['net_amount'],actual.loc[row['trade_date']])
  radio('누적 순매수');assert ui.locator('[data-testid="stVegaLiteChart"] svg g.mark-line.role-mark').count()>0
  radio('일별 순매수');chart=ui.locator('[data-testid="stVegaLiteChart"]');bars=chart.locator('svg g.mark-rect.role-mark path');assert bars.count()>0
  hover=False
  for i in range(bars.count()):
   box=bars.nth(i).bounding_box()
   if box and box['width'] and box['height']:
    bars.nth(i).hover(force=True);page.wait_for_timeout(150)
    if ui.locator('#vg-tooltip-element').is_visible():hover=True;break
  assert hover
  no_scores();page.screenshot(path=str(out/'detail.png'),full_page=True)
  click('← 이전 화면');assert ui.get_by_role('combobox',name='종목 정렬',exact=True).input_value()=='매수세 개선액'
  assert ui.get_by_role('radio',name='지난 20일',exact=True).is_checked()
  click('← 이전 화면');assert ui.get_by_role('combobox',name='섹터 정렬',exact=True).input_value()=='시총 대비 순매수 강도'
  assert ui.get_by_role('radio',name='지난 5일',exact=True).is_checked()
  report['checks'].append('Native sector row → stock row → detail; 50-sector and selected stock CSV reconciled; qualification never filters rows; back preserves periods and sorts')
  report['checks'].append('Actual 20-day chart amounts, daily/cumulative switches, tooltip, financial risk facts and held eligibility')
  # Theme lookup remains separate and evidence is accessible.
  expand=ui.locator('[data-testid="stExpandSidebarButton"]')
  if expand.is_visible():expand.click();pause()
  radio('투자 테마');ui.locator('[data-testid="stSidebarCollapseButton"] button').click();pause()
  choose('섹터 선택','테마 · 원전');click('선택 섹터의 종목 보기')
  choose('종목 선택','두산에너빌리티 · 034020');click('선택 종목 상세 보기')
  assert '실제 사업 내용 · 투자 테마' in ui.locator('body').inner_text()
  ui.get_by_text('원전 · 검증된 사업 근거',exact=True).click();pause()
  assert 'DART' in ui.locator('body').inner_text();no_scores()
  report['checks'].append('Verified nuclear theme, actual company details and DART evidence retained')
  for width in [768,390]:
   page.set_viewport_size({'width':width,'height':844});pause();click('전체 섹터')
   radio('지난 5일');choose('섹터 정렬','순매수 금액');row_click();row_click()
   assert '투자 적격성 · 재무 판단 근거' in ui.locator('body').inner_text()
   no_scores();size=ui.locator('body').evaluate('e=>({width:innerWidth,scroll:document.documentElement.scrollWidth})');assert size['scroll']<=size['width']+2,size
   page.screenshot(path=str(out/f'mobile-detail-{width}.png'),full_page=True)
   click('← 이전 화면');click('← 이전 화면');click('매크로 현황');page.screenshot(path=str(out/f'macro-{width}.png'),full_page=True)
   report['checks'].append(f'{width}px: native row navigation, back and macro switch work; no page overflow')
  report['success']=True
 except Exception as e:
  report.update(error_type=type(e).__name__,error=str(e)[:1800],traceback=traceback.format_exc()[-4000:])
  report['visible_text']=ui.locator('body').inner_text()[-16000:];page.screenshot(path=str(out/'failure.png'),full_page=True)
 finally:
  report['final_url']=page.url;report['app_frame_url']=ui.url
  (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');browser.close()
print(json.dumps({k:v for k,v in report.items() if k!='visible_text'},ensure_ascii=False))
if not report['success']:raise SystemExit(1)
