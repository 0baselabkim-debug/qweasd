"""Preserved real source + explicitly synthetic attestation; CI-only fixtures."""
import json,os,zipfile
from pathlib import Path
import pytest
from collector_v1.regular import operator,runner,policy
from collector_v1.core import Failure,Store
from test_regular_operations import resume_context

@pytest.fixture(scope='module')
def synthetic_source(tmp_path_factory):
 # Preserve real values/old ZIP unchanged; add SYNTHETIC source attestation solely for offline tests.
 path=Path(os.getenv('REGULAR_SOURCE_ZIP','/workspace/regular-41633101454-diagnostics/13230751197.zip'))
 root=tmp_path_factory.mktemp('SYNTHETIC-attested-source');out=root/'source'
 with zipfile.ZipFile(path) as z:z.extractall(out)
 c=json.loads((out/'context.json').read_bytes());s=Store(out)
 owner=dict(repository=policy.REPO,run_id=c['run_id'],commit=c['code_commit'],id='a'*32,acquired_at='2026-11-03T12:10:12Z',expires_at='2026-11-03T15:20:12Z')
 s.write('source-code.json',dict(protocol=operator.PROTOCOL,run_id=c['run_id'],code_commit=c['code_commit'],synchronous_hosted_worker=True,contract_hashes=operator.contract_hashes()))
 s.write('lease-acquisition.json',dict(revision='e'*40,owner=owner));s.write('SYNTHETIC-TEST-ONLY.json',dict(real_network=False))
 m=s.read('manifest.json');m['files']=runner.inventory(out);s.write('manifest.json',m)
 archive=root/'SYNTHETIC-source.zip'
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
  for p in out.rglob('*'):
   if p.is_file():z.write(p,str(p.relative_to(out)))
 return archive,c,owner


def test_resume_restores_original_budget_and_validated_prefix(synthetic_source,tmp_path):
 archive,c,_=synthetic_source;p=operator.restore_archive(archive,tmp_path/'restored',resume_context(),c['code_commit'])
 assert (p['total_http'],p['initial_collection_http'],p['validated'],p['target'],p['missing_requests'])==(568,564,280,2348,4135)
 assert p['total_http']+p['missing_requests']+1==4704
 assert len(p['allowed_failure_hashes'])==4135


def test_source_corruption_is_never_approved(synthetic_source,tmp_path):
 archive,c,_=synthetic_source
 with zipfile.ZipFile(archive) as a,zipfile.ZipFile(tmp_path/'bad.zip','w') as b:
  for n in a.namelist():b.writestr(n,a.read(n)+(b'corrupt' if n.endswith('universe.json') else b''))
 with pytest.raises(Failure,match='source_hash_mismatch'):operator.restore_archive(tmp_path/'bad.zip',tmp_path/'bad',resume_context(),c['code_commit'])


def test_changed_runtime_contract_rejected_before_any_provider_request(synthetic_source,tmp_path,monkeypatch):
 archive,c,_=synthetic_source
 monkeypatch.setattr(operator,'contract_hashes',lambda:{'not-reviewed':'f'*64})
 with pytest.raises(Failure,match='source_contract'):operator.restore_archive(archive,tmp_path/'changed',resume_context(),c['code_commit'])

