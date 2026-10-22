import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import checkpoint_storage as storage
from persist_daily_checkpoint import persist


def test_lossless_and_deterministic():
    raw = b'{"number":1.230000e-9,"text":"\\uac00"}\r\n' * 10000 + b'partial'
    files = storage.pack(raw)
    assert files == storage.pack(raw)
    assert storage.unpack(files.__getitem__, files) == raw
    assert storage.RAW not in files


def test_split_and_manifest():
    raw = os.urandom(24000)
    files = storage.pack(raw, limit=4096)
    m = json.loads(files[storage.MANIFEST])
    assert len(m['parts']) > 1
    assert all(len(b) <= 4096 for b in files.values())
    assert storage.unpack(files.__getitem__, files) == raw


@pytest.mark.parametrize('damage', ['hash', 'missing', 'order', 'raw_hash', 'size', 'manifest', 'extra'])
def test_corruption_never_falls_back(damage):
    files = storage.pack(os.urandom(12000), limit=4096)
    files[storage.RAW] = b'stale plaintext'
    m = json.loads(files[storage.MANIFEST])
    first = m['parts'][0]['path']
    if damage == 'hash': files[first] = b'corrupt'
    elif damage == 'missing': del files[first]
    elif damage == 'order': m['parts'].reverse()
    elif damage == 'raw_hash': m['raw']['sha256'] = '0' * 64
    elif damage == 'size': m['parts'][0]['bytes'] = storage.LIMIT + 1
    elif damage == 'extra': files[storage.PACKED + '.part-999999'] = b'extra'
    files[storage.MANIFEST] = json.dumps(m).encode()
    if damage == 'manifest': del files[storage.MANIFEST]
    with pytest.raises((ValueError, KeyError)):
        storage.unpack(files.__getitem__, files)


def test_legacy_and_empty():
    assert storage.unpack({storage.RAW: b'legacy\n'}.__getitem__, [storage.RAW]) == b'legacy\n'
    assert storage.unpack({}.__getitem__, []) is None
    files = storage.pack(b'')
    assert storage.unpack(files.__getitem__, files) == b''


def test_size_guard():
    with pytest.raises(ValueError): storage.pack(b'x', limit=0)
    with pytest.raises(ValueError): storage.pack(b'x', limit=storage.LIMIT + 1)
    with pytest.raises(ValueError): storage.pack(b'x', limit=20)


def test_git_migration_repeat_and_diagnostics(tmp_path):
    remote, repo = tmp_path/'remote.git', tmp_path/'repo'
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repo).decode().strip()
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    subprocess.run(['git', 'clone', str(remote), str(repo)], check=True, capture_output=True)
    git('config','user.name','test'); git('config','user.email','test@example.invalid')
    results = repo/'daily-results'; results.mkdir()
    (results/'bundle.json.gz').write_bytes(gzip.compress(b'{}'))
    raw = b'raw 1.0000\r\n' * 1000
    (repo/storage.RAW).write_bytes(raw)
    diagnostics = results/'diagnostics/old.json'; diagnostics.parent.mkdir(); diagnostics.write_text('{"evidence":1}')
    git('add','.'); git('commit','-m','legacy')
    head = git('rev-parse','HEAD')
    git('push','origin','HEAD:refs/heads/data/daily-20261022')
    # The parent's diagnostics must survive even when not present locally.
    diagnostics.unlink()
    original_index = git('ls-files','--stage')
    first = persist(repo,'20261022')
    names = git('ls-tree','-r','--name-only',first['commit']).splitlines()
    assert storage.RAW not in names and storage.MANIFEST in names
    assert 'daily-results/diagnostics/old.json' in names
    assert (repo/storage.RAW).read_bytes() == raw
    assert git('rev-parse','HEAD') == head and git('ls-files','--stage') == original_index
    assert persist(repo,'20261022')['status'] == 'unchanged'
    restored = tmp_path/'restored.jsonl'
    assert storage.restore(repo, first['commit'], restored) == storage.digest(raw)
    assert restored.read_bytes() == raw
    # Exercise a split-to-single replacement and ensure no stale parts survive.
    split = storage.pack(os.urandom(20000), limit=4096)
    for name, data in split.items(): (repo/name).write_bytes(data)
    git('add','.');git('commit','-m','split fixture')
    git('push','origin','HEAD:refs/heads/data/split-20261022')
    second = persist(repo,'20261022',prefix='data/split-')
    names = git('ls-tree','-r','--name-only',second['commit']).splitlines()
    assert not any('.part-' in name for name in names)
    assert storage.RAW not in names


def test_oversized_bundle_rejected_before_git(tmp_path, monkeypatch):
    results = tmp_path/'daily-results'; results.mkdir()
    (results/'bundle.json.gz').write_bytes(b'x'*101)
    import persist_daily_checkpoint as persistence
    monkeypatch.setattr(persistence, 'LIMIT', 100)
    with pytest.raises(ValueError, match='size limit'): persist(tmp_path,'20261022')


def test_restore_failure_preserves_existing_output(tmp_path, monkeypatch):
    files = storage.pack(b'new journal')
    files[storage.PACKED] = b'broken gzip'
    output = tmp_path/'successes.jsonl'; output.write_bytes(b'local original')
    def git_output(args, cwd):
        if args[1] == 'ls-tree': return ('\n'.join(files)+'\n').encode()
        return files[args[-1].split(':', 1)[1]]
    monkeypatch.setattr(subprocess, 'check_output', git_output)
    with pytest.raises(ValueError): storage.restore(tmp_path,'fixture',output)
    assert output.read_bytes() == b'local original'
