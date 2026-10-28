"""Read-only, exact-date handoff; never presents a prior date as today's data."""
import argparse
import datetime as dt
import json
from ..core import Failure
from .github import GitHub,APIError
from . import policy


def by_date(client,asof):
    dt.date.fromisoformat(asof)
    marker,_=client.contents('FORMAT.json')
    if marker!=policy.FORMAT:raise Failure('regular_state_branch_format_mismatch')
    try:doc,_=client.contents('ledger/'+asof[:4]+'.json')
    except APIError as e:
        if e.status!=404:raise
        return dict(status='NOT_READY_FOR_REQUESTED_DATE',requested_asof=asof)
    r=doc.get('slots',{}).get(asof+'/2030')
    if not r or r.get('status')!='COMPLETE_PROVISIONAL':
        return dict(status='NOT_READY_FOR_REQUESTED_DATE',requested_asof=asof,collector_status=r.get('status') if r else 'NO_RUN')
    c=r['context'];s=r['summary'];a=r['artifact']
    if c['asof']!=asof or s.get('internal_validation')!='PASS' or not a.get('digest') or type(s.get('target')) is not int or s['target']<=0 or s['target']!=s.get('kis_validated'):
        raise Failure('regular_handoff_inconsistent')
    if not a.get('expires_at') or policy.timestamp(a['expires_at'])<=policy.utcnow():
        return dict(status='ARTIFACT_EXPIRED_OR_EXPIRATION_UNVERIFIED',requested_asof=asof,generation_id=c['generation_id'])
    if s.get('provider_finality')!='UNVERIFIED' or s.get('official_price_status')!='PENDING_KRX':raise Failure('regular_handoff_finality_changed')
    return dict(status='READY_INTERNAL_PROVISIONAL',asof=asof,slot='2030',generation_id=c['generation_id'],
        run_id=c['run_id'],code_commit=c['code_commit'],internal_validation='PASS',target=s['target'],
        provider_finality='UNVERIFIED',official_price_status='PENDING_KRX',artifact=a,
        artifact_bytes_verified_by_consumer=False,
        dashboard_publication='NOT_PUBLISHED',application_reflection='NOT_UPDATED')


def main():
    p=argparse.ArgumentParser();p.add_argument('--asof',required=True);args=p.parse_args()
    try:
        r=by_date(GitHub(),args.asof);print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['status']=='READY_INTERNAL_PROVISIONAL' else 2
    except (Failure,ValueError):print(json.dumps({'status':'BLOCKED_HANDOFF_UNVERIFIED'}));return 1


if __name__=='__main__':raise SystemExit(main())
