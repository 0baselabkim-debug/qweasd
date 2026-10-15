from decimal import Decimal
import pytest
from screener.journal_store import aggregate,valuation,validate_purchase,StoreError,JournalStore,ConflictError,display_decimal

def row(price,qty):return {'id':str(qty),'symbol':'005930','buy_date':'2025-01-02','average_price':price,'quantity':qty,'revision':1}

def test_weighted_price_no_intermediate_rounding_and_duplicate_purchases():
    p=aggregate([row('100.1234',1),row('200.0001',2)])[0]
    assert p['quantity']==3;assert p['cost']==Decimal('500.1236');assert p['average_price']==Decimal('500.1236')/3
    assert valuation(p,'200')['pnl']==Decimal('99.8764');assert display_decimal(Decimal('1.005'))=='1.01'
    assert valuation(p,None)=={'pnl':None,'return':None}

@pytest.mark.parametrize('symbol,date,price,quantity',[('1','2025-01-01',100,1),('005930','2999-01-01',100,1),('005930','2025-01-01',0,1),('005930','2025-01-01',100,0),('005930','2025-01-01','NaN',1),('005930','2025-01-01',100,1.2),('005930','2025-01-01','1.12345',1)])
def test_reject_invalid_input(symbol,date,price,quantity):
    with pytest.raises(StoreError):validate_purchase(symbol,date,price,quantity)

def test_user_identity_is_server_verified(monkeypatch):
    seen=[]
    def fake(self,path,*args,**kwargs):seen.append(path);return {'id':'verified-server-user'}
    monkeypatch.setattr(JournalStore,'call',fake)
    store=JournalStore('https://test.supabase.co','anon',{'access_token':'TEST_ONLY','user_id':'forged'})
    assert store.user_id=='verified-server-user' and seen==['/auth/v1/user']

def test_optimistic_conflict_does_not_report_saved(monkeypatch):
    store=object.__new__(JournalStore);monkeypatch.setattr(store,'call',lambda *a,**k:[])
    with pytest.raises(ConflictError):store.save('005930','2025-01-01',100,1,row(100,1))
    with pytest.raises(ConflictError):store.delete(row(100,1))
