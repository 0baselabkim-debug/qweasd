"""Publish only checkpoint files without switching/deleting the running checkout."""
import argparse,datetime as dt,os,subprocess,tempfile
from pathlib import Path

def persist(repo,day,remote='origin',prefix='data/daily-'):
    dt.datetime.strptime(day,'%Y%m%d')
    repo=Path(repo).resolve();branch=prefix+day
    files=[p for p in ['daily-results/bundle.json.gz','daily-results/successes.jsonl'] if (repo/p).exists()]
    if not files or files[0]!='daily-results/bundle.json.gz':return {'status':'no_checkpoint'}
    files += sorted(str(p.relative_to(repo)) for p in (repo/'daily-results/diagnostics').rglob('*.json'))
    def git(*args,env=None,input=None):
        return subprocess.check_output(['git',*args],cwd=repo,env=env,input=input).decode().strip()
    ref='refs/heads/'+branch
    before=git('rev-parse','HEAD');exists=bool(git('ls-remote','--heads',remote,ref))
    parent=None
    if exists:
        git('fetch',remote,ref);parent=git('rev-parse','FETCH_HEAD')
    with tempfile.TemporaryDirectory(prefix='daily-checkpoint-') as folder:
        env=dict(os.environ,GIT_INDEX_FILE=str(Path(folder)/'index'),GIT_AUTHOR_NAME='github-actions[bot]',GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',GIT_COMMITTER_NAME='github-actions[bot]',GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
        git('read-tree',parent or '--empty',env=env)
        for name in files:
            blob=git('hash-object','-w','--',name)
            git('update-index','--add','--cacheinfo','100644',blob,name,env=env)
        tree=git('write-tree',env=env)
        if parent and tree==git('rev-parse',parent+'^{tree}'):return {'status':'unchanged','commit':parent}
        args=['commit-tree',tree]+(['-p',parent] if parent else [])
        commit=git(*args,env=env,input=('Preserve validated stock checkpoints '+day+'\n').encode())
        # No force: a concurrent checkpoint publisher is rejected safely.
        git('push',remote,commit+':'+ref)
    if git('rev-parse','HEAD')!=before:raise RuntimeError('Running checkout unexpectedly changed')
    return {'status':'saved','commit':commit,'branch':branch}

if __name__=='__main__':
    import json
    p=argparse.ArgumentParser();p.add_argument('--date',required=True);a=p.parse_args()
    print(json.dumps(persist(Path.cwd(),a.date)))
