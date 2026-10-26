"""Synthetic fixtures ONLY. No market requests and no operating snapshot writes."""
import contextlib
import datetime as dt
import gzip
import io
import json
from pathlib import Path
import urllib.error
import zipfile
import pytest
from collector_v1.core import Store, Budget, Failure, Stop, encoded, sha, collect_tasks, publish
from collector_v1.providers import Providers, task, LAYOUTS, market_tasks
from collector_v1.validation import validate, parse, master
from collector_v1.__main__ import main

DAY='2026-10-14'
OBS='2026-10-15T01:00:00+00:00'

@pytest.fixture
def store(tmp_path):return Store(tmp_path/'collector')

@pytest.fixture
def budget(store,monkeypatch):
    monkeypatch.setattr('collector_v1.core.time.sleep',lambda _:None)
    return Budget(store)


def zip_bytes(member,raw):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr(member,raw)
    return out.getvalue()


def master_bytes(market='KOSPI',symbol='005930',halt='N'):
    l=LAYOUTS[market];vals=['0']*len(l['widths'])
    vals[l['group_index']]='ST';vals[l['preferred_index']]='0';vals[l['listing_date_index']]='20000101'
    for i,c in enumerate(l['columns']):
        if c in ('거래정지','거래정지 여부','관리종목','관리 종목 여부','정리매매','정리매매 여부','SPAC','기업인수목적회사여부'):vals[i]='N'
        if c in ('거래정지','거래정지 여부'):vals[i]=halt
    line=symbol.ljust(9)+'KR0000000000'+'SYNTHETIC TEST ONLY'+''.join(v.ljust(n) for v,n in zip(vals,l['widths']))
    return zip_bytes(l['member'],line.encode('cp949'))


def seed(store, t, payload, observed=OBS):
    raw=payload if isinstance(payload,bytes) else encoded(payload)
    e=store.preserve(raw,dict(task=t,source='SYNTHETIC_TEST_ONLY',source_run='test-only',collected_at=observed))
    state=store.read('state.json',{'tasks':{}})
    state['tasks'][sha(encoded(t))]=dict(task=t,collection_status='received',evidence=e)
    store.write('state.json',state)
    return e


def market_fixture(store, conflict=False, missing=False):
    tasks=[task('master',DAY,market='KOSPI'),task('calendar',DAY),task('krx',DAY,market='KOSPI'),task('flow',DAY,symbol='005930',market='KOSPI'),task('price',DAY,symbol='005930',market='KOSPI')]
    seed(store,tasks[0],master_bytes(),observed='2026-10-14T01:00:00+00:00')
    seed(store,tasks[1],{'rt_cd':'0','output':[{'bass_dt':'20261014','opnd_yn':'Y'}]})
    r=dict(BAS_DD='20261014',ISU_CD='005930',TDD_OPNPRC='100',TDD_HGPRC='110',TDD_LWPRC='90',TDD_CLSPRC='105',ACC_TRDVOL='10',ACC_TRDVAL='1050',MKTCAP='100000')
    if conflict:r['TDD_CLSPRC']='106'
    seed(store,tasks[2],{'OutBlock_1':[r]})
    flow=dict(stck_bsop_date='20261014',frgn_ntby_tr_pbmn='-2',frgn_ntby_qty='-3',orgn_ntby_qty='0',orgn_shnu_vol='1',orgn_seln_vol='1',orgn_ntby_tr_pbmn='999',stck_clpr='105',acml_vol='10',acml_tr_pbmn='1050')
    if missing:flow['frgn_ntby_qty']=''
    seed(store,tasks[3],{'rt_cd':'0','output2':[flow]})
    price=dict(stck_bsop_date='20261014',stck_oprc='100',stck_hgpr='110',stck_lwpr='90',stck_clpr='105',acml_vol='10',acml_tr_pbmn='1050',mod_yn='N')
    seed(store,tasks[4],{'rt_cd':'0','output1':{'stck_shrn_iscd':'005930'},'output2':[price]})
    return tasks


def test_full_market_validation_and_atomic_idempotent_publication(store):
    rows,report=validate(store,market_fixture(store))
    assert report['held']==0
    flow=next(r['value'] for r in rows if r['kind']=='flow')
    assert flow['foreign_net_amount']==-2000000 and flow['institution_net_quantity']==0
    assert flow['institution_net_amount'] is None
    manifest=publish(store,'test-generation',rows,report)
    before=(store.root/'published.json').read_bytes()
    assert publish(store,'test-generation',rows,report)==manifest
    assert (store.root/'published.json').read_bytes()==before
    import duckdb
    with duckdb.connect(str(store.root/'generations/test-generation/data.duckdb'),read_only=True) as c:
        assert c.execute('select count(*) from observations').fetchone()[0]==len(rows)

