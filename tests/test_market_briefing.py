"""Real snapshot coverage plus isolated destructive fault fixtures (never published)."""
import copy,json,shutil
from pathlib import Path
import pytest
from scripts.export_daily_briefing import export
from scripts.export_market_briefing import install_dataset
from scripts.verify_market_briefing import verify_dataset
from scripts.briefing_schema import payload,digest,sector_id

@pytest.fixture(scope='module')
def generated(tmp_path_factory):
 root=tmp_path_factory.mktemp('market-briefing');snapshot=Path('snapshot')
 day=json.loads((snapshot/'manifest.json').read_text())['asof']
 export(day,root/'daily_briefing.json',snapshot,dataset_output=root/'briefing')
 return root

def index(root):return json.loads((root/'index.json').read_text())
def seal_index(root,doc):
 doc['index_content_sha256']=digest(payload({k:v for k,v in doc.items() if k!='index_content_sha256'}))
 (root/'index.json').write_bytes(payload(doc))
def reseal_child(root,doc,entry,child):
 content=payload(child);(root/entry['path']).write_bytes(content)
 entry.update(sha256=digest(content),bytes=len(content));seal_index(root,doc)

def test_all_records_validated_and_v2_unchanged(generated):
 root=generated/'briefing';doc=index(root);result=verify_dataset(root,'snapshot')
 assert result['stocks_checked']==doc['coverage']['included']
 assert doc['counts']['groups']==len(doc['sectors'])
 old=json.loads(Path('snapshot/daily_briefing.json').read_text());new=json.loads((generated/'daily_briefing.json').read_text())
 assert old['top_sectors']==new['top_sectors'] and old['schema_version']==new['schema_version']==2
 assert doc['coverage']['population_total']==sum(doc['coverage'][k] for k in ['included','excluded','not_observed'])
 assert sum(s['members'] for s in doc['sectors'])==doc['coverage']['included']

@pytest.mark.parametrize('fault',['missing_file','corrupt_file','hash','mixed_asof','mixed_generation','duplicate_stock','missing_stock','duplicate_sector','omit_sector','net','change','intensity','breadth','positive_days','null_to_zero','population','missing_flag','extra_file'])
def test_rejects_tampered_dataset(generated,tmp_path,fault):
 root=tmp_path/'briefing';shutil.copytree(generated/'briefing',root);doc=index(root);entry=doc['sectors'][0]
 child=json.loads((root/entry['path']).read_text())
 if fault=='missing_file':(root/entry['path']).unlink()
 elif fault=='corrupt_file':(root/entry['path']).write_text('{broken json')
 elif fault=='hash':entry['sha256']='0'*64;seal_index(root,doc)
 elif fault=='duplicate_sector':doc['sectors'].append(copy.deepcopy(entry));seal_index(root,doc)
 elif fault=='omit_sector':doc['sectors'].pop();seal_index(root,doc)
 elif fault=='breadth':entry['breadth_5d']=123;seal_index(root,doc)
 elif fault=='population':doc['coverage']['included']+=1;seal_index(root,doc)
 elif fault=='extra_file':(root/'unexpected.json').write_text('{}')
 else:
  stock=child['stocks'][0]
  if fault=='mixed_asof':child['asof']='2099-01-01'
  elif fault=='mixed_generation':child['dataset_id']='different-source'
  elif fault=='duplicate_stock':child['stocks'].append(copy.deepcopy(stock))
  elif fault=='missing_stock':child['stocks'].pop()
  elif fault=='net':stock['net_1d']=(stock['net_1d'] or 0)+1
  elif fault=='change':stock['change_5d']=(stock['change_5d'] or 0)+1
  elif fault=='intensity':stock['intensity_5d']=123
  elif fault=='positive_days':stock['positive_days_20d']=99
  elif fault=='missing_flag':stock['metric_validity']['net_1d']='missing'
  elif fault=='null_to_zero':
   found=False
   for entry in doc['sectors']:
    child=json.loads((root/entry['path']).read_text())
    for stock in child['stocks']:
     if stock['net_20d'] is None:stock['net_20d']=0;found=True;break
    if found:break
   if not found:pytest.skip('No incomplete history in this source; isolated raw NULL regression remains active')
  reseal_child(root,doc,entry,child)
 with pytest.raises((ValueError,FileNotFoundError,KeyError)):verify_dataset(root,'snapshot')

def test_source_hash_mismatch_rejected(generated,tmp_path):
 root=tmp_path/'briefing';shutil.copytree(generated/'briefing',root);doc=index(root)
 doc['source_snapshot_sha256']='0'*64;seal_index(root,doc)
 with pytest.raises(ValueError):verify_dataset(root,'snapshot')

