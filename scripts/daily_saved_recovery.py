"""Bounded, authenticated recovery of preserved daily bytes. Never calls market APIs."""
import argparse
import copy
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import zipfile

try:
    from .checkpoint_storage import digest, restore, RAW
    from .daily_checkpoint import Journal
    from .daily_publish_transaction import Blocked, current_main, guard_ref, git, publish
except ImportError:
    from checkpoint_storage import digest, restore, RAW
    from daily_checkpoint import Journal
    from daily_publish_transaction import Blocked, current_main, guard_ref, git, publish

REPO = '0baselabkim-debug/qweasd'
WORKFLOW = '.github/workflows/daily-dashboard.yml'
RECEIPT = 'recovery-source.json'
MAX_ARCHIVE = 1024 * 1024 * 1024


def api(path, binary=False):
    # Existing runner token only. No auth installation, scope changes, or retries.
    data = subprocess.check_output(['gh', 'api', path], stderr=subprocess.PIPE)
    return data if binary else json.loads(data)


def pages(path, key):
    result = []
    for page in range(1, 11):
        separator = '&' if '?' in path else '?'
        rows = api(f'{path}{separator}per_page=100&page={page}')[key]
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise Blocked('Pagination bound reached; cannot establish complete source list')


def source_run(run):
    if (run['repository']['full_name'] != REPO or run['head_repository']['full_name'] != REPO
        or run['path'] != WORKFLOW or run['head_branch'] != 'main'
        or run['event'] not in ('schedule', 'workflow_dispatch')
        or run['status'] != 'completed' or run['conclusion'] != 'failure'
        or run['run_attempt'] != 1):
        raise Blocked('Source is not an eligible first failed main collection run')


def archive_files(data, artifact):
    expected = artifact.get('digest') or ''
    if (not expected.startswith('sha256:') or hashlib.sha256(data).hexdigest() != expected[7:]
        or len(data) != artifact['size_in_bytes'] or artifact.get('expired')):
        raise Blocked('Artifact ZIP size/digest mismatch or expired artifact')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        members = z.infolist()
        names = [m.filename for m in members if not m.is_dir()]
        if len(names) != len(set(names)) or sum(m.file_size for m in members) > MAX_ARCHIVE:
            raise Blocked('Duplicate ZIP entries or oversized artifact')
        result = {}
        for item in members:
            name = item.filename
            path = PurePosixPath(name)
            if (path.is_absolute() or '..' in path.parts or '\\' in name
                or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise Blocked('Unsafe artifact member')
            if not item.is_dir():
                result[name] = z.read(item)
        return result


def download(run, name):
    artifacts = pages(f'repos/{REPO}/actions/runs/{run["id"]}/artifacts', 'artifacts')
    matches = [a for a in artifacts if a['name'] == name]
    if len(matches) != 1:
        raise Blocked('Expected exactly one trusted artifact: ' + name)
    artifact = matches[0]
    if artifact.get('workflow_run', {}).get('id') != run['id']:
        raise Blocked('Artifact run mismatch')
    data = api(f'repos/{REPO}/actions/artifacts/{artifact["id"]}/zip', binary=True)
    return archive_files(data, artifact)


def receipt(folder, day, run_id, attempt):
    dt.datetime.strptime(day, '%Y%m%d')
    folder = Path(folder)
    bundle = json.loads(gzip.decompress((folder/'bundle.json.gz').read_bytes()))
    if bundle['trade_date'] != day or bundle['exchange'] != 'KRX':
        raise Blocked('Receipt trading date mismatch')
    files = {}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise Blocked('Symlink in source')
        if path.is_file() and path.name != RECEIPT:
            files[path.relative_to(folder).as_posix()] = digest(path.read_bytes())
    result = {'schema': 1, 'repository': REPO, 'workflow': WORKFLOW, 'day': day,
              'run_id': str(run_id), 'run_attempt': int(attempt), 'files': files,
              'status': bundle.get('status'), 'counts': {k: len(bundle.get(k, {}))
              for k in ('records', 'excluded', 'failures')}}
    (folder/RECEIPT).write_text(json.dumps(result, sort_keys=True, indent=2)+'\n')
    return result


def preserved_files(folder):
    folder = Path(folder)
    info = json.loads((folder/RECEIPT).read_bytes())
    result = {RECEIPT: (folder/RECEIPT).read_bytes()}
    for name in info['files']:
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or '\\' in name
            or name == RECEIPT or (folder/name).is_symlink()):
            raise Blocked('Unsafe preserved source path')
        result[name] = (folder/name).read_bytes()
    return result


def durable_original(sealed, checkpoint_status, artifact_status):
    if sealed != 'success' or 'success' not in (checkpoint_status, artifact_status):
        raise Blocked('No confirmed durable original; publication blocked')
    return {'status': 'durable_original_confirmed', 'checkpoint': checkpoint_status,
            'artifact': artifact_status}


