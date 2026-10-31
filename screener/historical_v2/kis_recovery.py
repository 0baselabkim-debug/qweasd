"""One failed symbol only, anchored to the preserved failed generation's budget."""
import datetime as dt
import json
import os
from pathlib import Path
import urllib.request
import urllib.error
import subprocess
import time
from collector_v1.core import Store,encoded,sha,now,Failure
from collector_v1.providers import Providers,task,Q
from collector_v1.experiment.storage import events,append as journal_append
from collector_v1.experiment.engine import qualify_kis,identity
from kis_shared.runtime import configured
from .assets import extract_verified,file_sha
from .collector import inspect

SOURCE_ARTIFACT='12983267630'
SOURCE_ZIP_SHA='590847b13fa83a0634290a2792114b71b5efc9306697a51d5d8a8c31e2f81b74'
ASOF='2026-10-30'
SYMBOL='002690'


class RecoveryBudget:
    def __init__(self,store,previous=4693,validator=None,*,clock=time.monotonic,sleep=time.sleep):
        if previous!=4693:raise ValueError('previous_physical_budget_anchor_mismatch')
        self.store=store;self.previous=previous;self.used=0;self.finished=set();self.endpoints=set();self.validator=validator
        self.clock=clock;self.sleep=sleep;self.last_dispatch=None
        self.last_opener_return=None;self.dispatched=set()
        self.store.write('parent-budget.json',dict(source_artifact=SOURCE_ARTIFACT,source_zip_sha256=SOURCE_ZIP_SHA,
            original_physical_http=previous,original_max_total_http=5600,new_max_http=3,reset=False))
    def before(self,source,endpoint,reason):
        if self.validator:self.validator()
        if source!='KIS' or endpoint not in ('/oauth2/tokenP',Q+'investor-trade-by-stock-daily',Q+'inquire-daily-itemchartprice'):
            raise Failure('recovery_endpoint_not_authorized')
        if self.used>=3 or self.previous+self.used>=5600:raise Failure('recovery_physical_budget_exhausted')
        if endpoint in self.endpoints:raise Failure('recovery_duplicate_endpoint_retry_forbidden')
        if self.last_dispatch is not None:self.sleep(max(0,1.05-(self.clock()-self.last_dispatch)))
        if self.validator:self.validator()
        if self.last_dispatch is not None and self.clock()-self.last_dispatch<1.05-1e-9:raise Failure('recovery_physical_interval_not_elapsed')
        self.last_dispatch=self.clock()
        self.endpoints.add(endpoint)
        self.used+=1
        e=dict(event='started',sequence=self.used,cumulative_sequence=self.previous+self.used,endpoint=endpoint,reason=reason,started_at=now())
        journal_append(self.store.root/'http.jsonl',e);return e
    def finish(self,entry,status,**metadata):
        if entry['sequence'] in self.finished:raise Failure('duplicate_recovery_http_finish')
        self.finished.add(entry['sequence']);journal_append(self.store.root/'http.jsonl',dict(event='finished',sequence=entry['sequence'],status=status,completed_at=now(),**metadata))
    def dispatch(self,opener,request,timeout):
        # Waiting from the previous opener's RETURN is conservative even when
        # shared Git reservations or journal fsync happen at different speeds.
        # The old recovery attempt's sub-1.05s gap is not relabelled as fixed.
        if self.last_opener_return is not None:
            self.sleep(max(0,1.05-(self.clock()-self.last_opener_return)))
        if self.validator:self.validator()
        if self.used in self.dispatched:raise Failure('duplicate_recovery_dispatch')
        self.dispatched.add(self.used);at=now()
        try:return opener(request,timeout=timeout)
        finally:
            self.last_opener_return=self.clock()
            journal_append(self.store.root/'http.jsonl',dict(event='dispatched',sequence=self.used,dispatched_at=at,opener_returned_at=now()))


def control(c,at=None):
    at=at or dt.datetime.now(dt.timezone.utc)
    begin,end=(dt.datetime.fromisoformat(c[k]) for k in ('not_before','expires_at'))
    if (c.get('approval')!='USER_APPROVED_MINIMAL_HISTORICAL_RECOVERY' or c.get('symbol')!=SYMBOL or c.get('asof')!=ASOF or c.get('max_http')!=3
            or begin.tzinfo is None or end.tzinfo is None or not 0<(end-begin).total_seconds()<=600 or not begin<=at<end):raise ValueError('minimal_recovery_control_invalid')
    from scripts.collect_decision_prices import guard
    guard()
    if os.getenv('GITHUB_REF')!='refs/heads/feat/decision-evidence-v2' or os.getenv('GITHUB_RUN_ATTEMPT')!='1':raise ValueError('minimal_recovery_scope_or_rerun_rejected')


