import pytest
from pilot_client import Budget,BudgetExceeded

class Clock:
    def __init__(self):self.value=0
    def now(self):return self.value
    def sleep(self,n):self.value+=n

def test_auth_and_retries_share_physical_budget():
    clock=Clock();b=Budget(3,600,1.05,clock.now,clock.sleep)
    b.acquire('/oauth2/tokenP',0);b.acquire('/quote',0);b.acquire('/quote',1)
    with pytest.raises(BudgetExceeded):b.acquire('/quote',2)
    assert b.count==3 and len(b.metrics)==3
    assert b.metrics[0]['kind']=='authentication'

def test_spacing_and_time_limit():
    clock=Clock();b=Budget(40,2,1.05,clock.now,clock.sleep)
    b.acquire('/quote',0);b.acquire('/quote',0)
    assert clock.value==pytest.approx(1.05)
    with pytest.raises(BudgetExceeded):b.acquire('/quote',0)
    assert b.count==2

def test_backoff_cannot_cross_deadline():
    clock=Clock();b=Budget(40,10,1.05,clock.now,clock.sleep)
    clock.value=9
    with pytest.raises(BudgetExceeded):b.backoff(3)
    assert clock.value==9
