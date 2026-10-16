import copy
from scripts.saved_recovery import preflight,reference_index
from screener.technical_quality import digest,daily_accepted
from tests.test_technical_collection import accepted_fixture

def fixture():
 b=accepted_fixture();b['price_comparison']['kis_adjusted']=100
 return b,{(b['asof'],b['symbol']):{'row':b['daily_acceptance']['krx_row'],'source':'synthetic-test-only','sha256':'test'}}

def test_saved_full_verification_preserves_original_and_finality():
 b,index=fixture();before=copy.deepcopy(b);candidate,result=preflight(b,index)
 assert daily_accepted(candidate) and result['physical_requests']==0
 assert candidate['finality']=='UNVERIFIED' and b==before

def test_saved_bad_hash_wrong_day_flow_and_price_remain_blocked():
 for kind in ['hash','date','flow','price','reference']:
  b,index=fixture()
  if kind=='hash':b['daily_acceptance']['bars_sha256']='wrong'
  if kind=='date':b['daily_acceptance']['asof']='1900-01-01'
  if kind=='flow':b['daily_acceptance']['raw_flow_rows'][0]['frgn_ntby_tr_pbmn']='99'
  if kind=='price':b['price_comparison']['kis_unadjusted']=90
  if kind=='reference':index={}
  assert preflight(b,index)[0] is None,kind

def test_reference_hash_checked_and_newest_selected(tmp_path):
 from scripts.technical_batch import write
 b,index=fixture();row=copy.deepcopy(b['daily_acceptance']['krx_row']);row.pop('_retrieved_at');url=row.pop('_source_url')
 for i,observed in enumerate(['2020-12-30T00:00:00+00:00','2020-12-31T00:00:00+00:00']):
  c={'asof':b['asof'],'url':url,'observed_at':observed,'rows':[row],'rows_sha256':digest([row])}
  write(tmp_path/'reference-observations'/b['asof']/'KOSPI'/(digest(c)+'.json.gz'),c)
 write(tmp_path/'reference-observations'/b['asof']/'KOSPI'/'broken.json.gz',c)
 got,errors=reference_index(tmp_path)
 assert got[(b['asof'],b['symbol'])]['row']['_retrieved_at']=='2020-12-31T00:00:00+00:00'
 assert len(errors)==1

def test_full_saved_recovery_never_calls_krx_or_kis(tmp_path,monkeypatch):
 import pandas as pd
 from scripts import technical_batch as batch,recover_technical as recovery
 b,index=fixture();b['daily_acceptance']['status']='REVIEW_REQUIRED'
 # Selected observation is structurally not accepted until current reasons recalculated.
 b['daily_acceptance']['checked_flows']=[]
 monkeypatch.setattr(batch,'ROOT',tmp_path);monkeypatch.setattr(batch,'CACHE',tmp_path/'market')
 monkeypatch.setattr(batch,'refresh_summary',lambda *a:None)
 monkeypatch.setattr('scripts.saved_recovery.reference_index',lambda *a:(index,[]))
 def forbidden(*a,**k):raise AssertionError('No network permitted')
 monkeypatch.setattr(batch,'krx_reference',forbidden)
 batch.write(batch.CACHE/(b['symbol']+'.json.gz'),b)
 class API:
  metrics=[]
  request=forbidden
 r=recovery.run(API(),{'flows':pd.DataFrame({'trade_date':[pd.Timestamp(b['asof'])]})},limit=1)
 assert r['promoted']==1 and r['physical_request_attempts']==0 and not r['reference_status']
 assert r['results'][0]['verification_path']=='saved_evidence_full_verification'

def test_prior_seoul_reference_time_cannot_be_relaxed():
 b,index=fixture();index[(b['asof'],b['symbol'])]['row']['_retrieved_at']=b['asof']+'T00:00:00+00:00'
 candidate,info=preflight(b,index)
 assert candidate is None and info['new_request_reason']=='official_reference_identity_or_observation_time'

def test_planner_rejects_mixed_dates():
 from scripts.plan_completeness import plan
 import pytest
 with pytest.raises(ValueError,match='dates differ'):plan({'asof':'2026-10-15'},{'asof':'2026-10-14'})
