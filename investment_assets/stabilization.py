"""Manual offline backup readiness and producer handoff contracts. No uploader."""
import argparse
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from .archive import Archive
from .common import ArchiveError, digest, encoded, exclusive_write, fsync_dir, hash_name, timestamp, utcnow

REPO='0baselabkim-debug/qweasd'


def file_digest(path):
    import hashlib
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def classify_artifact_access(evidence):
    """Do not misclassify a proxy denial as token scope or artifact expiration."""
    status=evidence.get('http_status');headers={k.lower():v for k,v in evidence.get('headers',{}).items()}
    github=bool(headers.get('x-github-request-id'))
    if evidence.get('api_domain_allowed') is False and evidence.get('transport_error')=='Forbidden' and status is None:
        return dict(status='BLOCKED',cause='NETWORK_EGRESS_POLICY',authentication='UNVERIFIED',authorization='UNVERIFIED',expiration='UNVERIFIED')
    if github and status==401:return dict(status='BLOCKED',cause='AUTHENTICATION_REJECTED')
    if github and status==403:
        return dict(status='BLOCKED',cause='RATE_LIMIT' if str(headers.get('x-ratelimit-remaining'))=='0' else 'AUTHORIZATION_OR_PROVIDER_POLICY')
    if status==404:return dict(status='BLOCKED',cause='NOT_FOUND_OR_NOT_AUTHORIZED',expiration='UNVERIFIED')
    if status==200 and evidence.get('expired') is True:return dict(status='BLOCKED',cause='ARTIFACT_EXPIRED')
    if status==410:return dict(status='BLOCKED',cause='DOWNLOAD_GONE_CHECK_METADATA_EXPIRATION')
    if status==200 and evidence.get('expired') is False:
        return dict(status='METADATA_ACCESSIBLE',cause=None,download_verified=False)
    return dict(status='BLOCKED',cause='INSUFFICIENT_EVIDENCE')


def storage_scenarios(root, generation, *, sessions=250, multiplier=1):
    if type(sessions) is not int or not 1<=sessions<=366 or type(multiplier) is not int or not 1<=multiplier<=10:
        raise ArchiveError('invalid_storage_scenario')
    a=Archive(root);m=a.verify_generation(generation);folder=a._generation(generation)
    summary=json.loads((folder/'summary.json').read_bytes())
    if m['channel']!='operating_publication':raise ArchiveError('capacity_requires_operating_publication')
    originals=summary['archived_original_bytes'];parquet=sum(p.stat().st_size for p in folder.glob('*.parquet'))
    base=sum(p.stat().st_size for p in a.root.rglob('*') if p.is_file())
    delta=(originals+parquet)*multiplier
    # No linearity claim: this is a constant-size scenario, not a fitted forecast.
    return dict(status='SCENARIO_NOT_FORECAST',sessions=sessions,size_multiplier=multiplier,
        current_archive_bytes=base,measured_publication_original_bytes=originals,measured_publication_parquet_bytes=parquet,
        modeled_new_bytes_per_session=delta,annual_added_bytes=delta*sessions,
        modeled_year_end_incremental_archive_bytes=base+delta*sessions,
        retain_every_daily_full_archive_uncompressed_bytes=sessions*base+delta*sessions*(sessions+1)//2,
        assumptions=['One fixed-size operating publication per trading session; no analysis refreshes or experimental rounds.',
            'Assumes each generation adds all its original/Parquet bytes; exact object reuse may reduce this.',
            'Cumulative source DB/history growth can increase this; growth rate is unmeasured.',
            'Encryption metadata, revisions, replicas and object-operation costs are excluded.'])


def r2_monthly_storage_cost(bytes_stored, *, free_gb_month_available=0):
    """Cost if that volume is held all month; not an account's actual bill.

    Official R2 Standard storage billing rounds to a whole GB-month. Account
    free-tier availability is explicit and never assumed per bucket.
    """
    if type(bytes_stored) is not int or bytes_stored<0 or free_gb_month_available not in (0,10):
        raise ArchiveError('invalid_cost_input')
    billable=max(Decimal(0),Decimal(bytes_stored)/Decimal(1_000_000_000)-Decimal(free_gb_month_available))
    units=billable.to_integral_value(rounding=ROUND_CEILING)
    return dict(currency='USD',billable_gb_month=str(units),monthly_storage_only=str(units*Decimal('0.015')),
        basis='R2 Standard official public source; volume held all month; fees/tax/operations excluded',
        account_free_tier_assumed_gb_month=free_gb_month_available)


