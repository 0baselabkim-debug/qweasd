"""Retain resumable progress on a data branch and sanitized failure evidence."""
import json,re,shutil,subprocess,sys
from pathlib import Path

result={'success':False,'stage':'start'}
def run(args,allow_failure=False):
 result['stage']=' '.join(args[:4])
 p=subprocess.run(args,text=True,capture_output=True)
 if p.returncode and not allow_failure:
  message=re.sub(r'https://[^/@\s]+:[^/@\s]+@','https://[redacted]@',p.stderr[-3000:])
  result.update(exit_code=p.returncode,error=message)
  raise RuntimeError('checkpoint_command_failed')
 return p
try:
 source=Path('corporate-candidate/state.json.gz')
 if not source.exists():result.update(success=True,skipped='no_candidate')
 else:
  run(['git','config','user.name','github-actions[bot]']);run(['git','config','user.email','41898282+github-actions[bot]@users.noreply.github.com'])
  exists=run(['git','ls-remote','--exit-code','--heads','origin','data/corporate-checkpoint'],True).returncode==0
  if exists:run(['git','fetch','origin','data/corporate-checkpoint'])
  run(['git','worktree','add','--detach','/tmp/corporate-checkpoint','FETCH_HEAD' if exists else 'HEAD'])
  shutil.copy2(source,'/tmp/corporate-checkpoint/corporate-checkpoint.json.gz')
  run(['git','-C','/tmp/corporate-checkpoint','add','-f','corporate-checkpoint.json.gz'])
  if run(['git','-C','/tmp/corporate-checkpoint','diff','--cached','--quiet'],True).returncode:
   run(['git','-C','/tmp/corporate-checkpoint','commit','-m','Preserve disclosure collection progress'])
  run(['git','-C','/tmp/corporate-checkpoint','push','origin','HEAD:refs/heads/data/corporate-checkpoint'])
  run(['git','worktree','remove','/tmp/corporate-checkpoint'])
  result['success']=True
except Exception as e:result.setdefault('error',type(e).__name__)
finally:Path('corporate-checkpoint-result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
if not result['success']:raise SystemExit(1)
