"""Synthetic admission/data integration: never calls GitHub or KIS."""
import copy
import base64
import datetime as dt
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from collector_v1.core import Failure,Store,encoded,sha
from collector_v1.regular import authorized as a,policy,collector,runner
from regular_fixtures import Client,transport


@pytest.fixture
def admitted(tmp_path,monkeypatch):
    current=dt.datetime(2026,10,29,13,2,tzinfo=dt.timezone.utc)
    c=dict(schema=1,mode='AUTHORIZED_EXCLUSIVE_RECOVERY',asof=a.DATE,slot='manual-approved-recovery',request_id=a.REQUEST,
        run_id='123',code_commit='a'*40,generation_id='kis-authorized-20261029-123',scheduled_at='2026-10-29T20:30:00+09:00',
        execution_not_before='2026-10-29T13:00:00+00:00',expires_at='2026-10-29T15:50:00+00:00',admitted_at=current.isoformat())
    proof=dict(schema=1,status='QUIESCENT_WITH_NEW_REQUESTS_FENCED',request_id=a.REQUEST,asof=a.DATE,
        repository='0baselabkim-debug/kis-intraday-monitor',unprotected_active=0,automatic_release=False,
        fence_commit='b'*40,quiescent_at='2026-10-29T13:00:00+00:00',observations=[dict(unprotected=[])],
        source_hashes={k:'c'*64 for k in ('kis_shared/admission.py','config/kis-account-exclusive.json','scripts/actions_auth.py','stage3/live_probe.py')})
    path=tmp_path/'proof.json';path.write_bytes(encoded(proof))
    control=dict(monitor_proof_file=str(path),monitor_proof_sha256=sha(path.read_bytes()),monitor_fence_commit='b'*40)
    monkeypatch.setattr(policy,'utcnow',lambda:current)
    for name in ('KIS_APP_KEY','KIS_APP_SECRET'):monkeypatch.setenv(name,'SYNTHETIC_'+name)
    for module in ('collector_v1.providers','collector_v1.experiment.engine','collector_v1.kis_manual','collector_v1.core'):
        monkeypatch.setattr(module+'.now',lambda:current.isoformat())
    monkeypatch.setattr('collector_v1.kis_manual.ManualBudget.remaining_seconds',
        lambda self:(policy.timestamp(self.control['expires_at'])-policy.utcnow()).total_seconds())
    return c,control,proof,current


def test_authorized_window_and_original_control_binding(admitted):
    c,control,proof,current=admitted
    policy.window(c,current)
    assert policy.control(c)['not_before']==c['execution_not_before']
    assert policy.control(c)['max_total_http']==5600
    assert policy.control(c)['approval']=='USER_APPROVED_EXCLUSIVE_FULL_MARKET_RECOVERY'


@pytest.mark.parametrize('change',[dict(asof='2026-10-30'),dict(slot='2030'),dict(generation_id='kis-authorized-20261029-999'),
    dict(request_id='OTHER'),dict(expires_at='2026-10-30T15:50:00Z')])
def test_changed_target_or_identity_or_extended_budget_is_rejected(admitted,change):
    c,_,_,current=admitted;c.update(change)
    with pytest.raises(Failure):policy.window(c,current)


def test_after_midnight_does_not_change_target(admitted):
    c,_,_,_=admitted
    policy.window(c,dt.datetime(2026,10,29,15,10,tzinfo=dt.timezone.utc))
    assert c['asof']=='2026-10-29'


def test_after_deadline_is_rejected(admitted):
    c,_,_,_=admitted
    with pytest.raises(Failure):policy.window(c,dt.datetime(2026,10,29,15,51,tzinfo=dt.timezone.utc))


def test_native_fence_proof_accepted(admitted):
    c,control,proof,_=admitted
    assert a.verify_proof(control,c)==proof


@pytest.mark.parametrize('field,value',[('status','WAITING'),('automatic_release',True),('unprotected_active',1),('fence_commit','d'*40)])
def test_unverified_quiescence_cannot_authorize_calls(admitted,field,value):
    c,control,proof,_=admitted;proof[field]=value
    p=Path(control['monitor_proof_file']);p.write_bytes(encoded(proof));control['monitor_proof_sha256']=sha(p.read_bytes())
    with pytest.raises(Failure):a.verify_proof(control,c)


