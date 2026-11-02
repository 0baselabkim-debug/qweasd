"""Run pinned, hash-checked three-client snapshots without cross-repo credentials.

This CI proof checks transport/lease code, not deployed GitHub permissions.
The primary development proof also runs the three actual checked-out trees.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kis_shared.native_proof import Proof

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    source=Path(__file__).resolve().parents[1];bundle=source/'tests/native_clients_v2'
    manifest=json.loads((bundle/'manifest.json').read_bytes());roots={};base=Path(tempfile.mkdtemp(prefix='kis-native-clients-'))
    for role in ('eod','intraday','integrated'):
        roots[role]=base/role;roots[role].mkdir();shutil.copytree(source/'kis_shared',roots[role]/'kis_shared',ignore=shutil.ignore_patterns('__pycache__'))
    for name,row in manifest.items():
        path=(bundle/name).resolve()
        if not path.is_relative_to(bundle) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise RuntimeError('native_client_snapshot_hash_mismatch')
        role,relative=name.split('/',1);target=roots[role]/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    report=Proof(roots,Path(a.output),kind="pinned_client_bundle").run()
    (Path(a.output)/'client-snapshot-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(dict(status=report['status'],counts=report['counts'],actual_KIS_HTTP=0,proof='pinned native clients; GitHub deployment permissions NOT_RUN')))
    return 0 if report['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
