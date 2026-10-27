"""Read pinned local Git objects and local saved artifacts only. No HTTP clients."""
import gzip
import json
from pathlib import Path
import re
import subprocess
import tempfile
import duckdb
from .common import ArchiveError, digest, encoded, latest_time, safe_name, timestamp

REPOSITORY = '0baselabkim-debug/qweasd'
CHANNELS = ('operating_publication', 'operating_analysis', 'kis_full_trial')


class GitSource:
    def __init__(self, repo, commit, branch):
        self.repo = Path(repo).resolve()
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', branch) or branch.startswith('-'):
            raise ArchiveError('invalid_source_branch')
        self.branch = branch
        if not re.fullmatch('[0-9a-fA-F]{7,40}',commit):raise ArchiveError('pinned_hex_commit_required')
        self.commit = self.git('rev-parse', '--verify', commit+'^{commit}').decode().strip()
        if not re.fullmatch('[0-9a-f]{40}', self.commit):
            raise ArchiveError('source_commit_required')
        for ref in ('refs/heads/'+branch,'refs/remotes/origin/'+branch):
            check=subprocess.run(['git','-C',str(self.repo),'merge-base','--is-ancestor',self.commit,ref],capture_output=True)
            if check.returncode==0:break
        else:raise ArchiveError('commit_not_in_declared_source_branch')
        self.names = set(self.git('ls-tree', '-r', '--name-only', self.commit, 'snapshot', 'reports/kis-manual-20261026').decode().splitlines())

    def git(self, *args):
        r = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True)
        if r.returncode:
            raise ArchiveError('local_git_source_unavailable')
        return r.stdout

    def read(self, path):
        safe_name(path)
        return self.git('show', self.commit+':'+path)

    def modified(self, path):
        lines = self.git('log', '-1', '--format=%H%n%cI', self.commit, '--', path).decode().splitlines()
        if len(lines) != 2:
            raise ArchiveError('file_version_time_unknown')
        timestamp(lines[1])
        return dict(commit=lines[0], committed_at=lines[1])

    def provenance(self):
        return dict(repository=REPOSITORY, branch=self.branch, commit=self.commit,
                    repository_path=str(self.repo), acquisition='LOCAL_PINNED_GIT_OBJECTS',
                    availability_evidence='recorded_publication_and_git_committer_time_not_client_receipt')


def _read_db(raw, callback):
    with tempfile.TemporaryDirectory(prefix='asset-db-read-') as folder:
        p = Path(folder)/'source.duckdb'; p.write_bytes(raw); p.chmod(0o444)
        with duckdb.connect(str(p), read_only=True) as db:
            return callback(db)


def _rows(db, sql):
    cursor = db.execute(sql)
    names = [d[0] for d in cursor.description]
    result = []
    for values in cursor.fetchall():
        result.append({k: v.isoformat() if hasattr(v, 'isoformat') else v for k, v in zip(names, values)})
    return result


def observation(kind, entity, day, payload, *, observed_at=None, internal='NOT_ASSESSED',
                official='UNVERIFIED', finality='UNVERIFIED', eligible=None):
    if observed_at is not None:
        observed_at = timestamp(observed_at).isoformat()
    return dict(kind=kind, entity=entity, trade_date=str(day), observed_at=observed_at,
                observation_time_status='KNOWN' if observed_at else 'UNKNOWN',
                internal_validation=internal, official_validation=official,
                provider_finality=finality, historical_backtest_eligible=eligible, payload=payload)