def verify_backup_ready(root, backup, expected_sha256, *, external_config=None):
    a=Archive(root);check=a.verify()
    if check['status']!='PASS':raise ArchiveError('archive_integrity_not_pass')
    hash_name(expected_sha256)
    if file_digest(backup)!=expected_sha256:raise ArchiveError('backup_digest_mismatch')
    cfg=external_config or {}
    # This command is deliberately never permission to upload.
    missing=[]
    for field in ('provider','private_target','recipient_fingerprint','credential_binding','remote_restore_test'):
        if not cfg.get(field):missing.append(field)
    return dict(status='BLOCKED_EXTERNAL_SETUP' if missing else 'LOCAL_PREFLIGHT_ONLY',archive_integrity=check,
        backup_sha256=expected_sha256,backup_bytes=Path(backup).stat().st_size,
        missing_external_requirements=missing,external_upload_performed=False,upload_authorized=False,
        encrypted_backup_prepared=False,independent_failure_domain_verified=False)


def _gpg(args, *, homedir, input=None):
    executable=shutil.which('gpg')
    if not executable:raise ArchiveError('gpg_not_available')
    command=[executable,'--no-options','--homedir',str(homedir),'--batch','--no-tty',
        '--no-auto-key-retrieve','--auto-key-locate','clear',*args]
    p=subprocess.run(command,input=input,capture_output=True,timeout=120)
    if p.returncode:raise ArchiveError('gpg_operation_failed')
    return p.stdout


def prepare_encrypted_backup(root, backup, expected_sha256, public_key, fingerprint, destination):
    """Recipient-pinned public-key encryption; no private key/network/upload.

    Existing ciphertext is reused only after its receipt and hashes agree.
    Real recipient/recovery-key possession is a separate external prerequisite.
    """
    a=Archive(root);verify_backup_ready(root,backup,expected_sha256)
    if not re.fullmatch(r'[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64}',fingerprint):raise ArchiveError('full_recipient_fingerprint_required')
    fingerprint=fingerprint.upper();dest=a._guard_destination(destination)
    key=Path(public_key)
    if not key.is_file():raise ArchiveError('recipient_public_key_missing')
    if not 0<key.stat().st_size<=1_000_000:raise ArchiveError('public_key_file_size_limit')
    receipt_path=Path(str(dest)+'.json')
    if dest.exists() or receipt_path.exists():
        if not dest.is_file() or not receipt_path.is_file():raise ArchiveError('incomplete_existing_encrypted_bundle')
        r=json.loads(receipt_path.read_bytes())
        if r.get('plaintext_sha256')!=expected_sha256 or r.get('recipient_fingerprint')!=fingerprint or r.get('ciphertext_sha256')!=file_digest(dest) or r.get('public_key_sha256')!=file_digest(key) or r.get('ciphertext_bytes')!=dest.stat().st_size:
            raise ArchiveError('encrypted_bundle_identity_or_hash_mismatch')
        return dict(r,status='ALREADY_PREPARED',external_upload_performed=False)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='asset-encryption-') as temp:
        home=Path(temp)/'keyring';home.mkdir(mode=0o700)
        shown=_gpg(['--with-colons','--import-options','show-only','--dry-run','--import',str(key.resolve())],homedir=home).decode()
        rows=[line.split(':') for line in shown.splitlines()]
        if any(r[0] in ('sec','ssb') for r in rows):raise ArchiveError('private_key_input_forbidden')
        primary=[];current=None
        for r in rows:
            if r[0] in ('pub','sub'):current=r[0]
            if r[0]=='fpr' and current=='pub':primary.append(r[9]);current=None
        if primary!=[fingerprint]:raise ArchiveError('recipient_fingerprint_mismatch')
        _gpg(['--import',str(key.resolve())],homedir=home)
        # No compression of an already compressed archive; MDC authenticates it.
        staged=Path(temp)/'backup.gpg'
        _gpg(['--trust-model','always','--cipher-algo','AES256','--force-mdc','--compress-algo','none',
              '--recipient',fingerprint,'--output',str(staged),'--encrypt',str(Path(backup).resolve())],homedir=home)
        ciphertext_sha=file_digest(staged)
        # exclusive_write enforces no replacement; stage then publish exact bytes.
        with dest.open('xb') as out,staged.open('rb') as inp:
            shutil.copyfileobj(inp,out,length=1024*1024);out.flush();os.fsync(out.fileno())
        dest.chmod(0o444);fsync_dir(dest.parent)
    receipt=dict(schema=1,status='ENCRYPTED_LOCAL_ONLY',created_at=utcnow(),encryption='OpenPGP public-key AES256 with MDC',
        recipient_fingerprint=fingerprint,public_key_sha256=file_digest(key),plaintext_sha256=expected_sha256,
        ciphertext_sha256=ciphertext_sha,ciphertext_bytes=dest.stat().st_size,
        decryption_with_actual_recovery_key_verified=False,external_upload_performed=False)
    exclusive_write(receipt_path,encoded(receipt))
    return receipt


