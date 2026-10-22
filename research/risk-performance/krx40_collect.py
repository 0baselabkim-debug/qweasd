"""Explicit, manual KRX batches; no scheduler, KIS calls or signal registration.

The authorization belongs to this study, not to a date, process or batch. Every
potential request is charged durably BEFORE transport. Unknown interrupted
attempts block automatic continuation; all evidence is append-only.
"""
import datetime as dt
import fcntl
import gzip
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import digest, instant
from krx_validation import OfficialIndex, canonical, hash_bytes, write_once
from screener.liquidity_quality import digest as rows_digest

BRANCH = 'research/full-market-transition-20261019'
AUTHORIZATION = 'krx-official-40d-20261022-80-physical'
MAXIMUM = 80
BATCH_MAXIMUM = 20
INTERVAL = 1.05
ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
OUT = ROOT/'krx-40d-study'


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def require_window(now):
    hour = instant(now).astimezone(ZoneInfo('Asia/Seoul')).hour
    if not (hour >= 23 or hour < 9):
        raise ValueError('outside_approved_2300_0900_KST_window')


def endpoint(market):
    part = {'KOSPI': 'stk', 'KOSDAQ': 'ksq'}[market]
    return f'https://data-dbg.krx.co.kr/svc/apis/sto/{part}_bydd_trd'


class Journal:
    def __init__(self, root):
        self.root = Path(root)
        write_once(self.root/'authorization.json', canonical({
            'authorization': AUTHORIZATION, 'maximum_physical_requests': MAXIMUM,
            'maximum_batch_physical_requests': BATCH_MAXIMUM,
            'allowed_api': [endpoint(m) for m in ('KOSPI', 'KOSDAQ')]}))

    def events(self):
        result = []; previous = None
        for n, p in enumerate(sorted((self.root/'events').glob('*.json')), 1):
            x = json.loads(p.read_bytes()); body = x['body']
            if (p.name != f'{n:04d}.json' or x['sha256'] != digest(body)
                    or body['sequence'] != n or body['previous'] != previous):
                raise ValueError('request_journal_chain_corrupt')
            result.append(body); previous = x['sha256']
        return result

    def append(self, payload):
        prior = self.events()
        body = {**payload, 'sequence': len(prior)+1,
                'previous': digest(prior[-1]) if prior else None}
        write_once(self.root/'events'/f'{len(prior)+1:04d}.json',
                   canonical({'body': body, 'sha256': digest(body)}))
        return body

    def requests(self):
        starts = {}; ends = {}
        for e in self.events():
            if e['kind'] == 'attempt':
                if e['attempt'] in starts:
                    raise ValueError('duplicate_attempt')
                starts[e['attempt']] = e
            elif e['kind'] == 'result':
                if e['attempt'] not in starts or e['attempt'] in ends:
                    raise ValueError('unpaired_result')
                ends[e['attempt']] = e
            else:
                raise ValueError('unknown_journal_event')
        return [{**s, **ends.get(k, {}), 'attempt': k,
                 'uncertain': k not in ends} for k, s in starts.items()]

    def remaining(self):
        return MAXIMUM - len(self.requests())

    def checkpoint_response(self, entry):
        p = self.root/entry['file']; raw = gzip.decompress(p.read_bytes())
        if hash_bytes(p.read_bytes()) != entry['file_sha256'] or hash_bytes(raw) != entry['response_sha256']:
            raise ValueError('response_checkpoint_corrupt')
        return raw

    def summary(self):
        rows = self.requests()
        return {'authorization': AUTHORIZATION, 'maximum': MAXIMUM,
                'requests': rows, 'charged_requests': len(rows),
                'confirmed_physical_requests': sum(not r['uncertain'] for r in rows),
                'uncertain_attempts': sum(r['uncertain'] for r in rows),
                'remaining_budget': self.remaining(),
                'authentication_requests': 0, 'signals_registered': 0}


def response_envelope(raw, http, market, day, observed_at):
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError('provider_response_not_object')
    rows = obj.get('OutBlock_1', [])
    env = {'http': http, 'url': endpoint(market)+'?basDd='+day.replace('-', ''),
           'observed_at': observed_at, 'rows': rows, 'rows_sha256': rows_digest(rows),
           'raw_response_sha256': hash_bytes(raw)}
    # Partial invalid/suspended rows stay in the raw response and are held by
    # official_context. Never re-request an entire market to hide these rows.
    check = OfficialIndex(); check.add(env, market, day, hash_bytes(raw))
    return env


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def transport(url, params, key):
    request = urllib.request.Request(url+'?'+urllib.parse.urlencode(params), headers={'AUTH_KEY': key})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            return response.status, response.headers.get('Content-Type'), response.read(16_000_001)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Content-Type'), e.read(16_000_001)


