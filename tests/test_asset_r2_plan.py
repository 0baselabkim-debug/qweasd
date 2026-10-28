"""Local-only incremental inventory and pinned full-market completeness gates."""
import copy
import json
from pathlib import Path
import pytest
from investment_assets import Archive, ArchiveError
from investment_assets.common import encoded, digest
from investment_assets import r2_plan as p
from investment_assets.sources import _full_kis_scope
from test_investment_assets import bundle


@pytest.fixture
def archived(tmp_path,bundle):
    a=Archive(tmp_path/'archive');a.ingest(bundle)
    return a,bundle


def reseal(value):
    value['inventory_id']=digest(encoded({k:v for k,v in value.items() if k!='inventory_id'}));return value


def test_inventory_stable_deduplicated_and_not_remote_success(archived,tmp_path):
    a,b=archived;one=p.snapshot_inventory(a.root);two=p.snapshot_inventory(a.root)
    assert one==two and p.verify_inventory(one)==one
    plan=p.incremental_plan(one)
    assert plan['total_generations']==1 and not plan['upload_authorized'] and not plan['remote_presence_verified']
    assert plan['encrypted_payload_bytes'] is None and not plan['external_upload_performed']
    assert not any(f['path'] in ('catalog.duckdb','writer.lock') for f in one['files'])
    dest=tmp_path/'plans'/'first.json'
    first=p.write_local_plan(a.root,dest);before=dest.read_bytes()
    again=p.write_local_plan(a.root,dest)
    assert again==first and dest.read_bytes()==before


def test_repeated_baseline_has_zero_local_delta_not_proof_of_backup(archived):
    a,_=archived;v=p.snapshot_inventory(a.root);plan=p.incremental_plan(v,v)
    assert plan['local_new_logical_files']==plan['new_unique_plaintext_blobs']==plan['planned_plaintext_payload_bytes']==0
    assert plan['status']=='LOCAL_INCREMENT_PLAN_NOT_REMOTE_COMPLETION' and not plan['remote_presence_verified']


def test_new_generation_shares_original_but_retains_new_metadata(archived):
    a,b=archived;before=p.snapshot_inventory(a.root)
    b=copy.deepcopy(b);b['source']['commit']='b'*40;a.ingest(b)
    after=p.snapshot_inventory(a.root);plan=p.incremental_plan(after,before)
    assert plan['total_generations']==2 and plan['local_new_logical_files']>0
    assert plan['reused_plaintext_hashes']>0
    new_paths=[x for blob in plan['blobs'] for x in blob['source_paths']]
    assert not any(x.startswith('objects/sha256/') for x in new_paths)
    assert a.verify()['unique_original_objects']==1


def test_missing_history_and_mutated_previous_path_rejected(archived):
    a,_=archived;v=p.snapshot_inventory(a.root)
    wrong=copy.deepcopy(v);wrong['files'][0]['sha256']='0'*64;reseal(wrong)
    with pytest.raises(ArchiveError,match='changed'):p.incremental_plan(v,wrong)
    other=copy.deepcopy(v);other['files'].append(dict(path='objects/sha256/ff/'+'f'*64,sha256='f'*64,bytes=1));other['files'].sort(key=lambda f:f['path']);reseal(other)
    with pytest.raises(ArchiveError,match='history_missing'):p.incremental_plan(v,other)


@pytest.mark.parametrize('defect',['digest','traversal','duplicate','missing_commit','finality'])
def test_inventory_corruption_or_false_finality_never_accepted(archived,defect):
    a,_=archived;v=p.snapshot_inventory(a.root)
    if defect=='digest':v['files'][0]['bytes']+=1
    elif defect=='traversal':v['files'][0]['path']='../escape';reseal(v)
    elif defect=='duplicate':v['files'].insert(0,copy.deepcopy(v['files'][0]));reseal(v)
    elif defect=='missing_commit':v['files']=[f for f in v['files'] if not f['path'].startswith('commits/')];reseal(v)
    elif defect=='finality':v['generations'][0]['provider_finality']='FINAL';reseal(v)
    with pytest.raises(ArchiveError):p.verify_inventory(v)


