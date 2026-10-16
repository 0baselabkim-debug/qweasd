from test_dashboard import history
import io,json,urllib.error
from pathlib import Path
from scripts.technical_reference import fetch_references
from scripts.technical_batch import read,write

class Reply(io.BytesIO):
    status=200
    headers={'Content-Type':'application/json'}
    def __init__(self,p):super().__init__(json.dumps(p).encode())

def test_auth_denial_stops_other_markets_and_next_run(tmp_path,monkeypatch):
    monkeypatch.setenv('KRX_API_KEY','test-secret');calls=[]
    def deny(req,**kw):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url,403,'Forbidden',{'Content-Type':'text/html'},io.BytesIO(b'Forbidden'))
    monkeypatch.setattr('scripts.technical_reference.urllib.request.urlopen',deny)
    refs,status=fetch_references('2026-10-15',tmp_path,refresh=True)
    assert not refs and len(calls)==1
    assert set(status.values())=={'REFERENCE_AUTH_ERROR'}
    fetch_references('2026-10-14',tmp_path,refresh=True)
    assert len(calls)==1
    checks=read(tmp_path/'reference-status.json')['checks']
    assert checks[-1]['request_attempted'] is False and checks[-1]['retry_after']
    assert 'test-secret' not in (tmp_path/'reference-status.json').read_text()

def test_temporary_failure_is_not_auth_failure(tmp_path,monkeypatch):
    monkeypatch.setenv('KRX_API_KEY','test-secret')
    def fail(*a,**k):raise TimeoutError()
    monkeypatch.setattr('scripts.technical_reference.urllib.request.urlopen',fail)
    _,status=fetch_references('2026-10-15',tmp_path,refresh=True)
    assert set(status.values())=={'REFERENCE_TEMPORARY_FAILURE'}

def test_fresh_recovery_never_falls_back_to_cached_reference(tmp_path,monkeypatch):
    from screener.technical_quality import FIELDS
    day='2026-10-15';row={v:'1' for v in FIELDS.values()};row.update(BAS_DD='20261015',ISU_CD='000001')
    original={'asof':day,'observed_at':'2026-10-16T00:00:00+00:00','url':'https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20261015','rows':[row]}
    path=tmp_path/'reference'/day/'KOSPI.json.gz';write(path,original);before=path.read_bytes()
    monkeypatch.setenv('KRX_API_KEY','test-secret')
    def deny(req,**kw):raise urllib.error.HTTPError(req.full_url,403,'Forbidden',{},io.BytesIO(b'Forbidden'))
    monkeypatch.setattr('scripts.technical_reference.urllib.request.urlopen',deny)
    refs,_=fetch_references(day,tmp_path,refresh=True)
    assert not refs and path.read_bytes()==before

def test_run_status_requires_publication_and_preserves_mixed_outcomes():
    from scripts.recovery_outcome import outcome
    report={'promoted':1,'results':[{'promoted':True},{'promoted':False,'error_type':'RecoveryBudget'}],'reference_status':{}}
    r=outcome(report,published=False)
    assert r['status']!='RECOVERED' and r['counts']['BUDGET_PAUSED']==1
    assert outcome(report,published=True)['status']=='RECOVERED'
    assert outcome(report,publication_error=True)['status']=='PUBLICATION_FAILED'
    blocked={'results':[],'promoted':0,'reference_status':{'2026-10-15':{'KOSPI':'REFERENCE_AUTH_ERROR'}}}
    assert outcome(blocked,published=True)['status']=='PROVIDER_BLOCKED'
    assert outcome({'results':[],'promoted':0,'reference_status':{}},published=True)['status']=='NO_ELIGIBLE_WORK'

def test_sector_gap_diagnostic_uses_same_25_day_mask(history):
    from scripts.diagnose_activity import diagnose
    from screener.indicators import universe,stock_activity
    day='2026-10-14';history['metadata']['adjusted_price_conflicts']={day:[{'symbol':'000001'}]}
    report=diagnose(history,day)
    x,_=universe(history,day);x=stock_activity(history,x,day)
    assert report['stock_activity']==int(x.activity_ratio.notna().sum())
    row=next(r for r in report['sectors'] if r['sector']=='A')
    assert row['minimum_additional_date_symbol_checks']==1
    assert row['blocking_dates']==[day]
    assert row['reason_symbol_counts']['unresolved_price_conflict']==1

def test_correction_document_identity_and_no_final_acceptance():
    import zipfile
    from scripts.review_corrections import extract
    f={'receipt':'20260605000456','corp_code':'12345678'}
    xml='<DOCUMENT><DOCUMENT-NAME>사업보고서</DOCUMENT-NAME><COMPANY-NAME AREGCIK="12345678">기업</COMPANY-NAME><SECTION-2><TITLE>4. 재무제표</TITLE><P>(단위 : 원) 2025-12-31 자본총계 100</P></SECTION-2></DOCUMENT>'
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr(f['receipt']+'.xml',xml)
    r=extract(b.getvalue(),f)
    assert r['documents'][0]['identity_verified']
    assert '4. 재무제표' in r['documents'][0]['financial_sections']
    assert r['final_financial_acceptance'] is False
    assert not extract(b.getvalue(),dict(f,corp_code='87654321'))['documents'][0]['identity_verified']
    assert extract(b'<result><status>014</status></result>',f)['status']=='DART_014'

def test_reference_backoff_expiry_resumes_without_relaxing_schema(tmp_path,monkeypatch):
    import datetime as dt
    import scripts.technical_reference as ref
    from screener.technical_quality import FIELDS
    clock=dt.datetime(2026,10,16,tzinfo=dt.timezone.utc)
    monkeypatch.setattr(ref,'utcnow',lambda:clock)
    monkeypatch.setenv('KRX_API_KEY','test-secret')
    def deny(req,**kw):raise urllib.error.HTTPError(req.full_url,403,'Forbidden',{},io.BytesIO(b'Forbidden'))
    monkeypatch.setattr(ref.urllib.request,'urlopen',deny)
    fetch_references('2026-10-15',tmp_path,refresh=True)
    clock+=dt.timedelta(hours=2)
    row={v:'1' for v in FIELDS.values()};row.update(BAS_DD='20261015',ISU_CD='000001')
    monkeypatch.setattr(ref.urllib.request,'urlopen',lambda *a,**k:Reply({'OutBlock_1':[row]}))
    refs,status=fetch_references('2026-10-15',tmp_path,refresh=True)
    assert refs and set(status.values())=={'REFERENCE_RECEIVED'}
    assert 'provider_backoff' not in read(tmp_path/'reference-status.json')

def test_document_reviews_append_without_clearing_failed_jobs():
    from scripts.publish_document_reviews import add_reviews
    state={'filings':{'r':{'receipt':'r'}},'financials':{},'reviews':{},'jobs':{'r':{'financial_status':'failed'}},'events':[]}
    report={'corrections':[],'attachments':[{'receipt':'r','parent_receipt':'p','filing':state['filings']['r'],'status':'REVIEW_NEEDED','evidence':{'status':'DART_014'}}]}
    new,added=add_reviews(state,report)
    assert added==['r'] and new['jobs']==state['jobs'] and len(new['events'])==1
    again,added=add_reviews(new,report)
    assert not added and again==new
