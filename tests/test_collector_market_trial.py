"""Stage-two offline tests. Synthetic ZIPs/market payloads only; no live HTTP."""
import copy
import datetime as dt
import io
import json
from pathlib import Path
import urllib.error
import zipfile
import pytest
from collector_v1 import market_trial as market, trial
from collector_v1.core import Failure, Stop, Store, encoded, publish, sha
from collector_v1.providers import LAYOUTS, Q, task
from test_collector_v1 import master_bytes


@pytest.fixture(scope='module')
def source_zip(tmp_path_factory):
    s=Store(tmp_path_factory.mktemp('synthetic-master'))
    state={'schema':1,'tasks':{}};universe=[];sources=[]
    for t in market.master_tasks():
        if t['kind']=='calendar':raw=encoded({'rt_cd':'0','output':[{'bass_dt':'20261026','opnd_yn':'Y'}]})
        else:
            m=t['market'];n=1498 if m=='KOSPI' else 2449
            codes=[f'{i:06}' for i in range(100000 if m=='KOSPI' else 400000,(100000 if m=='KOSPI' else 400000)+n)]
            codes += [code for code,(board,name) in trial.TARGETS.items() if board==m]
            lines=[]
            for i,code in enumerate(codes):
                halted=(m=='KOSPI' and i<1498) or (m=='KOSDAQ' and i<103)
                with zipfile.ZipFile(io.BytesIO(master_bytes(m,code,'Y' if halted else 'N'))) as z:
                    member=z.namelist()[0];line=z.read(member)
                if code in trial.TARGETS:line=line.replace(b'SYNTHETIC TEST ONLY',trial.TARGETS[code][1].encode('cp949'))
                lines.append(line)
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as z:z.writestr(member,b'\n'.join(lines))
            raw=buf.getvalue()
        e=s.preserve(raw,dict(task=t,source='KIS' if t['kind']=='calendar' else 'KIS_MASTER',source_run='SYNTHETIC_MASTER_RUN',collected_at='2026-10-26T01:00:00+00:00'))
        state['tasks'][sha(encoded(t))]=dict(task=t,collection_status='received',evidence=e)
        if t['kind']=='master':universe.extend(market.parse(raw,e));sources.append(e)
    s.write('state.json',state);s.write('masters.json',universe)
    endpoints=[('KIS','/oauth2/tokenP'),('KIS',Q+'chk-holiday')]+[('KIS_MASTER',LAYOUTS[m]['url']) for m in ('KOSPI','KOSDAQ')]
    s.write('requests.json',[dict(sequence=i+1,source=p,endpoint=e,status='received',http=200,source_run='SYNTHETIC_MASTER_RUN') for i,(p,e) in enumerate(endpoints)])
    s.write('last-run.json',{'status':'SYNTHETIC_TEST_ONLY'})
    s.write('receipt.json',dict(github_run_id=market.MASTER_RUN,asof=market.ASOF,stage='master',status='PASS',trial_id='master-20261026',request_id='master-request',total_http=4,rows=len(universe),eligible=sum(r['eligible'] for r in universe),masters_sha256=sha(encoded(universe))))
    trial.seal(s)
    assert len(universe)==3950 and sum(r['eligible'] for r in universe)==2349
    assert len(s.read('state-manifest.json')['files'])==11
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for p in s.root.rglob('*'):
            if p.is_file():z.write(p,str(p.relative_to(s.root)))
    return output.getvalue()