@pytest.mark.parametrize('problem',['conflict','missing','date','symbol','duplicate','volume','hash','calendar','market','vintage'])
def test_bad_evidence_cannot_publish(store,problem):
    tasks=market_fixture(store,conflict=problem=='conflict',missing=problem=='missing')
    if problem=='hash':
        e=store.read('state.json')['tasks'][sha(encoded(tasks[-1]))]['evidence']
        (store.root/'raw'/(e['source_hash']+'.gz')).write_bytes(gzip.compress(b'{}'))
    elif problem not in ('conflict','missing'):
        t=tasks[-1];e=store.read('state.json')['tasks'][sha(encoded(t))]['evidence'];d=json.loads(store.raw(e))
        if problem=='date':d['output2'][0]['stck_bsop_date']='20261013'
        if problem=='symbol':d['output1']['stck_shrn_iscd']='000000'
        if problem=='duplicate':d['output2']*=2
        if problem=='volume':d['output2'][0]['acml_vol']='11'
        if problem=='vintage':e['collected_at']='2026-10-17T01:00:00+00:00'
        if problem=='calendar':
            t=tasks[1];d={'rt_cd':'0','output':[{'bass_dt':'20261014','opnd_yn':'N'}]}
        if problem=='market':
            t=tasks[2];d=json.loads(store.raw(store.read('state.json')['tasks'][sha(encoded(t))]['evidence']));d['OutBlock_1'][0]['MKT_NM']='KOSDAQ'
        seed(store,t,d,e['collected_at'])
    rows,report=validate(store,tasks)
    assert report['held']>0
    with pytest.raises(Failure):publish(store,'bad',rows,report)
    assert not (store.root/'published.json').exists()


def test_failed_publish_preserves_previous_and_recovers(store,monkeypatch):
    rows,r=validate(store,market_fixture(store));publish(store,'good',rows,r)
    old=(store.root/'published.json').read_bytes()
    def broken(*args):raise OSError('synthetic DB failure')
    with pytest.raises(OSError):publish(store,'next',rows,r,connect=broken)
    assert (store.root/'published.json').read_bytes()==old
    import collector_v1.core as core
    real=core.os.replace
    def fail_pointer(src,dst):
        if Path(dst).name=='published.json':raise OSError('synthetic publication failure')
        return real(src,dst)
    monkeypatch.setattr(core.os,'replace',fail_pointer)
    with pytest.raises(OSError):publish(store,'next',rows,r)
    assert (store.root/'published.json').read_bytes()==old
    monkeypatch.setattr(core.os,'replace',real)
    publish(store,'next',rows,r)
    assert store.read('published.json')['generation']=='next'


def test_budget_persisted_before_http_auth_and_retry_counted(store,budget,monkeypatch):
    for n in ('KIS_APP_KEY','KIS_APP_SECRET'):monkeypatch.setenv(n,'synthetic-'+n)
    class Response:
        status=200
        def __init__(self,raw):self.raw=raw
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return self.raw
    replies=[encoded({'access_token':'synthetic-token'}),encoded({'rt_cd':'0','output':[]}),encoded({'rt_cd':'0','output':[]})]
    def opener(req,timeout):
        assert store.read('requests.json')[-1]['status']=='attempted'
        return Response(replies.pop(0))
    api=Providers(store,budget,opener)
    api.fetch(task('calendar',DAY))
    api.fetch(task('calendar',DAY))
    assert budget.used==3 and len(store.read('requests.json'))==3
    assert b'synthetic-token' not in b''.join(p.read_bytes() for p in store.root.rglob('*.json'))
    budget.calls=3
    with pytest.raises(Stop):api.fetch(task('calendar',DAY))


def test_rate_limit_and_network_failures_are_accounted(store,budget):
    def limited(req,timeout):raise urllib.error.HTTPError(req.full_url,429,'limited',{},None)
    api=Providers(store,budget,limited)
    with pytest.raises(Failure) as e:api.http('FRED','/graph/graph.csv','https://fred.stlouisfed.org/graph/graph.csv')
    assert e.value.retryable and budget.used==1
    assert store.read('requests.json')[-1]['http']==429


