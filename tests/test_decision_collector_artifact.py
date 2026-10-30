"""Artifact trust anchors and bounded native inspection never invoke market APIs."""
import json
import zipfile
from pathlib import Path
import pytest
from screener.decision_evidence.collector_artifact import extract,ASOF,RUN,GENERATION
from screener.decision_support.evidence import sha,encoded


def archive(tmp_path, extra=None):
    value=dict(asof=ASOF,run_id=RUN,generation_id=GENERATION,files={'report.json':sha(b'original')})
    raw=encoded(value);p=tmp_path/'source.zip'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('manifest.json',raw);z.writestr('report.json',b'original')
        if extra:z.writestr(*extra)
    return p,sha(raw)


def test_zip_anchor_manifest_and_read_only_original(tmp_path):
    p,m=archive(tmp_path);original=p.read_bytes();r=extract(p,tmp_path/'new',sha(original),m)
    assert r['files_verified']==2 and (tmp_path/'new/report.json').read_bytes()==b'original' and p.read_bytes()==original
    with pytest.raises(ValueError,match='must_be_new'):extract(p,tmp_path/'new',sha(original),m)
    with pytest.raises(ValueError,match='ZIP_digest'):extract(p,tmp_path/'other')
    with pytest.raises(ValueError,match='manifest_anchor'):extract(p,tmp_path/'other',sha(original),'0'*64)


@pytest.mark.parametrize('path',['../escape','/absolute','dir/../../escape','dir\\escape'])
def test_unsafe_archive_never_extracts(tmp_path,path):
    p,m=archive(tmp_path,(path,b'payload'))
    with pytest.raises(ValueError,match='unsafe_member'):extract(p,tmp_path/'new',sha(p.read_bytes()),m)
    assert not (tmp_path/'new').exists()


def test_unmanifested_file_is_not_approved(tmp_path):
    p,m=archive(tmp_path,('extra.json',b'unknown'))
    with pytest.raises(ValueError,match='population_mismatch'):extract(p,tmp_path/'new',sha(p.read_bytes()),m)


def test_new_workflow_is_finite_separate_and_has_no_market_credential():
    text=Path('.github/workflows/decision-v2-source-20261029.yml').read_text()
    assert 'schedule:' not in text and 'cron:' not in text and 'secrets.' not in text
    assert 'branches: [feat/decision-evidence-v2]' in text and 'paths: [.github/trials/decision-v2-source-20261029.json]' in text
    assert 'kis-validation-auth' not in text and 'decision_v2_native.py' in text