def test_incomplete_or_corrupt_archive_produces_no_plan(archived,tmp_path):
    a,_=archived;g=next((a.root/'generations').iterdir());commit=a.root/'commits'/(g.name+'.json');commit.unlink()
    with pytest.raises(ArchiveError,match='complete_committed'):p.write_local_plan(a.root,tmp_path/'should-not-exist.json')
    assert not (tmp_path/'should-not-exist.json').exists()
    (g/'summary.json').chmod(0o644);(g/'summary.json').write_bytes(b'CORRUPT')
    with pytest.raises(ArchiveError):p.snapshot_inventory(a.root)


def test_wrong_previous_id_and_unsafe_destination_and_overwrite_blocked(archived,tmp_path):
    a,_=archived;dest=tmp_path/'first.json';r=p.write_local_plan(a.root,dest)
    with pytest.raises(ArchiveError,match='digest'):p.write_local_plan(a.root,tmp_path/'other.json',previous_path=dest,previous_id='0'*64)
    with pytest.raises(ArchiveError,match='together'):p.write_local_plan(a.root,tmp_path/'other.json',previous_path=dest)
    with pytest.raises(ArchiveError):p.write_local_plan(a.root,a.root/'forbidden.json')
    dest.chmod(0o644);dest.write_bytes(b'CORRUPT')
    with pytest.raises(ArchiveError,match='immutable'):p.write_local_plan(a.root,dest)
    assert dest.read_bytes()==b'CORRUPT'


def scope_fixture():
    selected=[dict(symbol=f'{i:06}',market='KOSPI' if i<1000 else 'KOSDAQ') for i in range(2349)]
    rows=[dict(**r,asof='2026-10-26',kis_validation_status='PASS',provider_finality='UNVERIFIED',official_price_status='PENDING_KRX') for r in selected]
    report=dict(status='PASS',mode='full',asof='2026-10-26',run_id='40326538555',target=2349,physical_http=4701,kis_validated=2349,received_symbols=2349,db_json_match=True,provider_finality='UNVERIFIED',official_price_status='PENDING_KRX',population_sha256='a'*64,target_symbols=sorted(r['symbol'] for r in selected),rows=rows)
    return selected,report,copy.deepcopy(report)


def test_full_2349_scope_has_no_missing_duplicate_wrong_day_or_promoted_finality():
    selected,report,receipt=scope_fixture()
    _full_kis_scope(report,receipt,selected)


@pytest.mark.parametrize('defect',['partial','duplicate','wrong_market','wrong_day','finality','receipt_conflict','wrong_run','DB_mismatch'])
def test_false_pass_full_market_reports_blocked(defect):
    selected,r,receipt=scope_fixture()
    if defect=='partial':r['rows'].pop()
    elif defect=='duplicate':r['rows'][-1]=copy.deepcopy(r['rows'][0])
    elif defect=='wrong_market':r['rows'][0]['market']='KOSDAQ'
    elif defect=='wrong_day':r['rows'][0]['asof']='2026-10-27'
    elif defect=='finality':r['rows'][0]['provider_finality']='FINAL'
    elif defect=='receipt_conflict':receipt['population_sha256']='b'*64
    elif defect=='wrong_run':r['run_id']='999'
    elif defect=='DB_mismatch':r['db_json_match']=False
    with pytest.raises(ArchiveError):_full_kis_scope(r,receipt,selected)


def test_missing_root_does_not_create_empty_successful_archive(tmp_path):
    root=tmp_path/'absent'
    with pytest.raises(ArchiveError,match='existing_archive'):p.snapshot_inventory(root)
    assert not root.exists()


def test_planner_has_no_network_or_upload_client():
    import ast
    text=Path(p.__file__).read_text();tree=ast.parse(text)
    imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    imports += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
    assert not any(str(x).startswith(('boto','requests','urllib','httpx','azure','google.cloud')) for x in imports)
    assert 'put_object(' not in text and 'delete_object(' not in text
