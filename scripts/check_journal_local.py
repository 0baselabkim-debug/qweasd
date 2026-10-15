"""Browser checks against synthetic LOCAL harness. Not external authentication/persistence proof."""
import json,os
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path(os.getenv('JOURNAL_TEST_OUTPUT','/tmp/journal-browser'));out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1100})
 def pause():
  page.wait_for_timeout(400);page.locator('.stApp[data-test-script-state="notRunning"]').wait_for(timeout=30000);page.wait_for_timeout(200)
 def click(label):
  button=page.get_by_role('button',name=label,exact=True)
  if label in ['수정','삭제'] and not button.is_visible():page.get_by_text('개별 매수 기록 수정·삭제 / 매도 판단 근거',exact=True).click();pause()
  button.click();pause()
 def records():return json.loads(page.locator('[data-testid="stCode"]').inner_text())
 def login(email):
  page.get_by_role('textbox',name='이메일',exact=True).fill(email);page.get_by_role('textbox',name='비밀번호',exact=True).fill('test-only');click('로그인')
 def enter(price,qty):
  box=page.get_by_role('combobox',name='종목명 또는 코드 검색');box.click();box.fill('삼성');page.get_by_role('option',name='삼성전자 (005930)',exact=True).click()
  page.get_by_role('spinbutton',name='평균매수가 (원)').fill(str(price));page.get_by_role('spinbutton',name='보유수량 (주)').fill(str(qty))
 page.goto('http://localhost:8510');page.get_by_role('button',name='로그인',exact=True).wait_for();assert page.get_by_role('button',name='+ 추가',exact=True).is_disabled();login('test-a@example.invalid')
 click('+ 추가');enter(100,1);page.screenshot(path=str(out/'popup-synthetic.png'),full_page=True);click('취소');assert records()==[]
 click('TEST_FAIL_NEXT_SAVE');click('+ 추가');enter(100,1);click('저장');assert page.get_by_role('dialog').count()==1 and 'TEST: 저장 실패' in page.inner_text('body');click('저장');assert page.get_by_role('dialog').count()==0 and len(records())==1
 click('+ 추가');enter(200,2);click('저장');assert len(records())==2
 page.screenshot(path=str(out/'holdings-synthetic.png'),full_page=True)
 page.get_by_text('개별 매수 기록 수정·삭제 / 매도 판단 근거',exact=True).click();pause();click('수정');page.get_by_role('spinbutton',name='평균매수가 (원)').fill('120');click('저장');assert any(float(r['average_price'])==120 for r in records())
 click('삭제');click('취소');assert len(records())==2;click('삭제');click('삭제 확인');assert len(records())==1
 click('로그아웃');assert not page.locator('[data-testid="stCode"]').count();login('test-b@example.invalid');assert records()==[];click('로그아웃');login('test-a@example.invalid');assert len(records())==1
 page.set_viewport_size({'width':390,'height':1000});click('+ 추가');page.screenshot(path=str(out/'popup-mobile-synthetic.png'),full_page=True);assert page.get_by_role('dialog').is_visible();click('취소')
 assert not page.locator('[data-testid="stException"]').count();browser.close()
(out/'result.json').write_text(json.dumps({'success':True,'backend':'synthetic local test only','checks':['login/logout UI','partial name search','cancel no write','save failure retains popup','save closes popup','duplicate purchases','edit/delete','session user display isolation','mobile dialog'],'external_supabase_verified':False,'persistent_storage_verified':False},ensure_ascii=False,indent=2))
print('Local synthetic journal browser checks passed; external persistence/auth not verified')