@pytest.fixture
def prepared(tmp_path,monkeypatch,source_zip):
    monkeypatch.chdir(tmp_path);monkeypatch.setattr(trial,'ROOT',tmp_path/'data/collector-v1-trial');monkeypatch.setattr(trial,'ADMIN',tmp_path/'admin')
    monkeypatch.setattr(trial,'local_date',lambda:'2026-10-27');monkeypatch.setattr(trial,'git',lambda *a:'current-test-sha')
    monkeypatch.setattr(market,'ZIP_SHA',sha(source_zip));monkeypatch.setattr('collector_v1.core.time.sleep',lambda _:None)
    c=dict(schema=1,trial_id='master-20261026',request_id='market-request',stage='market',asof=market.ASOF,previous_run=market.MASTER_RUN,max_total_http=20)
    Path(trial.CONTROL).parent.mkdir(parents=True);Path(trial.CONTROL).write_bytes(encoded(c))
    trial.ADMIN.mkdir(parents=True);archive=trial.ADMIN/'master-artifact.zip';archive.write_bytes(source_zip)
    pre=dict(control=c,source_commit='current-test-sha',previous_run=market.MASTER_RUN)
    (trial.ADMIN/'preflight.json').write_bytes(encoded(pre));(trial.ADMIN/'snapshot-before.json').write_bytes(encoded({}))
    (trial.ADMIN/'market-restoration.json').write_bytes(encoded(dict(status='PASS',control=c,zip_sha256=market.ZIP_SHA,artifact_id=market.ARTIFACT_ID,source_commit='current-test-sha')))
    monkeypatch.setenv('GITHUB_RUN_ID','40290000000')
    for n in trial.SECRET_NAMES:monkeypatch.setenv(n,'SYNTHETIC_'+n)
    market.extract_verified(archive,c)
    # Existing provider date gate must also observe the next KST day.
    real=dt.datetime
    class Future(real):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,27,1,tzinfo=dt.timezone.utc).astimezone(tz) if tz else cls(2026,10,27,1)
    monkeypatch.setattr('collector_v1.providers.dt.datetime',Future)
    return c


def mutate_sealed(store,change):
    change(store);trial.seal(store)


@pytest.mark.parametrize('case',['receipt_run','receipt_date','receipt_stage','receipt_status','http_count','http_code','state_task','raw_hash','normalized_master'])
def test_bad_restoration_never_opens_provider(prepared,monkeypatch,case):
    s=Store(trial.ROOT)
    def change(s):
        if case.startswith('receipt_'):
            r=s.read('receipt.json');key={'receipt_run':'github_run_id','receipt_date':'asof','receipt_stage':'stage','receipt_status':'status'}[case];r[key]='WRONG';s.write('receipt.json',r)
        elif case in ('http_count','http_code'):
            rows=s.read('requests.json')
            if case=='http_count':rows.append(copy.deepcopy(rows[-1]))
            else:rows[0]['http']=401
            s.write('requests.json',rows)
        elif case=='state_task':
            state=s.read('state.json');state['tasks']['not-a-valid-key']=state['tasks'].pop(next(iter(state['tasks'])));s.write('state.json',state)
        elif case=='raw_hash':next((s.root/'raw').glob('*.gz')).write_bytes(b'corrupt')
        else:s.write('masters.json',[])
    mutate_sealed(s,change)
    with pytest.raises((Failure,OSError)):
        market.verify_master(s,prepared)
    monkeypatch.setattr(market,'FixedProviders',lambda *a:pytest.fail('No new HTTP on bad restoration'))
    with pytest.raises(Failure):market.collect()
    assert len(s.read('requests.json')) in (4,5)


def test_zip_hash_and_path_traversal_rejected_before_install(tmp_path,monkeypatch):
    monkeypatch.setattr(trial,'ROOT',tmp_path/'root');monkeypatch.setattr(trial,'local_date',lambda:'2026-10-27')
    c=dict(stage='market',asof=market.ASOF,previous_run=market.MASTER_RUN,trial_id='master-20261026')
    f=tmp_path/'bad.zip';f.write_bytes(b'not original artifact')
    with pytest.raises(Failure,match='artifact_zip_sha256_mismatch'):market.extract_verified(f,c)
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('../escape',b'evil')
    f.write_bytes(b.getvalue());monkeypatch.setattr(market,'ZIP_SHA',sha(f.read_bytes()))
    with pytest.raises(Failure,match='unsafe_artifact_archive'):market.extract_verified(f,c)
    assert not trial.ROOT.exists() and not (tmp_path/'escape').exists()


@pytest.mark.parametrize('field,value',[('id',1),('digest','sha256:wrong'),('expired',True),('name','other'),('workflow_run',{'id':1})])
def test_pin_metadata_mismatch(field,value):
    meta=dict(id=int(market.ARTIFACT_ID),name=trial.ARTIFACT,expired=False,digest='sha256:'+market.ZIP_SHA,workflow_run={'id':int(market.MASTER_RUN)})
    market.metadata_guard(meta)
    meta[field]=value
    with pytest.raises(Failure):market.metadata_guard(meta)


