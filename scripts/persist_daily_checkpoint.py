"""Publish only checkpoint files without switching/deleting the running checkout."""
import argparse,datetime as dt,os,subprocess,tempfile
from pathlib import Path
try:
    from .checkpoint_storage import pack, RAW, MANIFEST, PACKED, LIMIT
except ImportError:
    from checkpoint_storage import pack, RAW, MANIFEST, PACKED, LIMIT

def persist(repo,day,remote='origin',prefix='data/daily-'):
    dt.datetime.strptime(day,'%Y%m%d')
    repo=Path(repo).resolve();branch=prefix+day
    files=[p for p in ['daily-results/bundle.json.gz','daily-results/recovery-source.json'] if (repo/p).exists()]
    if not files or files[0]!='daily-results/bundle.json.gz':return {'status':'no_checkpoint'}
    files += sorted(str(p.relative_to(repo)) for p in (repo/'daily-results/diagnostics').rglob('*.json'))
    def git(*args,env=None,input=None):
        return subprocess.check_output(['git',*args],cwd=repo,env=env,input=input).decode().strip()
    payloads={name:(repo/name).read_bytes() for name in files}
    if (repo/RAW).exists():payloads.update(pack((repo/RAW).read_bytes()))
    if any(len(data)>LIMIT for data in payloads.values()):
        raise ValueError('Checkpoint file exceeds safe Git size limit')
    ref='refs/heads/'+branch
    before=git('rev-parse','HEAD');exists=bool(git('ls-remote','--heads',remote,ref))
    parent=None
    if exists:
        git('fetch',remote,ref);parent=git('rev-parse','FETCH_HEAD')
    with tempfile.TemporaryDirectory(prefix='daily-checkpoint-') as folder:
        env=dict(os.environ,GIT_INDEX_FILE=str(Path(folder)/'index'),GIT_AUTHOR_NAME='github-actions[bot]',GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',GIT_COMMITTER_NAME='github-actions[bot]',GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
        git('read-tree',parent or '--empty',env=env)
        if (repo/RAW).exists():
            # Remove the old plaintext and every previous transport part only from
            # this isolated index. Local originals and diagnostics are untouched.
            names=git('ls-files',env=env).splitlines()
            for name in names:
                if name in (RAW,MANIFEST) or name.startswith(PACKED):
                    git('update-index','--force-remove','--',name,env=env)
        for name,data in payloads.items():
            blob=git('hash-object','-w','--stdin',input=data)
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