def collect_batch(plan, root, key, preflight, durable, *, send=transport,
                  clock=utcnow, monotonic=time.monotonic, sleep=time.sleep,
                  retry_failed=False):
    """One batch only. Caller must have independently validated the frozen plan.

    Test clocks/transports are injected only by isolated unit tests. CLI always
    supplies the real clock/transport. No automatic sleeping until nighttime.
    """
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    with (root/'batch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_window(clock()); preflight()
        if not key:
            raise ValueError('existing_KRX_credential_not_available')
        journal = Journal(root); records = journal.requests()
        if any(r['uncertain'] for r in records):
            raise ValueError('uncertain_prior_request_requires_evidence_review')
        if any(r.get('http') in (401, 403, 429) for r in records):
            raise ValueError('prior_access_or_rate_limit_requires_review_no_automatic_retry')
        successes = {(r['market'], r['date']) for r in records if r.get('status') == 'validated'}
        failed = {(r['market'], r['date']) for r in records if r.get('status') != 'validated'}
        for r in records:
            if r.get('status') == 'validated':
                raw = journal.checkpoint_response(r)
                response_envelope(raw, r['http'], r['market'], r['date'], r['completed_at'])
        jobs = [(j['market'], j['date']) for j in plan['missing_market_dates']]
        if len(jobs) != len(set(jobs)):
            raise ValueError('duplicate_market_date_plan')
        jobs = [j for j in jobs if j not in successes and (retry_failed or j not in failed)]
        if len(jobs) > journal.remaining():
            raise ValueError('remaining_plan_exceeds_remaining_authorization')
        batch_id = digest({'started_at': clock().isoformat(), 'prior_attempts': len(records), 'plan': digest(plan)})
        sent = 0; last = monotonic()  # interval also held across process restarts
        for market, day in jobs[:BATCH_MAXIMUM]:
            if day not in plan['sessions'][market][-40:] or day > plan['asof']:
                raise ValueError('request_outside_official_latest_40_calendar')
            sleep(max(0.0, INTERVAL-(monotonic()-last)))
            require_window(clock()); preflight()
            if len(journal.requests()) >= MAXIMUM:
                raise ValueError('physical_request_limit')
            n = len(journal.requests())+1
            entry = {'kind': 'attempt', 'attempt': n, 'batch_id': batch_id,
                     'plan_sha256': digest(plan), 'market': market, 'date': day,
                     'url': endpoint(market), 'params': {'basDd': day.replace('-', '')},
                     'reserved_at': clock().isoformat(), 'retry': (market, day) in failed,
                     'authentication': False}
            journal.append(entry); durable()  # abort transport if remote persistence fails
            # Operations may start during persistence. Never consume a market
            # call after discovering that; unresolved reservation blocks restart.
            require_window(clock()); preflight()
            start = clock(); last = monotonic(); raw = None
            result = {'kind': 'result', 'attempt': n, 'started_at': start.isoformat(),
                      'status': 'transport_error'}
            try:
                http, content_type, raw = send(entry['url'], entry['params'], key)
                result.update(http=http, content_type=content_type)
                if len(raw) > 16_000_000:
                    raise ValueError('response_size_limit')
                if key.encode() in raw:
                    raise ValueError('credential_echo_response_not_saved')
                file = 'responses/'+hash_bytes(raw)+'.bin.gz'
                packed = gzip.compress(raw, mtime=0)
                write_once(root/file, packed)
                result.update(file=file, file_sha256=hash_bytes(packed), response_sha256=hash_bytes(raw))
                response_envelope(raw, http, market, day, clock().isoformat())
                result['status'] = 'validated'
            except Exception as e:
                # Never log exception text from network libraries or credentials.
                result['error_type'] = type(e).__name__
                allowed_reasons = {
                    'response_size_limit', 'credential_echo_response_not_saved',
                    'provider_response_not_object', 'official_date_market_or_http_mismatch',
                    'official_rows_hash_mismatch', 'official_duplicate_or_invalid_symbol',
                    'official_row_date_market_mismatch', 'timezone_required'}
                if isinstance(e, ValueError) and str(e) in allowed_reasons:
                    result['validation_error'] = str(e)
                if result.get('http') is not None:
                    result['status'] = 'response_rejected'
            result.update(completed_at=clock().isoformat(), elapsed_seconds=round(monotonic()-last, 6))
            journal.append(result); durable(); sent += 1
            if result['status'] != 'validated':
                break  # every error ends the batch, no implicit retry
        return {'batch_id': batch_id, 'batch_requests': sent, **journal.summary()}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO).decode().strip()


