import hashlib,json,urllib.error
from pathlib import Path
from collect_calendar import reference_probe
from pilot_client import Budget

def budget():
 now=[0.0]
 def sleep(n):now[0]+=n
 return Budget(maximum=2,seconds=60,interval=1.05,clock=lambda:now[0],sleep=sleep)

def test_reference_authorization_failure_stops_without_another_market(monkeypatch,tmp_path):
 monkeypatch.setenv('KRX_API_KEY','test-not-real-key');calls=[]
 def denied(req,timeout):
  calls.append(req.full_url)
  import io
  raise urllib.error.HTTPError(req.full_url,403,'forbidden',{},io.BytesIO(b'denied'))
 monkeypatch.setattr('urllib.request.urlopen',denied);b=budget();reference_probe(b,tmp_path)
 result=json.loads((tmp_path/'official-reference-probe.json').read_text())
 assert len(calls)==b.count==len(result['records'])==1
 assert result['records'][0]['http']==403
 assert 'test-not-real-key' not in (tmp_path/'official-reference-probe.json').read_text()
 assert not list(tmp_path.glob('reference-responses/*'))

def test_reference_missing_credentials_uses_no_request(monkeypatch,tmp_path):
 monkeypatch.delenv('KRX_API_KEY',raising=False);b=budget();reference_probe(b,tmp_path)
 assert b.count==0 and json.loads((tmp_path/'official-reference-probe.json').read_text())['status']=='credential_not_bound'

def test_reference_wrong_date_is_not_saved_as_valid(monkeypatch,tmp_path):
 monkeypatch.setenv('KRX_API_KEY','test-not-real-key')
 class Response:
  status=200;headers={}
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def read(self,*args):return b'{"OutBlock_1":[{"BAS_DD":"20261020","ISU_CD":"000020"}]}'
 monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:Response());b=budget();reference_probe(b,tmp_path)
 r=json.loads((tmp_path/'official-reference-probe.json').read_text())['records'][0]
 assert r['status']=='unverified' and b.count==1 and not list(tmp_path.glob('reference-responses/*'))

def test_index_validation_checks_both_markets_and_reuses_saved_result(monkeypatch,tmp_path):
 import collect_calendar as m
 monkeypatch.delenv('DART_API_KEY',raising=False)
 monkeypatch.setattr(m,'ROOT',tmp_path);monkeypatch.setattr(m,'persist',lambda *a:None)
 monkeypatch.setattr(m,'git',lambda *a:m.BRANCH);monkeypatch.setenv('GITHUB_REF_NAME',m.BRANCH);monkeypatch.setenv('GITHUB_RUN_ID','fake-unit-test')
 monkeypatch.delenv('KRX_API_KEY',raising=False)
 (tmp_path/'inputs').mkdir();(tmp_path/'inputs/calendar.csv').write_text('trade_date\n2026-06-01\n2026-10-19\n')
 calls=[]
 class Fake:
  key='test-key';secret='test-secret';token='test-token'
  def __init__(self,b):self.b=b
  def auth(self):calls.append('auth');self.b.acquire('/oauth2/tokenP',0)
  def request(self,path,params,tr):
   calls.append((path,params,tr));self.b.acquire(path,0)
   return {'output2':[{'stck_bsop_date':d,'bstp_nmix_prpr':'100'} for d in ['20260601','20261019']]}
 monkeypatch.setattr(m,'Client',Fake);monkeypatch.setattr(m,'Budget',lambda **kw:budget())
 # Budget uses 3 requests for auth + the two markets.
 monkeypatch.setattr(m,'Budget',lambda **kw:Budget(maximum=3,seconds=60,interval=0,sleep=lambda n:None))
 m.main();r=json.loads((tmp_path/'collection/calendar.json').read_text())
 assert r['status']=='passed' and r['market_sessions']['KOSPI']==r['market_sessions']['KOSDAQ']
 assert len(calls)==3 and all('chk-holiday' not in str(x) for x in calls)
 m.main();assert len(calls)==3

def test_sangji_list_identity_error_cannot_download_or_confirm(monkeypatch,tmp_path):
 import official_action_probe as m
 monkeypatch.setenv('DART_API_KEY','test-not-a-real-key');calls=[]
 def fake(b,path,params,key,out):
  calls.append(path)
  return {'status':'received'},json.dumps({'status':'000','total_page':1,'list':[{'corp_code':'wrong','stock_code':'042940','rcept_dt':'20260922'}]}).encode()
 monkeypatch.setattr(m,'fetch',fake);m.probe(budget(),tmp_path)
 r=json.loads((tmp_path/'sangji-official-probe.json').read_text())
 assert len(calls)==1 and r['status']=='unverified' and r['action_confirmed'] is False
 assert 'test-not-a-real-key' not in (tmp_path/'sangji-official-probe.json').read_text()
