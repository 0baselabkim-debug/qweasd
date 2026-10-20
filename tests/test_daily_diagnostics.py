import copy,datetime as dt,json,sys
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from daily_diagnostics import preserve_failure,redact
from collect_daily_snapshot import fetch_stock
from test_daily import bundle
from test_daily_optimization import enrich,API

def test_failed_mismatch_preserves_values_but_keeps_failure(bundle,tmp_path):
 b,r=enrich(bundle);r['adjusted_prices'][0]['stck_clpr']='999';api=API(r)
 api.key='SECRET_TEST_KEY';api.secret='SECRET_TEST_SECRET';api.token='SECRET_TEST_TOKEN'
 r['flow']['authorization']='Bearer SECRET_TEST_TOKEN';r['flow']['note']='echo SECRET_TEST_KEY'
 _,record,excluded,error=fetch_stock(b['universe'][0],api,b['trade_date'],'20260816',b,set(),dt.datetime(2099,1,1,tzinfo=ZoneInfo('Asia/Seoul')),float('inf'),tmp_path)
 assert record is None and excluded is None and 'Dated chart/flow mismatch: stck_clpr' in error
 paths=list(tmp_path.rglob('*.json'));assert len(paths)==1
 raw=paths[0].read_text();x=json.loads(raw)
 assert all(s not in raw for s in [api.key,api.secret,api.token])
 assert x['observations'][1]['response']['output2'][0]['stck_clpr']=='999'
 assert len(api.calls)==2 and x['decision']=='REJECTED_PENDING_REVIEW'

def test_observations_append_and_recursive_redaction(tmp_path):
 obs=[{'response':{'nested':{'APP_SECRET':'private'},'other':'Bearer tokenvalue'}}]
 a=preserve_failure(tmp_path,'20261020','001420','x',obs,['tokenvalue'])
 b=preserve_failure(tmp_path,'20261020','001420','y',obs,['tokenvalue'])
 assert a!=b and Path(a).exists() and Path(b).exists()
 assert 'private' not in Path(a).read_text() and 'tokenvalue' not in Path(a).read_text()
 assert obs[0]['response']['nested']['APP_SECRET']=='private'

def test_real_taewon_probe_remains_blocked():
 from screener.daily_quote import chart_quote
 root=Path('reports/taewon-20261020');x=json.loads((root/'probe.json').read_text())
 obs={r['kind']:json.loads((root/r['file']).read_text())['response'] for r in x['observations']}
 import pytest
 flow=next(r for r in obs['flow']['output2'] if r['stck_bsop_date']=='20261020')
 assert flow['stck_clpr']=='3020'
 for kind in ['adjusted','raw']:
  price=next(r for r in obs[kind]['output2'] if r['stck_bsop_date']=='20261020')
  assert price['stck_clpr']=='2995' and price['acml_vol']==flow['acml_vol'] and price['acml_tr_pbmn']==flow['acml_tr_pbmn']
  with pytest.raises(ValueError,match='Dated chart/flow mismatch: stck_clpr'):
   chart_quote(obs[kind],flow,{'symbol':'001420','shares_thousands':'7600'},'20261020','2026-10-20T12:39:29+00:00',True)

def test_checkpoint_keeps_prior_diagnostics_on_resume(tmp_path):
 import subprocess,gzip
 from persist_daily_checkpoint import persist
 remote=tmp_path/'remote.git';repo=tmp_path/'repo'
 def git(*args,cwd=None):return subprocess.check_output(['git',*args],cwd=cwd,stderr=subprocess.DEVNULL).decode().strip()
 git('init','--bare',str(remote));git('clone',str(remote),str(repo))
 git('config','user.name','test',cwd=repo);git('config','user.email','test@example.invalid',cwd=repo)
 (repo/'source').write_text('same');git('add','source',cwd=repo);git('commit','-m','fixture',cwd=repo)
 (repo/'daily-results').mkdir();(repo/'daily-results/bundle.json.gz').write_bytes(gzip.compress(b'{}'))
 first=Path(preserve_failure(repo/'daily-results/diagnostics','20261020','001420','first',[]))
 a=persist(repo,'20261020');first_relative=str(first.relative_to(repo));first.unlink()
 second=Path(preserve_failure(repo/'daily-results/diagnostics','20261020','001420','second',[]))
 b=persist(repo,'20261020');names=git('ls-tree','-r','--name-only',b['commit'],cwd=repo).splitlines()
 assert first_relative in names and str(second.relative_to(repo)) in names
 assert git('rev-parse',b['commit']+'^',cwd=repo)==a['commit']
