"""Exercise checkpoint publication with existing real data, never a new trading date."""
import gzip,hashlib,json,os,subprocess
from pathlib import Path
from persist_daily_checkpoint import persist

def git(*args):return subprocess.check_output(['git',*args])
day=json.loads(Path('snapshot/manifest.json').read_text())['asof'].replace('-','')
head=git('rev-parse','HEAD').decode().strip()
git('fetch','origin','refs/heads/data/daily-'+day)
raw=git('show','FETCH_HEAD:daily-results/bundle.json.gz');b=json.loads(gzip.decompress(raw))
assert b['trade_date']==day and b['status']=='complete'
p=Path('daily-results/bundle.json.gz');p.parent.mkdir(exist_ok=True);p.write_bytes(raw)
prefix='evidence/checkpoint-'+os.environ['GITHUB_RUN_ID']+'-'
a=persist(Path.cwd(),day,prefix=prefix);z=persist(Path.cwd(),day,prefix=prefix)
assert a['status']=='saved' and z['status']=='unchanged' and git('rev-parse','HEAD').decode().strip()==head
r={'source_day':day,'source_status':b['status'],'source_records':len(b['records']),'source_sha256':hashlib.sha256(raw).hexdigest(),'first':a,'second':z,'checkout_unchanged':True,'market_api_calls':0,'published_market_snapshot':False}
p=Path('reports/daily-optimization/probe/checkpoint.json');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
