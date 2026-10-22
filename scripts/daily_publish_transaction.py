"""Publish preserved bytes against current main; no market API or collection calls."""
import argparse
import datetime as dt
import gzip
import shutil
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class Blocked(RuntimeError):
    pass


def command(args, cwd, env=None):
    return subprocess.check_output(args, cwd=cwd, env=env, stderr=subprocess.PIPE).decode().strip()


def git(repo, *args):
    return command(['git', *args], repo)


def current_main(repo):
    git(repo, 'fetch', 'origin', 'main')
    return git(repo, 'rev-parse', 'FETCH_HEAD')


def publication_guard(day, manifest, ledger):
    date = dt.datetime.strptime(day, '%Y%m%d').date().isoformat()
    if date in ledger:
        return 'already_published'
    if manifest['asof'] >= date:
        return 'same_or_newer_publication'
    return None


def guard_ref(repo, ref, day):
    return publication_guard(day,
        json.loads(git(repo, 'show', ref + ':snapshot/manifest.json')),
        json.loads(git(repo, 'show', ref + ':snapshot/publication-ledger.json')))


def denied(error):
    message = (getattr(error, 'stderr', b'') or b'').decode(errors='replace').lower()
    return any(s in message for s in ('403', '401', 'authentication', 'permission denied',
        'protected branch', 'repository rule', 'gh006', 'gh013', 'not permitted', 'access denied',
        'could not read username', 'invalid username', 'write access', 'resource not accessible', 'insufficient'))


