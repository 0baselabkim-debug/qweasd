import datetime as dt
import gzip
import io
import json
from pathlib import Path
import urllib.error
import zipfile
import pytest
from test_decision_evidence import synthetic_source
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


def test_retrospective_paths_do_not_create_trades_or_backtest_eligibility():
    from screener.historical_v2.outcomes import path
    days=[(dt.date(2020,1,1)+dt.timedelta(days=i)).isoformat() for i in range(25)]
    # Explicit synthetic calendar, not a production weekday/holiday generator.
    cal=dict(status='OFFICIAL_POSITIVE_SESSIONS_INDEPENDENTLY_CROSSCHECKED',days=days)
    bars={d:dict(symbol='000720',market='KOSPI',trade_date=d,value=dict(close=100+i),official_source_ids=['synthetic'],sources=[dict(observed_at='2020-02-01T00:00:00Z')]) for i,d in enumerate(days)}
    r=path('000720','KOSPI',days[0],cal,bars,'2020-03-01T00:00:00Z')
    assert r['actual_user_trade'] is None and r['historical_backtest_eligible'] is False
    assert r['outcomes']['20']['gross_price_change_pct']==20
    assert path('000720','KOSPI',days[0],cal,bars,'2020-01-01T00:00:00Z')['outcomes']['5']['status']=='UNAVAILABLE'
    del bars[days[2]]
    assert path('000720','KOSPI',days[0],cal,bars,'2020-03-01T00:00:00Z')['outcomes']['20']['status']=='UNAVAILABLE'


def test_source_adapter_requires_external_proof_and_manifest_anchor(tmp_path):
    from screener.historical_v2.collector import inspect
    with pytest.raises(ValueError,match='acquisition_proof'):inspect(tmp_path,{})


def test_recovery_followup_is_only_october30_two_references(monkeypatch):
    from screener.historical_v2.krx_recovery import FOLLOWUP_PAIRS
    monkeypatch.setenv('GITHUB_REF','refs/heads/feat/decision-evidence-v2');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    c=control();c.update(pairs=[list(p) for p in FOLLOWUP_PAIRS],max_http=2,priority_eight_completed_run='41212889496')
    check_control(c,dt.datetime.fromisoformat('2026-10-31T15:05:00Z'))
    c['priority_eight_completed_run']='unknown'
    with pytest.raises(ValueError):check_control(c,dt.datetime.fromisoformat('2026-10-31T15:05:00Z'))


def test_minimal_recovery_rate_is_dispatch_based_after_shared_reservation(tmp_path):
    from screener.historical_v2.kis_recovery import RecoveryBudget
    from collector_v1.core import Store
    from collector_v1.providers import Q
    current=[100.0];waits=[]
    def sleep(n):waits.append(n);current[0]+=n
    b=RecoveryBudget(Store(tmp_path),clock=lambda:current[0],sleep=sleep)
    b.before('KIS','/oauth2/tokenP','synthetic');current[0]+=.5
    b.before('KIS',Q+'investor-trade-by-stock-daily','synthetic')
    assert abs(waits[0]-.55)<1e-8 and current[0]>=101.05


@pytest.mark.parametrize('kind',['historical-collector','historical-publication'])
def test_retrospective_full_v2_e2e_labels_and_all_formats(synthetic_source,tmp_path,monkeypatch,kind):
    from screener.decision_evidence import build as b
    from screener.historical_v2 import collector,publication
    from screener.investment_v2.pipeline import run,verify_bundle
    root,out=synthetic_source;value=b.load(root,'2020-04-02T00:00:00Z')
    value['source_verification'].update(collection_complete=True)
    monkeypatch.setattr(collector if kind=='historical-collector' else publication,'load',lambda *a,**kw:value)
    e=b.build(root,out,'2020-04-02T00:00:00Z',source_kind=kind,source_proof={'synthetic_test_only':True})
    r=run(e['directory'],e['validation']['manifest_sha256'],tmp_path/'investment')
    p=Path(r['directory']);market=json.loads(gzip.decompress((p/'market.json.gz').read_bytes()))
    assert market['analysis_mode']=='RETROSPECTIVE_REANALYSIS' and market['stocks'][0]['analysis_mode']=='RETROSPECTIVE_REANALYSIS'
    assert market['historical_backtest_eligible'] is False and r['quality']['OpenAI_live_HTTP']==0
    assert '당시 투자 판단 또는 실제 매매 기록이 아닙니다' in (p/'briefing.html').read_text()
    assert verify_bundle(p,r['validation']['manifest_sha256'])['database_json_parquet_match']
    assert (root/'immutable').read_bytes()==b'original'


def test_price_pool_anchor_and_corrupt_file_never_approve(tmp_path):
    from screener.historical_v2.price_pool import load
    manifest=dict(analysis_mode='RETROSPECTIVE_REANALYSIS',historical_backtest_eligible=False,cutoff='2020-01-01T00:00:00Z',files={'bars.json.gz':'0'*64})
    (tmp_path/'manifest.json').write_bytes(encoded(manifest));(tmp_path/'bars.json.gz').write_bytes(b'broken')
    with pytest.raises(ValueError,match='anchor'):load(tmp_path,'a'*64,'2020-01-02T00:00:00Z')
    with pytest.raises(ValueError,match='hash_mismatch'):load(tmp_path,sha(encoded(manifest)),'2020-01-02T00:00:00Z')


@pytest.mark.parametrize('kind',['hash','incomplete','duplicate_universe','overlapping_exclusions'])
def test_legacy_checkpoint_rejects_invalid_source_before_loading_pool(tmp_path,kind):
    from screener.historical_v2.checkpoint import load
    b=dict(trade_date='20261015',exchange='KRX',status='complete',failures={},completed_at='2026-10-15T12:00:00Z',universe=[dict(symbol='000720')],records={'000720':{}},excluded={})
    if kind=='incomplete':b['status']='partial'
    if kind=='duplicate_universe':b['universe']*=2
    if kind=='overlapping_exclusions':b['excluded']={'000720':'synthetic'}
    raw=gzip.compress(encoded(b));(tmp_path/'bundle.json.gz').write_bytes(raw)
    proof=dict(bundle_sha256='0'*64 if kind=='hash' else sha(raw),asof='2026-10-15')
    with pytest.raises(ValueError):load(tmp_path,'2026-10-31T00:00:00Z',None,proof=proof)


def test_native_reconstruction_has_no_market_secret_schedule_or_shared_lock():
    text=Path('.github/workflows/historical-v2-validation.yml').read_text()
    assert 'secrets.' not in text and 'schedule:' not in text and 'cron:' not in text
    assert 'kis-validation-auth' not in text and 'contents: read' in text
    assert "paths: ['.github/recovery/historical-v2-validation.json']" in text