def test_future_date_control_and_fixed_plan(prepared,monkeypatch):
    assert trial.control()['stage']=='market'
    monkeypatch.setattr(trial,'local_date',lambda:'2026-10-26')
    with pytest.raises(Failure,match='too_early'):trial.control()
    s=Store(trial.ROOT);b=market.MarketBudget(s)
    with pytest.raises(Stop):b.before('KIS','/oauth2/tokenP','authentication')
    assert b.used==0
    monkeypatch.setattr(trial,'local_date',lambda:'2026-10-27')
    with pytest.raises(Failure):market.FixedProviders(s,b).fetch(task('flow',market.ASOF,symbol='005930',market='KOSPI'))
    with pytest.raises(Failure):b.before('KIS_MASTER','url','master')
    assert b.used==0


def responses(conflict=False,wrong_date=False):
    out=[encoded({'access_token':'SYNTHETIC_TOKEN'})]
    out += [encoded({'rt_cd':'0','output2':[{'stck_bsop_date':'20261026','bstp_nmix_prpr':'1000'}]}) for _ in range(2)]
    for m in ('KOSPI','KOSDAQ'):
        rows=[]
        for s,(board,_) in trial.TARGETS.items():
            if board==m:rows.append(dict(BAS_DD='20261026',ISU_CD=s,TDD_OPNPRC='100',TDD_HGPRC='110',TDD_LWPRC='90',TDD_CLSPRC='106' if conflict and s=='000720' else '105',ACC_TRDVOL='10',ACC_TRDVAL='1050',MKTCAP='100000'))
        out.append(encoded({'OutBlock_1':rows}))
    for s in trial.TARGETS:
        out.append(encoded({'rt_cd':'0','output2':[dict(stck_bsop_date='20261025' if wrong_date else '20261026',frgn_ntby_tr_pbmn='2',frgn_ntby_qty='3',orgn_ntby_qty='-1',orgn_shnu_vol='1',orgn_seln_vol='2',orgn_ntby_tr_pbmn='999',stck_clpr='105',acml_vol='10',acml_tr_pbmn='1050')]}))
        out.append(encoded({'rt_cd':'0','output1':{'stck_shrn_iscd':s},'output2':[dict(stck_bsop_date='20261026',stck_oprc='100',stck_hgpr='110',stck_lwpr='90',stck_clpr='105',acml_vol='10',acml_tr_pbmn='1050',mod_yn='N')]}))
    return out


def install_transport(monkeypatch,replies,denied=False):
    calls=[]
    class Response:
        status=200
        def __init__(self,raw):self.raw=raw
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return self.raw
    def opener(req,timeout):
        calls.append(req.full_url)
        assert Store(trial.ROOT).read('requests.json')[-1]['status']=='attempted'
        if denied:raise urllib.error.HTTPError(req.full_url,401,'unauthorized',{},None)
        return Response(replies.pop(0))
    real=market.FixedProviders
    monkeypatch.setattr(market,'FixedProviders',lambda s,b:real(s,b,opener))
    return calls


def test_full_stage2_reuses_master_and_stores_exact_DB_JSON(prepared,monkeypatch):
    calls=install_transport(monkeypatch,responses())
    assert market.collect()==0
    s=Store(trial.ROOT);r=s.read('receipt.json')
    assert r['total_http']==15 and r['physical_http_this_run']==11 and r['status']=='PASS'
    assert r['db_json_match'] and r['idempotent_storage'] and r['validated_symbols']==3
    assert len(calls)==11 and not any('master' in c or 'chk-holiday' in c for c in calls)
    data=json.loads((s.root/'generations'/r['generation']/'analysis.json').read_bytes())
    flows=[r['value'] for r in data if r['kind']=='flow']
    assert len(flows)==3 and all(r['foreign_net_amount']==2000000 and r['institution_net_quantity']==-1 and r['institution_net_amount'] is None for r in flows)
    assert len(list((s.root/'generations').iterdir()))==1
    # A completed stage-two root cannot be reset back to the original four calls.
    with pytest.raises(Failure):market.collect()
    assert len(calls)==11


def test_price_conflict_preserves_values_and_stops_next_symbol(prepared,monkeypatch):
    calls=install_transport(monkeypatch,responses(conflict=True))
    assert market.collect()==1
    s=Store(trial.ROOT);failure=s.read('validation-failure.json')
    assert failure['reason']=='KIS_KRX_six_field_conflict'
    assert failure['details']['differences']['close']=={'kis':105,'krx':106,'delta':-1}
    assert s.read('receipt.json')['total_http']==11 and len(calls)==7
    assert not (s.root/'published.json').exists()
    p=task('price',market.ASOF,symbol='000720',market='KOSPI')
    e=s.read('state.json')['tasks'][sha(encoded(p))]
    assert e['collection_status']=='received' and e['validation_status']=='HOLD'
    s.raw(e['evidence'])