def publish(repo, bundle, day, source_run, mode='collect', report=None):
    repo, bundle = Path(repo).resolve(), Path(bundle).resolve()
    result = {'day': day, 'source_run': str(source_run), 'retryable': False,
              'status': 'blocked', 'stage': 'preflight', 'attempts': 0}
    # Always leave a classified report; only transport failures permit auto recovery.
    try:
        raw_bundle = json.loads(gzip.decompress(bundle.read_bytes()))
        if raw_bundle.get('trade_date') != day or raw_bundle.get('exchange') != 'KRX':
            raise Blocked('Preserved bundle date/exchange mismatch')
        if raw_bundle.get('status') == 'closed':
            result['status'] = 'exchange_closed'
            return result
        if mode != 'verify_saved':
            try:
                from .daily_saved_recovery import verify_source, RECEIPT, preserved_files
            except ImportError:
                from daily_saved_recovery import verify_source, RECEIPT, preserved_files
            receipt_data = json.loads((bundle.parent/RECEIPT).read_bytes())
            files = preserved_files(bundle.parent)
            verify_source(files, source_run, day, receipt_data['run_attempt'])
        for attempt in range(2):
            result['attempts'] = attempt + 1
            result['stage'] = 'fetch_main'
            base = current_main(repo)
            result['stage'] = 'guard_main'
            skip = guard_ref(repo, base, day)
            if skip:
                result.update(status=skip)
                return result
            with tempfile.TemporaryDirectory(prefix='daily-publish-') as folder:
                checkout = Path(folder)/'checkout'
                git(repo, 'worktree', 'add', '--detach', str(checkout), base)
                try:
                    result['stage'] = 'validation'
                    command([sys.executable, 'scripts/prepare_daily_publish.py', '--bundle', str(bundle)], checkout)
                    command([sys.executable, '-m', 'pytest', '-q', 'tests/test_dashboard.py',
                             'tests/test_enrichment.py', 'tests/test_daily.py'], checkout)
                    candidate = checkout/'daily-candidate/manifest.json'
                    if not candidate.exists():
                        raise Blocked('No validated candidate')
                    expected = dt.datetime.strptime(day, '%Y%m%d').date().isoformat()
                    if json.loads(candidate.read_text())['asof'] != expected:
                        raise Blocked('Candidate date differs from preserved source date')
                    result['stage'] = 'stage_publication'
                    if mode == 'verify_saved' and (bundle.parent/'calendar-check.json').exists():
                        (checkout/'daily-results').mkdir(exist_ok=True)
                        shutil.copy2(bundle.parent/'calendar-check.json', checkout/'daily-results/calendar-check.json')
                    env = dict(os.environ, GITHUB_RUN_ID=str(source_run), MODE=mode)
                    command([sys.executable, '-m', 'scripts.stage_daily_publication'], checkout, env)
                    git(checkout, 'config', 'user.name', 'github-actions[bot]')
                    git(checkout, 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
                    git(checkout, 'add', 'snapshot/dashboard.duckdb', 'snapshot/manifest.json',
                        'snapshot/daily-validation.json', 'snapshot/publication-ledger.json',
                        'snapshot/daily_briefing.json', 'snapshot/publication-history', 'snapshot/briefing')
                    if (checkout/'snapshot/calendar-check.json').exists() and mode == 'verify_saved':
                        git(checkout, 'add', 'snapshot/calendar-check.json')
                    git(checkout, 'commit', '-m', 'Publish preserved validated KRX snapshot ' + day)
                    commit = git(checkout, 'rev-parse', 'HEAD')
                    result['stage'] = 'fetch_main'
                    latest = current_main(repo)
                    result['stage'] = 'pre_push'
                    skip = guard_ref(repo, latest, day)
                    if skip:
                        result.update(status=skip)
                        return result
                    if latest != base:
                        result['stage'] = 'main_changed'
                        if attempt == 0:
                            continue  # Rebuild and revalidate on the new main, never rebase data.
                        raise Blocked('Main changed twice; bounded publication stopped')
                    result['stage'] = 'push'
                    try:
                        git(checkout, 'push', 'origin', commit + ':refs/heads/main')
                    except subprocess.CalledProcessError as error:
                        # An error can mean the server accepted the push but its reply was lost.
                        result['stage'] = 'resolve_push_outcome'
                        actual = current_main(repo)
                        accepted = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, actual],
                                                  cwd=repo, capture_output=True).returncode == 0
                        if accepted:
                            result.update(status='published', commit=commit)
                            return result
                        skip = guard_ref(repo, actual, day)
                        if skip:
                            result.update(status=skip)
                            return result
                        if denied(error):
                            result['stage'] = 'access_denied'
                            raise
                        if actual != base and attempt == 0:
                            continue
                        result['stage'] = 'push_not_applied'
                        raise
                    result['stage'] = 'verify_push'
                    actual = current_main(repo)
                    git(repo, 'merge-base', '--is-ancestor', commit, actual)
                    result.update(status='published', commit=commit)
                    return result
                finally:
                    evidence = repo/'daily-publication-evidence'/str(attempt + 1)
                    for relative in ('daily-candidate/manifest.json', 'daily-candidate/validation.json',
                                     'daily-candidate/daily_briefing.json', 'daily-candidate/briefing/index.json',
                                     'briefing-failure.json'):
                        original = checkout/relative
                        if original.exists():
                            destination = evidence/relative
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(original, destination)
                    # Only this function's temporary worktree is removed.
                    git(repo, 'worktree', 'remove', '--force', str(checkout))
    except Exception as error:
        result['reason'] = type(error).__name__ + ': ' + str(error)
        if result['stage'] in ('validation', 'stage_publication'):
            result['validation_error'] = (getattr(error, 'stderr', b'') or b'').decode(errors='replace')
        result['retryable'] = (result['stage'] in ('fetch_main', 'main_changed', 'push_not_applied')
                               and not denied(error))
        # Uncertain push outcomes and validation/access failures require inspection.
        raise
    finally:
        if report:
            Path(report).write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--bundle', required=True)
    p.add_argument('--day', required=True)
    p.add_argument('--source-run', required=True)
    p.add_argument('--mode', default='collect')
    p.add_argument('--report', default='daily-publish-report.json')
    a = p.parse_args()
    publish(Path.cwd(), a.bundle, a.day, a.source_run, a.mode, a.report)