def test_resume_only_failed_or_pending_and_operator_stop(store,budget):
    tasks=[task('calendar',DAY),task('index',DAY,market='KOSPI')];calls=[]
    def fetch(t):
        calls.append(t)
        if t['kind']=='index':raise Failure('network_failure',True)
        return seed(store,t,{'rt_cd':'0','output':[]})
    report=collect_tasks(store,tasks,fetch,budget)
    assert report['received']==1 and report['collection_status']=='partial'
    collect_tasks(store,tasks,fetch,budget);assert len(calls)==2  # cooldown honored
    state=store.read('state.json');state['tasks'][sha(encoded(tasks[1]))]['retry_after']='';store.write('state.json',state)
    def good(t):calls.append(t);return seed(store,t,{'rt_cd':'0'})
    assert collect_tasks(store,tasks,good,budget)['collection_status']=='complete'
    assert len(calls)==3
    (store.root/'STOP').touch()
    assert collect_tasks(store,[task('index',DAY,market='KOSDAQ')],good,budget)['stopped']=='operator_stop'


def test_total_failure_and_time_limit(store,budget):
    t=[task('calendar',DAY)]
    def failed(t):raise Failure('authentication_failure')
    report=collect_tasks(store,t,failed,budget)
    assert report['received']==0 and report['tasks'][0]['collection_status']=='blocked'
    budget.started-=1000
    assert collect_tasks(store,[task('index',DAY,market='KOSPI')],failed,budget)['stopped']=='budget_exhausted'


def test_single_writer_lock_and_secret_echo(store):
    with store.lock():
        with pytest.raises(Failure,match='execution_conflict'):
            with Store(store.root).lock():pass
    with pytest.MonkeyPatch.context() as m:
        m.setenv('KRX_API_KEY','SYNTHETIC-SECRET')
        with pytest.raises(Failure,match='credential_echo'):store.preserve(b'SYNTHETIC-SECRET',{})


def test_master_exclusions_no_backdating(store):
    t=task('master',DAY,market='KOSPI')
    e=seed(store,t,master_bytes(halt='Y'),'2026-10-14T01:00:00+00:00')
    r=parse(store.raw(e),e)[0]
    assert not r['eligible'] and 'halted' in r['exclusion_reasons'] and r['delisted'] is None
    e=seed(store,t,master_bytes(halt='Y'),OBS)
    with pytest.raises(Failure,match='historical_universe_unverified'):parse(store.raw(e),e)


def test_cli_cannot_publish_bootstrap_or_empty_plan(store):
    assert main(['--root',str(store.root),'init','--date',DAY])==0
    assert main(['--root',str(store.root),'validate'])==1
    with pytest.raises(Failure):main(['--root',str(store.root),'publish'])


def test_corrupt_existing_publication_is_rejected(store):
    rows,r=validate(store,market_fixture(store));publish(store,'good',rows,r)
    (store.root/'generations/good/data.duckdb').write_bytes(b'corrupt')
    with pytest.raises(Failure,match='publication_hash_mismatch'):publish(store,'good',rows,r)


def test_macro_observation_dates_and_missing_not_zero(store):
    t=task('macro',DAY,indicator='us2y')
    e=seed(store,t,b'observation_date,DGS2\n2026-10-09,4.8\n2026-10-12,.\n2026-10-13,4.79\n2026-10-16,999\n')
    values=parse(store.raw(e),e)
    assert values[0]['date']=='2026-10-13' and values[0]['previous_date']=='2026-10-09'
    assert values[0]['observed_at']==OBS
    rows,r=validate(store,[t],require_market=False)
    assert r['held']==0 and len(rows)==1


def test_financial_filing_receipt_currency_and_future_guard(store):
    from screener.corporate import filing_record
    f=filing_record({'report_nm':'반기보고서 (2026.06)','rcept_no':'20260814000001','corp_code':'00000001','rcept_dt':'20260814'},'000001')
    t=task('financial',DAY,filing=f,scope='CFS')
    row=dict(rcept_no=f['receipt'],reprt_code='11012',bsns_year='2026',sj_div='IS',account_id='ifrs-full_Revenue',account_nm='매출액',account_detail='-',currency='KRW',thstrm_amount='40',thstrm_add_amount='70',frmtrm_add_amount='50')
    e=seed(store,t,{'status':'000','list':[row]})
    values=parse(store.raw(e),e)
    assert values[0]['values']['revenue']==70 and values[0]['standalone_quarter']['revenue']==40
    e=seed(store,t,{'status':'000','list':[{**row,'rcept_no':'bad'}]})
    assert validate(store,[t],require_market=False)[1]['held']==1
    t=task('financial','2026-08-13',filing=f,scope='CFS');e=seed(store,t,{'status':'000','list':[row]})
    with pytest.raises(Failure,match='future_disclosure'):parse(store.raw(e),e)


