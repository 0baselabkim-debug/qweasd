from copy import deepcopy
import datetime as dt,json
import pytest
import official_probe
from official_reconcile import compare_six,candidate_context
from price_link import six
from conflict_reconciliation import official_rows
from screener.liquidity_quality import digest

def bar(close=10):return dict(open=10,high=12,low=8,close=close,volume=100,turnover=1000)
def record(b,flag='0'):
 return {'adjusted_prices':[dict(stck_bsop_date='20261016',stck_oprc=b['open'],stck_hgpr=b['high'],stck_lwpr=b['low'],stck_clpr=b['close'],acml_vol=b['volume'],acml_tr_pbmn=b['turnover'],prtt_rate=flag)]}
def test_full_six_field_match_required():
 a=bar();b=bar(11);o=bar(11);o['volume']=101
 assert compare_six(a,b,o)=='neither_matches'
 assert compare_six(a,b,b)=='later_matches'
 assert compare_six(a,b,a)=='original_matches'
 assert compare_six(a,b,None)=='official_unavailable_or_invalid'
def test_official_candidate_preserves_original_and_full_bar():
 original={'2026-10-16':bar()};saved=deepcopy(original);r=record(bar(11));refs={'2026-10-16':{six(bar(11))}}
 candidate,audit=candidate_context(original,r,refs)
 assert original==saved and candidate['2026-10-16']==bar(11) and len(audit)==1
 candidate['2026-10-16']['volume']=999
 assert original==saved and audit[0]['candidate']['volume']==100
@pytest.mark.parametrize('flag,explicit', [('0',True),('2.0',False)])
def test_action_uncertainty_cannot_be_cleared(flag,explicit):
 original={'2026-10-16':bar()}
 c,a=candidate_context(original,record(bar(11),flag),{'2026-10-16':{six(bar(11))}},explicit)
 assert c==original and not a
@pytest.mark.parametrize('refs',[{}, {'2026-10-19':{six(bar(11))}}, {'2026-10-16':{six(bar(11)),six(bar())}}])
def test_no_evidence_wrong_day_or_multiple_versions_blocks(refs):
 original={'2026-10-16':bar()};c,a=candidate_context(original,record(bar(11)),refs)
 assert c==original and not a

def envelope():
 rows=[dict(BAS_DD='20261016',ISU_CD='000020',MKT_NM='KOSPI',TDD_OPNPRC='10',TDD_HGPRC='12',TDD_LWPRC='8',TDD_CLSPRC='11',ACC_TRDVOL='100',ACC_TRDVAL='1000')]
 return {'url':'https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20261016','http':200,'rows':rows,'rows_sha256':digest(rows),'observed_at':'2026-10-21T05:41:00+00:00'}
@pytest.mark.parametrize('change',['date','market','duplicate','hash','http'])
def test_official_invalid_envelopes(change):
 e=envelope()
 if change=='date':e['rows'][0]['BAS_DD']='20261019'
 elif change=='market':e['rows'][0]['MKT_NM']='KOSDAQ'
 elif change=='duplicate':e['rows']*=2
 elif change=='http':e['http']=403
 if change!='hash':e['rows_sha256']=digest(e['rows'])
 else:e['rows_sha256']='wrong'
 with pytest.raises(ValueError):official_rows(e,'KOSPI','2026-10-16')

def test_attempt_is_durable_before_transport_and_exception_counted(tmp_path,monkeypatch):
 history=[]
 p=official_probe.Probe(tmp_path,lambda:history.append(json.loads((tmp_path/'API_EXECUTION.json').read_text())))
 monkeypatch.setattr(official_probe,'window',lambda t:None)
 class Broken:
  def open(self,*a,**kw):
   assert history[-1]['requests'][-1]['status']=='reserved'
   raise TimeoutError('not saved')
 monkeypatch.setattr(official_probe.urllib.request,'build_opener',lambda *a:Broken())
 assert p.request('KOSPI-20261016','https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd',{'basDd':'20261016'}) is None
 assert len(p.ledger['requests'])==1 and p.ledger['requests'][0]['physical_request'] and p.ledger['requests'][0]['error']=='TimeoutError'
 q=official_probe.Probe(tmp_path,lambda:None)
 assert q.request('KOSPI-20261016','invalid') is None

def test_redirects_not_followed():
 assert official_probe.NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid') is None
