"""Shared fence contract. No network, credentials or production ledger."""
import copy
import datetime as dt
import pytest
from kis_shared.lease import initial,Blocked,REPOSITORIES
from kis_shared.runtime import Session
from kis_shared.boundary import authorize
class Clock:
    def __init__(self):self.at=dt.datetime(2026,11,3,10,tzinfo=dt.timezone.utc)
    def __call__(self):return self.at
    def sleep(self,n):self.at+=dt.timedelta(seconds=n)
class Backend:
    native_proof=True
    def __init__(self):self.state=initial();self.revision=0;self.unavailable=False
    def read(self):
        if self.unavailable:raise Blocked('fixture_ledger_unavailable')
        return str(self.revision),copy.deepcopy(self.state)
    def compare_and_append(self,revision,state):
        if revision!=str(self.revision):return False
        self.revision+=1;self.state=copy.deepcopy(state);return True
@pytest.fixture
def setup():
    clock=Clock();backend=Backend();env=dict(GITHUB_REPOSITORY=REPOSITORIES[2],GITHUB_RUN_ID='101',GITHUB_SHA='a'*40,KIS_APP_KEY='SYNTHETIC_ONLY',KIS_APP_SECRET='SYNTHETIC_ONLY',EXECUTION_MODE='NATIVE_PROOF',KIS_VALIDATION_REQUEST_ID='fixture-fixed')
    return Session(dict(mode='native-proof',token_expiry_timezone='UTC'),backend=backend,env=env,clock=clock),backend,clock,env
@pytest.mark.parametrize('url,method',[
 ('https://openapi.koreainvestment.com:9443/oauth2/tokenP','GET'),
 ('http://openapi.koreainvestment.com:9443/oauth2/tokenP','POST'),
 ('https://openapi.koreainvestment.com/oauth2/tokenP','POST'),
 ('https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/order-cash','POST'),
 ('https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/../trading/order','GET'),
 ('https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price','POST'),
 ('https://user@openapi.koreainvestment.com:9443/oauth2/tokenP','POST'),
 ('https://openapi.koreainvestment.com:9443/oauth2/tokenP#fragment','POST'),
 ('https://example.invalid/redirect','GET')])
def test_unreviewed_routes_never_authorized(url,method):
    with pytest.raises(Blocked):authorize(url,{'appkey':'SYNTHETIC_ONLY'},method=method)
def test_even_correct_oauth_route_requires_session():
    with pytest.raises(Blocked,match='session_not_installed'):authorize('https://openapi.koreainvestment.com:9443/oauth2/tokenP',method='POST')
def test_expired_owner_stops_before_auth(setup):
    session,backend,clock,_=setup
    with pytest.raises(Blocked):
        with session:clock.sleep(12000);session.before_http(auth=True)
    assert backend.state['last_auth'] is None and backend.state['owner'] is not None
def test_lost_owner_stops_before_auth(setup):
    session,backend,clock,_=setup
    with pytest.raises(Blocked):
        with session:backend.state['owner']['id']='b'*32;session.before_http(auth=True)
    assert backend.state['last_auth'] is None
def test_read_failure_never_reuses_cache(setup):
    session,backend,clock,_=setup
    with pytest.raises(Blocked):
        with session:backend.unavailable=True;session.before_http(auth=True)
    assert backend.state['last_auth'] is None
def test_actor_change_cannot_reuse_session(setup):
    session,backend,clock,env=setup
    with pytest.raises(Blocked):
        with session:env['GITHUB_RUN_ID']='999';session.before_http(auth=True)
    assert backend.state['last_auth'] is None
def test_raw_market_has_no_implicit_token_expiry(setup):
    session,backend,clock,_=setup
    with pytest.raises(Blocked,match='token_expiry'):
        with session:authorize('https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price',method='GET')
    assert backend.state['last_auth'] is None
def test_outcome_unknown_no_second_request(setup):
    session,_,_,_=setup
    with pytest.raises(Blocked,match='unresolved'):
        with session:
            session.before_http(auth=True);authorize('https://openapi.koreainvestment.com:9443/oauth2/tokenP',method='POST')
            session.before_http()
def test_closed_auth_one_attempt_and_no_refresh(setup):
    session,_,_,_=setup
    with pytest.raises(Blocked,match='one_attempt'):
        with session:session.before_http(auth=True);session.http_response_closed();session.before_http(auth=True)
def test_no_unreserved_low_level_send(setup):
    session,_,_,_=setup
    with pytest.raises(Blocked,match='low_level'):
        with session:authorize('https://openapi.koreainvestment.com:9443/oauth2/tokenP',method='POST',transport=True)
def test_no_scope_reset_on_new_process_or_output(setup):
    session,backend,clock,env=setup
    with session:pass
    second=Session(session.config,backend=backend,env={**env,'GITHUB_RUN_ID':'999'},clock=clock)
    with pytest.raises(Blocked,match='scope_already_claimed'):
        with second:pass
def test_schema_one_never_silently_enrolls_third_actor(setup):
    session,backend,clock,env=setup;backend.native_proof=False
    with pytest.raises(Blocked,match='participant_not_activated'):session.review()
def test_real_credential_environment_cannot_use_native_gate(setup):
    session,backend,clock,env=setup;env['KIS_APP_KEY']='NOT_SYNTHETIC'
    with pytest.raises(Blocked,match='synthetic_credentials'):session.review()
def test_schema_two_flags_alone_cannot_activate(setup):
    session,backend,clock,env=setup;backend.native_proof=False;session.config={'schema':2,'enabled':True}
    with pytest.raises(Blocked,match='activation_not_approved'):session.review()


def test_two_threads_cannot_both_reserve_oauth(setup):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    session,backend,clock,_=setup;session.acquire_lease();start=threading.Barrier(2)
    def request():
        start.wait()
        try:session.before_http(auth=True);return True
        except Blocked:return False
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(lambda _:request(),range(2)))
    assert sum(result)==1 and backend.state['sequence']==2

def test_unconsumed_reservation_cannot_be_replaced(setup):
    session,backend,clock,_=setup;session.acquire_lease();session.before_http(auth=True)
    clock.sleep(120)
    with pytest.raises(Blocked,match='unresolved'):session.before_http()
