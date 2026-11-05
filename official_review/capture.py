"""One bounded official KRX review; no KIS imports, tokens or ledger writes."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

def encoded(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
def sha(b):return hashlib.sha256(b).hexdigest()
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def capture(root,control,credential,*,opener=None,pause=time.sleep):
    if control!={'asof':'2026-11-02','markets':['KOSPI','KOSDAQ'],'max_http':2,'request_id':'official-review-20261102-v1','purpose':'OFFICIAL_SIX_FIELD_READ_ONLY_REVIEW'}:
        raise ValueError('unapproved_capture_control')
    root=Path(root)
    if root.exists():raise ValueError('new_output_required')
    root.mkdir(parents=True);journal=[];sources=[];status='NOT_RUN'
    try:
        if not credential:raise ValueError('existing_KRX_credential_unavailable')
        fetch=opener or urllib.request.build_opener(NoRedirect()).open
        for market in control['markets']:
            if journal:pause(1.05)
            endpoint='/svc/apis/sto/'+('stk' if market=='KOSPI' else 'ksq')+'_bydd_trd'
            url='https://data-dbg.krx.co.kr'+endpoint+'?basDd=20261102'
            entry=dict(sequence=len(journal)+1,url=url,market=market,asof=control['asof'],started_at=now(),status='ATTEMPTED');journal.append(entry)
            (root/'requests.json').write_bytes(encoded(journal))
            try:
                with fetch(urllib.request.Request(url,headers={'AUTH_KEY':credential}),timeout=25) as response:
                    http=response.status;raw=response.read(16000001)
            except urllib.error.HTTPError as error:
                http=error.code;raw=error.read(16000001)
            except Exception:
                entry.update(status='TRANSPORT_OUTCOME_UNKNOWN',completed_at=now());raise ValueError('KRX_transport_outcome_unknown_no_retry') from None
            entry.update(http=http,completed_at=now())
            if len(raw)>16000000:raise ValueError('KRX_response_too_large')
            if credential.encode() in raw:raise ValueError('credential_echo_not_preserved')
            h=sha(raw);name=h+'.bin';(root/name).write_bytes(raw)
            entry.update(source_hash=h,file=name,status='RECEIVED')
            if http!=200:raise ValueError('KRX_HTTP_'+str(http))
            try:obj=json.loads(raw)
            except ValueError:raise ValueError('KRX_non_JSON_response') from None
            rows=obj.get('OutBlock_1')
            if not isinstance(rows,list):raise ValueError('KRX_missing_OutBlock_1')
            state='OFFICIAL_RESPONSE_RECEIVED' if rows else 'EMPTY_RESPONSE_CAUSE_NOT_IDENTIFIED'
            sources.append(dict(asof=control['asof'],market=market,url=url,http=http,observed_at=entry['completed_at'],raw_file=name,raw_sha256=h,row_count=len(rows),status=state))
        status='OFFICIAL_RESPONSES_PRESERVED' if all(s['row_count'] for s in sources) else 'PENDING_KRX_EMPTY_RESPONSE'
    except ValueError as error:status=str(error)
    finally:
        (root/'requests.json').write_bytes(encoded(journal))
        report=dict(status=status,asof=control['asof'],sources=sources,physical_KRX_HTTP=len(journal),max_http=2,
            credential_present=bool(credential),run_id=os.environ.get('GITHUB_RUN_ID'),code_commit=os.environ.get('GITHUB_SHA'),
            actual_KIS_HTTP=0,actual_OAuth=0,operating_writes=0)
        (root/'report.json').write_bytes(encoded(report));(root/'control.json').write_bytes(encoded(control))
        manifest={p.name:sha(p.read_bytes()) for p in root.iterdir() if p.is_file()}
        if credential and any(credential.encode() in (root/n).read_bytes() for n in manifest):raise ValueError('secret_scan_failed_no_upload')
        (root/'manifest.json').write_bytes(encoded(dict(files=manifest,asof=control['asof'],run_id=os.environ.get('GITHUB_RUN_ID'))))
    return report

if __name__=='__main__':
    if os.environ.get('GITHUB_REPOSITORY')!='0baselabkim-debug/qweasd' or os.environ.get('GITHUB_REF')!='refs/heads/feat/krx-20261102-official-review' or os.environ.get('GITHUB_RUN_ATTEMPT')!='1':
        raise SystemExit('source_actor_or_attempt_not_approved')
    control=json.loads(Path('.github/trials/krx-20261102-official-review.json').read_bytes())
    report=capture(Path(os.environ['RUNNER_TEMP'])/'krx-official-review',control,os.environ.get('KRX_API_KEY'))
    print(json.dumps(report,ensure_ascii=False))