def test_proof_tamper_rejected(admitted):
    c,control,_,_=admitted;Path(control['monitor_proof_file']).write_text('{}')
    with pytest.raises(Failure,match='hash'):a.verify_proof(control,c)


def test_auth_quarantine_is_not_optional(admitted):
    c,control,_,_=admitted;c['admitted_at']='2026-10-29T13:00:30Z'
    with pytest.raises(Failure,match='quarantine'):a.verify_proof(control,c)


def test_date_claim_preserves_failed_regular_and_prevents_duplicates(admitted):
    c,_,_,_=admitted;client=Client(c)
    prior=dict(status='BLOCKED',context=dict(run_id='99'),summary=dict(total_physical_http=0))
    client.docs['ledger/2026.json']=dict(slots={a.DATE+'/2030':copy.deepcopy(prior)},latest_complete=None)
    ticket=a.Ticket(client,c);ticket.claim()
    assert client.docs['ledger/2026.json']['slots'][a.DATE+'/2030']==prior
    with pytest.raises(Failure,match='already_claimed'):ticket.claim()


def test_previous_physical_calls_cannot_be_reset(admitted):
    c,_,_,_=admitted;client=Client(c)
    client.docs['ledger/2026.json']=dict(slots={a.DATE+'/2030':dict(status='BLOCKED',summary=dict(total_physical_http=1))})
    with pytest.raises(Failure,match='zero_HTTP'):a.Ticket(client,c).claim()


def test_combined_budget_auth_failure_and_master_requests_included(admitted,monkeypatch):
    c,control,_,_=admitted;guard=a.Guard(c,control,None,None);guard.checked=True
    for k,v in {'GITHUB_REPOSITORY':policy.REPO,'GITHUB_REF':a.REF,'GITHUB_EVENT_NAME':'push','GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':'123','GITHUB_SHA':'a'*40}.items():monkeypatch.setenv(k,v)
    guard.register_api(SimpleNamespace(budget=SimpleNamespace(used=4)))
    guard.register_api(SimpleNamespace(budget=SimpleNamespace(used=5596)))
    with pytest.raises(Failure,match='budget'):guard()


def test_live_scan_and_manifest_required_for_artifact(tmp_path,monkeypatch):
    monkeypatch.setattr(a,'ROOT',tmp_path);store=Store(tmp_path);store.write('report.json',dict(status='SYNTHETIC'))
    with pytest.raises(Failure,match='scan'):a.seal()
    store.write('SCAN_COMPLETE.json',dict(files=runner.inventory(tmp_path)))
    assert a.seal()==0
    assert runner.verify_manifest(tmp_path)['files']==runner.inventory(tmp_path)


def test_changed_files_after_live_scan_cannot_be_uploaded(tmp_path,monkeypatch):
    monkeypatch.setattr(a,'ROOT',tmp_path);store=Store(tmp_path);store.write('report.json',{})
    store.write('SCAN_COMPLETE.json',dict(files=runner.inventory(tmp_path)));store.write('report.json',dict(tampered=True))
    with pytest.raises(Failure,match='scan'):a.seal()
    assert not (tmp_path/'UPLOAD_ALLOWED').exists()


def test_full_synthetic_data_uses_original_schema_and_no_extra_requests(admitted,tmp_path,monkeypatch):
    c,_,_,_=admitted;monkeypatch.setattr('time.sleep',lambda _:None)
    calls,opener=transport(c);apis=[];progress=[]
    class Observer:
        def __call__(self):pass
        def register_api(self,api):apis.append(api)
    r,_=collector.run(tmp_path/'generation',c,Observer(),opener=opener,progress=progress.append,progress_step=60)
    assert r['status']=='INTERNAL_PASS' and r['db_json_match'] is True
    assert r['target']==r['kis_validated']==2003
    assert len(calls)==r['total_physical_http']==2*r['target']+6
    assert len(apis)==2 and sum(x.budget.used for x in apis)==r['total_physical_http']
    assert progress[0]['physical_http']==0 and len(progress)>2
    assert r['official_price_status']=='PENDING_KRX' and r['provider_finality']=='UNVERIFIED'


def test_stored_zip_hash_and_every_manifest_member_are_verified(tmp_path):
    path=tmp_path/'artifact.zip';raw=b'SYNTHETIC';manifest=dict(files={'raw/data':sha(raw)})
    with zipfile.ZipFile(path,'w') as z:z.writestr('raw/data',raw);z.writestr('manifest.json',encoded(manifest))
    assert a.verify_zip(path,sha(path.read_bytes()),manifest)['files_verified']==2
    with pytest.raises(Failure,match='digest'):a.verify_zip(path,'0'*64,manifest)