def verify_source(files, run_id, day=None, attempt=1):
    receipt_data = json.loads(files[RECEIPT])
    if (receipt_data['schema'] != 1 or receipt_data['repository'] != REPO
        or receipt_data['workflow'] != WORKFLOW or receipt_data['run_id'] != str(run_id)
        or receipt_data['run_attempt'] != int(attempt)):
        raise Blocked('Preserved source provenance mismatch')
    fixed_day = receipt_data['day']
    dt.datetime.strptime(fixed_day, '%Y%m%d')
    if day is not None and fixed_day != day:
        raise Blocked('Preserved source target date mismatch')
    if set(files) != set(receipt_data['files']) | {RECEIPT}:
        raise Blocked('Missing or unexpected preserved files')
    for name, expected in receipt_data['files'].items():
        if digest(files[name]) != expected:
            raise Blocked('Preserved file byte/hash mismatch: ' + name)
    bundle = json.loads(gzip.decompress(files['bundle.json.gz']))
    counts = {k: len(bundle[k]) for k in ('records', 'excluded', 'failures')}
    codes = [r['symbol'] for r in bundle['universe']]
    if (bundle['trade_date'] != fixed_day or bundle['exchange'] != 'KRX'
        or bundle.get('status') != 'complete' or receipt_data['status'] != 'complete'
        or counts != receipt_data['counts'] or counts['failures'] != 0
        or len(codes) != len(set(codes))
        or set(bundle['records']) & set(bundle['excluded'])
        or set(codes) != set(bundle['records']) | set(bundle['excluded'])):
        raise Blocked('Incomplete, conflicting or wrong-date bundle')
    # Preserve original bytes. Journal validation runs against a temporary copy;
    # a truncated tail, new outcome, row hash/date mismatch or conflict blocks recovery.
    with tempfile.TemporaryDirectory(prefix='daily-journal-check-') as temp:
        path = Path(temp)/'successes.jsonl'
        path.write_bytes(files['successes.jsonl'])
        copied = copy.deepcopy(bundle)
        added = Journal(path, fixed_day).restore(copied)
        if added or copied != bundle or path.read_bytes() != files['successes.jsonl']:
            raise Blocked('Journal and completed bundle do not agree')
    return receipt_data


def publication_report(run):
    source_run(run)
    report_files = download(run, 'daily-publish-status-' + str(run['id']) + '-1')
    if set(report_files) != {'daily-publish-report.json'}:
        raise Blocked('Unexpected publication report artifact')
    report = json.loads(report_files['daily-publish-report.json'])
    if (report.get('source_run') != str(run['id']) or report.get('retryable') is not True
        or report.get('stage') not in ('fetch_main', 'main_changed', 'push_not_applied')):
        raise Blocked('Failure is not an automatically retryable publication transport error')
    dt.datetime.strptime(report['day'], '%Y%m%d')
    return report


def trusted_source(run):
    report = publication_report(run)
    day = report['day']
    files = download(run, 'daily-source-' + day + '-' + str(run['id']) + '-1')
    info = verify_source(files, run['id'], day)
    return files, info


