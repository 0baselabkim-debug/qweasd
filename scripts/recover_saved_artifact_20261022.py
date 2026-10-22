"""Read one saved artifact using existing runner auth. Never collect or publish main."""
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import zipfile
from checkpoint_storage import pack, unpack, digest
from daily_checkpoint import Journal
from persist_daily_checkpoint import persist

EXPECTED = {'bytes': 57118617, 'sha256': '5bb6e061082877f0713b36a3c1af02f57c109c16991bec0f7cba937728696ef7'}
BRANCH = 'recovery/artifact-20261022'


def main():
    root = Path('saved-artifact')
    root.mkdir(exist_ok=False)
    archive = root / 'artifact-12422660017.zip'
    with archive.open('wb') as out:
        r = subprocess.run(['gh', 'api', 'repos/0baselabkim-debug/qweasd/actions/artifacts/12422660017/zip'],
                           stdout=out, stderr=subprocess.PIPE, timeout=180)
    if r.returncode:
        raise RuntimeError('Authenticated artifact download failed; no retry or permission expansion')
    if digest(archive.read_bytes()) != EXPECTED:
        raise ValueError('ZIP byte length/hash mismatch')
    extracted = root / 'extracted'
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        seen = set()
        if sum(i.file_size for i in infos) > 3 * 1024**3:
            raise ValueError('Archive expansion limit exceeded')
        for i in infos:
            p = PurePosixPath(i.filename)
            mode = i.external_attr >> 16
            if (p.is_absolute() or '..' in p.parts or '\\' in i.filename or ':' in i.filename or
                i.filename in seen or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR) or
                i.flag_bits & 1 or i.file_size > 1024**3):
                raise ValueError('Unsafe archive member')
            seen.add(i.filename)
        for i in infos:
            target = extracted.joinpath(*PurePosixPath(i.filename).parts)
            if i.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
    def unique(name):
        matches = list(extracted.rglob(name))
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous artifact source: ' + name)
        return matches[0]
    bundle_path, journal_path = unique('bundle.json.gz'), unique('successes.jsonl')
    bundle = json.loads(gzip.decompress(bundle_path.read_bytes()))
    if (bundle['trade_date'] != '20261022' or bundle['status'] != 'complete' or
        len(bundle['records']) != 2432 or len(bundle['excluded']) != 108 or bundle['failures']):
        raise ValueError('Bundle date/status/count mismatch')
    universe = [r['symbol'] for r in bundle['universe']]
    if (len(universe) != len(set(universe)) or set(bundle['records']) & set(bundle['excluded']) or
        set(universe) != set(bundle['records']) | set(bundle['excluded'])):
        raise ValueError('Universe coverage mismatch')
    raw = journal_path.read_bytes()
    transport = pack(raw)
    restored = unpack(transport.__getitem__, transport)
    if raw != restored:
        raise ValueError('Journal transport changed original bytes')
    trial = root / 'journal-validation-copy.jsonl'
    trial.write_bytes(raw)
    replay = {'records': {}, 'excluded': {}, 'failures': {}}
    Journal(trial, '20261022').restore(replay)
    if replay['records'] != bundle['records']:
        raise ValueError('Journal successes disagree with bundle')
    if any(bundle['excluded'].get(c) != v for c, v in replay['excluded'].items()):
        raise ValueError('Journal exclusions disagree with bundle')
    results = Path('daily-results')
    results.mkdir(exist_ok=False)
    shutil.copy2(bundle_path, results / 'bundle.json.gz')
    shutil.copy2(journal_path, results / 'successes.jsonl')
    if (bundle_path.parent / 'diagnostics').exists():
        shutil.copytree(bundle_path.parent / 'diagnostics', results / 'diagnostics')
    report = {'artifact_id': '12422660017', 'source_run': '39758472001', 'zip': EXPECTED,
              'bundle': digest(bundle_path.read_bytes()), 'trade_date': bundle['trade_date'],
              'status': bundle['status'], 'success': 2432, 'excluded': 108, 'failed': 0,
              'journal_original': digest(raw), 'journal_restored': digest(restored),
              'journal_truncated_tail_bytes': len(raw) - trial.stat().st_size,
              'members': sorted(seen), 'additional_market_api_calls': 0,
              'publication': 'not_attempted', 'verification_run': os.environ.get('GITHUB_RUN_ID')}
    dest = results / 'diagnostics' / 'artifact-recovery-verification.json'
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'members'}, ensure_ascii=False))
    print(json.dumps(persist(Path.cwd(), '20261022', prefix='recovery/artifact-')))
    # Keep the original ZIP too, in the same isolated evidence branch.
    subprocess.run(['git', 'fetch', 'origin', BRANCH], check=True)
    checkout = Path('/tmp/verified-artifact-evidence')
    subprocess.run(['git', 'worktree', 'add', '--detach', str(checkout), 'FETCH_HEAD'], check=True)
    shutil.copy2(archive, checkout / archive.name)
    def git(*args):
        subprocess.run(['git', '-C', str(checkout), *args], check=True)
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('add', archive.name)
    git('commit', '-m', 'Preserve verified original 20261022 artifact ZIP')
    git('push', 'origin', 'HEAD:refs/heads/' + BRANCH)


if __name__ == '__main__':
    main()