def publication(source):
    names = ['snapshot/dashboard.duckdb', 'snapshot/manifest.json', 'snapshot/daily-validation.json',
             'snapshot/publication-ledger.json']
    names += sorted(n for n in source.names if n == 'snapshot/daily_briefing.json' or n.startswith('snapshot/briefing/'))
    files = {n: source.read(n) for n in names}
    m = json.loads(files['snapshot/manifest.json']); v = json.loads(files['snapshot/daily-validation.json'])
    ledger = json.loads(files['snapshot/publication-ledger.json']); day = m['asof']
    entry = ledger[day]; h = digest(files['snapshot/dashboard.duckdb'])
    if not h == m['sha256'] == v['sha256'] == entry['sha256'] == m['daily_validation']['sha256']:
        raise ArchiveError('publication_hash_or_generation_mismatch')
    if not m['daily_validation'].get('preservation_verified'):
        raise ArchiveError('source_preservation_validation_missing')
    dt = __import__('datetime');dt.date.fromisoformat(day)
    versions={name:source.modified(name) for name in files}
    version = versions['snapshot/dashboard.duckdb']
    available = latest_time(entry.get('published_at_utc'), *(v['committed_at'] for v in versions.values()))
    if 'snapshot/briefing/index.json' in files:
        idx=json.loads(files['snapshot/briefing/index.json'])
        if (idx['asof'],idx['source_snapshot_sha256'])!=(day,h):raise ArchiveError('briefing_generation_mismatch')
        for part in idx['sectors']:
            name='snapshot/briefing/'+safe_name(part['path'])
            if digest(files[name])!=part['sha256'] or len(files[name])!=part['bytes']:raise ArchiveError('briefing_part_hash_mismatch')
    # A recovery may change the hash while retaining the first publication time.
    # Use the actual version's commit, never only that retained timestamp.
    def extract(db):
        for table in ('flows', 'prices', 'benchmarks', 'calendar', 'exclusions'):
            key = 'market' if table in ('benchmarks', 'calendar') else 'symbol'
            if db.execute(f'SELECT count(*) FROM (SELECT {key},trade_date FROM {table} GROUP BY ALL HAVING count(*)>1)').fetchone()[0]:
                raise ArchiveError('duplicate_source_identity:'+table)
            if db.execute(f'SELECT count(*) FROM {table} WHERE trade_date>CAST(? AS DATE)', [day]).fetchone()[0]:
                raise ArchiveError('future_source_trade_date:'+table)
        if db.execute("SELECT count(*) FROM flows WHERE symbol IS NULL OR trade_date IS NULL OR net_amount IS NULL OR net_quantity IS NULL OR exchange!='KRX'").fetchone()[0]:raise ArchiveError('missing_required_foreign_flow')
        if db.execute('SELECT count(*) FROM prices WHERE symbol IS NULL OR trade_date IS NULL OR (close IS NULL AND adjusted_close IS NULL)').fetchone()[0]:raise ArchiveError('missing_required_price_identity_or_value')
        instruments = _rows(db, 'SELECT * FROM instruments ORDER BY symbol')
        if len({r['symbol'] for r in instruments})!=len(instruments):raise ArchiveError('duplicate_source_instrument')
        exclusions = _rows(db, "SELECT * FROM exclusions WHERE trade_date=DATE '"+day+"' ORDER BY symbol")
        memberships = _rows(db, "SELECT * FROM memberships WHERE valid_from<=DATE '"+day+"' AND (valid_to IS NULL OR valid_to>=DATE '"+day+"') ORDER BY symbol,valid_from")
        pop = dict(profile='OPERATING_DATE_MEMBERSHIP_V1', instruments=instruments, memberships=memberships, exclusions=exclusions)
        out = []
        for r in _rows(db, 'SELECT * FROM flows ORDER BY symbol,trade_date'):
            out.append(observation('foreign_flow', r['symbol'], r['trade_date'], r,
                       internal='SOURCE_PUBLICATION_ONLY', finality=r.get('finality') or 'UNVERIFIED'))
        for r in _rows(db, 'SELECT * FROM prices ORDER BY symbol,trade_date'):
            if r['close'] is not None:
                out.append(observation('price_original', r['symbol'], r['trade_date'],
                    {k:v for k,v in r.items() if k!='adjusted_close'}, internal='SOURCE_PUBLICATION_ONLY'))
            if r['adjusted_close'] is not None:
                out.append(observation('price_adjusted', r['symbol'], r['trade_date'],
                    dict(symbol=r['symbol'], trade_date=r['trade_date'], adjusted_close=r['adjusted_close'],
                         source=r['source'], exchange=r['exchange']), internal='SOURCE_PUBLICATION_ONLY'))
        for r in _rows(db, 'SELECT * FROM benchmarks ORDER BY market,trade_date'):
            out.append(observation('index_close', r['market'], r['trade_date'], r, internal='SOURCE_PUBLICATION_ONLY'))
        return pop, out
    population, rows = _read_db(files['snapshot/dashboard.duckdb'], extract)
    return dict(channel='operating_publication', asof=day, files=files, population=population, observations=rows,
                source=source.provenance(), source_run=str(m.get('daily_run') or entry.get('run_id')),publication_run=entry.get('run_id'),collection_commit=None,
                observed_at=None, published_at=entry.get('published_at_utc'), available_at=available,
                file_version=version, file_versions=versions, source_internal_validation='PUBLICATION_PASSED',
                flow_validation='SOURCE_PUBLICATION_ONLY', price_validation='SOURCE_PUBLICATION_ONLY',
                provider_finality=m.get('finality', 'UNVERIFIED'), historical_backtest_eligible=None,
                archive_scope='PUBLISHED_SNAPSHOT_AND_BRIEFING; observation timestamps not inferred',
                archive_status='COMPLETE_BYTES', source_reported_target=m.get('current_day_flow_rows'), warnings=['Per-row flow/price observation times absent in operating DB; snapshot restoration is available, per-observation replay is not asserted.'])


