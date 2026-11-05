"""Offline reproduction of the completed 274-original backup byte verification.

Usage: python verify_original_backup.py BACKUP.zip
No download, credential, source write, extraction or provider request.
"""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

EXPECTED='95524a1db242e117db4e2950cd1bdf7a8a0ab200221774312f321dcda7956242'

def check(archive):
    h=hashlib.sha256()
    with Path(archive).open('rb') as source:
        for b in iter(lambda:source.read(1048576),b''):h.update(b)
    if h.hexdigest()!=EXPECTED:raise ValueError('outer_backup_digest_mismatch')
    with zipfile.ZipFile(archive) as z:
        index=json.loads(z.read('source-index.json'));summary=json.loads(z.read('summary.json'))
        if index['completed']!=274 or index['total']!=274 or summary['passed']!=274 or summary['failed']:
            raise ValueError('backup_incomplete')
        seen=set()
        for row in index['results']:
            if row['artifact_id'] in seen:raise ValueError('duplicate_origin')
            seen.add(row['artifact_id']);h=hashlib.sha256();size=0
            with z.open(row['file']) as original:
                for b in iter(lambda:original.read(1048576),b''):h.update(b);size+=len(b)
            if h.hexdigest()!=row['zip_sha256'] or size!=row['bytes']:
                raise ValueError('original_zip_digest_or_size_mismatch')
    return dict(status='PASS_ALL_274_ORIGINAL_ZIPS',originals=len(seen),market_HTTP=0)

if __name__=='__main__':print(json.dumps(check(sys.argv[1])))
