"""Offline archive tests. Synthetic fixtures are distinct from actual Git integration."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import io
import gzip
import pytest
from investment_assets import Archive, ArchiveError
from investment_assets.common import encoded,digest,exclusive_write
from investment_assets.sources import GitSource, publication, analysis, kis_receipt, kis_artifact

REPO=Path(__file__).resolve().parents[1]


@pytest.fixture
def bundle(tmp_path):
    source=tmp_path/'SYNTHETIC-source';source.mkdir()
    return dict(channel='operating_publication',asof='2026-10-14',files={'snapshot/SYNTHETIC.json':b'{"synthetic":true}'},
        population=dict(profile='SYNTHETIC_ONLY',instruments=[{'symbol':'SYNTHETIC'}]),
        observations=[dict(kind='price_adjusted',entity='SYNTHETIC',trade_date='2026-10-14',observed_at='2026-10-14T11:00:00+00:00',
            observation_time_status='KNOWN',internal_validation='SOURCE_PUBLICATION_ONLY',official_validation='UNVERIFIED',
            provider_finality='UNVERIFIED',historical_backtest_eligible=None,payload={'adjusted_close':100,'label':'SYNTHETIC_ONLY'})],
        source=dict(repository='SYNTHETIC',branch='main',commit='a'*40,repository_path=str(source)),source_run='SYNTHETIC',
        observed_at=None,published_at='2026-10-15T00:00:00+00:00',available_at='2026-10-15T00:00:00+00:00',
        source_internal_validation='PUBLICATION_PASSED',flow_validation='SOURCE_PUBLICATION_ONLY',price_validation='SOURCE_PUBLICATION_ONLY',
        provider_finality='UNVERIFIED',historical_backtest_eligible=None,archive_scope='SYNTHETIC_ONLY',archive_status='COMPLETE_BYTES',source_reported_target=1,warnings=[])


def ingest(a,b,time='2026-10-15T01:00:00+00:00'):
    return a.ingest(b,clock=lambda:time)


def test_repeat_import_and_new_process_have_no_duplicate_records(tmp_path,bundle):
    a=Archive(tmp_path/'archive');first=ingest(a,bundle);second=ingest(a,bundle,'2026-10-16T00:00:00+00:00')
    assert first['status']=='IMPORTED' and second['status']=='ALREADY_PRESENT'
    assert first['generation_id']==second['generation_id'] and first['ingested_at']==second['ingested_at']
    reopened=Archive(a.root)
    assert reopened.verify()['generations']==1 and reopened.verify()['archived_observations']==1
    assert reopened.verify()['unique_original_objects']==1


def test_new_adjustment_version_cannot_change_past_query(tmp_path,bundle):
    a=Archive(tmp_path/'archive');one=ingest(a,bundle)
    cutoff='2026-10-15T02:00:00+00:00'
    before=a.query(channel='operating_publication',at=cutoff)
    later=copy.deepcopy(bundle);later['source']['commit']='b'*40;later['available_at']='2026-10-16T00:00:00+00:00'
    later['observations'][0]['payload']['adjusted_close']=90;later['files']['snapshot/SYNTHETIC.json']=b'{"synthetic":true,"revision":2}'
    two=ingest(a,later,'2026-10-16T01:00:00+00:00')
    assert a.query(channel='operating_publication',at=cutoff)==before
    assert a.query(channel='operating_publication',at='2026-10-16T02:00:00+00:00')['rows'][0]['payload']['adjusted_close']==90
    assert before['rows'][0]['payload']['adjusted_close']==100 and one['generation_id']!=two['generation_id']
    assert a.verify()['generations']==2


def test_late_backfill_requires_explicit_archive_knowledge_cutoff(tmp_path,bundle):
    a=Archive(tmp_path/'archive');cutoff='2026-10-15T02:00:00+00:00'
    before=a.query(channel='operating_publication',at=cutoff)
    ingest(a,bundle,'2026-10-17T01:00:00+00:00')
    assert a.query(channel='operating_publication',at=cutoff)==before
    reconstructed=a.query(channel='operating_publication',at=cutoff,knowledge_at='2026-10-17T02:00:00+00:00')
    assert reconstructed['replay_mode']=='EXPLICIT_HISTORICAL_RECONSTRUCTION' and reconstructed['total_rows']==1


def test_source_channel_and_generation_never_silently_union(tmp_path,bundle):
    a=Archive(tmp_path/'archive');ingest(a,bundle)
    second=copy.deepcopy(bundle);second['channel']='kis_full_trial';second['population']['profile']='SYNTHETIC_DIFFERENT_POPULATION'
    ingest(a,second)
    r=a.query(channel='operating_publication',at='2026-10-15T02:00:00+00:00')
    assert r['total_rows']==1 and r['channel']=='operating_publication'
    with pytest.raises(ArchiveError):a.query(channel='all',at='2026-10-15T02:00:00+00:00')


def test_exact_same_bytes_across_paths_and_generations_are_stored_once(tmp_path,bundle):
    a=Archive(tmp_path/'archive');bundle['files']['snapshot/another.json']=bundle['files']['snapshot/SYNTHETIC.json']
    ingest(a,bundle);bundle['source']['commit']='c'*40;ingest(a,bundle)
    assert a.verify()['unique_original_objects']==1 and a.verify()['generations']==2


def test_unknown_observation_time_and_false_backtest_flag_preserved(tmp_path,bundle):
    a=Archive(tmp_path/'archive');r=bundle['observations'][0]
    r.update(observed_at=None,observation_time_status='UNKNOWN',historical_backtest_eligible=False)
    bundle['historical_backtest_eligible']=False;ingest(a,bundle)
    q=a.query(channel='operating_publication',at='2026-10-15T02:00:00+00:00')
    assert q['rows'][0]['observed_at'] is None and not q['rows'][0]['observation_time_reproducible']
    assert q['historical_backtest_eligible'] is False and q['rows'][0]['historical_backtest_eligible'] is False
    assert a.query(channel='operating_publication',at='2026-10-15T02:00:00+00:00',reproducible_only=True)['total_rows']==0


def test_unverified_never_becomes_officially_verified(tmp_path,bundle):
    a=Archive(tmp_path/'archive');r=ingest(a,bundle)
    assert r['source_internal_validation']=='PUBLICATION_PASSED' and r['provider_finality']=='UNVERIFIED'
    q=a.query(channel='operating_publication',at='2026-10-15T02:00:00+00:00')
    assert q['rows'][0]['official_validation']=='UNVERIFIED'
    bad=copy.deepcopy(bundle);bad['provider_finality']='FINAL'
    with pytest.raises(ArchiveError,match='finality'):ingest(a,bad)


def test_corrupt_original_is_rejected_and_never_repaired_by_reimport(tmp_path,bundle):
    a=Archive(tmp_path/'archive');r=ingest(a,bundle);h=digest(bundle['files']['snapshot/SYNTHETIC.json']);p=a._object(h)
    p.chmod(0o644);p.write_bytes(b'CORRUPT_SYNTHETIC')
    with pytest.raises(ArchiveError,match='corrupt'):a.verify()
    with pytest.raises(ArchiveError):ingest(a,bundle)
    assert p.read_bytes()==b'CORRUPT_SYNTHETIC'


def test_missing_original_and_corrupt_parquet_and_manifest_fail_closed(tmp_path,bundle):
    for defect in ('missing','parquet','manifest'):
        a=Archive(tmp_path/defect);r=ingest(a,bundle);g=a._generation(r['generation_id'])
        if defect=='missing':a._object(digest(bundle['files']['snapshot/SYNTHETIC.json'])).unlink()
        else:
            path=g/('observations.parquet' if defect=='parquet' else 'manifest.json');path.chmod(0o644);path.write_bytes(b'CORRUPT')
        with pytest.raises(ArchiveError):a.query(channel='operating_publication',at='2026-10-15T02:00:00+00:00')


def test_duplicate_rows_rejected_not_deduplicated_to_success(tmp_path,bundle):
    a=Archive(tmp_path/'archive');bundle['observations']*=2
    with pytest.raises(ArchiveError,match='duplicate'):ingest(a,bundle)
    assert not list((a.root/'generations').iterdir())


def test_foreign_and_price_validation_statuses_remain_independent(tmp_path,bundle):
    a=Archive(tmp_path/'archive');bundle['flow_validation']='UNVERIFIED';bundle['price_validation']='SIX_FIELD_MATCH'
    r=ingest(a,bundle)
    assert r['flow_validation']=='UNVERIFIED' and r['price_validation']=='SIX_FIELD_MATCH' and r['provider_finality']=='UNVERIFIED'


def test_explicit_timezone_and_query_limit_required(tmp_path,bundle):
    a=Archive(tmp_path/'archive');ingest(a,bundle)
    with pytest.raises(ArchiveError):a.query(channel='operating_publication',at='2026-10-15')
    with pytest.raises(ArchiveError):a.query(channel='operating_publication',at='2026-10-15T02:00:00Z',limit=10001)


def test_backup_restore_rebuilds_catalog_in_new_location(tmp_path,bundle):
    a=Archive(tmp_path/'archive');g=ingest(a,bundle)['generation_id'];b=a.backup(tmp_path/'backups'/'one.tar.gz')
    restored=tmp_path/'restored'
    result=Archive.restore_backup(b['path'],restored,b['sha256']);new=Archive(restored)
    assert result['operation']=='BACKUP_RESTORED_AND_VERIFIED' and new.verify()==a.verify()
    assert new.query(channel='operating_publication',at='2026-10-15T02:00:00Z')['generation_id']==g
    import duckdb
    with duckdb.connect(str(new.root/'catalog.duckdb'),read_only=True) as db:
        assert db.execute('select count(*) from observations').fetchone()[0]==1


def test_restore_exact_bytes_and_prevent_overwrites_or_source_destination(tmp_path,bundle):
    a=Archive(tmp_path/'archive');g=ingest(a,bundle)['generation_id']
    dest=tmp_path/'restored-publication';r=a.restore_generation(g,dest)
    assert r['status']=='RESTORED_EXACT_ORIGINAL_BYTES'
    assert (dest/'snapshot/SYNTHETIC.json').read_bytes()==bundle['files']['snapshot/SYNTHETIC.json']
    with pytest.raises(ArchiveError):a.restore_generation(g,dest)
    with pytest.raises(ArchiveError):a.restore_generation(g,Path(bundle['source']['repository_path'])/'snapshot')
    with pytest.raises(ArchiveError):a.backup(a.root/'self.tar.gz')


def test_corrupt_backup_and_traversal_rejected_before_writing_destination(tmp_path,bundle):
    a=Archive(tmp_path/'archive');ingest(a,bundle);b=a.backup(tmp_path/'one.tar.gz')
    with pytest.raises(ArchiveError,match='digest'):Archive.restore_backup(b['path'],tmp_path/'bad','0'*64)
    assert not (tmp_path/'bad').exists()
    attack=tmp_path/'attack.tar.gz'
    with tarfile.open(attack,'w:gz') as t:
        info=tarfile.TarInfo('../escape');info.size=1;t.addfile(info,io.BytesIO(b'x'))
    with pytest.raises(ArchiveError):Archive.restore_backup(attack,tmp_path/'attack-dest',digest(attack.read_bytes()))
    assert not (tmp_path/'escape').exists() and not (tmp_path/'attack-dest').exists()


def test_failure_after_generation_commit_does_not_touch_source_and_retries_idempotently(tmp_path,bundle,monkeypatch):
    original=Path(bundle['source']['repository_path'])/'operating.json';original.write_bytes(b'EXISTING_SOURCE')
    before=original.read_bytes();a=Archive(tmp_path/'archive');real=a._catalog
    monkeypatch.setattr(a,'_catalog',lambda: (_ for _ in ()).throw(RuntimeError('SYNTHETIC_CACHE_FAILURE')))
    with pytest.raises(RuntimeError):ingest(a,bundle)
    assert original.read_bytes()==before and len(list((a.root/'generations').iterdir()))==1
    monkeypatch.setattr(a,'_catalog',real)
    assert ingest(a,bundle)['status']=='ALREADY_PRESENT' and a.verify()['generations']==1


def test_git_checkout_archive_location_forbidden(tmp_path):
    repo=tmp_path/'repo';(repo/'.git').mkdir(parents=True);(repo/'.git/HEAD').write_text('ref: refs/heads/main\n')
    with pytest.raises(ArchiveError):Archive(repo/'new-data')


def test_receipt_only_does_not_claim_stock_rows(tmp_path):
    source=GitSource(REPO,'3d838753','feat/market-data-collector-v1')
    b,receipt,meta=kis_receipt(source,'reports/kis-manual-20261026/40326538555.json')
    a=Archive(tmp_path/'archive');r=ingest(a,b)
    assert r['archived_observations']==0 and r['source_reported_target']==2349
    assert r['archive_status']=='SOURCE_BYTES_UNAVAILABLE' and r['flow_validation']==r['price_validation']=='NOT_RESTORED'
    bad=tmp_path/'fake.zip';bad.write_bytes(b'NOT_REAL_ZIP')
    with pytest.raises(ArchiveError,match='digest'):kis_artifact(source,'reports/kis-manual-20261026/40326538555.json',bad)


def test_real_20261014_publication_restore_and_sha256(tmp_path):
    source=GitSource(REPO,'c9ee3e48','main');b=publication(source)
    assert b['asof']=='2026-10-14' and b['published_at']=='2026-10-14T22:51:53.980549+00:00'
    assert b['available_at']=='2026-10-14T22:51:54+00:00'
    a=Archive(tmp_path/'actual-archive');r=ingest(a,b,'2026-10-27T07:00:00+00:00')
    assert r['archived_observations']==174875 and r['provider_finality']=='UNVERIFIED'
    restored=tmp_path/'actual-restore';a.restore_generation(r['generation_id'],restored)
    expected=json.loads(b['files']['snapshot/manifest.json'])['sha256']
    assert digest((restored/'snapshot/dashboard.duckdb').read_bytes())==expected
    # The system did not exist then; retrospective inspection is explicitly separate.
    assert not a.query(channel='operating_publication',at='2026-10-15T01:00:00Z')['rows']
    q=a.query(channel='operating_publication',at='2026-10-15T01:00:00Z',knowledge_at='2026-10-27T08:00:00Z',kind='foreign_flow',limit=1)
    assert q['replay_mode']=='EXPLICIT_HISTORICAL_RECONSTRUCTION' and q['rows'][0]['observed_at'] is None


def test_source_hash_mismatch_rejected_before_archive_approval(tmp_path,monkeypatch):
    source=GitSource(REPO,'c9ee3e48','main');original=source.read
    def read(path):
        raw=original(path)
        return raw+b'corrupt' if path=='snapshot/dashboard.duckdb' else raw
    monkeypatch.setattr(source,'read',read)
    with pytest.raises(ArchiveError,match='hash'):publication(source)


def test_new_module_has_no_market_clients_scheduler_or_operating_writes():
    import ast
    for p in (REPO/'investment_assets').glob('*.py'):
        tree=ast.parse(p.read_text())
        imports=[]
        for n in ast.walk(tree):
            if isinstance(n,ast.Import):imports += [a.name for a in n.names]
            elif isinstance(n,ast.ImportFrom):imports.append(n.module or '')
        assert not any(x.startswith(('requests','urllib','httpx','streamlit')) for x in imports)
        assert 'git push' not in p.read_text() and 'workflow_dispatch' not in p.read_text()


def test_commit_time_not_ingestion_start_controls_visibility(tmp_path,bundle):
    a=Archive(tmp_path/'archive');times=iter(['2026-10-15T01:00:00Z','2026-10-15T02:00:00Z'])
    r=a.ingest(bundle,clock=lambda:next(times))
    assert r['committed_at']=='2026-10-15T02:00:00+00:00'
    assert a.query(channel='operating_publication',at='2026-10-15T01:30:00Z')['status']=='NO_ELIGIBLE_GENERATION'
    assert a.query(channel='operating_publication',at='2026-10-15T02:01:00Z')['total_rows']==1


def test_uncommitted_prepared_generation_never_visible_and_resume_does_not_duplicate(tmp_path,bundle,monkeypatch):
    import investment_assets.archive as module
    a=Archive(tmp_path/'archive');real=module.exclusive_write
    def crash(path,raw):
        if Path(path).parent.name=='commits':raise OSError('SYNTHETIC_BEFORE_COMMIT')
        return real(path,raw)
    monkeypatch.setattr(module,'exclusive_write',crash)
    with pytest.raises(OSError):ingest(a,bundle)
    assert a.verify()['status']=='INCOMPLETE'
    assert a.query(channel='operating_publication',at='2026-10-16T00:00:00Z')['status']=='NO_ELIGIBLE_GENERATION'
    monkeypatch.setattr(module,'exclusive_write',real)
    assert ingest(a,bundle)['status']=='ALREADY_PRESENT'
    assert a.verify()['status']=='PASS' and a.verify()['generations']==1


def test_actual_current_publication_uses_recovery_version_time_not_retained_original_time():
    b=publication(GitSource(REPO,'197aa235','main'))
    assert b['asof']=='2026-10-23' and len(b['observations'])==226693
    assert b['published_at'].startswith('2026-10-23') and b['available_at'].startswith('2026-10-26')
    assert {r['kind'] for r in b['observations']}=={'foreign_flow','price_original','price_adjusted','index_close'}


def test_actual_analysis_retains_historical_backtest_false_and_independent_states(tmp_path):
    b=analysis(GitSource(REPO,'197aa235','main'))
    assert b['asof']=='2026-10-23' and len(b['observations'])==224216
    assert b['historical_backtest_eligible'] is False and all(r['historical_backtest_eligible'] is False for r in b['observations'])
    assert all(r['provider_finality']=='UNVERIFIED' for r in b['observations'])
    assert any(r['official_validation']=='SIX_FIELD_MATCH' for r in b['observations'] if r['kind']=='analysis_price')
    assert all(r['official_validation']=='UNVERIFIED' for r in b['observations'] if r['kind']=='institution_flow')


def test_full_artifact_adapter_positive_path_uses_synthetic_offline_responses(tmp_path,bundle,monkeypatch):
    """The real ZIP is inaccessible; this is deliberately synthetic positive-path coverage."""
    from collector_v1 import kis_manual as manual
    from collector_v1.experiment import runtime
    from test_full_market_experiment import universe, transport
    import investment_assets.sources as module
    original=manual.validate_control
    monkeypatch.setattr(manual,'validate_control',lambda c,current=None:original(c,dt.datetime(2026,10,26,12,tzinfo=dt.timezone.utc)))
    monkeypatch.setattr('collector_v1.experiment.storage.time.sleep',lambda _:None)
    monkeypatch.setattr(manual.ManualBudget,'remaining_seconds',lambda self:10000)
    monkeypatch.setenv('KIS_APP_KEY','SYNTHETIC_APP_KEY');monkeypatch.setenv('KIS_APP_SECRET','SYNTHETIC_APP_SECRET')
    control=dict(schema=1,trial_id='kis-full-20261026',request_id='SYNTHETIC_ONLY',mode='full',asof='2026-10-26',previous_run=None,max_total_http=5600,not_before='2026-10-26T11:45:00+00:00',expires_at='2026-10-26T14:35:00+00:00',approval=manual.FULL_APPROVAL)
    state=tmp_path/'SYNTHETIC-state';u=universe();calls,opener=transport(u)
    r=manual.run(state/'modes/full',control,u,'SYNTHETIC_ONLY',opener=opener)
    assert r['physical_http']==len(calls)==4701 and r['status']=='PASS'
    (state/'universe.json').write_bytes(encoded(u))
    manifest=dict(asof='2026-10-26',run_id='40326538555',files=runtime.inventory(state))
    (state/'manifest.json').write_bytes(encoded(manifest))
    archive=tmp_path/'SYNTHETIC-artifact.zip'
    import zipfile
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in state.rglob('*'):
            if p.is_file():z.write(p,str(p.relative_to(state)))
    fake_receipt=dict(state_manifest_sha256=digest(encoded(manifest)))
    fake_meta=dict(digest='sha256:'+digest(archive.read_bytes()))
    monkeypatch.setattr(module,'kis_receipt',lambda *a: (copy.deepcopy(bundle),fake_receipt,fake_meta))
    restored=kis_artifact(None,'SYNTHETIC',archive)
    assert restored['archive_status']=='COMPLETE_BYTES' and len(restored['observations'])==4700
    assert restored['flow_validation']=='KIS_INTERNAL_PASS' and restored['price_validation']=='KIS_INTERNAL_PASS_PENDING_KRX'
    assert all(r['provider_finality']=='UNVERIFIED' for r in restored['observations'])
