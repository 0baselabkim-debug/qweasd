"""Read-only real browser proof of fixed sidebar, genuine double clicks, tabs and actual values."""
import csv,json,os,sys,traceback
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from screener.store import load
from screener.indicators import universe,stock_activity,sector_indicators
from screener.macro import read as macro_read,catalog
from screener.exploration_ui import macro_rows
URL=os.environ.get('DASHBOARD_CHECK_URL','https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/')
out=Path(os.environ.get('DASHBOARD_CHECK_OUTPUT','reports/live-dashboard'));out.mkdir(parents=True,exist_ok=True)
data=load();asof=str(data['flows'].trade_date.max().date());stocks,matrix=universe(data,asof);stocks=stock_activity(data,stocks,asof);sectors,_=sector_indicators(data,stocks,matrix,asof)
report={'url':URL,'success':False,'checks':[],'asof':asof}
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['BROWSER_EXECUTABLE'],'args':['--no-sandbox','--disable-dev-shm-usage']} if os.environ.get('BROWSER_EXECUTABLE') else {}))
 page=browser.new_page(viewport={'width':1920,'height':1200},device_scale_factor=1);ui=page
 def pause():
  page.wait_for_timeout(650);ui.locator('.stApp[data-test-script-state="notRunning"]').wait_for(timeout=90000);page.wait_for_timeout(500)
 def connect():
  response=page.goto(URL,wait_until='domcontentloaded',timeout=60000)
  for _ in range(30):
   found=next((f for f in page.frames if f.get_by_role('button',name='매크로 지표',exact=True).count()),None)
   if found:return found,response
   wake=page.get_by_role('button',name='Yes, get this app back up!')
   if wake.count():wake.click()
   page.wait_for_timeout(4000)
  raise AssertionError('Redesigned sidebar not loaded')
 def click(label):ui.get_by_role('button',name=label,exact=True).click();pause()
 def choose(label,value):
  box=ui.get_by_role('combobox',name=label,exact=True);box.click();box.fill(value);page.wait_for_timeout(150)
  ui.get_by_role('option',name=value,exact=True).click();pause();ui.locator('body').press('Escape')
 def radio(value):ui.get_by_role('radio',name=value,exact=True).check(force=True);pause()
 def table():
  for _ in range(30):
   f=next((f for f in page.frames if '/flow_explorer/' in f.url or 'exploration_ui.flow_explorer' in f.url),None)
   if f and f.locator('td.name').count():return f
   page.wait_for_timeout(300)
  raise AssertionError('Interactive table missing')
 def name_cell(name):return table().get_by_role('cell',name=name,exact=True)
 def double(name):name_cell(name).dblclick();pause()
 def close(kind):
  ui.locator('.st-key-hierarchy_'+kind).hover();ui.locator('.st-key-close_'+kind+' button:visible').click();pause()
 def download(label,name):
  with page.expect_download(timeout=30000) as event:ui.get_by_role('button',name=label,exact=True).click()
  dest=out/name;event.value.save_as(str(dest));return list(csv.DictReader(dest.read_text(encoding='utf-8-sig').splitlines()))
 def clean():
  assert not ui.locator('[data-testid="stException"]').count()
  for term in ['종합 점수','수급 부분 점수','/70','/100']:assert term not in ui.locator('[data-testid="stMain"]').inner_text()
 def layout(mobile=False):
  ui.locator('[data-testid="stAppViewContainer"]').evaluate('e=>e.scrollTop=0')
  side=ui.locator('[data-testid="stSidebar"]');assert side.is_visible()
  for label in ['종목 검색','수집·게시 상태','공시 자동화 · 운영 검증','데이터 출처 · 계산 기준']:assert label not in side.inner_text()
  for selector in ['stSidebarCollapseButton','stExpandSidebarButton']:
   x=ui.locator('[data-testid="'+selector+'"]');assert not x.count() or not x.is_visible()
  a=side.bounding_box();b=ui.locator('[data-testid="stMain"]').bounding_box()
  assert (b['y']>=a['y']+a['height']-2) if mobile else b['x']>=a['x']+a['width']-2,(a,b)
  if not mobile:assert abs(a['width']-270)<2
  assert ui.locator('body').evaluate('document.documentElement.scrollWidth<=innerWidth+2')
 def readability(stage):
  width=ui.locator('body').evaluate('innerWidth');area=ui.locator('[data-testid="stMain"]').bounding_box();content=ui.locator('[data-testid="stMain"] .block-container').bounding_box()
  ratio,minimum={'macro':(.70,1050),'sectors':(.85,980),'stocks':(.85,980),'detail':(.75,980)}[stage]
  expected=min(area['width'],max(minimum,area['width']*ratio)) if width>=1360 else area['width']
  assert abs(content['width']-expected)<3,(stage,content,expected)
  assert abs((content['x']-area['x'])-(area['width']-content['width'])/2)<3,(stage,content,area)
  record={'viewport':width,'stage':stage,'available_width':area['width'],'content_width':content['width']}
  if stage=='macro':
   panels=[c.bounding_box() for c in ui.locator('.macro-card').all()]
   if width>1180:
    assert abs(panels[0]['height']-panels[1]['height'])<2
    assert abs(panels[2]['height']-panels[3]['height'])<2
    assert panels[2]['height']<panels[0]['height']-100
    assert abs(panels[0]['y']+panels[0]['height']-panels[1]['y']-panels[1]['height'])<2
    assert abs(panels[0]['x']-panels[2]['x'])<2 and abs(panels[1]['x']-panels[3]['x'])<2
    assert abs(panels[0]['y']-panels[1]['y'])<2 and abs(panels[2]['y']-panels[3]['y'])<2
   else:
    assert max(c['x'] for c in panels)-min(c['x'] for c in panels)<2
    assert all(b['y']>=a['y']+a['height'] for a,b in zip(panels,panels[1:]))
   for card in ui.locator('.macro-card').all():
    panel=card.bounding_box();tablebox=card.locator('.mini-scroll').bounding_box()
    assert abs((tablebox['x']-panel['x'])-(panel['width']-tablebox['width'])/2)<3
   rows=ui.locator('.macro-table tbody tr,.investor-table tbody tr').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().height)')
   assert rows and all(44<=v<=48 for v in rows),rows
   assert ui.locator('.macro-table td').evaluate_all('es=>es.every(e=>parseFloat(getComputedStyle(e).fontSize)>=14)')
   record['row_heights']=sorted(set(rows))
   record['header_heights']=ui.locator('.macro-table thead tr,.investor-table thead tr').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().height)')
   assert all(42<=h<=46 for h in record['header_heights'])
  if stage in ['sectors','stocks']:
   f=table();heights=f.locator('tbody tr').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().height)')
   assert heights and min(heights)>=42,heights
   record['header_height']=f.locator('thead tr').bounding_box()['height']
   assert 42<=record['header_height']<=46,record['header_height']
   assert f.locator('td').evaluate_all('es=>es.every(e=>parseFloat(getComputedStyle(e).fontSize)>=14 && e.scrollWidth<=e.clientWidth+2 && e.scrollHeight<=e.clientHeight+2)')
   assert f.locator('td.num').evaluate_all('es=>es.every(e=>getComputedStyle(e).textAlign==="right")')
   scroll=f.locator('.scroll');dims=scroll.evaluate('e=>({view:e.clientWidth,full:e.scrollWidth})');record.update(row_heights=sorted(set(heights)),table=dims)
   if dims['full']>dims['view']:
    scroll.evaluate('e=>e.scrollLeft=e.scrollWidth');assert scroll.evaluate('e=>e.scrollLeft')>0;scroll.evaluate('e=>e.scrollLeft=0')
  if stage=='detail':
   assert ui.locator('.kpi-value,.kpi-label').evaluate_all('es=>es.every(e=>e.scrollWidth<=e.clientWidth+2 && e.scrollHeight<=e.clientHeight+2)')
   chart=ui.locator('[data-testid="stVegaLiteChart"]').bounding_box();assert chart['width']>=min(320,content['width']-30)
  report.setdefault('layout_measurements',[]).append(record)
 def match(v,e):
  import pandas as pd
  if pd.isna(e):assert v in ['',None]
  else:assert v not in ['',None] and abs(float(v)-float(e))<1e-6,(v,e)
 try:
  ui,response=connect();pause();report['http_status']=response.status if response else None
  assert ui.locator('[data-testid="stSidebar"] .sidebar-title').inner_text()=='한국 증시 수급 필터'
  assert not ui.locator('[data-testid="stMain"] h1').count();layout()
  cards=ui.locator('.macro-card');assert cards.count()==4
  for card in cards.filter(has=ui.locator('.macro-table')).all():assert card.locator('th').all_text_contents()==['항목명','최신 데이터 값','전일 대비']
  main=ui.locator('[data-testid="stMain"]')
  assert '공표 기준' not in main.inner_text() and '수급 기준일' not in main.inner_text() and '업데이트 상태' not in main.inner_text()
  assert cards.locator('h3').all_text_contents()==['증시 지수','투자자별 거래 동향','금리','기타 경제지표']
  assert not main.get_by_role('heading',name='매크로 지표',exact=True).count()
  assert cards.locator('h3 [data-heading-text]').evaluate_all('es=>es.every(e=>parseFloat(getComputedStyle(e).fontSize)>=20)')
  assert abs(cards.nth(0).locator('table').bounding_box()['width']-cards.nth(1).locator('table').bounding_box()['width'])<20
  assert cards.nth(0).locator('tbody tr').count()==6
  boxes=[c.bounding_box() for c in cards.all()]
  assert abs((boxes[1]['x']-boxes[0]['x']-boxes[0]['width'])-(boxes[2]['y']-boxes[0]['y']-boxes[0]['height']))<3
  assert abs(cards.nth(2).bounding_box()['y']-cards.nth(3).bounding_box()['y'])<3
  assert ui.locator('.macro-card th,.macro-table td:first-child,.investor-market,.investor-name').evaluate_all('es=>es.every(e=>getComputedStyle(e).textAlign==="center")')
  economy_widths=ui.locator('.economy-card th').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().width)')
  investor_widths=ui.locator('.investor-table th').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().width)')
  assert len(economy_widths)==3 and all(abs(a-b)<2 for a,b in zip(economy_widths,investor_widths))
  state=macro_read()
  for group in ['금리','환율·원자재','미국 증시','한국 증시']:
   for expected in macro_rows(state,catalog(),group):
    row=ui.locator('.macro-table tr').filter(has=ui.get_by_role('cell',name=expected['항목명'],exact=True))
    assert row.locator('td').all_text_contents()==[expected[c] for c in ['항목명','최신 데이터 값','전일 대비']]
    assert expected['date'] in row.get_attribute('title')
  assert ui.locator('.investor-table tbody tr').count()==6
  assert ui.locator('.investor-table td[rowspan="3"]').all_text_contents()==['KOSPI','KOSDAQ']
  assert ui.locator('.investor-table .investor-name').all_text_contents()==['개인','외국인','기관']*2
  assert ui.locator('.investor-table td.num').count()==6
  rows=download('매크로 현황 CSV','macro.csv');assert len(rows)==12
  for row in rows:
   key=next(k for k,m in catalog().items() if m['name']==row['지표']);match(row['최신 공표값'],state['indicators'][key]['value']);assert row['기준일']==state['indicators'][key]['date']
  clean();readability('macro');page.screenshot(path=str(out/'macro.png'),full_page=True)
  report['checks'].append('Default macro, fixed sidebar-only title/menu, 12 unchanged actual values, three columns, rates bp, dates/provenance, six separate investor rows')
  click('수급 필터');choose('시장','KOSPI');radio('지난 5일');radio('전체 섹터');choose('섹터 정렬','시총 대비 순매수 강도')
  rows=download('섹터 현황 CSV','sectors.csv');expected=sectors[sectors.market=='KOSPI'].set_index('sector');assert len(rows)==len(expected)
  for row in rows:
   r=expected.loc[row['섹터']]
   for c,k,scale in [('선택 기간 순매수(억원)','net_5d',1e8),('확산율(%)','breadth_5d',1),('거래 활성도(배)','activity_ratio',1)]:match(row[c],r[k]/scale)
  readability('sectors')
  cell=name_cell('IT 서비스');assert cell.evaluate('e=>getComputedStyle(e).cursor')=='pointer';cell.click();page.wait_for_timeout(500)
  assert ui.get_by_text('전체 섹터 수급',exact=True).count()==1
  table().get_by_role('button',name='5일 변화(억원)',exact=True).click();assert table().locator('th[aria-sort="ascending"]').count()==1
  table().get_by_role('textbox',name='표 내 검색').fill('IT 서비스');assert table().locator('tbody tr').count()==1
  table().locator('td').first.dblclick();page.wait_for_timeout(350);assert ui.get_by_text('전체 섹터 수급',exact=True).count()==1
  table().get_by_role('textbox',name='표 내 검색').fill('');double('IT 서비스')
  assert 'KOSPI · IT 서비스 · 종목 수급' in ui.locator('body').inner_text()
  assert ui.locator('.st-key-tab_sector button:visible').inner_text()=='IT 서비스'
  radio('지난 20일');choose('종목 정렬','매수세 개선액');table().get_by_role('textbox',name='표 내 검색').fill('NAVER')
  rows=download('선택 종목 CSV','stocks.csv');naver=next(r for r in rows if r['코드']=='035420');match(naver['선택 기간 순매수(억원)'],stocks.set_index('symbol').loc['035420','net_20d']/1e8)
  name_cell('NAVER').click();page.wait_for_timeout(400);assert ui.get_by_text('KOSPI · IT 서비스 · 종목 수급',exact=True).count()==1
  readability('stocks');page.screenshot(path=str(out/'stocks.png'),full_page=True);double('NAVER');assert ui.get_by_text('NAVER · 035420',exact=True).count()==1
  assert ui.locator('.st-key-hierarchy button:visible').all_text_contents()==['전체섹터 조회','IT 서비스','×','NAVER','×']
  # Desktop close controls appear on hover / keyboard focus only.
  ui.locator('.st-key-header').hover();page.wait_for_timeout(200);assert float(ui.locator('.st-key-close_stock').evaluate('e=>getComputedStyle(e).opacity'))==0
  ui.locator('.st-key-hierarchy_stock').hover();page.wait_for_timeout(200);assert float(ui.locator('.st-key-close_stock').evaluate('e=>getComputedStyle(e).opacity'))==1
  ui.locator('.st-key-header').hover();ui.locator('.st-key-close_stock button:visible').focus();page.wait_for_timeout(200);assert float(ui.locator('.st-key-close_stock').evaluate('e=>getComputedStyle(e).opacity'))==1
  rows=download('일별 수급 CSV','chart.csv');assert len(rows)==20
  for r in rows:match(r['net_amount'],matrix['035420'].loc[r['trade_date']])
  radio('누적 순매수');assert ui.locator('[data-testid="stVegaLiteChart"] svg g.mark-line.role-mark').count()>0
  radio('일별 순매수');readability('detail');page.screenshot(path=str(out/'detail.png'),full_page=True)
  click('전체섹터 조회');assert ui.locator('.st-key-tab_stock button:visible').inner_text()=='NAVER'
  assert ui.get_by_role('radio',name='지난 5일',exact=True).is_checked();assert ui.get_by_role('combobox',name='섹터 정렬',exact=True).input_value()=='시총 대비 순매수 강도'
  assert table().locator('th[aria-sort="ascending"]').count()==1
  page.screenshot(path=str(out/'sectors.png'),full_page=True)
  click('IT 서비스');assert ui.get_by_role('radio',name='지난 20일',exact=True).is_checked();assert ui.get_by_role('combobox',name='종목 정렬',exact=True).input_value()=='매수세 개선액'
  click('NAVER');close('stock');assert ui.get_by_text('KOSPI · IT 서비스 · 종목 수급',exact=True).count()==1;assert not ui.locator('.st-key-tab_stock').count()
  double('NAVER');close('sector');assert not ui.locator('.st-key-tab_sector').count() and not ui.locator('.st-key-tab_stock').count();assert ui.get_by_text('전체 섹터 수급',exact=True).count()==1
  choose('시장','KOSDAQ');double('IT 서비스');assert ui.get_by_text('KOSDAQ · IT 서비스 · 종목 수급',exact=True).count()==1
  report['checks'].append('Genuine sector/name double click; mouse single click and other-cell double click do not navigate; client sort/filter; matching raw CSV; KOSPI/KOSDAQ identity')
  report['checks'].append('Hierarchy retains child tabs on upward navigation; active stock close goes to sector; sector close removes descendants; hover X; periods, market, sorts and search retained')
  # Existing corporate publication and verified business evidence remain accessible.
  choose('시장','전체');radio('투자 테마');click('전체섹터 조회');double('원전');double('두산에너빌리티')
  ui.get_by_text('원전 · 검증된 사업 근거',exact=True).click();assert 'DART' in ui.locator('body').inner_text();clean()
  report['checks'].append('Actual financial eligibility, daily chart, existing theme business evidence and DART retained')
  for width in [1440,768,390]:
   touch=width<=768
   page=browser.new_page(viewport={'width':width,'height':1000},has_touch=touch,is_mobile=width==390);ui,response=connect();pause();layout(width<=760);readability('macro')
   page.screenshot(path=str(out/f'macro-{width}.png'),full_page=True)
   click('수급 필터');radio('전체 섹터');readability('sectors');page.screenshot(path=str(out/f'sectors-{width}.png'),full_page=True)
   cell=name_cell('IT 서비스').first
   cell.tap() if touch else cell.dblclick()
   pause();assert ui.locator('.st-key-tab_sector').count()==1;readability('stocks');page.screenshot(path=str(out/f'stocks-{width}.png'),full_page=True)
   cell=table().locator('td.name').first
   cell.tap() if touch else cell.dblclick()
   pause();assert ui.get_by_text('투자 적격성 · 재무 판단 근거',exact=True).count()==1
   readability('detail');clean();page.screenshot(path=str(out/f'detail-{width}.png'),full_page=True)
   close('stock');assert not ui.locator('.st-key-tab_stock').count()
   close('sector');assert not ui.locator('.st-key-tab_sector').count()
   layout(width<=760);click('매크로 지표');assert ui.locator('.macro-card').count()==4
   if width==390:
    page.set_viewport_size({'width':1920,'height':1200});pause();layout();readability('macro')
   report['checks'].append(f'{width}px: page widths, row heights, 14px text, internal scroll, no clipped cells/KPIs, navigation, tabs, sidebar non-overlap')
  report['success']=True
 except Exception as e:
  report.update(error_type=type(e).__name__,error=str(e)[:1800],traceback=traceback.format_exc()[-4000:]);report['visible_text']=ui.locator('body').inner_text()[-16000:];page.screenshot(path=str(out/'failure.png'),full_page=True)
 finally:
  report['final_url']=page.url;report['app_frame_url']=ui.url;(out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');browser.close()
print(json.dumps({k:v for k,v in report.items() if k!='visible_text'},ensure_ascii=False))
if not report['success']:raise SystemExit(1)
