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
