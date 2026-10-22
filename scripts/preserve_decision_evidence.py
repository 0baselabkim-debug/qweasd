"""Append auxiliary evidence only; full ancestry required, never force-push."""
from pathlib import Path
import subprocess

def run(*args,**kw):return subprocess.run(['git',*args],check=True,**kw)
if Path('auxiliary-prices').is_dir():
    run('config','user.name','github-actions[bot]')
    run('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    run('add','auxiliary-prices')
    if subprocess.run(['git','diff','--cached','--quiet']).returncode:
        run('commit','-m','Preserve auxiliary price evidence and durable request lease')
        ref='refs/heads/evidence/decision-price-source'
        if subprocess.run(['git','ls-remote','--exit-code','origin',ref],stdout=subprocess.DEVNULL).returncode==0:
            run('fetch','origin',ref)
            run('merge','-s','ours','--no-edit','FETCH_HEAD')
        run('push','origin','HEAD:'+ref)
