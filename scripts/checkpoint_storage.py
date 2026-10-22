"""Lossless Git transport for journals; collector and Journal validation stay unchanged."""
import argparse
import gzip
import hashlib
import json
import subprocess
from pathlib import Path

LIMIT = 90 * 1024 * 1024
ROOT = 'daily-results/'
RAW = ROOT + 'successes.jsonl'
MANIFEST = ROOT + 'successes.manifest.json'
PACKED = ROOT + 'successes.jsonl.gz'


def digest(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def pack(raw, limit=LIMIT):
    if not 0 < limit <= LIMIT:
        raise ValueError('Invalid checkpoint size limit')
    compressed = gzip.compress(raw, mtime=0)
    chunks = [compressed[i:i + limit] for i in range(0, len(compressed), limit)]
    files = {}
    parts = []
    for i, chunk in enumerate(chunks):
        name = PACKED if len(chunks) == 1 else PACKED + f'.part-{i:06d}'
        files[name] = chunk
        parts.append({'index': i, 'path': name, **digest(chunk)})
    manifest = {'version': 1, 'encoding': 'gzip', 'raw': digest(raw),
                'compressed': digest(compressed), 'parts': parts}
    files[MANIFEST] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    if any(len(data) > limit for data in files.values()):
        raise ValueError('Checkpoint transport file exceeds safe size limit')
    return files


def check(data, expected):
    if digest(data) != expected:
        raise ValueError('Checkpoint byte length/SHA-256 mismatch')


def unpack(read, names):
    if MANIFEST not in names:
        if any(n.startswith(PACKED) for n in names):
            raise ValueError('Compressed journal is missing its manifest')
        return read(RAW) if RAW in names else None
    manifest = json.loads(read(MANIFEST))
    if manifest['version'] != 1 or manifest['encoding'] != 'gzip':
        raise ValueError('Unsupported checkpoint format')
    parts = manifest['parts']
    if not parts:
        raise ValueError('Empty checkpoint part list')
    chunks = []
    expected_names = {MANIFEST}
    for i, part in enumerate(parts):
        name = PACKED if len(parts) == 1 else PACKED + f'.part-{i:06d}'
        if part['index'] != i or part['path'] != name:
            raise ValueError('Checkpoint part order/path mismatch')
        if not 0 < part['bytes'] <= LIMIT:
            raise ValueError('Checkpoint part size exceeds safe limit')
        chunk = read(name)
        check(chunk, {k: part[k] for k in ('bytes', 'sha256')})
        chunks.append(chunk)
        expected_names.add(name)
    actual_names = {n for n in names if n == MANIFEST or n.startswith(PACKED)}
    if actual_names != expected_names:
        raise ValueError('Missing or unexpected checkpoint parts')
    compressed = b''.join(chunks)
    check(compressed, manifest['compressed'])
    raw = gzip.decompress(compressed)
    check(raw, manifest['raw'])
    return raw


def restore(repo, ref, output):
    repo, output = Path(repo), Path(output)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=repo)
    names = git('ls-tree', '-r', '--name-only', ref, '--', 'daily-results').decode().splitlines()
    raw = unpack(lambda name: git('show', ref + ':' + name), names)
    if raw is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + '.restore-tmp')
        temporary.write_bytes(raw)
        temporary.replace(output)
    return None if raw is None else digest(raw)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--ref', default='FETCH_HEAD')
    p.add_argument('--output', type=Path, default=Path(RAW))
    a = p.parse_args()
    print(json.dumps(restore(Path.cwd(), a.ref, a.output)))