def validate_shared_runner(run, workflow):
    """Require the existing shared Actions lock, not a local process-only lock."""
    # Only the existing literal top-level block is supported. Expressions,
    # aliases, duplicate keys and alternate/ambiguous YAML layouts fail closed.
    blocks = re.findall(r'^concurrency:\s*\n((?:[ ]+[^\n]*\n)+)', workflow, re.M)
    concurrency = {}
    if len(blocks) == 1:
        for line in blocks[0].splitlines():
            match = re.fullmatch(r'  (group|cancel-in-progress|queue): ([a-z0-9-]+)', line)
            if not match or match[1] in concurrency:
                raise ValueError('existing_shared_manual_research_concurrency_required')
            concurrency[match[1]] = match[2]
    if (run.get('head_branch') != BRANCH or run.get('status') != 'in_progress'
            or run.get('event') != 'workflow_dispatch'
            or concurrency.get('group') != 'kis-validation-auth'
            or concurrency.get('cancel-in-progress') != 'false'):
        raise ValueError('existing_shared_manual_research_concurrency_required')


def github_preflight(plan):
    """Conservative: any other unfinished repository workflow blocks research."""
    require_window(utcnow())
    if git('branch', '--show-current') != BRANCH:
        raise ValueError('research_branch_required')
    remote = git('ls-remote', 'origin', 'refs/heads/main').split()[0]
    if remote != plan['source_commit']:
        raise ValueError('main_changed_refresh_read_only_plan_before_collection')
    this_run = os.environ.get('GITHUB_RUN_ID')
    if not this_run or not this_run.isdigit():
        raise ValueError('shared_Actions_runner_required_no_local_market_calls')
    run = json.loads(subprocess.check_output([
        'gh', 'api', 'repos/0baselabkim-debug/qweasd/actions/runs/'+this_run], cwd=REPO))
    workflow_path = run.get('path', '').split('@')[0]
    if not workflow_path.startswith('.github/workflows/') or not workflow_path.endswith('.yml'):
        raise ValueError('workflow_identity_missing')
    definition = git('show', run['head_sha']+':'+workflow_path)
    validate_shared_runner(run, definition)
    for status in ('queued', 'in_progress', 'waiting', 'pending', 'requested'):
        response = subprocess.check_output([
            'gh', 'api', '--paginate', '--slurp',
            f'repos/0baselabkim-debug/qweasd/actions/runs?status={status}&per_page=100'], cwd=REPO)
        for page in json.loads(response):
            if any(str(r['id']) != this_run for r in page['workflow_runs']):
                raise ValueError('other_operation_active_or_waiting')
    remote_branch = git('ls-remote', 'origin', 'refs/heads/'+BRANCH).split()[0]
    if remote_branch != git('rev-parse', 'HEAD'):
        raise ValueError('research_remote_has_changed_no_concurrent_publication')


def persist_research():
    """Only this new research directory; failed pushes abort BEFORE more calls."""
    if git('branch', '--show-current') != BRANCH:
        raise ValueError('research_branch_required')
    rel = str(OUT.relative_to(REPO))
    staged = git('diff', '--cached', '--name-only').splitlines()
    if any(not p.startswith(rel+'/') for p in staged):
        raise ValueError('unrelated_staged_changes')
    subprocess.run(['git', 'add', '--', rel], cwd=REPO, check=True)
    if git('diff', '--cached', '--name-only'):
        subprocess.run(['git', 'commit', '-q', '-m', 'Preserve bounded official 40-day research checkpoint'], cwd=REPO, check=True)
        subprocess.run(['git', 'push', 'origin', 'HEAD:refs/heads/'+BRANCH], cwd=REPO, check=True)
