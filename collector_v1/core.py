"""Durable evidence store, physical request accounting and atomic publication."""
import contextlib
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
import uuid


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with tmp.open('xb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class Stop(Exception):
    pass


class Failure(Exception):
    def __init__(self, code, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self, relative, default=None):
        path = self.root / relative
        return json.loads(path.read_bytes()) if path.exists() else default

    def write(self, relative, value):
        atomic(self.root / relative, encoded(value))

    @contextlib.contextmanager
    def lock(self):
        with (self.root / 'writer.lock').open('a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure('execution_conflict') from None
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def preserve(self, raw, metadata):
        # Retain exact response bytes; never serialize credentials or request headers.
        for name in ('KIS_APP_KEY', 'KIS_APP_SECRET', 'KRX_API_KEY', 'DART_API_KEY', 'ECOS_API_KEY', 'FRED_API_KEY'):
            value = os.getenv(name)
            if value and value.encode() in raw:
                raise Failure('credential_echo')
        h = sha(raw)
        path = self.root / 'raw' / (h + '.gz')
        if path.exists():
            if sha(gzip.decompress(path.read_bytes())) != h:
                raise Failure('source_hash_mismatch')
        else:
            atomic(path, gzip.compress(raw, mtime=0))
        entry = {**metadata, 'source_hash': h}
        self.write('observations/' + sha(encoded(entry)) + '.json', entry)
        return entry

    def raw(self, entry):
        h = entry['source_hash']
        observation = self.root / 'observations' / (sha(encoded(entry)) + '.json')
        if not observation.exists() or observation.read_bytes() != encoded(entry):
            raise Failure('source_metadata_hash_mismatch')
        raw = gzip.decompress((self.root / 'raw' / (h + '.gz')).read_bytes())
        if sha(raw) != h:
            raise Failure('source_hash_mismatch')
        return raw


class Budget:
    def __init__(self, store, calls=20, seconds=240, cumulative=300, interval=1.05):
        if not 1 <= calls <= 20 or not 1 <= seconds <= 600 or not calls <= cumulative <= 300 or interval < 1.05:
            raise Failure('unsafe_budget')
        self.store, self.calls, self.seconds, self.cumulative = store, calls, seconds, cumulative
        self.interval, self.started, self.last, self.used = interval, time.monotonic(), 0., 0
        self.run = uuid.uuid4().hex
        self.ledger = store.read('requests.json', [])

    def check(self):
        if (self.store.root / 'STOP').exists():
            raise Stop('operator_stop')
        if self.used >= self.calls or len(self.ledger) >= self.cumulative or time.monotonic() - self.started >= self.seconds:
            raise Stop('budget_exhausted')

    def before(self, source, endpoint, reason):
        self.check()
        time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
        self.check()
        self.last = time.monotonic(); self.used += 1
        entry = dict(sequence=len(self.ledger) + 1, source=source, endpoint=endpoint,
                     reason=reason, source_run=self.run, started_at=now(), status='attempted')
        self.ledger.append(entry)
        self.store.write('requests.json', self.ledger)  # persisted BEFORE HTTP, even auth/retries
        return entry

    def finish(self, entry, status, **metadata):
        entry.update(status=status, completed_at=now(), **metadata)
        self.store.write('requests.json', self.ledger)


def collect_tasks(store, tasks, fetch, budget):
    """Cache only exact task identities; validation and retrieval are separate."""
    state = store.read('state.json', {'schema': 1, 'tasks': {}})
    started = time.monotonic(); stopped = None
    for task in tasks:
        key = sha(encoded(task))
        old = state['tasks'].get(key, {})
        if old.get('collection_status') == 'received':
            store.raw(old['evidence'])  # hash mismatch is a hard error, never a cache hit
            continue
        if old.get('retry_after', '') > now() or old.get('collection_status') == 'blocked':
            continue
        try:
            budget.check()
            evidence = fetch(task)
            state['tasks'][key] = dict(task=task, collection_status='received', evidence=evidence,
                                       attempts=old.get('attempts', 0) + 1)
        except Stop as exc:
            stopped = str(exc); break
        except Failure as exc:
            attempts = old.get('attempts', 0) + 1
            failure = dict(task=task, collection_status='failed' if exc.retryable else 'blocked',
                           attempts=attempts, error=exc.code, failed_at=now())
            if exc.retryable:
                failure['retry_after'] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=min(3600, 60 * 2 ** min(attempts-1, 5)))).isoformat()
            state['tasks'][key] = failure
            history = store.read('errors.json', [])
            store.write('errors.json', history + [failure])
        finally:
            store.write('state.json', state)
    selected = [state['tasks'].get(sha(encoded(t)), {'task': t, 'collection_status': 'pending'}) for t in tasks]
    completed=[e for e in budget.ledger if e.get('source_run')==budget.run and e.get('completed_at')]
    request_seconds=[(dt.datetime.fromisoformat(e['completed_at'])-dt.datetime.fromisoformat(e['started_at'])).total_seconds() for e in completed]
    report = dict(source_run=budget.run, completed_at=now(), physical_requests=budget.used,
                  average_request_seconds=round(sum(request_seconds)/len(request_seconds),4) if request_seconds else None,
                  total_reserved_requests=len(budget.ledger), elapsed_seconds=round(time.monotonic()-started, 4),
                  collection_status='complete' if all(t['collection_status']=='received' for t in selected) else 'partial',
                  received=sum(t['collection_status']=='received' for t in selected), target=len(tasks), stopped=stopped,
                  tasks=selected)
    store.write('last-run.json', report)
    return report


def publish(store, generation, rows, manifest, connect=None):
    """Immutable generation; a single pointer selects DB + JSON + manifest together."""
    import duckdb
    connect = connect or duckdb.connect
    if not rows or manifest['held'] or manifest['collection_status'] != 'complete':
        raise Failure('incomplete_generation')
    target = store.root / 'generations' / generation
    content = encoded(rows)
    inventory = dict(manifest, generation=generation, analysis_sha256=sha(content), publication_status='published')
    if target.exists():
        if {k:v for k,v in json.loads((target/'manifest.json').read_bytes()).items() if k != 'db_sha256'} != inventory:
            raise Failure('generation_conflict')
    else:
        stage = store.root / 'staging' / uuid.uuid4().hex
        stage.mkdir(parents=True)
        # Preserve failed stages for diagnosis/retry; never touch the previous pointer.
        with connect(str(stage/'data.duckdb')) as db:
            db.execute('CREATE TABLE observations (kind VARCHAR, identity VARCHAR PRIMARY KEY, payload JSON)')
            db.execute('BEGIN')
            for row in rows:
                db.execute('INSERT INTO observations VALUES (?,?,?)', [row['kind'], row['identity'], encoded(row).decode()])
            db.execute('COMMIT')
        atomic(stage/'analysis.json', content)
        inventory['db_sha256'] = sha((stage/'data.duckdb').read_bytes())
        # DB hashes can differ across equivalent builds. Idempotence uses input generation + content.
        atomic(stage/'manifest.json', encoded(inventory))
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(stage, target)
    existing = json.loads((target/'manifest.json').read_bytes())
    if existing.get('analysis_sha256') != sha(content) or sha((target/'analysis.json').read_bytes()) != sha(content) or sha((target/'data.duckdb').read_bytes()) != existing.get('db_sha256'):
        raise Failure('publication_hash_mismatch')
    store.write('published.json', {'generation': generation, 'manifest_hash': sha((target/'manifest.json').read_bytes())})
    return existing