def test_stored_zip_corrupt_member_cannot_be_approved(tmp_path):
    path=tmp_path/'artifact.zip';manifest=dict(files={'raw/data':sha(b'ORIGINAL')})
    with zipfile.ZipFile(path,'w') as z:z.writestr('raw/data',b'TAMPERED');z.writestr('manifest.json',encoded(manifest))
    with pytest.raises(Failure,match='file_hash'):a.verify_zip(path,sha(path.read_bytes()),manifest)


def test_unknown_network_outcome_is_not_resolved(tmp_path,admitted,monkeypatch):
    c,_,_,_=admitted;monkeypatch.setattr(a,'ROOT',tmp_path)
    Store(tmp_path/c['generation_id']/'preparation').write('requests.json',[dict(status='network_failure')])
    assert a.physical_state(c)[3] is False


def test_authorized_driver_claim_collect_seal_download_verify_and_complete(admitted,tmp_path,monkeypatch):
    from test_kis_shared import Backend
    c,control,proof,current=admitted
    control.update(schema=1,request_id=a.REQUEST,asof=a.DATE,max_total_http=5600,
        approval='USER_APPROVED_EXCLUSIVE_FULL_MARKET_RECOVERY',not_before=c['execution_not_before'],expires_at=c['expires_at'])
    path=tmp_path/'control.json';path.write_bytes(encoded(control));monkeypatch.setattr(a,'CONTROL',path)
    monkeypatch.setattr(a,'ROOT',tmp_path/'result');monkeypatch.setattr(a,'now',lambda:current.isoformat())
    monkeypatch.setattr('time.sleep',lambda _:None)
    for k,v in {'GITHUB_REPOSITORY':policy.REPO,'GITHUB_REF':a.REF,'GITHUB_EVENT_NAME':'push',
                'GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':'123','GITHUB_SHA':'a'*40,'RUNNER_TEMP':str(tmp_path)}.items():monkeypatch.setenv(k,v)
    generated=a.make_context(control);client=Client(generated)
    client.docs['ledger/2026.json']=dict(slots={a.DATE+'/2030':dict(status='BLOCKED',context=dict(run_id='99'),summary=dict(total_physical_http=0))},latest_complete=None)
    client.run=lambda id:dict(id=id,status='in_progress',head_sha='a'*40,path=a.WORKFLOW)
    monkeypatch.setattr(a,'GitHub',lambda:client)
    backend=Backend();monkeypatch.setattr('kis_shared.runtime.GitBackend',lambda _:backend)
    calls,opener=transport(generated);original=collector.run
    monkeypatch.setattr(collector,'run',lambda *args,**kwargs:original(*args,opener=opener,**kwargs))
    assert a.collect()==0
    record,_=a.Ticket(client,generated).read()
    assert record['status']=='INTERNAL_PASS' and record['summary']['all_http_resolved'] is True
    assert record['summary']['account_lease_released'] is True and backend.state['owner'] is None
    assert a.seal()==0
    archive=tmp_path/'stored.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for file in a.ROOT.rglob('*'):
            if file.is_file() and file.name!='writer.lock':z.write(file,str(file.relative_to(a.ROOT)))
    raw=archive.read_bytes();digest=sha(raw)
    monkeypatch.setenv('ARTIFACT_ID','1');monkeypatch.setenv('ARTIFACT_DIGEST',digest)
    client.request=lambda _:dict(id=1,name=generated['artifact_name'],digest='sha256:'+digest,expired=False,
                                 expires_at='2027-01-01T00:00:00Z',workflow_run=dict(id=123))
    def download(args,**kwargs):
        assert args[:2]==['gh','api'];kwargs['stdout'].write(raw);return SimpleNamespace(returncode=0)
    monkeypatch.setattr(a.subprocess,'run',download)
    assert a.finalize()==0
    record,_=a.Ticket(client,generated).read()
    assert record['status']=='COMPLETE_PROVISIONAL'
    assert record['summary']['stored_zip_verification']['files_verified']>4000
    assert len(record['sample_rows'])==3
    assert runner.verify_manifest(a.ROOT)['generation_id']==generated['generation_id']