def install(files, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = folder/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def checkpoint(repo, day, folder):
    branch = 'refs/heads/data/daily-' + day
    check = subprocess.run(['git', 'ls-remote', '--exit-code', '--heads', 'origin', branch],
                           cwd=repo, capture_output=True)
    if check.returncode == 2:
        return False
    if check.returncode:
        raise Blocked('Cannot establish checkpoint branch presence')
    git(repo, 'fetch', 'origin', branch)
    ref = git(repo, 'rev-parse', 'FETCH_HEAD')
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    (folder/'bundle.json.gz').write_bytes(subprocess.check_output(
        ['git', 'show', ref + ':daily-results/bundle.json.gz'], cwd=repo))
    restore(repo, ref, folder/'successes.jsonl')
    names = git(repo, 'ls-tree', '-r', '--name-only', ref, '--', 'daily-results').splitlines()
    for name in names:
        if name == 'daily-results/' + RECEIPT or (name.startswith('daily-results/diagnostics/') and name.endswith('.json')):
            destination = folder/Path(name).relative_to('daily-results')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(subprocess.check_output(['git', 'show', ref + ':' + name], cwd=repo))
    return True


def claim(repo, source_id, recovery_id):
    ref = 'refs/heads/recovery/daily-' + str(source_id)
    existing = git(repo, 'ls-remote', '--heads', 'origin', ref)
    if existing:
        return False
    env = dict(os.environ, GIT_AUTHOR_NAME='github-actions[bot]',
               GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',
               GIT_COMMITTER_NAME='github-actions[bot]',
               GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
    tree = subprocess.check_output(['git', 'mktree'], cwd=repo, input=b'').decode().strip()
    commit = subprocess.check_output(['git', 'commit-tree', tree], cwd=repo, env=env,
        input=f'Claim daily recovery source={source_id} recovery={recovery_id}\n'.encode()).decode().strip()
    result = subprocess.run(['git', 'push', 'origin', commit + ':' + ref], cwd=repo, capture_output=True)
    # Resolve ambiguous result before doing any work. A different claimant wins.
    actual = git(repo, 'ls-remote', '--heads', 'origin', ref)
    if actual and actual.split()[0] == commit:
        return True
    if actual:
        return False
    raise Blocked('Recovery claim not confirmed; no retry')


def recover(repo, source_id, recovery_id, attempt, output):
    if int(attempt) != 1:
        raise Blocked('Automatic recovery is limited to one attempt')
    run = api(f'repos/{REPO}/actions/runs/{source_id}')
    source_run(run)
    source_report = publication_report(run)
    day = source_report['day']
    base = current_main(repo)
    skip = guard_ref(repo, base, day)
    if skip:
        return {'status': skip, 'day': day}
    if not claim(repo, source_id, recovery_id):
        return {'status': 'already_claimed', 'day': day}
    with tempfile.TemporaryDirectory(prefix='daily-recovery-') as folder:
        if checkpoint(repo, day, folder):
            saved = Path(folder)/RECEIPT
            if not saved.exists():
                raise Blocked('Checkpoint has no source integrity receipt')
            info = json.loads(saved.read_bytes())
            files = preserved_files(folder)
            verify_source(files, source_id, day)
            # If a second durable copy exists, it must agree; corruption blocks.
            artifacts = pages(f'repos/{REPO}/actions/runs/{source_id}/artifacts', 'artifacts')
            artifact_name = 'daily-source-' + day + '-' + str(source_id) + '-1'
            if any(a['name'] == artifact_name for a in artifacts):
                other, _ = trusted_source(run)
                if any(other[n] != files[n] for n in ('bundle.json.gz', 'successes.jsonl')):
                    raise Blocked('Checkpoint conflicts with preserved artifact')
        else:
            files, info = trusted_source(run)
        install(files, output)
    return publish(repo, Path(output)/'bundle.json.gz', day, source_id,
                   mode='saved_artifact_recovery', report='daily-publish-report.json')


def load_available(repo, day, output):
    if checkpoint(repo, day, output):
        return {'status': 'checkpoint_restored'}
    # No checkpoint: consider only failed, completed, exact-main workflow sources.
    # Complete artifacts of multiple attempts must agree byte-for-byte.
    runs = pages(f'repos/{REPO}/actions/runs?created={dt.datetime.strptime(day,"%Y%m%d").date().isoformat()}', 'workflow_runs')
    sources = []
    for run in runs:
        if run.get('path') != WORKFLOW or run.get('conclusion') != 'failure':
            continue
        source_run(run)
        artifacts = pages(f'repos/{REPO}/actions/runs/{run["id"]}/artifacts', 'artifacts')
        name = 'daily-source-' + day + '-' + str(run['id']) + '-1'
        if not any(a['name'] == name for a in artifacts):
            continue
        files, info = trusted_source(run)
        if info['day'] != day:
            raise Blocked('Artifact target day mismatch')
        sources.append(files)
    if not sources:
        return {'status': 'no_saved_source'}
    first = sources[0]
    if any(any(s[n] != first[n] for n in ('bundle.json.gz', 'successes.jsonl')) for s in sources[1:]):
        raise Blocked('Conflicting complete artifacts')
    install(first, output)
    return {'status': 'artifact_restored'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['receipt', 'load', 'recover', 'durable'])
    p.add_argument('--day')
    p.add_argument('--source-run', default=os.environ.get('GITHUB_RUN_ID'))
    p.add_argument('--output', default='daily-results')
    a = p.parse_args()
    try:
        if a.action == 'receipt':
            result = receipt(a.output, a.day, a.source_run, os.environ['GITHUB_RUN_ATTEMPT'])
        elif a.action == 'durable':
            result = durable_original(os.environ['SEALED'], os.environ['CHECKPOINT'], os.environ['ARTIFACT'])
        elif a.action == 'load':
            result = load_available(Path.cwd(), a.day, a.output)
        else:
            result = recover(Path.cwd(), a.source_run, os.environ['GITHUB_RUN_ID'],
                             os.environ['GITHUB_RUN_ATTEMPT'], a.output)
        Path('daily-recovery-result.json').write_text(json.dumps(result, indent=2)+'\n')
    except Exception as error:
        Path('daily-recovery-result.json').write_text(json.dumps({
            'status': 'blocked', 'reason': type(error).__name__ + ': ' + str(error)}, indent=2)+'\n')
        raise
