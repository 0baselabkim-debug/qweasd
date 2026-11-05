"""One finite original-byte backup in existing repository Actions storage.

No market/OpenAI request, secret read, permission change or extraction. Original
ZIPs retain their official digests and each origin/quality status. Duplicate ZIP
bytes are stored once; aliases are preserved. This is not permanent storage.
"""
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.historical_v2.assets import file_sha
from screener.decision_support.evidence import encoded,sha


def acquire(args):
    source,root=args;target=root/'objects'/(source['zip_sha256']+'.zip')
    if not target.exists():
        partial=target.with_suffix('.partial-'+source['artifact_id'])
        with partial.open('xb') as out:
            result=subprocess.run(['gh','api','repos/0baselabkim-debug/qweasd/actions/artifacts/'+source['artifact_id']+'/zip'],stdout=out,stderr=subprocess.PIPE,timeout=180)
        if result.returncode:return dict(**source,status='BLOCKED_GITHUB_READ',market_HTTP=0)
        if partial.stat().st_size!=source['bytes'] or file_sha(partial)!=source['zip_sha256']:return dict(**source,status='FAIL_BYTE_DIGEST',market_HTTP=0)
        try:os.link(partial,target)
        except FileExistsError:pass
        partial.unlink()
    if file_sha(target)!=source['zip_sha256']:return dict(**source,status='FAIL_EXISTING_DIGEST',market_HTTP=0)
    return dict(**source,status='PASS_ORIGINAL_ZIP_BYTES',file='objects/'+target.name,data_approval='NOT_PROMOTED',market_HTTP=0)


def main():
    c=json.loads(Path('.github/recovery/historical-source-backup.json').read_bytes());at=dt.datetime.now(dt.timezone.utc)
    if (os.getenv('GITHUB_REPOSITORY')!='0baselabkim-debug/qweasd' or os.getenv('GITHUB_REF')!='refs/heads/feat/decision-evidence-v2' or os.getenv('GITHUB_RUN_ATTEMPT')!='1'
            or c['mode']!='PRESERVE_EXISTING_ORIGINAL_ZIP_BYTES' or not dt.datetime.fromisoformat(c['not_before'])<=at<dt.datetime.fromisoformat(c['expires_at'])):raise ValueError('finite_backup_control_rejected')
    data=Path('reports/historical-v2-recovery/backup-source-anchors.json').read_bytes()
    if sha(data)!=c['source_anchors_sha256']:raise ValueError('backup_source_anchors_changed')
    anchors=json.loads(data);sources=anchors['sources']
    review=Path('reports/historical-v2-recovery/credential-exposure-review.json').read_bytes()
    if sha(review)!=anchors['credential_review_sha256'] or json.loads(review).get('status')!='PASS_NO_UNRESOLVED_CREDENTIAL_CANDIDATES':raise ValueError('source_credential_exposure_review_required')
    metadata=subprocess.run(['gh','api','repos/0baselabkim-debug/qweasd'],capture_output=True,timeout=40)
    if metadata.returncode:raise ValueError('repository_access_not_verified')
    current_repository=json.loads(metadata.stdout)
    if current_repository['id']!=anchors['repository_id'] or (anchors['repository_private'] and not current_repository['private']):raise ValueError('repository_identity_or_access_widened')
    sources=sorted(sources,key=lambda s:s['original_expires_at'])
    if len(sources)!=274 or len({s['artifact_id'] for s in sources})!=274 or sum(s['bytes'] for s in sources)>4*1024**3:raise ValueError('finite_backup_population_or_size_changed')
    for s in sources:
        if not s['artifact_id'].isdigit() or not re.fullmatch('[0-9a-f]{64}',s['zip_sha256']):raise ValueError('backup_source_identity_invalid')
    root=Path(os.environ['RUNNER_TEMP'])/'historical-source-backup';(root/'objects').mkdir(parents=True)
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for r in executor.map(acquire,[(s,root) for s in sources]):
            results.append(r)
            if len(results)%20==0:print('Original ZIP byte checks completed:',len(results),flush=True)
            (root/'source-index.json').write_bytes(encoded(dict(results=results,completed=len(results),total=len(sources),market_HTTP=0)))
    failed=[r['artifact_id'] for r in results if r['status']!='PASS_ORIGINAL_ZIP_BYTES']
    summary=dict(status='PARTIAL' if failed else 'PASS_ORIGINAL_BYTES_PRESERVED',run_id=os.environ['GITHUB_RUN_ID'],code_commit=os.environ['GITHUB_SHA'],
        source_anchors_sha256=c['source_anchors_sha256'],source_count=len(results),passed=len(results)-len(failed),failed=failed,
        unique_zip_objects=len(list((root/'objects').glob('*.zip'))),zip_bytes=sum(p.stat().st_size for p in (root/'objects').glob('*.zip')),
        retention_days_requested=90,permanent_backup=False,repository_access='same existing repository artifact access; no new external service',
        repository_private=current_repository['private'],client_side_encryption=False,credential_review_sha256=anchors['credential_review_sha256'],
        provider_finality='NOT_CHANGED',market_HTTP=0,oauth_HTTP=0,OpenAI_HTTP=0)
    (root/'summary.json').write_bytes(encoded(summary))
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('Historical original-byte preservation\n\n```json\n'+json.dumps(summary,indent=2)+'\n```\n\nByte preservation is not market-data approval.\n')
    if failed:raise SystemExit(1)


if __name__=='__main__':main()