def main(control_path):
    c=json.loads(Path(control_path).read_bytes());control(c)
    from .krx_recovery import operating_clear
    operating_clear()
    output=Path(os.environ['RUNNER_TEMP'])/'kis-minimal-recovery';output.mkdir()
    archive=output/'parent.zip'
    # Official CLI handles authenticated artifact redirects; credentials are
    # not forwarded by a custom cross-host urllib redirect handler.
    with archive.open('xb') as f:
        downloaded=subprocess.run(['gh','api','repos/0baselabkim-debug/qweasd/actions/artifacts/'+SOURCE_ARTIFACT+'/zip'],stdout=f,stderr=subprocess.PIPE,timeout=180)
    if downloaded.returncode:raise ValueError('official_parent_artifact_download_failed')
    source=output/'parent';extract_verified(archive,SOURCE_ZIP_SHA,source)
    proof=dict(zip_sha256=SOURCE_ZIP_SHA,manifest_sha256=sha((source/'manifest.json').read_bytes()),artifact_id=SOURCE_ARTIFACT,
        run_id='41052557610',report_path='kis-regular-20261030-2030/collection/report.json',universe_path='kis-regular-20261030-2030/preparation/universe.json')
    v=inspect(source,proof);old=v['report']
    if old['validation_failed_symbols']!=[SYMBOL] or v['unresolved_http']!=0 or v['physical_http']+4!=4693:raise ValueError('failed_symbol_or_parent_budget_mismatch')
    store=Store(output/'recovery');budget=RecoveryBudget(store,validator=lambda:control(c))
    generation='kis-minimal-20261030-'+os.environ['GITHUB_RUN_ID'];market=v['selected'][SYMBOL]['market'];cache={}
    result=dict(schema=1,asof=ASOF,symbol=SYMBOL,market=market,generation_id=generation,parent=proof,parent_failed_status=old['status'],
                parent_physical_http=4693,max_new_http=3,max_cumulative_http=5600,provider_finality='UNVERIFIED',official_price_status='PENDING_KRX',
                status='STARTED',run_id=os.environ['GITHUB_RUN_ID'],code_commit=os.environ['GITHUB_SHA'],source_original_unchanged=True,automatic_retries=0)
    try:
        control(c);operating_clear()
        # Existing enrolled session protects OAuth and BOTH requests; existing
        # conservative token lifetime, spacing, owner and policy checks apply.
        with configured('.github/config/kis-shared-account.json'):
            api=Providers(store,budget)
            original_opener=api.opener
            def preserve_failure(request,timeout):
                try:return budget.dispatch(original_opener,request,timeout)
                except urllib.error.HTTPError as error:
                    if not request.full_url.endswith('/oauth2/tokenP'):
                        try:body=error.read(1000000)
                        finally:error.close()
                        if not (api.token and api.token.encode() in body):
                            store.preserve(body,dict(source='KIS',source_run=os.environ['GITHUB_RUN_ID'],collected_at=now(),http=error.code,
                                endpoint=request.full_url.split('?')[0].split(':9443')[-1],failure_response=True))
                    raise
            api.opener=preserve_failure  # keep _shared_required=True from the live constructor
            for kind in ('flow','price'):
                control(c)
                t=task(kind,ASOF,round=1,symbol=SYMBOL,market=market,generation_id=generation)
                if kind=='flow':
                    endpoint=Q+'investor-trade-by-stock-daily';params=dict(FID_COND_MRKT_DIV_CODE='J',FID_INPUT_ISCD=SYMBOL,FID_INPUT_DATE_1='20261030',FID_ORG_ADJ_PRC='',FID_ETC_CLS_CODE='');tr='FHPTJ04160001'
                else:
                    endpoint=Q+'inquire-daily-itemchartprice';params=dict(FID_COND_MRKT_DIV_CODE='J',FID_INPUT_ISCD=SYMBOL,FID_INPUT_DATE_1='20260831',FID_INPUT_DATE_2='20261030',FID_PERIOD_DIV_CODE='D',FID_ORG_ADJ_PRC='1');tr='FHKST03010100'
                raw=api.kis(endpoint,params,tr)
                e=store.preserve(raw,dict(task=t,source='KIS',endpoint=endpoint,params=params,collected_at=now(),source_run=os.environ['GITHUB_RUN_ID']))
                journal_append(store.root/'tasks.jsonl',dict(task=t,status='received',evidence=e));cache[identity(t)]=e
                obj=json.loads(raw)
                if obj.get('rt_cd')!='0':raise ValueError('provider_response_rejected')
            api.token=None
        # Validate after the synchronous clean session has discarded its token
        # and released ownership. A schema/data conflict is not a new HTTP retry.
        row=qualify_kis(v['selected'][SYMBOL],cache,store,1,asof=ASOF,generation_id=generation)
        result.update(status='PASS_MINIMAL_RECOVERY',row=row,previous_successes_requeried=0)
    except Exception as error:
        result.update(status='FAIL',reason=getattr(error,'code',str(error) if isinstance(error,ValueError) else type(error).__name__))
    finally:
        result.update(new_physical_http=len(budget.dispatched),reserved_physical_budget=budget.used,
            unknown_or_undispatched_reservations=budget.used-len(budget.dispatched),cumulative_physical_http=4693+budget.used,completed_at=now())
        store.write('report.json',result)
        inventory={str(p.relative_to(output)):sha(p.read_bytes()) for p in output.rglob('*') if p.is_file()}
        (output/'manifest.json').write_bytes(encoded(dict(files=inventory,asof=ASOF,generation_id=generation)))
        if os.getenv('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('Minimal historical KIS recovery\n\n'+result['status']+'; new HTTP '+str(budget.used)+'; original full-market generation remains unchanged.\n')
        print(json.dumps({k:v for k,v in result.items() if k not in ('row','parent')},ensure_ascii=False))
    if result['status']!='PASS_MINIMAL_RECOVERY':raise SystemExit(1)
