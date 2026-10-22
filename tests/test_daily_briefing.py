"""Real October 16 regression; destructive fault fixtures remain under tmp_path."""
import copy,hashlib,json,shutil
from pathlib import Path
import duckdb,pytest
from scripts.export_daily_briefing import export
from scripts.verify_daily_briefing import verify
from scripts.stage_daily_publication import stage

@pytest.fixture(scope='module')
def snapshot16():
    expected=json.loads(Path('tests/fixtures/briefing-20261016.json').read_text())['source_snapshot_sha256']
    for p in [Path('snapshot'),*sorted(Path('snapshot/publication-history').glob('*'))]:
        if json.loads((p/'manifest.json').read_text()).get('sha256')==expected:return p
    pytest.fail('Required verified October 16 historical fixture missing')

@pytest.fixture(scope='module')
def briefing(snapshot16,tmp_path_factory):
    p=tmp_path_factory.mktemp('briefing')/'briefing.json'
    return export('2026-10-16',p,snapshot16)

def test_exact_original_branch_numbers_and_54_raw_checks(briefing):
    old=json.loads(Path('tests/fixtures/briefing-20261016.json').read_text())
    assert briefing['top_sectors']==old['top_sectors']
    assert briefing['raw_flow_reconciliation']['checks']==54
    assert briefing['raw_flow_reconciliation']['additional_value_checks']>=33
    assert briefing['validation_status']=='passed' and briefing['asof']=='2026-10-16'

@pytest.mark.parametrize('fault',['sector_order','stock_order','net','change','breadth','intensity','symbol','short_top'])
def test_independent_reconciliation_rejects_bad_data(snapshot16,briefing,fault):
    b=copy.deepcopy(briefing);s=b['top_sectors'][0]
    if fault=='sector_order':b['top_sectors'].reverse()
    elif fault=='stock_order':s['top_stocks'].reverse()
    elif fault=='net':s['net_20d']+=1
    elif fault=='change':s['top_stocks'][0]['change_5d']+=1
    elif fault=='breadth':s['breadth_5d']+=1
    elif fault=='intensity':s['intensity_5d']+=.1
    elif fault=='symbol':s['top_stocks'][0]['symbol']='000000'
    else:b['top_sectors'].pop()
    with pytest.raises(ValueError):verify(b,snapshot16/'dashboard.duckdb')

def copy_snapshot(source,dest):
    dest.mkdir()
    for name in ['dashboard.duckdb','manifest.json','daily-validation.json','publication-ledger.json']:shutil.copy2(source/name,dest/name)
    return dest

@pytest.mark.parametrize('fault',['date','hash','validation'])
def test_failed_export_preserves_previous_file(snapshot16,tmp_path,fault):
    s=copy_snapshot(snapshot16,tmp_path/'snapshot');out=tmp_path/'briefing.json';out.write_text('previous normal file')
    asof='2026-10-16'
    if fault=='date':asof='2026-10-19'
    if fault=='hash':
        with (s/'dashboard.duckdb').open('ab') as f:f.write(b'corrupt')
    if fault=='validation':
        v=json.loads((s/'daily-validation.json').read_text());v['preservation_verified']=False;(s/'daily-validation.json').write_text(json.dumps(v))
    with pytest.raises(ValueError):export(asof,out,s)
    assert out.read_text()=='previous normal file'

def test_null_is_not_zero_and_missing_window_blocks_rank(snapshot16,briefing,tmp_path):
    db=tmp_path/'fault.duckdb';shutil.copy2(snapshot16/'dashboard.duckdb',db)
    code=briefing['top_sectors'][0]['top_stocks'][0]['symbol']
    with duckdb.connect(str(db)) as c:c.execute("delete from flows where symbol=? and trade_date='2026-10-16'",[code])
    with pytest.raises(ValueError):verify(briefing,db)

def test_insufficient_top3_preserves_previous(snapshot16,tmp_path,monkeypatch):
    import scripts.export_daily_briefing as exporter
    original=exporter.sector_indicators
    def short(*args,**kwargs):
        frame,series=original(*args,**kwargs)
        return frame.iloc[:2],series
    monkeypatch.setattr(exporter,'sector_indicators',short)
    out=tmp_path/'briefing.json';out.write_text('previous')
    with pytest.raises(ValueError,match='TOP 3'):export('2026-10-16',out,snapshot16)
    assert out.read_text()=='previous'

def test_export_idempotent(snapshot16,tmp_path):
    out=tmp_path/'briefing.json';export('2026-10-16',out,snapshot16)
    before=out.read_bytes();stamp=out.stat().st_mtime_ns
    export('2026-10-16',out,snapshot16)
    assert out.read_bytes()==before and out.stat().st_mtime_ns==stamp

def test_stage_no_duplicate_day_and_failure_isolation(snapshot16,tmp_path,monkeypatch):
    import scripts.stage_daily_publication as publisher
    old=copy_snapshot(snapshot16,tmp_path/'old');new=copy_snapshot(snapshot16,tmp_path/'new')
    shutil.copy2(new/'daily-validation.json',new/'validation.json')
    (old/'daily_briefing.json').write_text('previous')
    original={p.name:p.read_bytes() for p in old.iterdir()}
    assert stage(new,old,'TEST') is False
    assert original=={p.name:p.read_bytes() for p in old.iterdir()}
    ledger=json.loads((old/'publication-ledger.json').read_text());ledger.pop('2026-10-16');(old/'publication-ledger.json').write_text(json.dumps(ledger))
    original={p.name:p.read_bytes() for p in old.iterdir()}
    def reject(*a,**kw):raise ValueError('Test raw validation failure')
    monkeypatch.setattr(publisher,'export',reject)
    with pytest.raises(ValueError):stage(new,old,'TEST')
    assert original=={p.name:p.read_bytes() for p in old.iterdir()}

def test_workflow_atomic_files_and_unchanged_schedule():
    w=Path('.github/workflows/daily-dashboard.yml').read_text()
    assert "cron: '30 9 * * 1-5'" in w and "cron: '0 12 * * 1-5'" in w
    assert 'python scripts/daily_publish_transaction.py' in w
    transaction=Path('scripts/daily_publish_transaction.py').read_text()
    assert "'-m', 'scripts.stage_daily_publication'" in transaction
    assert "'snapshot/publication-ledger.json'" in transaction and "'snapshot/daily_briefing.json'" in transaction
    assert 'kis-validation-auth' in w and 'cancel-in-progress: false' in w
    assert 'daily_briefing.json' in Path('scripts/publish_liquidity.py').read_text()

def test_successful_staging_coherent_snapshot_and_json(snapshot16,tmp_path):
    from screener.publication import coherent
    old=copy_snapshot(snapshot16,tmp_path/'old');new=copy_snapshot(snapshot16,tmp_path/'new')
    shutil.copy2(new/'daily-validation.json',new/'validation.json')
    ledger=json.loads((old/'publication-ledger.json').read_text())
    ledger.pop('2026-10-16')
    (old/'publication-ledger.json').write_text(json.dumps(ledger))
    assert stage(new,old,'TEST-STAGING') is True
    assert coherent(old)
    result=json.loads((old/'daily_briefing.json').read_text())
    assert result['asof']=='2026-10-16'
    assert result['source_snapshot_sha256']==hashlib.sha256((old/'dashboard.duckdb').read_bytes()).hexdigest()
    assert verify(result,old/'dashboard.duckdb')['checks']==54