def analysis(source):
    prefix = 'snapshot/decision-support/'
    files = {n:source.read(n) for n in sorted(source.names) if n.startswith(prefix)}
    if not files:
        raise ArchiveError('analysis_source_missing')
    index = json.loads(files[prefix+'index.json']); manifest = json.loads(source.read('snapshot/manifest.json'))
    if (index['asof'], index['source_snapshot_sha256']) != (manifest['asof'], manifest['sha256']):
        raise ArchiveError('analysis_parent_generation_mismatch')
    inventory = json.loads(files[prefix+'source-manifest.json'])
    for group in inventory.values():
        for v in group.values():
            if digest(files[prefix+safe_name(v['file'])]) != v['sha256']:
                raise ArchiveError('analysis_evidence_hash_mismatch')
    for shard in index['sectors']:
        raw = files[prefix+safe_name(shard['file'])]
        if digest(raw) != shard['sha256']:
            raise ArchiveError('analysis_shard_hash_mismatch')
        doc=json.loads(raw)
        if (doc['asof'],doc['dataset_id'],doc['source_snapshot_sha256']) != (index['asof'],index['dataset_id'],index['source_snapshot_sha256']):
            raise ArchiveError('analysis_shard_generation_mismatch')
        if any(r.get('validity',{}).get('historical_backtest_eligible') is not False for r in doc['stocks']):
            raise ArchiveError('analysis_backtest_policy_unexpected')
    compressed=files[prefix+safe_name(index['database']['file'])]; raw=gzip.decompress(compressed)
    if digest(compressed)!=index['database']['sha256'] or digest(raw)!=index['database']['uncompressed_sha256']:
        raise ArchiveError('analysis_database_hash_mismatch')
    validation=json.loads(files[prefix+'validation.json'])
    if any(validation[k]!=index[k] for k in ('asof','dataset_id','source_snapshot_sha256')):
        raise ArchiveError('analysis_validation_generation_mismatch')
    versions={name:source.modified(name) for name in files}
    version=versions[prefix+'index.json'];available=latest_time(index.get('generated_at'),*(v['committed_at'] for v in versions.values()))
    def extract(db):
        rows=[]
        for table,kind in [('institution_flows_daily','institution_flow'),('stock_ohlcv_daily','analysis_price')]:
            for r in _rows(db,'SELECT * FROM '+table):
                rows.append(observation(kind,r['symbol'],r['trade_date'],r,observed_at=r.get('observed_at'),
                    internal=r.get('validation_status','SOURCE_ANALYSIS_ONLY'),
                    official='SIX_FIELD_MATCH' if kind=='analysis_price' and r.get('validation_status')=='operational_crosscheck_pass' and r.get('official_source_ids') else 'UNVERIFIED',
                    finality=r.get('provider_finality','UNVERIFIED'),eligible=False))
        return rows
    rows=_read_db(raw,extract)
    population=dict(profile='ANALYSIS_DATASET_V1',source_dataset_id=index['dataset_id'],population=index['source_population'])
    return dict(channel='operating_analysis',asof=index['asof'],files=files,population=population,observations=rows,
        source=source.provenance(),source_run=str(manifest.get('daily_run')) if manifest.get('daily_run') else None,publication_run=None,collection_commit=None,observed_at=index.get('source_collected_at'),
        published_at=None,available_at=available,file_version=version,file_versions=versions,source_internal_validation=index['validation_status'],
        flow_validation='SOURCE_ANALYSIS_ONLY',price_validation='ROW_SPECIFIC',provider_finality=index['provider_finality'],
        historical_backtest_eligible=False,archive_scope='ANALYSIS_SIDECAR_WITH_REGISTERED_EVIDENCE',
        archive_status='COMPLETE_BYTES',source_reported_target=index['coverage']['stocks'],warnings=['historical_backtest_eligible=false retained; source observation and later validation availability are distinct.'])


