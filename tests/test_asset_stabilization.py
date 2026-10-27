"""Offline readiness, costing, encryption and handoff gates. No external writes."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import pytest
from investment_assets import Archive, ArchiveError
from investment_assets.common import digest
from investment_assets import stabilization as s
from test_investment_assets import bundle


@pytest.fixture
def local_backup(tmp_path,bundle):
    a=Archive(tmp_path/'archive');r=a.ingest(bundle)
    b=a.backup(tmp_path/'backup.tar.gz')
    return a,b,r


def test_network_block_not_misreported_as_auth_or_expiration():
    r=s.classify_artifact_access(dict(api_domain_allowed=False,transport_error='Forbidden',http_status=None))
    assert r['cause']=='NETWORK_EGRESS_POLICY' and r['authentication']==r['authorization']==r['expiration']=='UNVERIFIED'


@pytest.mark.parametrize('evidence,cause',[
 ({'http_status':401,'headers':{'x-github-request-id':'SYNTHETIC'}},'AUTHENTICATION_REJECTED'),
 ({'http_status':403,'headers':{'x-github-request-id':'SYNTHETIC'}},'AUTHORIZATION_OR_PROVIDER_POLICY'),
 ({'http_status':403,'headers':{'x-github-request-id':'SYNTHETIC','x-ratelimit-remaining':'0'}},'RATE_LIMIT'),
 ({'http_status':404},'NOT_FOUND_OR_NOT_AUTHORIZED'),
 ({'http_status':410},'DOWNLOAD_GONE_CHECK_METADATA_EXPIRATION'),
 ({'http_status':200,'expired':True},'ARTIFACT_EXPIRED'),
 ({},'INSUFFICIENT_EVIDENCE')])
def test_access_classification_requires_actual_provider_evidence(evidence,cause):
    assert s.classify_artifact_access(evidence)['cause']==cause


def test_metadata_access_is_not_download_verification():
    r=s.classify_artifact_access({'http_status':200,'expired':False})
    assert r['status']=='METADATA_ACCESSIBLE' and not r['download_verified']


def test_external_setup_absent_blocks_upload_and_encryption(local_backup):
    a,b,_=local_backup;r=s.verify_backup_ready(a.root,b['path'],b['sha256'])
    assert r['status']=='BLOCKED_EXTERNAL_SETUP' and not r['external_upload_performed'] and not r['upload_authorized']
    assert 'recipient_fingerprint' in r['missing_external_requirements'] and not r['encrypted_backup_prepared']
    with pytest.raises(ArchiveError,match='digest'):s.verify_backup_ready(a.root,b['path'],'0'*64)


def test_capacity_is_explicit_scenario_not_projection(local_backup):
    a,b,g=local_backup;r=s.storage_scenarios(a.root,g['generation_id'],sessions=250)
    d=r['modeled_new_bytes_per_session'];base=r['current_archive_bytes']
    assert r['annual_added_bytes']==250*d
    assert r['retain_every_daily_full_archive_uncompressed_bytes']==250*base+d*250*251//2
    assert r['status']=='SCENARIO_NOT_FORECAST' and len(r['assumptions'])>=4
    with pytest.raises(ArchiveError):s.storage_scenarios(a.root,g['generation_id'],sessions=0)


def test_r2_pricing_rounds_units_and_never_assumes_account_free_tier():
    assert s.r2_monthly_storage_cost(1_100_000_000)['monthly_storage_only']=='0.030'
    assert s.r2_monthly_storage_cost(11_100_000_000,free_gb_month_available=10)['monthly_storage_only']=='0.030'
    assert s.r2_monthly_storage_cost(9_000_000_000,free_gb_month_available=10)['monthly_storage_only']=='0.000'
    assert s.r2_monthly_storage_cost(50_000_000_000)['monthly_storage_only']=='0.750'
    with pytest.raises(ArchiveError):s.r2_monthly_storage_cost(-1)


@pytest.fixture(scope='module')
def test_recipient(tmp_path_factory):
    if not shutil.which('gpg'):pytest.skip('GnuPG prerequisite not installed')
    folder=tmp_path_factory.mktemp('SYNTHETIC-gpg');home=folder/'keyring';home.mkdir(mode=0o700)
    config=b'Key-Type: RSA\nKey-Length: 2048\nKey-Usage: encrypt\nName-Real: SYNTHETIC ARCHIVE TEST ONLY\nExpire-Date: 1d\n%no-protection\n%commit\n'
    subprocess.run(['gpg','--no-options','--homedir',str(home),'--batch','--generate-key'],input=config,capture_output=True,check=True,timeout=60)
    p=subprocess.run(['gpg','--no-options','--homedir',str(home),'--batch','--with-colons','--list-keys'],capture_output=True,check=True)
    fingerprint=next(l.split(':')[9] for l in p.stdout.decode().splitlines() if l.startswith('fpr:'))
    key=folder/'SYNTHETIC-public.asc'
    key.write_bytes(subprocess.check_output(['gpg','--no-options','--homedir',str(home),'--armor','--export',fingerprint]))
    return home,key,fingerprint


def test_public_key_encryption_roundtrip_and_archive_restore(local_backup,test_recipient,tmp_path):
    a,b,_=local_backup;home,key,fpr=test_recipient;dest=tmp_path/'encrypted'/'backup.gpg'
    r=s.prepare_encrypted_backup(a.root,b['path'],b['sha256'],key,fpr,dest)
    assert r['status']=='ENCRYPTED_LOCAL_ONLY' and not r['external_upload_performed']
    assert not r['decryption_with_actual_recovery_key_verified']
    before=dest.read_bytes();again=s.prepare_encrypted_backup(a.root,b['path'],b['sha256'],key,fpr,dest)
    assert again['status']=='ALREADY_PREPARED' and dest.read_bytes()==before
    plain=tmp_path/'decrypted.tar.gz'
    subprocess.run(['gpg','--no-options','--homedir',str(home),'--batch','--output',str(plain),'--decrypt',str(dest)],capture_output=True,check=True,timeout=30)
    assert s.file_digest(plain)==b['sha256']
    restored=Archive.restore_backup(plain,tmp_path/'restored',b['sha256'])
    assert restored['status']=='PASS' and Archive(tmp_path/'restored').verify()==a.verify()


def test_wrong_recipient_and_private_key_inputs_rejected(local_backup,test_recipient,tmp_path):
    a,b,_=local_backup;home,key,fpr=test_recipient;dest=tmp_path/'cipher.gpg'
    with pytest.raises(ArchiveError,match='fingerprint'):s.prepare_encrypted_backup(a.root,b['path'],b['sha256'],key,'0'*40,dest)
    assert not dest.exists()
    private=tmp_path/'SYNTHETIC-private.asc'
    private.write_bytes(subprocess.check_output(['gpg','--no-options','--homedir',str(home),'--batch','--pinentry-mode','loopback','--passphrase','','--armor','--export-secret-keys',fpr]))
    with pytest.raises(ArchiveError,match='private_key'):s.prepare_encrypted_backup(a.root,b['path'],b['sha256'],private,fpr,dest)
    assert not dest.exists()


def test_corrupt_ciphertext_and_backup_never_overwritten(local_backup,test_recipient,tmp_path):
    a,b,_=local_backup;_,key,fpr=test_recipient;dest=tmp_path/'cipher.gpg'
    s.prepare_encrypted_backup(a.root,b['path'],b['sha256'],key,fpr,dest)
    dest.chmod(0o644);dest.write_bytes(b'CORRUPT SYNTHETIC ONLY')
    with pytest.raises(ArchiveError,match='hash_mismatch'):s.prepare_encrypted_backup(a.root,b['path'],b['sha256'],key,fpr,dest)
    assert dest.read_bytes()==b'CORRUPT SYNTHETIC ONLY'
    with pytest.raises(ArchiveError,match='digest'):s.prepare_encrypted_backup(a.root,b['path'],'0'*64,key,fpr,tmp_path/'other.gpg')


def handoff_pair(kind='manual_full'):
    event=dict(repository=s.REPO,branch='feat/market-data-collector-v1',run_id='12345',status='completed',conclusion='success',completed_at='2026-10-27T12:00:00+00:00',published_commit='a'*40,collection_commit='b'*40,workflow_path='.github/workflows/kis-manual-validation.yml',artifact=dict(id=123,name='SYNTHETIC',digest='sha256:'+'c'*64,expired=False,run_id='12345'))
    receipt=dict(run_id='12345',source_commit='b'*40,artifacts=[dict(id=123,name='SYNTHETIC',digest='sha256:'+'c'*64)],report=dict(status='PASS',mode='full',target=3,received_symbols=3,kis_validated=3,asof='2026-10-26',db_json_match=True,population_sha256='d'*64,provider_finality='UNVERIFIED',official_price_status='PENDING_KRX'))
    return event,receipt


def test_handoff_ready_does_not_claim_archived_or_official_finality():
    e,r=handoff_pair();out=s.validate_handoff(e,r,kind='manual_full')
    assert out['status']=='READY_FOR_OFFLINE_FETCH_AND_VERIFY'
    assert not out['archived'] and not out['raw_integrity_verified'] and not out['automatic_execution_registered']
    assert out['provider_finality']=='UNVERIFIED' and out['official_price_status']=='PENDING_KRX'
    assert s.validate_handoff(e,r,kind='manual_full')['delivery_id']==out['delivery_id']


@pytest.mark.parametrize('mutation,code',[
 ('in_progress','producer_not_successfully_completed'),('wrong_branch','source_repository_or_branch'),
 ('expired','artifact_expiration_unverified'),('other_run','artifact_run_mismatch'),
 ('code_commit_not_published_commit','published_data_commit_required'),
 ('digest_conflict','artifact_receipt_mismatch'),('wrong_workflow','unexpected_producer_workflow'),
 ('db_failed','DB_JSON_failed'),('false_finality','unproven_finality_claim')])
def test_handoff_blocks_partial_wrong_or_unverified_sources(mutation,code):
    e,r=handoff_pair()
    if mutation=='in_progress':e['status']='in_progress'
    elif mutation=='wrong_branch':e['branch']='main'
    elif mutation=='expired':e['artifact']['expired']=True
    elif mutation=='other_run':e['artifact']['run_id']='54321'
    elif mutation=='code_commit_not_published_commit':e['published_commit']=None
    elif mutation=='digest_conflict':e['artifact']['digest']='sha256:'+'f'*64
    elif mutation=='wrong_workflow':e['workflow_path']='UNKNOWN'
    elif mutation=='db_failed':r['report']['db_json_match']=False
    elif mutation=='false_finality':r['report']['provider_finality']='FINAL'
    out=s.validate_handoff(e,r,kind='manual_full')
    assert out['status']=='BLOCKED' and code in out['failures']


def test_completed_round_can_be_preserved_while_other_rounds_still_running():
    e,r=handoff_pair();e.update(status='in_progress',conclusion=None,workflow_path='.github/workflows/market-20261027.yml',job_id='99',job_status='completed',job_conclusion='success',job_completed_at='2026-10-27T12:00:00+00:00',generation_id='SYNTHETIC_GENERATION')
    r['report']['generation_id']='SYNTHETIC_GENERATION';r['report'].pop('db_json_match')
    out=s.validate_handoff(e,r,kind='time_slot')
    assert out['status']=='READY_FOR_OFFLINE_FETCH_AND_VERIFY' and not out['raw_integrity_verified']
    assert out['DB_JSON_reported'] is None and 'DB_JSON' in out['required_postfetch_checks']
    e['job_conclusion']='failure'
    assert s.validate_handoff(e,r,kind='time_slot')['status']=='BLOCKED'


def test_actual_saved_receipt_alone_cannot_authorize_handoff():
    receipt=json.loads((Path(__file__).resolve().parents[1]/'reports/kis-manual-20261026/40326538555.json').read_bytes())
    out=s.validate_handoff({},receipt,kind='manual_full')
    assert out['status']=='BLOCKED' and 'producer_not_successfully_completed' in out['failures']


@pytest.mark.parametrize('field,value,reason',[
 ('mode','sample','full_mode_required'),('target',0,'incomplete_collection'),
 ('received_symbols',2,'incomplete_collection'),('kis_validated',2,'incomplete_collection'),
 ('official_price_status','MATCHED_KRX','official_attestation_requires_separate_contract')])
def test_full_handoff_never_accepts_sample_partial_or_unproven_official_data(field,value,reason):
    e,r=handoff_pair();r['report'][field]=value
    out=s.validate_handoff(e,r,kind='manual_full')
    assert out['status']=='BLOCKED' and reason in out['failures']


def test_operating_publication_requires_preservation_evidence():
    e,r=handoff_pair();e.update(branch='main',workflow_path='.github/workflows/daily-dashboard.yml')
    r['report'].update(snapshot_sha256='e'*64,preservation_verified=True)
    assert s.validate_handoff(e,r,kind='operating_publication')['status']=='READY_FOR_OFFLINE_FETCH_AND_VERIFY'
    r['report']['preservation_verified']=False
    assert 'published_snapshot_contract_missing' in s.validate_handoff(e,r,kind='operating_publication')['failures']


def test_no_upload_client_or_automatic_workflow_added():
    import ast
    tree=ast.parse(Path(s.__file__).read_text())
    names=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    names += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
    assert not any(str(n).startswith(('boto','requests','urllib','httpx','azure','google.cloud')) for n in names)
    assert 'upload_file' not in Path(s.__file__).read_text()
