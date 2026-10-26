"""Offline KRX incident audit and a two-request plan. No network implementation."""
import argparse
import json
from pathlib import Path
from ..core import Failure, Store, encoded, sha
from . import runtime
from .storage import events
RUN_ID=40311038702
ARTIFACT_ID=12644378705
ZIP_SHA='466dcdb27bc838700643266b8d4f77f1110d124e02335d107904e9ea828a24a8'


def diagnostic_plan():
    return dict(status='NOT_APPROVED_NOT_RUN',market='KOSPI',method='GET',
                credential_binding='KRX_API_KEY',header_names=['AUTH_KEY'],
                max_physical_http=2,automatic_retries=0,parallelism=1,stop_on_first_transport_or_authorization_error=True,
                requires_separate_explicit_execution_approval=True,
                requests=[dict(role=role,url='https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd',params={'basDd':day}) for role,day in [('known_success_control','20261015'),('incident_target','20261026')]],
                interpretation={
                    'control_nonempty_target_empty':'Supports date-specific unavailability; does not establish publication schedule or prove its sole cause',
                    'both_nonempty':'Current access works; original incident cause remains retrospective and unresolved',
                    'both_empty':'Cannot distinguish entitlement, request policy, missing data, or provider issue',
                    '401_or_403':'Check account/service approval and denial origin; no bypass or retry',
                    '429_or_5xx_or_timeout':'Stop at the first transport error and preserve it; provider incident requires independent evidence'})


def audit_archive(archive, target):
    """Only a locally supplied original archive with the pinned digest is accepted."""
    target=Path(target)
    runtime.safe_extract(Path(archive), target, ZIP_SHA)
    manifest=runtime.verify(target)
    if str(manifest['run_id'])!=str(RUN_ID) or manifest['round']!=1 or manifest['asof']!='2026-10-26':
        raise Failure('incident_artifact_identity_mismatch')
    store=Store(target)
    started=[e for e in events(target/'http.jsonl') if e['event']=='started']
    finished=[e for e in events(target/'http.jsonl') if e['event']=='finished']
    if len(started)!=9 or len(finished)!=9 or any(e.get('http')!=200 for e in finished):
        raise Failure('incident_request_ledger_mismatch')
    observations=[]
    for p in (target/'observations').glob('*.json'):
        entry=json.loads(p.read_bytes());raw=store.raw(entry)
        if entry['task']['kind']=='krx':
            if entry['endpoint']!='/svc/apis/sto/stk_bydd_trd' or entry['params']!={'basDd':'20261026'} or entry['task']['market']!='KOSPI':
                raise Failure('incident_request_parameters_differ_from_report')
            body=json.loads(raw)
            if body!={'OutBlock_1':[]}:raise Failure('incident_response_differ_from_report')
            observations.append(dict(collected_at=entry['collected_at'],params=entry['params'],endpoint=entry['endpoint'],source_hash=entry['source_hash'],observation_id=sha(encoded(entry)),response_shape='OutBlock_1=[]'))
    if len(observations)!=6:raise Failure('incident_krx_observations_incomplete')
    return dict(run_id=RUN_ID,artifact_id=ARTIFACT_ID,zip_sha256=ZIP_SHA,manifest_files=len(manifest['files']),physical_http=9,krx_empty_observations=observations,root_cause='UNDETERMINED',new_provider_http=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['plan','audit-local-archive'])
    parser.add_argument('--archive');parser.add_argument('--extract-to')
    args=parser.parse_args()
    if args.mode=='plan':result=diagnostic_plan()
    else:
        if not args.archive or not args.extract_to:parser.error('--archive and --extract-to are required')
        result=audit_archive(args.archive,args.extract_to)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
