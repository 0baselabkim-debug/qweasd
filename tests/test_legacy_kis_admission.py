import io,json
from unittest.mock import Mock
import pytest
from kis_shared.lease import Blocked
from scripts.kis_public_client import KIS

class Reply(io.BytesIO):
    status=200;headers={}
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def setup(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY','TEST_KEY');monkeypatch.setenv('KIS_APP_SECRET','TEST_SECRET')
    monkeypatch.setattr('scripts.kis_public_client.time.sleep',lambda _:None)

def test_legacy_default_transport_cannot_authenticate_without_shared_session(monkeypatch):
    setup(monkeypatch);network=Mock();monkeypatch.setattr('scripts.kis_public_client.open_request',network)
    with pytest.raises(Blocked,match='shared_session_not_installed'):KIS()
    network.assert_not_called()

def test_legacy_owner_failure_blocks_before_injected_network(monkeypatch):
    setup(monkeypatch);shared=Mock();shared.before_http.side_effect=Blocked('owner_changed')
    monkeypatch.setattr('scripts.kis_public_client.account_session',lambda:shared);network=Mock();monkeypatch.setattr('scripts.kis_public_client.open_request',network)
    with pytest.raises(Blocked):KIS()
    network.assert_not_called()

def test_every_legacy_request_uses_shared_boundary_and_token_expiry_review(monkeypatch):
    setup(monkeypatch);shared=Mock();monkeypatch.setattr('scripts.kis_public_client.account_session',lambda:shared)
    responses=[dict(access_token='TEST_TOKEN'),dict(rt_cd='0')];calls=[]
    def fetch(req,timeout):calls.append(req.full_url);return Reply(json.dumps(responses.pop(0)).encode())
    monkeypatch.setattr('scripts.kis_public_client.open_request',fetch)
    api=KIS();api.request('/uapi/domestic-stock/v1/quotations/inquire-price',{'FID_INPUT_ISCD':'000720'},'TEST')
    assert len(calls)==2 and shared.before_http.call_count==2 and shared.http_response_closed.call_count==2
    shared.token_response.assert_called_once_with(dict(access_token='TEST_TOKEN'))

def test_unknown_legacy_network_outcome_never_retries(monkeypatch):
    setup(monkeypatch);shared=Mock();monkeypatch.setattr('scripts.kis_public_client.account_session',lambda:shared)
    network=Mock(side_effect=TimeoutError());monkeypatch.setattr('scripts.kis_public_client.open_request',network)
    with pytest.raises(RuntimeError,match='unknown_no_retry'):KIS()
    assert network.call_count==1 and shared.uncertain is True

def test_legacy_auth_headers_are_never_forwarded_across_redirects():
    from scripts.kis_public_client import NoRedirect
    assert NoRedirect().redirect_request(None,None,302,'redirect',{},'https://example.invalid') is None

@pytest.mark.parametrize('path,body',[('/uapi/domestic-stock/v1/trading/order-cash',None),('/uapi/domestic-stock/v1/quotations/../trading/order',None),('/uapi/domestic-stock/v1/quotations/inquire-price',{'bad':'POST'})])
def test_legacy_read_only_endpoint_restrictions(monkeypatch,path,body):
    setup(monkeypatch);api=object.__new__(KIS)
    with pytest.raises(ValueError):api.request(path,body=body)


def test_legacy_thread_preserves_original_session_and_request_budget(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from kis_shared.runtime import current
    from scripts.kis_public_client import REQUEST_BUDGET
    setup(monkeypatch);shared=Mock();budget=Mock()
    monkeypatch.setattr('scripts.kis_public_client.account_session',lambda:shared)
    responses=[dict(access_token='TEST_TOKEN'),dict(rt_cd='0')]
    def fetch(req,timeout):
        assert current() is shared and REQUEST_BUDGET.get() is budget
        return Reply(json.dumps(responses.pop(0)).encode())
    monkeypatch.setattr('scripts.kis_public_client.open_request',fetch)
    marker=REQUEST_BUDGET.set(budget)
    try:
        api=KIS()
        with ThreadPoolExecutor(1) as pool:pool.submit(api.request,'/uapi/domestic-stock/v1/quotations/inquire-price',{},'TEST').result()
    finally:REQUEST_BUDGET.reset(marker)
    assert budget.before_http.call_count==2


def test_low_level_legacy_helper_also_requires_session_before_network(monkeypatch):
    import urllib.request
    from scripts.kis_public_client import open_request
    transport=Mock();monkeypatch.setattr(urllib.request.HTTPSHandler,'https_open',transport)
    with pytest.raises(Blocked,match='session_not_installed'):
        open_request(urllib.request.Request('https://openapi.koreainvestment.com:9443/oauth2/tokenP',data=b'{}'),30)
    transport.assert_not_called()


def test_historical_inline_kis_bodies_are_hard_disabled():
    import ast
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for name in ('kis-20-validation','kis-connectivity','kis-finality-probe','kis-five-status','kis-samsung-sample'):
        workflow=json.loads((root/'.github/workflows'/f'{name}.yml').read_bytes())
        bodies=[step['run'] for job in workflow['jobs'].values() for step in job['steps'] if any(marker in step.get('run','') for marker in ('openapi.koreainvestment','oauth2/token'))]
        assert len(bodies)==1
        source=bodies[0].split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        tree=ast.parse(source)
        assert isinstance(tree.body[0],ast.Raise) and isinstance(tree.body[0].exc,ast.Call) and tree.body[0].exc.func.id=='SystemExit'


def test_retired_github_helpers_block_before_every_kis_transport():
    import ast
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    for name,function in [('kis_enrich_collect','request'),('kis_history_probe','call'),('kis_collect_once','api'),('kis_missing_analysis','api')]:
        tree=ast.parse((root/'.github/scripts'/f'{name}.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==function)
        assert isinstance(node.body[0],ast.Raise)
