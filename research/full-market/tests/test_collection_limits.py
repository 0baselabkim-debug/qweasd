import json
from pathlib import Path
from collect_batch import GLOBAL_LIMIT,BATCH_LIMIT,BRANCH
ROOT=Path(__file__).resolve().parents[1]
def test_plan_excludes_all_existing_baseline_and_pilot():
 import pandas as pd
 plan=json.loads((ROOT/'plan.json').read_text());targets={r['symbol'] for r in plan['targets']}
 base=pd.read_csv(ROOT/'inputs/baseline-screening.csv',dtype={'symbol':str})
 pilot=json.loads((ROOT/'inputs/pilot/run.json').read_text())['records']
 assert not targets & set(base.loc[base.research_price_eligible,'symbol'])
 assert not targets & set(base.loc[base.consecutive_60,'symbol'])
 assert not targets & set(pilot)
 assert len(targets)==len(plan['targets']) and 2*len(targets)+20<GLOBAL_LIMIT
 assert GLOBAL_LIMIT==3800 and BATCH_LIMIT==300

def test_research_workflow_has_no_schedule_and_never_pushes_main():
 text=(ROOT.parents[1]/'.github/workflows/kis-history-probe.yml').read_text()
 assert 'cron:' not in text and 'schedule:' not in text
 import re
 assert 'kis-validation-auth' in text
 assert all(0 < int(n) <= 15 for n in re.findall(r'timeout-minutes: (\d+)', text))
 assert 'HEAD:main' not in text
 if 'from krx40_collect import persist_research' in text:
  # The manual workflow now delegates durable writes to the bounded collector.
  # Verify the real push boundary and branch guard instead of a YAML literal.
  helper=(ROOT.parent/'risk-performance/krx40_collect.py').read_text()
  persistence=helper.split('def persist_research():',1)[1]
  assert "BRANCH = '"+BRANCH+"'" in helper
  assert "git('branch', '--show-current') != BRANCH" in persistence
  assert "['git', 'push', 'origin', 'HEAD:refs/heads/'+BRANCH]" in persistence
  assert "unrelated_staged_changes" in persistence and "HEAD:main" not in persistence
 else:
  assert 'HEAD:refs/heads/'+BRANCH in text

def test_old_rule_modules_byte_identical():
 import hashlib
 # Original research modules are pinned, rather than silently reinterpreted.
 expected=json.loads((ROOT/'rule-hashes.json').read_text())
 for file,sha in expected.items():assert hashlib.sha256((ROOT/file).read_bytes()).hexdigest()==sha
