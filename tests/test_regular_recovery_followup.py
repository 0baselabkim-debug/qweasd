"""Pinned second partial, real raw verification, zero provider network."""
import copy,datetime as dt,json,os,urllib.error
from pathlib import Path
import pytest
from collector_v1.regular import recovery_followup as r
from collector_v1.core import Store,Failure,encoded,sha
from collector_v1.experiment.storage import Metrics,events,Evidence
from collector_v1 import kis_manual
from collector_v1.providers import network_diagnostics,task
from collector_v1.validation import parse
START=dt.datetime.fromisoformat('2026-11-03T21:34:30+00:00')

def control():
 c=r.read_control();c.update(enabled=True,expected_revision='a'*40,reviewed_code_commit='b'*40,offline_ci_run='999');return c

@pytest.fixture(scope='module')
def restored(tmp_path_factory):
 path=Path(os.getenv('REGULAR_FOLLOWUP_SOURCE_ZIP','/workspace/regular-recovery-41649661831-diagnosis/artifact.zip'))
 out=tmp_path_factory.mktemp('verified-followup')/'source';proof=r.restore_zip(path,out)
 return path,out,proof

def test_real_latest_partial_and_budget(restored):
 _,root,p=restored
 assert p['validated_symbols']==1217 and p['successful_observations']==2437
 assert p['collection_http']==2440 and p['total_http']==2444 and p['remaining_tasks']==2261
 assert len(p['allowed_failure_hashes'])==4136
 assert p['total_http']+p['remaining_tasks']+1==4706<5600
 assert Store(root).read('manifest.json')['files']==r.inventory(root)

def test_corrupt_ZIP_never_resets_existing_budget(restored,tmp_path):
 path,_,_=restored;bad=tmp_path/'bad.zip';bad.write_bytes(path.read_bytes()+b'changed')
 with pytest.raises(Failure,match='zip_digest'):r.restore_zip(bad,tmp_path/'out')
 assert not (tmp_path/'out').exists()

@pytest.mark.parametrize('field,value',[('source_run','41633101454'),('artifact_id',13230751197),('max_total_http',5601),('asof','2026-11-04'),('enabled',False),('expires_at','2026-11-04T00:01:00+00:00')])
def test_new_control_pins_scope_and_nine_AM_cutoff(field,value):
 c=control();c[field]=value
 with pytest.raises(Failure):r.check_control(c,START)

def test_control_window_and_no_budget_increase():
 c=control();assert r.check_control(c,START)==c
 with pytest.raises(Failure):r.check_control(c,dt.datetime(2026,11,4,0,tzinfo=dt.timezone.utc))
 assert (r.stamp(c['expires_at'])-r.stamp(c['not_before'])).total_seconds()<=10200

def test_retry_review_only_exact_previous_guard_failures(restored):
 _,root,p=restored;review=r.ResumeReview(dict(status='RECOVERED_STOPPED_OWNER',source=p))
 fs=events(root/r.GENERATION/'collection/failures.jsonl')
 assert all(not review.permits(e) for e in fs if e['reason']=='network_failure')
 assert all(review.permits(e) for e in fs if e['reason']=='shared_prior_HTTP_outcome_unresolved')
 e=copy.deepcopy(fs[-1]);e['task']['asof']='2026-11-04';assert not review.permits(e)

def test_projection_only_counts_missing_requests():
 ts=[task(k,r.ASOF,round=1,symbol='060900',market='KOSDAQ',generation_id=r.GENERATION) for k in ('flow','price')]
 m=Metrics();m.seconds.update({'api:'+r.kis_manual.Q+'investor-trade-by-stock-daily':60,'api:'+r.kis_manual.Q+'inquire-daily-itemchartprice':40});m.counts.update({'api:'+r.kis_manual.Q+'investor-trade-by-stock-daily':10,'api:'+r.kis_manual.Q+'inquire-daily-itemchartprice':10})
 cache={r.engine.identity(ts[0]):{}}
 assert kis_manual.pending_projection(ts,cache,m)==4
 cache[r.engine.identity(ts[1])]={};assert kis_manual.pending_projection(ts,cache,m)==0

@pytest.mark.parametrize('error,reason,number',[(urllib.error.URLError(TimeoutError('SECRET_TOKEN')), 'TimeoutError',None),(urllib.error.URLError(ConnectionResetError(104,'SECRET_TOKEN')),'ConnectionResetError',104),(OSError(5,'SECRET_TOKEN'),None,5)])
def test_network_details_never_contain_exception_text_or_credentials(error,reason,number):
 value=network_diagnostics(error,'connect_or_response_headers')
 assert 'SECRET_TOKEN' not in json.dumps(value)
 if reason:assert value['reason_class']==reason
 if number:assert value['errno']==number
 assert value['phase']=='connect_or_response_headers'

def test_unknown_wire_failure_stops_before_inner_retry(tmp_path):
 from types import SimpleNamespace
 calls=[]
 def fail(t):calls.append(t);raise Failure('network_failure',True)
 api=SimpleNamespace(stop_on_unknown=True,store=SimpleNamespace(root=tmp_path),fetch=fail)
 budget=SimpleNamespace(check=lambda:None)
 with pytest.raises(Failure,match='network_failure'):r.engine.fetch_task(task('price',r.ASOF),api,budget,{}, {},Metrics())
 assert len(calls)==1 and len(events(tmp_path/'failures.jsonl'))==1

def test_one_shot_workflow_does_not_change_regular_schedule():
 w=Path('.github/workflows/kis-regular-recovery-followup-20261103.yml').read_text()
 assert '[execute-regular-followup]' in w and 'schedule:' not in w
 assert 'recovery_followup wait' in w and 'recovery_followup collect' in w
 assert 'cron: \'30 11 * * 1-5\'' in Path('.github/workflows/kis-market-2030.yml').read_text()
