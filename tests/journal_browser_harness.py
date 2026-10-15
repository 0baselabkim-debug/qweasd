"""LOCAL TEST SERVER ONLY. Synthetic journal backend, never used by app.py."""
import sys,json,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import streamlit as st
from screener import journal_ui as ui,exploration_ui as explorer,dashboard_style
from screener.journal_store import validate_purchase,StoreError
st.set_page_config(layout='wide');dashboard_style.install();explorer.install()
class FakeStore:
 def __init__(self,url,key,session):self.user=session['access_token'];st.session_state.setdefault('TEST_DB',{}).setdefault(self.user,[])
 @staticmethod
 def login(url,key,email,password):
  if password!='test-only':raise StoreError('로그인 실패')
  return {'access_token':email,'expires_at':9999999999}
 @staticmethod
 def refresh(url,key,session):return session
 def logout(self):pass
 def purchases(self):return list(st.session_state.TEST_DB[self.user])
 def save(self,symbol,date,price,quantity,record=None):
  value=validate_purchase(symbol,date,price,quantity)
  if st.session_state.pop('TEST_FAIL',False):raise StoreError('TEST: 저장 실패')
  if record:self.delete(record)
  value.update(id=record['id'] if record else str(uuid.uuid4()),revision=record['revision']+1 if record else 1)
  st.session_state.TEST_DB[self.user].append(value);return value
 def delete(self,record):st.session_state.TEST_DB[self.user]=[r for r in self.purchases() if r['id']!=record['id']]
 def cache(self,symbol):return {}
 def risk(self,symbol):return {}
 def save_risk(self,*args):pass
 def request(self,symbol):pass
ui.JournalStore=FakeStore;ui.settings=lambda:('https://test.supabase.co','TEST_ONLY')
ui.technical_cache.get_bundle=lambda *a,**k:(None,None)
if st.button('TEST_FAIL_NEXT_SAVE'):st.session_state.TEST_FAIL=True
ui.render(pd.DataFrame([{'symbol':'005930','name':'삼성전자','market':'KOSPI'},{'symbol':'000660','name':'SK하이닉스','market':'KOSPI'},{'symbol':'035420','name':'NAVER','market':'KOSPI'}]))
st.markdown('TEST HARNESS: synthetic records only; not persistence or RLS evidence')
store=ui.signed_store()
if store:st.code(json.dumps(store.purchases(),ensure_ascii=False),language='json')