def kis_receipt(source, receipt_path):
    raw=source.read(receipt_path);r=json.loads(raw);report=r['report']
    if (str(r['run_id']),report['asof'],report['mode'])!=('40326538555','2026-10-26','full'):
        raise ArchiveError('unsupported_full_trial_receipt')
    matches=[a for a in r['artifacts'] if a['id']==12658373693]
    if len(matches)!=1 or matches[0]['digest']!='sha256:eef7625731b266aca24e39865a976925901bf52be746d0961c3a4f00e574c4e2':
        raise ArchiveError('full_trial_artifact_identity_mismatch')
    version=source.modified(receipt_path)
    result=dict(channel='kis_full_trial',asof=report['asof'],files={'receipt.json':raw},
        population=dict(profile='KIS_FULL_TRIAL_RECEIPT_ONLY',target_symbols=report['target_symbols'],
                        reported_population_sha256=report['population_sha256']),observations=[],source=source.provenance(),
        source_run=str(r['run_id']),collection_commit=r['source_commit'],receipt_commit=version['commit'],observed_at=report['actual_started_at'],published_at=r['published_at'],
        available_at=latest_time(r['published_at'],version['committed_at']),file_version=version,
        source_internal_validation=report['status'],flow_validation='NOT_RESTORED',price_validation='NOT_RESTORED',
        provider_finality=report['provider_finality'],historical_backtest_eligible=None,
        archive_scope='RECEIPT_ONLY_NOT_2349_RAW_ROWS',archive_status='SOURCE_BYTES_UNAVAILABLE',
        source_reported_target=report['target'],warnings=['Original ZIP not restored; reported PASS is not an independent raw-data approval.'])
    return result,r,matches[0]


def kis_artifact(source, receipt_path, archive):
    result,receipt,meta=kis_receipt(source,receipt_path)
    archive=Path(archive); zipraw=archive.read_bytes()
    if digest(zipraw)!=meta['digest'].removeprefix('sha256:'):
        raise ArchiveError('artifact_zip_digest_mismatch')
    # Reuse established offline hash/task/unit/DB verifiers, never their executors.
    from collector_v1.experiment import runtime
    from collector_v1 import kis_manual, kis_manual_actions
    from collector_v1.core import encoded as collector_encoded
    with tempfile.TemporaryDirectory(prefix='asset-artifact-read-') as tmp:
        folder=Path(tmp)/'state';runtime.safe_extract(archive,folder,meta['digest'][7:]);manifest=runtime.verify(folder)
        if (str(manifest['run_id']),manifest['asof'])!=('40326538555','2026-10-26') or digest(collector_encoded(manifest))!=receipt['state_manifest_sha256']:
            raise ArchiveError('artifact_manifest_identity_mismatch')
        report=json.loads((folder/'modes/full/report.json').read_bytes())
        if report['status']!='PASS' or report['physical_http']!=4701 or report['target']!=2349:
            raise ArchiveError('artifact_full_report_mismatch')
        kis_manual_actions.verify_mode(folder/'modes/full',report)
        universe=json.loads((folder/'universe.json').read_bytes());selected=kis_manual.eligible(universe)
        if digest(collector_encoded(universe))!=report['population_sha256'] or len(selected)!=2349:
            raise ArchiveError('artifact_population_mismatch')
        from collector_v1.experiment.storage import events
        ledger=events(folder/'modes/full/http.jsonl')
        if len([e for e in ledger if e['event']=='finished' and e['status']=='received'])!=4701:
            raise ArchiveError('artifact_http_ledger_mismatch')
        rows=[]
        for r in report['rows']:
            rows.append(observation('foreign_flow',r['symbol'],report['asof'],r,observed_at=r['flow_observed_at'],internal=r['kis_validation_status'],official='UNVERIFIED',finality=r['provider_finality']))
            rows.append(observation('price_original',r['symbol'],report['asof'],r,observed_at=r['price_observed_at'],internal=r['kis_validation_status'],official=r['official_price_status'],finality=r['provider_finality']))
        for r in report['indices']:
            rows.append(observation('index_close',r['market'],r['trade_date'],r,observed_at=r['observed_at'],internal='PASS'))
    # Exact ZIP is the original; its verified internal files remain recoverable.
    result['files']['artifact.zip']=zipraw
    result.update(population=dict(profile='KIS_ELIGIBLE_MASTER_V1',universe=universe),observations=rows,
        archive_status='COMPLETE_BYTES',archive_scope='PINNED_ORIGINAL_ZIP_AND_VERIFIED_FULL_ROWS',flow_validation='KIS_INTERNAL_PASS',price_validation='KIS_INTERNAL_PASS_PENDING_KRX',warnings=['PENDING_KRX and UNVERIFIED retained; no official foreign-flow finality assertion.'])
    return result
