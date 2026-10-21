import datetime as dt,json
import pytest
import kis_version_probe as probe
from kis_version_analysis import classify,validate_new
UTC=dt.timezone.utc
@pytest.mark.parametrize('value',['2026-10-21T13:59:59+00:00','2026-10-21T00:00:00+00:00','2026-10-22T00:00:00+00:00'])
def test_before23_and_daytime_blocked(value):
 with pytest.raises(RuntimeError):probe.time_gate(dt.datetime.fromisoformat(value))
@pytest.mark.parametrize('value',['2026-10-21T14:00:00+00:00','2026-10-21T23:59:59+00:00'])
def test_allowed_overnight(value):probe.time_gate(dt.datetime.fromisoformat(value))
def test_queued_operation_and_own_run():
 jobs=[[{'id':1,'name':'Daily validated dashboard refresh','status':'queued','head_branch':'main'},{'id':2,'name':'Research','status':'in_progress','head_branch':'research'}]]
 assert len(probe.active_operations(jobs,2))==1
 with pytest.raises(RuntimeError):probe.active_operations([[{'id':1}]*100],2)
def test_failed_request_is_never_replayed_and_budget_survives(tmp_path):
 p=probe.Probe(tmp_path,lambda:None,lambda:{})
 p.ledger['requests']=[{'key':str(i),'status':'rejected','authentication':i==0} for i in range(5)];p.checkpoint()
 q=probe.Probe(tmp_path,lambda:None,lambda:{})
 assert q.request('0') is None
 with pytest.raises(RuntimeError,match='physical_request_limit'):q.request('new')
def test_before23_does_not_authenticate(tmp_path,monkeypatch):
 p=probe.Probe(tmp_path,lambda:None,lambda:pytest.fail('should not even query active jobs'))
 monkeypatch.setattr(probe,'time_gate',lambda t:(_ for _ in ()).throw(RuntimeError('before23')))
 p.run();assert not p.ledger['requests'] and p.ledger['status']=='blocked'
def test_reserved_attempt_blocks_restart(tmp_path):
 p=probe.Probe(tmp_path,lambda:None,lambda:{})
 p.ledger['requests']=[{'status':'reserved'}];p.checkpoint()
 with pytest.raises(RuntimeError,match='uncertain'):probe.Probe(tmp_path,lambda:None,lambda:{})
def test_scope_before_gate(tmp_path):
 p=probe.Probe(tmp_path,lambda:None,lambda:pytest.fail('not allowed'))
 with pytest.raises(RuntimeError,match='outside_authorization'):p.request('bad',{'FID_INPUT_ISCD':'005930','FID_ORG_ADJ_PRC':'0'})
 assert not p.ledger['requests']
def fixture():
 p={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':'095570','FID_INPUT_DATE_1':probe.START,'FID_INPUT_DATE_2':probe.END,'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0'}
 e={'params':p,'http':200,'endpoint':probe.HOST+probe.CHART}
 x={'rt_cd':'0','output1':{'stck_shrn_iscd':'095570'},'output2':[{'stck_bsop_date':'20261020','stck_oprc':'10','stck_hgpr':'12','stck_lwpr':'8','stck_clpr':'11','acml_vol':'100','acml_tr_pbmn':'1000'}]}
 return e,x
@pytest.mark.parametrize('bad',['missing_day','duplicate','wrong_symbol','wrong_scope','invalid_ohlc','future','api_error'])
def test_bad_observations_never_use_other_day(bad):
 e,x=fixture()
 if bad=='missing_day':x['output2'][0]['stck_bsop_date']='20261019'
 elif bad=='duplicate':x['output2']*=2
 elif bad=='wrong_symbol':x['output1']['stck_shrn_iscd']='006840'
 elif bad=='wrong_scope':e['params']['FID_COND_MRKT_DIV_CODE']='UN'
 elif bad=='invalid_ohlc':x['output2'][0]['stck_clpr']='30'
 elif bad=='future':x['output2'][0]['stck_bsop_date']='20261021'
 else:x['rt_cd']='1'
 with pytest.raises(ValueError):validate_new(e,x,'095570','0')
def test_full_six_fields_and_missing_are_distinct():
 e,x=fixture();a,_=validate_new(e,x,'095570','0');b={**a,'close':10};c={**a,'volume':101}
 assert classify(b,a,a,a).startswith('1_')
 assert classify(b,a,a,b).startswith('2_')
 assert classify(b,a,b,a).startswith('3_')
 assert classify(b,a,b,c).startswith('4_')
 assert classify(b,a,b,b).startswith('5_')
 assert classify(b,a,None,a).startswith('6_')
 assert classify(b,a,c,c).startswith('추가유형')

def test_nonfinite_observation_rejected():
 e,x=fixture();x['output2'][0]['acml_vol']='inf'
 with pytest.raises(ValueError):validate_new(e,x,'095570','0')
