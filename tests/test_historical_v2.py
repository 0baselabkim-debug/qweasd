import datetime as dt
import gzip
import io
import json
from pathlib import Path
import urllib.error
import zipfile
import pytest
from screener.historical_v2.assets import inspect_zip,extract_verified,select_generation
from screener.historical_v2.krx_recovery import check_control,collect_one,PAIRS
from screener.decision_support.evidence import encoded,sha


def fixture_zip(tmp_path,entries):
    p=tmp_path/'original.zip'
    with zipfile.ZipFile(p,'w') as z:
        for name,raw in entries:z.writestr(name,raw)
    return p


@pytest.mark.parametrize('name',['../bad','/bad','a/../../bad','a\\bad'])
def test_recovery_rejects_zip_paths_before_extract(tmp_path,name):
    p=fixture_zip(tmp_path,[(name,b'x')])
    with pytest.raises(ValueError,match='unsafe_archive'):extract_verified(p,sha(p.read_bytes()),tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_nested_manifest_uses_directory_not_filename_prefix(tmp_path):
    payload=b'original';m=encoded(dict(files={'data.json':sha(payload)}))
    p=fixture_zip(tmp_path,[('seed/state-manifest.json',m),('seed/data.json',payload)])
    r=inspect_zip(p,sha(p.read_bytes()))
    assert r['manifests'][0]['checked']==1 and not r['manifests'][0]['failures']


def test_corrupt_raw_is_not_accepted_even_with_valid_zip(tmp_path):
    p=fixture_zip(tmp_path,[('raw/'+'a'*64+'.gz',gzip.compress(b'wrong'))])
    r=inspect_zip(p,sha(p.read_bytes()))
    assert r['status']=='FAIL' and r['integrity_errors'][0]['reason']=='raw_SHA_mismatch'


def test_expected_zip_digest_and_no_overwrite(tmp_path):
    p=fixture_zip(tmp_path,[('data.json',b'{}')]);before=p.read_bytes()
    with pytest.raises(ValueError,match='ZIP_digest'):extract_verified(p,'0'*64,tmp_path/'out')
    extract_verified(p,sha(before),tmp_path/'out')
    with pytest.raises(ValueError,match='must_be_new'):extract_verified(p,sha(before),tmp_path/'out')
    assert p.read_bytes()==before


def candidate(generation,value='a',**kw):
    return dict(generation_id=generation,market_payload_sha256=value,integrity='PASS',complete=True,six_field_pass=10,validated=10,target=10,**kw)


def test_selection_does_not_pick_arbitrary_latest_generation():
    a=candidate('early');b=candidate('late')
    assert select_generation([b,a])['selected']==['early','late']
    b['market_payload_sha256']='other'
    assert select_generation([a,b])['status']=='CONFLICT'
    b['complete']=False
    assert select_generation([a,b])['selected']==['early']
    assert select_generation([b])['status']=='HOLD'


def control():
    return dict(approval='USER_APPROVED_HISTORICAL_RECOVERY',pairs=[list(p) for p in PAIRS],max_http=8,
                not_before='2026-10-31T15:00:00Z',expires_at='2026-10-31T15:10:00Z')


def test_krx_recovery_exact_scope_window_and_no_rerun(monkeypatch):
    monkeypatch.setenv('GITHUB_REF','refs/heads/feat/decision-evidence-v2');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    c=control();check_control(c,dt.datetime.fromisoformat('2026-10-31T15:05:00Z'))
    for key,val in [('max_http',9),('pairs',[]),('expires_at','2026-10-31T16:00:00Z')]:
        bad=dict(c,**{key:val})
        with pytest.raises(ValueError):check_control(bad,dt.datetime.fromisoformat('2026-10-31T15:05:00Z'))
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT','2')
    with pytest.raises(ValueError,match='rerun'):check_control(c,dt.datetime.fromisoformat('2026-10-31T15:05:00Z'))


class Budget:
    def __init__(self):self.calls=[];self.ends=[]
    def before_http(self,url):self.calls.append(url)
    def finish(self,status,**kw):self.ends.append(dict(status=status,**kw))


@pytest.mark.parametrize('body,http,reason',[(b'{"OutBlock_1":[]}',200,'REFERENCE_NOT_AVAILABLE'),(b'denied',403,'KRX_HTTP_403'),(b'broken',200,'Expecting value')])
def test_krx_failure_saved_and_exactly_one_physical_attempt(tmp_path,body,http,reason):
    b=Budget();seen=[]
    def opener(req,timeout):
        seen.append(req.full_url)
        if http!=200:raise urllib.error.HTTPError(req.full_url,http,'denied',{},io.BytesIO(body))
        r=io.BytesIO(body);r.status=200;return r
    with pytest.raises(ValueError,match=reason):collect_one('2026-10-26','KOSPI',tmp_path,b,'synthetic-key',opener)
    assert len(b.calls)==len(b.ends)==len(seen)==1
    assert (tmp_path/'recovery-raw'/(sha(body)+'.bin')).read_bytes()==body


def test_credential_echo_never_preserved(tmp_path):
    b=Budget()
    def opener(req,timeout):r=io.BytesIO(b'synthetic-key');r.status=200;return r
    with pytest.raises(ValueError,match='credential_echo'):collect_one('2026-10-26','KOSPI',tmp_path,b,'synthetic-key',opener)
    assert not (tmp_path/'recovery-raw').exists() and len(b.calls)==1


def test_only_explicit_control_push_and_krx_secret_no_schedule_or_kis():
    text=Path('.github/workflows/historical-krx-recovery.yml').read_text()
    assert 'schedule:' not in text and 'cron:' not in text and 'KIS_APP' not in text
    assert "paths: ['.github/recovery/historical-krx.json']" in text
    assert 'cancel-in-progress: false' in text and 'queue: max' in text


def test_observed_calendar_requires_exact_independent_session_set():
    from screener.historical_v2.calendar import check_sessions
    days=['2026-10-23','2026-10-26','2026-10-27']
    assert check_sessions({m:days for m in ['KOSPI','KOSDAQ']},{m:days for m in ['KOSPI','KOSDAQ']})==days
    with pytest.raises(ValueError,match='gap_or_conflict'):
        check_sessions({m:days for m in ['KOSPI','KOSDAQ']},{m:days+['2026-10-24'] for m in ['KOSPI','KOSDAQ']})
    with pytest.raises(ValueError,match='disagree'):
        check_sessions({'KOSPI':days,'KOSDAQ':days[:-1]},{m:days for m in ['KOSPI','KOSDAQ']})


def test_minimal_kis_budget_preserves_parent_and_allows_no_retry(tmp_path):
    from screener.historical_v2.kis_recovery import RecoveryBudget
    from collector_v1.core import Store,Failure
    from collector_v1.providers import Q
    b=RecoveryBudget(Store(tmp_path))
    for endpoint in ['/oauth2/tokenP',Q+'investor-trade-by-stock-daily',Q+'inquire-daily-itemchartprice']:
        e=b.before('KIS',endpoint,'synthetic');b.finish(e,'received',http=200)
    assert b.used==3 and json.loads((tmp_path/'parent-budget.json').read_bytes())['original_physical_http']==4693
    with pytest.raises(Failure):b.before('KIS','/oauth2/tokenP','retry')
    with pytest.raises(ValueError,match='anchor'):RecoveryBudget(Store(tmp_path/'wrong'),0)


def test_minimal_kis_checks_window_before_each_physical_dispatch(tmp_path):
    from screener.historical_v2.kis_recovery import RecoveryBudget
    from collector_v1.core import Store
    def expired():raise ValueError('expired')
    b=RecoveryBudget(Store(tmp_path),validator=expired)
    with pytest.raises(ValueError,match='expired'):b.before('KIS','/oauth2/tokenP','synthetic')
    assert b.used==0 and not (tmp_path/'http.jsonl').exists()


def test_official_alphanumeric_symbol_uses_unchanged_six_field_values(tmp_path):
    from screener.decision_support.evidence import official_rows
    obj=dict(asof='2026-10-29',observed_at='2026-10-31T15:00:00Z',url='https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20261029',
             rows=[dict(ISU_CD='0001A0',BAS_DD='20261029',MKT_NM='KOSPI',TDD_OPNPRC='100',TDD_HGPRC='110',TDD_LWPRC='90',TDD_CLSPRC='105',ACC_TRDVOL='10',ACC_TRDVAL='1000')])
    p=tmp_path/'official.json';p.write_bytes(encoded(obj));rows,_,errors=official_rows([p],'2026-10-29')
    assert rows[('0001A0','2026-10-29','KOSPI')][0]['value']['close']==105 and not errors


def test_unknown_historical_population_cannot_generate_candidates():
    from screener.decision_evidence.build import candidates
    assert candidates([dict(population_eligibility='UNKNOWN_HISTORICAL')],[])['returned']==0