def validate_handoff(event, receipt, *, kind):
    """Offline completion gate. A published PASS receipt alone is insufficient."""
    failures=[]
    if kind not in ('manual_full','time_slot','operating_publication'):raise ArchiveError('unknown_handoff_kind')
    branch='main' if kind=='operating_publication' else 'feat/market-data-collector-v1'
    if event.get('repository')!=REPO or event.get('branch')!=branch:failures.append('source_repository_or_branch')
    paths={'manual_full':{'.github/workflows/kis-manual-validation.yml'},'time_slot':{'.github/workflows/market-20261027.yml'},'operating_publication':{'.github/workflows/daily-dashboard.yml','.github/workflows/liquidity-recovery.yml'}}
    if event.get('workflow_path') not in paths[kind]:failures.append('unexpected_producer_workflow')
    if kind=='time_slot':
        if event.get('status') not in ('in_progress','completed'):failures.append('producer_run_not_started')
    elif event.get('status')!='completed' or event.get('conclusion')!='success':failures.append('producer_not_successfully_completed')
    if not str(event.get('run_id','')).isdigit():failures.append('missing_run_identity')
    if not re.fullmatch('[0-9a-f]{40}',str(event.get('published_commit',''))):failures.append('published_data_commit_required')
    completed=event.get('job_completed_at') if kind=='time_slot' else event.get('completed_at')
    if completed:
        try:timestamp(completed)
        except ArchiveError:failures.append('completion_time_unknown')
    else:failures.append('completion_time_unknown')
    report=receipt.get('report',receipt)
    if report.get('status')!='PASS':failures.append('source_internal_validation_not_pass')
    if str(receipt.get('run_id',report.get('run_id','')))!=str(event.get('run_id')):failures.append('receipt_run_mismatch')
    asof=report.get('asof')
    try:
        import datetime
        datetime.date.fromisoformat(asof)
    except (ValueError,TypeError):failures.append('trade_date_missing')
    if kind!='operating_publication':
        target=report.get('target')
        if type(target) is not int or target<=0 or report.get('received_symbols')!=target or report.get('kis_validated')!=target:
            failures.append('incomplete_collection')
        if kind=='manual_full' and report.get('mode')!='full':failures.append('full_mode_required')
        if report.get('db_json_match') is False:failures.append('DB_JSON_failed')
        artifact=event.get('artifact',{})
        if artifact.get('expired') is not False:failures.append('artifact_expiration_unverified')
        if not re.fullmatch('sha256:[0-9a-f]{64}',str(artifact.get('digest',''))):failures.append('artifact_digest_missing')
        if not str(artifact.get('id','')).isdigit() or not artifact.get('name'):failures.append('artifact_identity_missing')
        if str(artifact.get('run_id',''))!=str(event.get('run_id')):failures.append('artifact_run_mismatch')
        if not re.fullmatch('[0-9a-f]{64}',str(report.get('population_sha256',''))):failures.append('population_hash_missing')
        if kind=='manual_full':
            matches=[a for a in receipt.get('artifacts',[]) if str(a.get('id'))==str(artifact.get('id')) and a.get('digest')==artifact.get('digest') and a.get('name')==artifact.get('name')]
            if len(matches)!=1:failures.append('artifact_receipt_mismatch')
            if not re.fullmatch('[0-9a-f]{40}',str(receipt.get('source_commit',''))) or event.get('collection_commit')!=receipt.get('source_commit'):failures.append('collection_commit_mismatch')
        else:
            if not str(event.get('job_id','')).isdigit():failures.append('round_job_identity_missing')
            if event.get('job_status')!='completed' or event.get('job_conclusion')!='success':failures.append('round_job_not_completed')
            if not report.get('generation_id') or event.get('generation_id')!=report.get('generation_id'):failures.append('round_generation_mismatch')
    if kind=='operating_publication' and (not re.fullmatch('[0-9a-f]{64}',str(report.get('snapshot_sha256',''))) or not report.get('preservation_verified')):
        failures.append('published_snapshot_contract_missing')
    provider=report.get('provider_finality',report.get('finality','UNVERIFIED'))
    if provider not in ('UNVERIFIED','UNKNOWN'):failures.append('unproven_finality_claim')
    if kind!='operating_publication' and report.get('official_price_status','UNVERIFIED') not in ('PENDING_KRX','UNVERIFIED','UNKNOWN'):
        failures.append('official_attestation_requires_separate_contract')
    identity=dict(kind=kind,repository=event.get('repository'),branch=branch,run_id=str(event.get('run_id')),
        published_commit=event.get('published_commit'),asof=asof,generation_id=report.get('generation_id'),artifact=event.get('artifact'))
    return dict(schema=1,delivery_id=digest(encoded(identity)),status='BLOCKED' if failures else 'READY_FOR_OFFLINE_FETCH_AND_VERIFY',
        identity=identity,failures=failures,provider_finality=provider,
        official_price_status=report.get('official_price_status','UNVERIFIED'),
        archive_channel='operating_publication' if kind=='operating_publication' else 'kis_full_trial',
        source_population_sha256=report.get('population_sha256'),
        raw_integrity_verified=False,DB_JSON_reported=report.get('db_json_match'),required_postfetch_checks=['ZIP_SHA256','manifest','raw_hashes','population','DB_JSON'],archived=False,automatic_execution_registered=False,
        next_gate='Authenticated retrieval, ZIP/manifest/raw hashes and source-specific read-only validation before ingestion')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('capacity');q.add_argument('--root',required=True);q.add_argument('--generation',required=True);q.add_argument('--sessions',type=int,default=250)
    q=sub.add_parser('preflight');q.add_argument('--root',required=True);q.add_argument('--backup',required=True);q.add_argument('--sha256',required=True)
    q=sub.add_parser('encrypt');q.add_argument('--root',required=True);q.add_argument('--backup',required=True);q.add_argument('--sha256',required=True);q.add_argument('--public-key',required=True);q.add_argument('--fingerprint',required=True);q.add_argument('--destination',required=True)
    q=sub.add_parser('handoff');q.add_argument('--event',required=True);q.add_argument('--receipt',required=True);q.add_argument('--kind',required=True)
    a=p.parse_args(argv)
    try:
        if a.command=='capacity':result=storage_scenarios(a.root,a.generation,sessions=a.sessions)
        elif a.command=='preflight':result=verify_backup_ready(a.root,a.backup,a.sha256)
        elif a.command=='encrypt':result=prepare_encrypted_backup(a.root,a.backup,a.sha256,a.public_key,a.fingerprint,a.destination)
        else:result=validate_handoff(json.loads(Path(a.event).read_bytes()),json.loads(Path(a.receipt).read_bytes()),kind=a.kind)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 1 if result['status'].startswith('BLOCKED') else 0
    except (ArchiveError,OSError,ValueError,KeyError) as exc:
        print(json.dumps(dict(status='FAIL',reason=str(exc) if isinstance(exc,ArchiveError) else type(exc).__name__,external_upload_performed=False)));return 1


if __name__=='__main__':raise SystemExit(main())
