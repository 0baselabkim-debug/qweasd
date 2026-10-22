import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
import zipfile

import pytest
from scripts import daily_saved_recovery as r
from scripts import daily_publish_transaction as p
from scripts.daily_checkpoint import Journal

DAY = '20261023'


def source(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    bundle = {'trade_date': DAY, 'exchange': 'KRX', 'status': 'complete',
              'records': {'000001': {'price': '1.00000000001'}}, 'excluded': {}, 'failures': {},
              'universe': [{'symbol': '000001'}]}
    (tmp_path/'bundle.json.gz').write_bytes(gzip.compress(json.dumps(bundle).encode()))
    Journal(tmp_path/'successes.jsonl', DAY).append('000001', record=bundle['records']['000001'])
    r.receipt(tmp_path, DAY, '100', 1)
    return {f.name: f.read_bytes() for f in tmp_path.iterdir()}


def zip_source(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    data = buf.getvalue()
    return data, {'digest': 'sha256:'+hashlib.sha256(data).hexdigest(), 'size_in_bytes': len(data), 'expired': False}


def run_info():
    return {'id': 100, 'repository': {'full_name': r.REPO}, 'head_repository': {'full_name': r.REPO},
            'path': r.WORKFLOW, 'head_branch': 'main', 'event': 'schedule', 'status': 'completed',
            'conclusion': 'failure', 'run_attempt': 1}


def test_verified_zip_and_source_roundtrip(tmp_path):
    files = source(tmp_path)
    data, meta = zip_source(files)
    restored = r.archive_files(data, meta)
    assert r.verify_source(restored, 100, DAY)['day'] == DAY
    assert restored == files


@pytest.mark.parametrize('damage', ['zip_hash', 'wrong_day', 'partial', 'hash', 'journal', 'conflict', 'missing', 'provenance'])
def test_bad_sources_block(tmp_path, damage):
    files = source(tmp_path)
    if damage == 'zip_hash':
        data, meta = zip_source(files); meta['digest'] = 'sha256:'+'0'*64
        with pytest.raises(r.Blocked): r.archive_files(data, meta)
        return
    if damage == 'wrong_day':
        with pytest.raises(r.Blocked): r.verify_source(files, 100, '20261024')
        return
    if damage == 'hash': files['successes.jsonl'] += b'corruption'
    elif damage == 'missing': del files['successes.jsonl']
    elif damage == 'provenance':
        info = json.loads(files[r.RECEIPT]); info['repository'] = 'other/repo'; files[r.RECEIPT] = json.dumps(info).encode()
    else:
        if damage == 'partial':
            bundle = json.loads(gzip.decompress(files['bundle.json.gz'])); bundle['status'] = 'partial'
            (tmp_path/'bundle.json.gz').write_bytes(gzip.compress(json.dumps(bundle).encode()))
        if damage == 'journal': (tmp_path/'successes.jsonl').write_bytes(files['successes.jsonl'] + b'truncated')
        if damage == 'conflict': Journal(tmp_path/'successes.jsonl', DAY).append('000001', record={'price': 'wrong'})
        r.receipt(tmp_path, DAY, 100, 1)
        files = {f.name: f.read_bytes() for f in tmp_path.iterdir()}
    with pytest.raises((r.Blocked, ValueError, KeyError)): r.verify_source(files, 100, DAY)


@pytest.mark.parametrize('field,value', [('event','workflow_run'), ('path','other.yml'), ('head_branch','feature'), ('run_attempt',2), ('conclusion','success')])
def test_ineligible_runs_never_recovered(field, value):
    run = run_info(); run[field] = value
    with pytest.raises(r.Blocked): r.source_run(run)


def test_unsafe_zip_rejected():
    data, meta = zip_source({'../outside': b'x'})
    with pytest.raises(r.Blocked): r.archive_files(data, meta)


def repository(tmp_path):
    remote, repo = tmp_path/'remote.git', tmp_path/'repo'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    subprocess.run(['git', 'clone', str(remote), str(repo)], check=True, capture_output=True)
    p.git(repo, 'config', 'user.name', 'test'); p.git(repo, 'config', 'user.email', 'test@example.invalid')
    p.git(repo, 'checkout', '-b', 'main')
    (repo/'snapshot').mkdir()
    (repo/'snapshot/manifest.json').write_text(json.dumps({'asof': '2026-10-22'}))
    (repo/'snapshot/publication-ledger.json').write_text('{}')
    p.git(repo, 'add', '.'); p.git(repo, 'commit', '-m', 'baseline'); p.git(repo, 'push', 'origin', 'main')
    return repo


def test_claim_race_and_no_repeat(tmp_path):
    repo = repository(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda recovery_id: r.claim(repo, 100, recovery_id), ['r1', 'r2']))
    assert sorted(results) == [False, True]
    assert r.claim(repo, 100, 'r3') is False
    assert p.git(repo, 'status', '--porcelain') == ''


def test_already_published_or_newer():
    assert p.publication_guard(DAY, {'asof': '2026-10-23'}, {'2026-10-23': {}}) == 'already_published'
    assert p.publication_guard(DAY, {'asof': '2026-10-24'}, {}) == 'same_or_newer_publication'


def test_recovery_midnight_fixed_day_and_attempt_bound(tmp_path, monkeypatch):
    files = source(tmp_path/'source')
    monkeypatch.setattr(r, 'api', lambda _: run_info())
    monkeypatch.setattr(r, 'publication_report', lambda _: {'day': DAY})
    monkeypatch.setattr(r, 'current_main', lambda _: 'main')
    monkeypatch.setattr(r, 'guard_ref', lambda repo, ref, day: None if day == DAY else pytest.fail('date changed'))
    monkeypatch.setattr(r, 'claim', lambda *args: True)
    monkeypatch.setattr(r, 'checkpoint', lambda *args: False)
    monkeypatch.setattr(r, 'trusted_source', lambda _: (files, {'day': DAY}))
    calls = []
    monkeypatch.setattr(r, 'publish', lambda *args, **kwargs: calls.append(args) or {'status':'published'})
    assert r.recover(tmp_path, 100, 200, 1, tmp_path/'output')['status'] == 'published'
    assert calls[0][2] == DAY
    assert (tmp_path/'output/bundle.json.gz').read_bytes() == files['bundle.json.gz']
    with pytest.raises(r.Blocked): r.recover(tmp_path, 100, 200, 2, tmp_path/'output')
    assert len(calls) == 1


def test_failure_claim_blocks_second_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(r, 'api', lambda _: run_info())
    monkeypatch.setattr(r, 'publication_report', lambda _: {'day': DAY})
    monkeypatch.setattr(r, 'current_main', lambda _: 'main')
    monkeypatch.setattr(r, 'guard_ref', lambda *args: None)
    claims = set()
    def claim(repo, source, recovery):
        if source in claims: return False
        claims.add(source); return True
    monkeypatch.setattr(r, 'claim', claim)
    monkeypatch.setattr(r, 'checkpoint', lambda *args: False)
    monkeypatch.setattr(r, 'trusted_source', lambda _: (_ for _ in ()).throw(r.Blocked('bad source')))
    with pytest.raises(r.Blocked): r.recover(tmp_path, 100, 200, 1, tmp_path/'output')
    assert r.recover(tmp_path, 100, 201, 1, tmp_path/'output')['status'] == 'already_claimed'


def test_saved_artifact_fallback_and_conflict(tmp_path, monkeypatch):
    files = source(tmp_path/'source')
    monkeypatch.setattr(r, 'checkpoint', lambda *args: False)
    run = run_info()
    monkeypatch.setattr(r, 'pages', lambda path, key: [run] if key == 'workflow_runs' else [{'name': 'daily-source-'+DAY+'-100-1'}])
    monkeypatch.setattr(r, 'trusted_source', lambda _: (files, {'day':DAY}))
    assert r.load_available(tmp_path, DAY, tmp_path/'out')['status'] == 'artifact_restored'
    assert (tmp_path/'out/successes.jsonl').read_bytes() == files['successes.jsonl']


def test_validation_failure_is_not_retryable(tmp_path, monkeypatch):
    repo = repository(tmp_path); source(tmp_path/'source')
    original = p.command
    def command(args, cwd, env=None):
        if args[0] != 'git': raise subprocess.CalledProcessError(1, args, stderr=b'validation failed')
        return original(args, cwd, env)
    monkeypatch.setattr(p, 'command', command)
    report = tmp_path/'report.json'
    with pytest.raises(subprocess.CalledProcessError): p.publish(repo, tmp_path/'source/bundle.json.gz', DAY, 100, report=report)
    result = json.loads(report.read_text())
    assert result['stage'] == 'validation' and result['retryable'] is False


def fake_build(monkeypatch, hook=None):
    original = p.command
    def command(args, cwd, env=None):
        cwd = Path(cwd)
        if args[0] == 'git':
            if hook:
                answer = hook(args, cwd)
                if answer is not None: return answer
            return original(args, cwd, env)
        if 'scripts/prepare_daily_publish.py' in args:
            (cwd/'daily-candidate').mkdir()
            (cwd/'daily-candidate/manifest.json').write_text(json.dumps({'asof':'2026-10-23'}))
        elif 'scripts.stage_daily_publication' in args:
            (cwd/'snapshot/manifest.json').write_text(json.dumps({'asof':'2026-10-23'}))
            (cwd/'snapshot/publication-ledger.json').write_text(json.dumps({'2026-10-23':{}}))
            for n in ['dashboard.duckdb','daily-validation.json','daily_briefing.json']:
                (cwd/'snapshot'/n).write_text('fixture')
            for n in ['publication-history','briefing']:
                (cwd/'snapshot'/n).mkdir(); (cwd/'snapshot'/n/'fixture.json').write_text('{}')
        return ''
    monkeypatch.setattr(p, 'command', command)


def test_publish_lost_reply_checks_actual_result(tmp_path, monkeypatch):
    repo = repository(tmp_path); source(tmp_path/'source')
    original = p.command
    pushes = []
    def hook(args, cwd):
        if args[1] == 'push':
            pushes.append(args)
            original(args, cwd)
            raise subprocess.CalledProcessError(1,args,stderr=b'connection lost after acceptance')
    fake_build(monkeypatch, hook)
    result = p.publish(repo, tmp_path/'source/bundle.json.gz', DAY, 100)
    assert result['status'] == 'published' and len(pushes) == 1


def test_main_change_rebuilds_once(tmp_path, monkeypatch):
    repo = repository(tmp_path); source(tmp_path/'source')
    original = p.current_main; calls = []
    def current(repo):
        calls.append(1)
        if len(calls) == 2:
            (repo/'other.txt').write_text('concurrent independent update')
            p.git(repo, 'add', 'other.txt'); p.git(repo, 'commit', '-m', 'concurrent')
            p.git(repo, 'push', 'origin', 'main')
        return original(repo)
    monkeypatch.setattr(p, 'current_main', current)
    fake_build(monkeypatch)
    result = p.publish(repo, tmp_path/'source/bundle.json.gz', DAY, 100)
    assert result['status'] == 'published' and result['attempts'] == 2
    assert p.git(repo, 'show', p.current_main(repo)+':other.txt') == 'concurrent independent update'


def test_workflow_storage_failure_isolated_and_recovery_has_no_collect():
    workflow = Path('.github/workflows/daily-dashboard.yml').read_text()
    assert 'id: checkpoint\n        if:' in workflow
    checkpoint = workflow.split('id: checkpoint',1)[1].split('- name:',1)[0]
    assert 'continue-on-error: true' in checkpoint
    assert 'python scripts/daily_saved_recovery.py durable' in workflow
    recovery = Path('.github/workflows/daily-saved-recovery.yml').read_text()
    assert 'workflow_run:' in recovery and 'schedule:' not in recovery
    assert 'collect_daily_snapshot' not in recovery and 'KIS_APP' not in recovery
    assert 'actions: read' in recovery and 'actions: write' not in recovery


@pytest.mark.parametrize('checkpoint,artifact,allowed', [('failure','success',True), ('success','failure',True), ('failure','failure',False), ('skipped','skipped',False)])
def test_storage_failures_require_one_durable_copy(checkpoint, artifact, allowed):
    if allowed:
        assert r.durable_original('success', checkpoint, artifact)['status'] == 'durable_original_confirmed'
    else:
        with pytest.raises(r.Blocked): r.durable_original('success', checkpoint, artifact)
    with pytest.raises(r.Blocked): r.durable_original('failure', checkpoint, artifact)


def test_main_already_published_after_build_skips_push(tmp_path, monkeypatch):
    repo = repository(tmp_path); source(tmp_path/'source')
    original = p.guard_ref; calls=[]
    def guard(repo, ref, day):
        calls.append(1)
        return 'already_published' if len(calls)==2 else original(repo,ref,day)
    monkeypatch.setattr(p,'guard_ref',guard)
    def hook(args,cwd):
        if args[1]=='push': pytest.fail('duplicate publication')
    fake_build(monkeypatch,hook)
    assert p.publish(repo,tmp_path/'source/bundle.json.gz',DAY,100)['status']=='already_published'


def test_two_main_changes_stop_and_classify(tmp_path, monkeypatch):
    repo=repository(tmp_path);source(tmp_path/'source')
    original=p.current_main;calls=[]
    def current(repo):
        calls.append(1)
        if len(calls) in (2,4):
            (repo/'other').write_text(str(len(calls)))
            p.git(repo,'add','other');p.git(repo,'commit','-m','concurrent');p.git(repo,'push','origin','main')
        return original(repo)
    monkeypatch.setattr(p,'current_main',current);fake_build(monkeypatch)
    report=tmp_path/'report.json'
    with pytest.raises(p.Blocked):p.publish(repo,tmp_path/'source/bundle.json.gz',DAY,100,report=report)
    result=json.loads(report.read_text())
    assert result['retryable'] and result['attempts']==2 and result['stage']=='main_changed'


def test_denied_push_is_not_retried(tmp_path,monkeypatch):
    repo=repository(tmp_path);source(tmp_path/'source');pushes=[]
    def hook(args,cwd):
        if args[1]=='push':
            pushes.append(1);raise subprocess.CalledProcessError(1,args,stderr=b'403 permission denied')
    fake_build(monkeypatch,hook);report=tmp_path/'report.json'
    with pytest.raises(subprocess.CalledProcessError):p.publish(repo,tmp_path/'source/bundle.json.gz',DAY,100,report=report)
    assert len(pushes)==1 and json.loads(report.read_text())['retryable'] is False


def test_conflicting_artifact_sources_block(tmp_path,monkeypatch):
    files=source(tmp_path/'source');other=dict(files);other['bundle.json.gz']+=b'other'
    monkeypatch.setattr(r,'checkpoint',lambda *args:False)
    run=run_info();run2=copy.deepcopy(run);run2['id']=101
    def pages(path,key):
        if key=='workflow_runs':return [run,run2]
        run_id='101' if '/101/' in path else '100'
        return [{'name':'daily-source-'+DAY+'-'+run_id+'-1'}]
    monkeypatch.setattr(r,'pages',pages)
    monkeypatch.setattr(r,'trusted_source',lambda run: (files if run['id']==100 else other,{'day':DAY}))
    with pytest.raises(r.Blocked,match='Conflicting'):r.load_available(tmp_path,DAY,tmp_path/'out')


def test_checkpoint_access_failure_never_looks_absent(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:type('Result',(),{'returncode':128})())
    with pytest.raises(r.Blocked):r.checkpoint(tmp_path,DAY,tmp_path/'out')


@pytest.mark.parametrize('stage,retryable', [('validation',False),('stage_publication',False),('resolve_push_outcome',False),('access_denied',False)])
def test_nontransport_reports_cannot_trigger_recovery(monkeypatch,stage,retryable):
    report={'day':DAY,'source_run':'100','stage':stage,'retryable':retryable}
    monkeypatch.setattr(r,'download',lambda *args:{'daily-publish-report.json':json.dumps(report).encode()})
    with pytest.raises(r.Blocked):r.publication_report(run_info())


def test_expired_artifact_and_missing_digest_block():
    data,meta=zip_source({'test':b'value'})
    for modified in [dict(meta,expired=True),dict(meta,digest=None)]:
        with pytest.raises(r.Blocked):r.archive_files(data,modified)


def test_publish_skips_newer_main_before_build(tmp_path,monkeypatch):
    repo=repository(tmp_path);source(tmp_path/'source')
    monkeypatch.setattr(p,'guard_ref',lambda *args:'same_or_newer_publication')
    assert p.publish(repo,tmp_path/'source/bundle.json.gz',DAY,100)['status']=='same_or_newer_publication'
