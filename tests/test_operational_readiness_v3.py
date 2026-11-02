"""Deployment readiness uses GitHub metadata only; no KIS credential values."""
import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('readiness_v3',ROOT/'.github/scripts/kis_lock_operational_readiness_v3.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
@pytest.fixture
def env(tmp_path):
    cfg=json.loads((ROOT/'.github/config/kis-shared-account-v2.proposed.json').read_text())
    proof=json.loads((ROOT/cfg['native_proof_file']).read_text())
    repo=next(k for k,v in proof['runtime_source_hashes'].items() if v==cfg['runtime_source_hashes'])
    return dict(GITHUB_REPOSITORY=repo,GITHUB_RUN_ID='123',GITHUB_SHA='a'*40,RUNNER_TEMP=str(tmp_path),GH_TOKEN='SYNTHETIC_ONLY',KIS_APP_KEY_PRESENT='true',KIS_APP_SECRET_PRESENT='true')
class Backend:
    def __init__(self,*a,**kw):
        from kis_shared.lease import initial
        self.state=initial()
    def read(self):return 'a'*40,self.state

def api(path):return dict(full_name=r.CENTRAL) if path=='repos/'+r.CENTRAL else dict(workflow_runs=[])
def test_actual_source_contract_and_readonly_ledger(env):
    result=r.inspect(ROOT,env,api=api,backend_type=Backend)
    assert result['status']=='PASS_READONLY' and result['production_lease_mutations']==0 and result['actual_KIS_HTTP']==0
    assert result['candidate_source_hashes_valid'] and not result['live_validation_enabled']
@pytest.mark.parametrize('name',['KIS_APP_KEY_PRESENT','KIS_APP_SECRET_PRESENT','GH_TOKEN'])
def test_missing_runtime_binding_never_approves_probe(env,name):
    env[name]='';result=r.inspect(ROOT,env,api=api,backend_type=Backend)
    assert result['status']=='BLOCKED' and result['blockers']
    wire=Mock()
    with pytest.raises(r.Blocked):r.probe(ROOT,env,{},result,api=wire,backend_type=wire)
    wire.assert_not_called()
def test_unknown_actor_stops_before_GitHub(env):
    env['GITHUB_REPOSITORY']='other/repo';wire=Mock()
    with pytest.raises(r.Blocked):r.inspect(ROOT,env,api=wire)
    wire.assert_not_called()
def test_remote_access_failure_is_fail_closed(env):
    with pytest.raises(r.Blocked):r.inspect(ROOT,env,api=Mock(side_effect=r.Blocked('fixture_denied')),backend_type=Backend)
@pytest.mark.parametrize('branch',[r.BRANCH,'main','kis-shared-probe-other','../main'])
def test_probe_cannot_write_production_or_unreviewed_destination(env,branch):
    wire=Mock()
    with pytest.raises(r.Blocked):r.probe(ROOT,env,dict(probe_branch=branch,allow_isolated_GitHub_probe=True),dict(status='PASS_READONLY'),api=wire,backend_type=wire)
    wire.assert_not_called()
def test_probe_requires_explicit_local_control(env):
    with pytest.raises(r.Blocked):r.probe(ROOT,env,dict(probe_branch='kis-shared-probe-v3-fixture'),dict(status='PASS_READONLY'),backend_type=Mock())
@pytest.mark.parametrize('event',['socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.sendto','http.client.connect'])
def test_Python_network_forbidden_independent_of_credential_flags(event):
    with pytest.raises(r.Blocked):r.deny_network(event,())
def test_GitHub_route_guard_never_accepts_market_or_secret_mutations(monkeypatch):
    process=Mock();monkeypatch.setattr(r.subprocess,'run',process)
    for path,method,body in [('https://openapi.koreainvestment.com:9443/oauth2/tokenP','POST',None),('repos/'+r.CENTRAL+'/actions/secrets','POST',{}),('repos/'+r.CENTRAL+'/git/refs','POST',dict(ref='refs/heads/main'))]:
        with pytest.raises(r.Blocked):r.gh(path,method,body)
    process.assert_not_called()


def quote_payload():
    return dict(rt_cd='0',output=dict(stck_shrn_iscd='000720',stck_prpr='100',stck_oprc='99',stck_hgpr='101',stck_lwpr='98',acml_vol='1000',acml_tr_pbmn='100000'))
@pytest.mark.parametrize('kind',['business_error','empty','missing','non_integer','wrong_symbol','invalid_range'])
def test_limited_quote_never_accepts_provider_errors_or_invalid_evidence(kind):
    from kis_shared.worker import validate_quote
    value=quote_payload()
    if kind=='business_error':value['rt_cd']='1'
    if kind=='empty':value['output']={}
    if kind=='missing':value['output'].pop('acml_vol')
    if kind=='non_integer':value['output']['acml_vol']='1.5'
    if kind=='wrong_symbol':value['output']['stck_shrn_iscd']='005930'
    if kind=='invalid_range':value['output']['stck_hgpr']='97'
    with pytest.raises(r.Blocked):validate_quote(json.dumps(value).encode())
def test_limited_quote_retains_unverified_finality_and_unknown_date():
    from kis_shared.worker import validate_quote
    result=validate_quote(json.dumps(quote_payload()).encode());assert result['official_price_status']=='PENDING_KRX' and result['provider_business_date']=='UNAVAILABLE'
def test_limited_quote_missing_symbol_is_explicitly_request_bound_only():
    from kis_shared.worker import validate_quote
    value=quote_payload();value['output'].pop('stck_shrn_iscd')
    assert validate_quote(json.dumps(value).encode())['response_symbol']=='REQUEST_BOUND_ONLY'
