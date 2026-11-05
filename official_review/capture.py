"""Bounded November4 KRX acquisition adapted from PR3; no KIS imports or writes."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

REPO='0baselabkim-debug/qweasd'
BRANCH='feat/krx-official-nov4-review'
WORKFLOW='krx-official-nov4-review.yml'
REQUEST='krx-20261104-20261105-51940e8f'
PLAN=[['2026-10-29','KOSPI'],['2026-11-04','KOSPI'],['2026-10-29','KOSDAQ'],['2026-11-04','KOSDAQ']]
CONTROL=dict(asof='2026-11-04',control_asof='2026-10-29',pairs=PLAN,max_http=4,
             request_id=REQUEST,purpose='OFFICIAL_SIX_FIELD_READ_ONLY_REVIEW')
def encoded(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def save(path,value):
    with Path(path).open('wb') as f:f.write(encoded(value));f.flush();os.fsync(f.fileno())
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def actor(env,runs):
    if (env.get('GITHUB_REPOSITORY'),env.get('GITHUB_REF'),env.get('GITHUB_EVENT_NAME'),env.get('GITHUB_RUN_ATTEMPT'))!=(REPO,'refs/heads/'+BRANCH,'push','1'):
        raise ValueError('source_actor_or_attempt_not_approved')
    # All prior runs, including cancelled/failed runs, consume this one-shot grant.
    if runs.get('total_count')!=1 or len(runs.get('workflow_runs',[]))!=1:
        raise ValueError('one_shot_run_history_not_unique')
    run=runs['workflow_runs'][0]
    if str(run['id'])!=env.get('GITHUB_RUN_ID') or run['head_sha']!=env.get('GITHUB_SHA') or run['head_branch']!=BRANCH:
        raise ValueError('one_shot_current_run_identity_mismatch')

def rows_status(raw,day,market):
    try:obj=json.loads(raw)
    except ValueError:raise ValueError('KRX_non_JSON_response') from None
    rows=obj.get('OutBlock_1') if isinstance(obj,dict) else None
    if not isinstance(rows,list):raise ValueError('KRX_missing_OutBlock_1')
    seen=set()
    for row in rows:
        code=row.get('ISU_CD') if isinstance(row,dict) else None
        if not isinstance(code,str) or len(code)!=6 or code in seen or row.get('BAS_DD')!=day.replace('-','') or row.get('MKT_NM') not in (market,'KOSDAQ GLOBAL' if market=='KOSDAQ' else market):
            raise ValueError('KRX_date_market_code_or_duplicate_mismatch')
        seen.add(code)
    return rows

def capture(root,control,credential,*,opener,pause=time.sleep):
    if control!=CONTROL:raise ValueError('unapproved_capture_control')
    root=Path(root)
    if root.exists():raise ValueError('new_output_required_no_budget_reset')
    root.mkdir(parents=True);journal=[];sources=[];status='NOT_RUN'
    try:
        if not credential:raise ValueError('existing_KRX_credential_unavailable')
        for day,market in PLAN:
            if len(journal)>=control['max_http']:raise ValueError('physical_budget_exceeded')
            if journal:pause(1.05)
            endpoint='/svc/apis/sto/'+('stk' if market=='KOSPI' else 'ksq')+'_bydd_trd'
            url='https://data-dbg.krx.co.kr'+endpoint+'?basDd='+day.replace('-','')
            entry=dict(sequence=len(journal)+1,url=url,market=market,asof=day,started_at=now(),status='ATTEMPTED')
            journal.append(entry);save(root/'requests.json',journal)
            try:
                with opener(urllib.request.Request(url,headers={'AUTH_KEY':credential}),timeout=25) as response:
                    http=response.status;raw=response.read(16000001)
            except urllib.error.HTTPError as error:
                http=error.code;raw=error.read(16000001)
            except Exception:
                entry.update(status='TRANSPORT_OUTCOME_UNKNOWN',completed_at=now())
                raise ValueError('KRX_transport_outcome_unknown_no_retry') from None
            entry.update(http=http,completed_at=now())
            if len(raw)>16000000:raise ValueError('KRX_response_too_large')
            if credential.encode() in raw:raise ValueError('credential_echo_not_preserved')
            digest=sha(raw);name=digest+'.bin'
            if not (root/name).exists():
                with (root/name).open('xb') as f:f.write(raw)
            entry.update(source_hash=digest,file=name,status='RECEIVED')
            if http!=200:raise ValueError('KRX_HTTP_'+str(http))
            rows=rows_status(raw,day,market)
            sources.append(dict(asof=day,market=market,url=url,http=http,observed_at=entry['completed_at'],
                raw_file=name,raw_sha256=digest,row_count=len(rows),
                status='OFFICIAL_RESPONSE_RECEIVED_NOT_YET_COMPARED' if rows else 'EMPTY_RESPONSE_CAUSE_NOT_IDENTIFIED'))
        targets=[x for x in sources if x['asof']==control['asof']]
        status='TARGET_RESPONSES_PRESERVED_NOT_YET_COMPARED' if len(targets)==2 and all(x['row_count'] for x in targets) else 'PENDING_KRX_EMPTY_RESPONSE'
    except ValueError as error:status=str(error)
    finally:
        save(root/'requests.json',journal)
        report=dict(status=status,asof=control['asof'],control_asof=control['control_asof'],sources=sources,
            physical_KRX_HTTP=len(journal),max_http=4,automatic_retries=0,credential_present=bool(credential),
            request_id=REQUEST,run_id=os.environ.get('GITHUB_RUN_ID'),code_commit=os.environ.get('GITHUB_SHA'),
            actual_KIS_HTTP=0,actual_OAuth_HTTP=0,operating_writes=0,official_prices_approved=0)
        save(root/'report.json',report);save(root/'control.json',control)
        files={p.name:sha(p.read_bytes()) for p in root.iterdir() if p.is_file()}
        if credential and any(credential.encode() in (root/n).read_bytes() for n in files):raise ValueError('secret_scan_failed_no_upload')
        save(root/'manifest.json',dict(files=files,asof=control['asof'],run_id=os.environ.get('GITHUB_RUN_ID')))
    return report

def main():
    # The GitHub credential is used only for official read-only run-history checks.
    if os.environ.get('GITHUB_REF')!='refs/heads/'+BRANCH:raise SystemExit('feature_branch_only')
    url=f'https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?branch={BRANCH}&per_page=100'
    request=urllib.request.Request(url,headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=25) as response:runs=json.load(response)
    actor(os.environ,runs)
    control=json.loads(Path('.github/trials/krx-official-nov4-review.json').read_bytes())
    report=capture(Path(os.environ['RUNNER_TEMP'])/'krx-official-nov4-review',control,os.environ.get('KRX_API_KEY'),
                   opener=urllib.request.build_opener(NoRedirect()).open)
    print(json.dumps(report,ensure_ascii=False))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('Official KRX capture, not price approval\n\n```json\n'+json.dumps(report,indent=2)+'\n```\n')

if __name__=='__main__':main()
