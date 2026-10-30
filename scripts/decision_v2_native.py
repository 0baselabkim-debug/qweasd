"""Finite native artifact verification. No KIS credentials, requests or operating writes."""
import base64
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.decision_evidence.collector_artifact import ARTIFACT,RUN,ZIP_SHA,ASOF,extract,inspect
from screener.decision_support.evidence import encoded,sha
REPO='0baselabkim-debug/qweasd'
REF='refs/heads/feat/decision-evidence-v2'
STATE='evidence/decision-v2-source-20261029'
CONTROL=Path('.github/trials/decision-v2-source-20261029.json')


def api(path,method='GET',value=None):
    args=['gh','api','repos/'+REPO+'/'+path,'--method',method]
    if value is not None:args+=['--input','-']
    p=subprocess.run(args,input=encoded(value) if value is not None else None,capture_output=True,timeout=60)
    if p.returncode:
        if b'HTTP 404' in p.stderr:raise FileNotFoundError(path)
        raise ValueError('official_GitHub_API_failed_'+str(p.returncode))
    return json.loads(p.stdout)


def publish(name,value):
    raw=encoded(value)
    if len(raw)>850000:raise ValueError('native_report_size_limit')
    if os.getenv('GH_TOKEN') and os.environ['GH_TOKEN'].encode() in raw:raise ValueError('credential_echo')
    try:api('git/ref/heads/'+STATE)
    except FileNotFoundError:
        tree=api('git/trees','POST',dict(tree=[dict(path='FORMAT.json',mode='100644',type='blob',content='{"scope":"independent-decision-v2-read-only-source-reports"}')]))
        commit=api('git/commits','POST',dict(message='Independent V2 evidence reports; no operating state',tree=tree['sha'],parents=[]))
        api('git/refs','POST',dict(ref='refs/heads/'+STATE,sha=commit['sha']))
    marker=api('contents/FORMAT.json?ref='+STATE)
    if json.loads(base64.b64decode(marker['content']))!={'scope':'independent-decision-v2-read-only-source-reports'}:
        raise ValueError('native_report_branch_marker_mismatch')
    api('contents/'+name,'PUT',dict(branch=STATE,message='Independent V2 artifact verification '+name,content=base64.b64encode(raw+b'\n').decode()))


def main():
    c=json.loads(CONTROL.read_bytes())
    if (os.getenv('GITHUB_REPOSITORY'),os.getenv('GITHUB_REF'),os.getenv('GITHUB_EVENT_NAME'),os.getenv('GITHUB_RUN_ATTEMPT'))!=(REPO,REF,'push','1'):
        raise ValueError('native_runtime_scope_mismatch')
    if (c['artifact_id'],c['source_run_id'],c['zip_sha256'],c['asof'],c['mode'])!=(ARTIFACT,RUN,ZIP_SHA,ASOF,'inspect'):
        raise ValueError('native_control_scope_mismatch')
    request=c['request_id']
    if not request.replace('-','').isalnum() or len(request)>80:raise ValueError('native_request_id_invalid')
    at=dt.datetime.now(dt.timezone.utc)
    if not dt.datetime.fromisoformat(c['not_before'])<=at<dt.datetime.fromisoformat(c['expires_at']):raise ValueError('native_window_closed')
    result=dict(request_id=request,run_id=os.environ['GITHUB_RUN_ID'],code_commit=os.environ['GITHUB_SHA'],
        market_HTTP=0,oauth_HTTP=0,source_run_id=RUN,artifact_id=ARTIFACT,verified_at=at.isoformat())
    publish(request+'-started.json',dict(result,status='STARTED'))
    folder=Path(tempfile.mkdtemp(prefix='decision-v2-source-'))
    try:
        meta=api('actions/artifacts/'+ARTIFACT)
        if (str(meta.get('workflow_run',{}).get('id')),meta.get('digest'),meta.get('expired'))!=(RUN,'sha256:'+ZIP_SHA,False):raise ValueError('official_artifact_metadata_mismatch')
        archive=folder/'source.zip'
        with archive.open('xb') as out:
            p=subprocess.run(['gh','api','repos/'+REPO+'/actions/artifacts/'+ARTIFACT+'/zip'],stdout=out,stderr=subprocess.PIPE,timeout=180)
        if p.returncode:raise ValueError('official_artifact_download_failed')
        proof=extract(archive,folder/'source')
        result.update(status='PASS_ARTIFACT_INSPECTION',artifact=meta,zip_verification=proof,inspection=inspect(folder/'source'))
    except Exception as exc:
        result.update(status='FAIL',reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
    (Path(os.environ['RUNNER_TEMP'])/'decision-v2-inspection.json').write_bytes(encoded(result))
    publish(request+'-complete.json',result)
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('# Independent V2 artifact inspection\n\n'+result['status']+'; KIS HTTP 0; OAuth 0\n')
    print(json.dumps({k:result[k] for k in ('status','run_id','market_HTTP','oauth_HTTP')}))
    return 0 if result['status']=='PASS_ARTIFACT_INSPECTION' else 1

if __name__=='__main__':raise SystemExit(main())