def test_generation_reproducible_and_failure_preserves_previous(generated,tmp_path,monkeypatch):
 root=tmp_path/'output';shutil.copytree(generated,root)
 before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*.json')}
 day=index(root/'briefing')['asof']
 export(day,root/'daily_briefing.json','snapshot',dataset_output=root/'briefing')
 assert before=={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*.json')}
 import scripts.export_market_briefing as writer
 def reject(*args,**kwargs):raise ValueError('Injected independent verification failure')
 monkeypatch.setattr(writer,'verify_dataset',reject)
 with pytest.raises(ValueError):export(day,root/'daily_briefing.json','snapshot',dataset_output=root/'briefing')
 assert before=={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*.json')}

def test_zero_negative_and_null_in_raw_reference():
 # Exercise independent arithmetic using a tiny isolated in-memory copy schema.
 import duckdb,datetime as dt
 from scripts.briefing_raw import reference
 from tempfile import TemporaryDirectory
 with TemporaryDirectory() as temp:
  db=Path(temp)/'test.duckdb'
  with duckdb.connect(str(db)) as c:
   c.execute("create table instruments(symbol varchar,name varchar,market varchar); insert into instruments values ('TEST','test','KOSPI')")
   c.execute('create table memberships(symbol varchar,sector varchar,valid_from date,valid_to date)')
   c.execute("insert into memberships values ('TEST','test', '2020-01-01',null)")
   c.execute('create table prices(symbol varchar,trade_date date,exchange varchar,market_cap bigint,volume bigint)')
   c.execute('create table flows(symbol varchar,trade_date date,exchange varchar,net_amount bigint)')
   c.execute('create table exclusions(symbol varchar,name varchar,trade_date date,reason varchar)')
   c.execute('create table calendar(market varchar,trade_date date)')
   for n in range(20):
    day=dt.date(2026,1,1)+dt.timedelta(days=n)
    for m in ['KOSPI','KOSDAQ']:c.execute('insert into calendar values (?,?)',[m,day])
    c.execute("insert into flows values ('TEST',?,'KRX',?)",[day,0 if n==19 else -10])
   c.execute("insert into prices values ('TEST','2026-01-20','KRX',1000,1)")
  stocks,*_=reference(db,'2026-01-20')
  assert stocks['TEST']['net_1d']==0 and stocks['TEST']['net_5d']==-40 and stocks['TEST']['positive_days_20d']==0
  with duckdb.connect(str(db)) as c:c.execute("delete from flows where trade_date='2026-01-19'")
  stocks,*_=reference(db,'2026-01-20')
  assert stocks['TEST']['net_1d']==0 and stocks['TEST']['net_5d'] is None and stocks['TEST']['positive_days_20d'] is None

def test_ids_and_scheduled_paths():
 assert sector_id('KOSPI','같은 이름')!=sector_id('KOSDAQ','같은 이름')
 assert sector_id('KOSPI','업종/특수')==sector_id('KOSPI','업종/특수')
 assert sector_id('KOSPI','업종/특수').isascii()
 daily=Path('.github/workflows/daily-dashboard.yml').read_text()
 assert "cron: '30 9 * * 1-5'" in daily and "cron: '0 12 * * 1-5'" in daily
 assert 'snapshot/briefing' in daily
 for name in ['stage_daily_publication.py','publish_liquidity.py']:
  text=Path('scripts',name).read_text();assert 'dataset_output=' in text and 'install_dataset' in text


def test_historical_oct16_full_dataset(tmp_path):
 expected=json.loads(Path('tests/fixtures/briefing-20261016.json').read_text())
 roots=[Path('snapshot'),*Path('snapshot/publication-history').glob('*')]
 source=next(p for p in roots if json.loads((p/'manifest.json').read_text())['sha256']==expected['source_snapshot_sha256'])
 export('2026-10-16',tmp_path/'legacy.json',source,dataset_output=tmp_path/'briefing')
 assert json.loads((tmp_path/'legacy.json').read_text())['top_sectors']==expected['top_sectors']
 result=verify_dataset(tmp_path/'briefing',source)
 assert result['stocks_checked']==2430 and result['sectors_checked']==50

def test_legacy_bootstrap_cannot_bypass_coherent_publisher():
 import subprocess,sys
 before=Path('snapshot/manifest.json').read_bytes()
 result=subprocess.run([sys.executable,'scripts/publish_snapshot.py','--raw','/not-used'],capture_output=True,text=True)
 assert result.returncode!=0 and 'Legacy bootstrap publisher cannot overwrite' in result.stderr
 assert Path('snapshot/manifest.json').read_bytes()==before

def test_directory_install_rolls_back_on_error(tmp_path,monkeypatch):
 source=tmp_path/'source';target=tmp_path/'target';source.mkdir();target.mkdir()
 (source/'index.json').write_text('new');(target/'index.json').write_text('old')
 rename=Path.rename
 def fail_new(self,to):
  if self.name=='new':raise OSError('simulated rename failure')
  return rename(self,to)
 monkeypatch.setattr(Path,'rename',fail_new)
 with pytest.raises(OSError):install_dataset(source,target)
 assert (target/'index.json').read_text()=='old'
