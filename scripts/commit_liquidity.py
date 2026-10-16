"""Publish one coherent commit; preserve a rejected candidate on an evidence branch."""
import os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.publication import coherent

def git(*args,check=True):return subprocess.run(['git',*args],capture_output=True,text=True,check=check)
def main():
    git('config','user.name','github-actions[bot]');git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    base=git('rev-parse','HEAD').stdout.strip()
    git('add','liquidity','snapshot')
    if git('diff','--cached','--quiet',check=False).returncode==0:return
    git('commit','-m','Publish verified recovery evidence and date-specific liquidity')
    def preserve(reason):
        branch='evidence/liquidity-recovery-'+os.environ['GITHUB_RUN_ID']
        git('push','origin','HEAD:refs/heads/'+branch)
        print(reason+'; candidate preserved at '+branch)
        raise SystemExit(1)
    if os.getenv('RECOVERY_PREPARE_OK','success')!='success':preserve('Candidate preparation failed; retain published snapshot')
    if not coherent(Path('snapshot')):preserve('Publication coherence failed')
    if git('push','origin','HEAD:main',check=False).returncode==0:return
    git('fetch','origin','main')
    ours=set(git('diff','--name-only',base,'HEAD').stdout.splitlines())
    theirs=set(git('diff','--name-only',base,'origin/main').stdout.splitlines())
    if ours & theirs:preserve('Concurrent changes affect the same files; rebuild required')
    # Nonoverlapping corporate/operational changes can be retained without rebasing
    # or taking either side of a binary snapshot conflict.
    if git('merge','--no-edit','origin/main',check=False).returncode:
        git('merge','--abort');preserve('Nonoverlapping merge failed')
    if not coherent(Path('snapshot')):preserve('Merged publication coherence failed')
    if git('push','origin','HEAD:main',check=False).returncode:preserve('Another publication race')
    print('Published coherent recovery and retained concurrent nonoverlapping changes')
if __name__=='__main__':main()
