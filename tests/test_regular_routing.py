"""Synthetic routing metadata; no provider network or production writes."""
import copy,datetime as dt
from concurrent.futures import ThreadPoolExecutor
import pytest
from collector_v1.regular import routing,policy
from collector_v1.regular.ledger import Ledger
from collector_v1.core import encoded,sha,Failure
try:
 from regular_fixtures import Client,context
except ImportError:
 from regular_live_fixtures import Client,context


def decision(actor=routing.I):
 return dict(schema=routing.SCHEMA,status='GO',actor=actor,slot='2030',cron=policy.CRON,effective_from='2026-11-04',
  decided_at='2026-11-04T10:00:00+09:00',expires_at='2027-01-27T00:00:00Z',checks={k:'PASS' for k in routing.CHECKS},
  approval='USER_APPROVED_REGULAR_COLLECTION_CUTOVER_20261104',market_use_approval='USER_APPROVED_KIS_REGULAR_FULL_5600_PER_GENERATION',
  max_total_http=5600,interval_seconds=1.05,source_proofs={routing.Q:'a'*64,routing.I:'b'*64})

@pytest.fixture
def env(monkeypatch):
 monkeypatch.setattr(policy,'utcnow',lambda:policy.timestamp('2026-11-04T11:30:01Z'))
 c=context('2026-11-04');return c,Client(c)


def test_missing_control_preserves_existing_producer(env):
 c,client=env
 assert routing.bind(client,c,routing.Q) and c['routing_revision']=='ABSENT'
 routing.recheck(client,c,routing.Q)
 assert not routing.bind(client,dict(c),routing.I)


def test_old_and_new_share_one_date_slot_and_CAS(env):
 c,client=env;client.docs[routing.PATH]=decision()
 q=dict(c,source_repository=routing.Q);i=dict(c,run_id='100',source_repository=routing.I)
 assert not routing.bind(client,q,routing.Q) and routing.bind(client,i,routing.I)
 with ThreadPoolExecutor(2) as p:results=list(p.map(lambda x:Ledger(client,x).claim()[0],[q,i]))
 assert results.count(True)==1
 before=copy.deepcopy(client.docs)
 assert not Ledger(client,dict(i,run_id='101')).claim()[0] and client.docs==before


@pytest.mark.parametrize('changes',[{'status':'PARTIAL'},{'actor':'unknown'},{'max_total_http':5601},{'interval_seconds':.1},
 {'decided_at':'2026-11-04T19:30:01+09:00'},{'expires_at':'2026-11-04T00:00:00Z'},
 {'checks':{}},{'source_proofs':{}},{'approval':'UNKNOWN'},{'cron':'0 12 * * *'}])
def test_bad_or_unapproved_routing_fails_closed(env,changes):
 c,client=env;client.docs[routing.PATH]=dict(decision(),**changes)
 with pytest.raises((Failure,KeyError)):routing.bind(client,c,routing.Q)
 assert 'ledger/2026.json' not in client.docs


def test_route_change_after_freeze_blocks_before_claim(env):
 c,client=env;client.docs[routing.PATH]=decision();routing.bind(client,c,routing.I)
 client.docs[routing.PATH]['actor']=routing.Q
 with pytest.raises(Failure):routing.recheck(client,c,routing.I)


def test_source_hash_change_is_not_a_grant(env):
 c,client=env;client.docs[routing.PATH]=decision()
 with pytest.raises(Failure):routing.bind(client,c,routing.I,config=dict(native_proof_sha256='c'*64))
 assert routing.bind(client,c,routing.I,config=dict(native_proof_sha256='b'*64))


def test_verified_prestart_handover_preserves_failed_attempt(env):
 c,client=env;first=dict(c,source_repository=routing.I)
 ledger=Ledger(client,first);ledger.claim();ledger.transition('FAILED',reason='SYNTHETIC_BEFORE_HTTP')
 year=client.docs['ledger/2026.json'];old=year['slots'][ledger.key]
 old.update(status='PRESTART_RELEASED',prestart_handover=dict(schema='verified-zero-http-handover-v1',asof=c['asof'],source_run=c['run_id'],
  total_physical_http=0,all_workers_stopped=True,central_owner_clear=True,source_artifact_verified=True,successor_repository=routing.Q))
 next_context=dict(c,run_id='100',source_repository=routing.Q)
 assert Ledger(client,next_context).claim()[0]
 current=Ledger(client,next_context).current()
 assert current['previous_attempts'][0]['context']==first
 assert current['previous_attempts'][0]['history'][-1]['status']=='FAILED'
 assert current['context']==next_context


@pytest.mark.parametrize('field,value',[('total_physical_http',1),('all_workers_stopped',False),('central_owner_clear',False),('source_artifact_verified',False),('successor_repository',routing.I),('source_run','other')])
def test_partial_or_unproved_handover_never_resets_budget(env,field,value):
 c,client=env;ledger=Ledger(client,c);ledger.claim();ledger.transition('FAILED',reason='SYNTHETIC')
 old=client.docs['ledger/2026.json']['slots'][ledger.key]
 proof=dict(schema='verified-zero-http-handover-v1',asof=c['asof'],source_run=c['run_id'],total_physical_http=0,all_workers_stopped=True,
  central_owner_clear=True,source_artifact_verified=True,successor_repository=routing.Q)
 proof[field]=value;old.update(status='PRESTART_RELEASED',prestart_handover=proof)
 before=copy.deepcopy(client.docs)
 with pytest.raises(Failure):Ledger(client,dict(c,run_id='101')).claim()
 assert client.docs==before


def test_repository_dispatch_is_dated_zero_http_approval_only(env):
 c,client=env;run=dict(id=100,event='repository_dispatch',created_at='2026-11-04T11:31:00Z',head_sha='b'*40)
 payload=dict(asof='2026-11-04',request_id='SYNTHETIC_HANDOVER',approval='USER_APPROVED_ZERO_HTTP_HANDOVER',source_run='99',source_artifact_digest='c'*64)
 result=policy.handover_start(run,payload)
 assert result['trigger']=='repository_dispatch' and result['handover_source_run']=='99' and policy.control(result)['max_total_http']==5600
 for changes in [dict(asof='2026-11-03'),dict(approval='UNKNOWN'),dict(max_total_http=9999)]:
  with pytest.raises(Failure):policy.handover_start(run,dict(payload,**changes))