def test_wrong_source_date_holds_and_auth_failure_counts(prepared,monkeypatch):
    calls=install_transport(monkeypatch,responses(wrong_date=True))
    assert market.collect()==1
    assert Store(trial.ROOT).read('validation-failure.json')['reason']=='requested_date_missing'
    assert len(calls)==6 and not (trial.ROOT/'published.json').exists()


def test_auth_failure_never_calls_following_endpoints(prepared,monkeypatch):
    calls=install_transport(monkeypatch,[],denied=True)
    assert market.collect()==1
    r=Store(trial.ROOT).read('receipt.json')
    assert len(calls)==1 and r['total_http']==5 and r['status']=='BLOCKED'


def test_budget_never_exceeds_twenty_including_restored_calls(prepared):
    s=Store(trial.ROOT);b=market.MarketBudget(s)
    for i in range(16):b.before('KIS','/oauth2/tokenP','SYNTHETIC_RESERVED_CALL')
    with pytest.raises(Stop):b.before('KIS','/oauth2/tokenP','authentication')
    assert b.used==16 and len(s.read('requests.json'))==20


def test_DB_verifier_failure_does_not_approve_pointer(tmp_path):
    s=Store(tmp_path/'trial-only')
    def bad(*a):raise Failure('DB_JSON_value_mismatch')
    with pytest.raises(Failure):publish(s,'test',[{'identity':'one','kind':'test','value':1}],{'held':0,'collection_status':'complete'},verify=bad)
    assert not (s.root/'published.json').exists()


def test_restore_before_not_before_date_never_downloads(prepared,monkeypatch):
    monkeypatch.setattr(trial,'local_date',lambda:'2026-10-26')
    monkeypatch.setattr(trial,'github_json',lambda *a:pytest.fail('must stop before GitHub download'))
    with pytest.raises(Failure,match='too_early'):market.restore()


def test_master_command_cannot_be_used_by_market_control(prepared):
    with pytest.raises(Failure,match='master_command_requires_master_stage'):trial.collect_master()


def test_workflow_keeps_separate_restoration_and_collect_gates():
    root=Path(__file__).resolve().parents[1]
    s=(root/'.github/workflows/collector-v1-trial.yml').read_text()
    assert "steps.preflight.outputs.stage == 'market'" in s
    assert 'python -m collector_v1.market_trial restore' in s and 'python -m collector_v1.market_trial collect' in s
    assert 'paths: [.github/trials/collector-v1.json]' in s and 'schedule:' not in s
    assert "steps.preflight.outputs.stage == 'master' && steps.preflight.outputs.previous_run != ''" in s


def test_restore_command_verifies_download_before_install(prepared,monkeypatch,source_zip):
    import shutil
    from types import SimpleNamespace
    shutil.rmtree(trial.ROOT)  # This fixture's temporary directory only.
    archive=trial.ADMIN/'master-artifact.zip';archive.unlink()
    meta=dict(id=int(market.ARTIFACT_ID),name=trial.ARTIFACT,expired=False,digest='sha256:'+market.ZIP_SHA,workflow_run={'id':int(market.MASTER_RUN)})
    monkeypatch.setattr(trial,'github_json',lambda path:meta)
    def download(args,stdout,stderr):
        assert args==['gh','api',f'repos/{trial.REPO}/actions/artifacts/{market.ARTIFACT_ID}/zip']
        assert not trial.ROOT.exists()
        stdout.write(source_zip)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(market.subprocess,'run',download)
    monkeypatch.setattr(market,'FixedProviders',lambda *a:pytest.fail('Restoration cannot call providers'))
    assert market.restore()==0
    assert Store(trial.ROOT).read('receipt.json')['total_http']==4
    assert json.loads((trial.ADMIN/'market-restoration.json').read_bytes())['zip_sha256']==market.ZIP_SHA


def test_restore_bad_metadata_does_not_download(prepared,monkeypatch):
    monkeypatch.setattr(trial,'github_json',lambda path:{'id':0})
    monkeypatch.setattr(market.subprocess,'run',lambda *a,**kw:pytest.fail('must not download unpinned artifact'))
    with pytest.raises(Failure,match='metadata_mismatch'):market.restore()