def test_institution_quantity_identity_failure_not_normalized(store):
    tasks=market_fixture(store);t=tasks[3]
    e=store.read('state.json')['tasks'][sha(encoded(t))]['evidence'];d=json.loads(store.raw(e))
    d['output2'][0]['orgn_ntby_qty']='9';seed(store,t,d)
    rows,r=validate(store,tasks)
    flow=next(x['value'] for x in rows if x['kind']=='flow')
    assert flow['institution_net_quantity'] is None and flow['institution_net_amount'] is None
    assert flow['institution_quantity_reasons']


def test_mixed_dates_and_cumulative_budget_fail_closed(store,monkeypatch):
    with pytest.raises(Failure,match='empty_or_mixed'):validate(store,[])
    with pytest.raises(Failure,match='empty_or_mixed'):validate(store,[task('calendar',DAY),task('calendar','2026-10-15')])
    store.write('requests.json',[{'status':'attempted'}]*20)
    b=Budget(store,calls=20,cumulative=20)
    with pytest.raises(Stop):b.before('KIS','/oauth2/tokenP','auth')


def test_manual_workflow_has_no_schedule_or_push():
    text=Path('.github/workflows/collector-v1.yml').read_text()
    assert 'workflow_dispatch:' in text and 'schedule:' not in text and 'cron:' not in text
    assert 'contents: read' in text and 'if: always()' in text and 'previous_run' in text
    assert 'git push' not in text and 'group: kis-validation-auth' in text


def test_proxy_denial_not_retryable(store,budget):
    def denied(req,timeout):raise urllib.error.URLError(OSError('Tunnel connection failed: 403 Forbidden'))
    with pytest.raises(Failure) as error:Providers(store,budget,denied).http('FRED','/graph/graph.csv','https://fred.stlouisfed.org/graph/graph.csv')
    assert error.value.code=='network_policy_denied' and not error.value.retryable
    assert budget.used==1


def test_source_metadata_binding_and_secret_redirect(store):
    e=seed(store,task('calendar',DAY),{'rt_cd':'0'})
    e['collected_at']='2026-10-20T01:00:00+00:00'
    with pytest.raises(Failure,match='source_metadata_hash_mismatch'):store.raw(e)
    from collector_v1.providers import NoRedirect
    req=__import__('urllib.request',fromlist=['Request']).Request('https://example.com',headers={'Authorization':'synthetic'})
    assert NoRedirect().redirect_request(req,None,302,'found',{},'https://other.example') is None


@pytest.mark.parametrize('code',['missing_KIS_APP_KEY','authentication_failure','network_failure','network_policy_denied'])
def test_phase2_failure_stops_following_requests(store,budget,code):
    tasks=[task('calendar',DAY),task('krx',DAY,market='KOSPI'),task('flow',DAY,symbol='000720',market='KOSPI')]
    attempted=[]
    def fetch(t):
        attempted.append(t)
        raise Failure(code, code=='network_failure')
    r=collect_tasks(store,tasks,fetch,budget,fail_fast=True)
    assert len(attempted)==1 and r['stopped']==code and r['collection_status']=='partial'
    assert r['tasks'][1]['collection_status']=='pending'
    assert store.read('state.json')['tasks'][sha(encoded(tasks[0]))]['error']==code


def test_cli_twenty_total_limit_survives_restart(store,monkeypatch):
    store.write('plan.json',{'schema':1,'asof':DAY,'mode':'macro','tasks':[task('macro',DAY,indicator='us2y')]})
    store.write('requests.json',[{'status':'attempted','source_run':'prior-process'}]*20)
    def forbidden(*a):raise AssertionError('budget must stop before provider access')
    monkeypatch.setattr(Providers,'fetch',forbidden)
    for _ in range(2):
        assert main(['--root',str(store.root),'collect','--max-calls','20'])==1
        assert store.read('last-run.json')['physical_requests']==0
        assert store.read('last-run.json')['total_reserved_requests']==20
