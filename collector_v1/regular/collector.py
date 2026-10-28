"""Reuse the tested KIS algorithm; calendar/master and generation remain separate."""
import datetime as dt
import hashlib
import hmac
import json
import os
import time
from collections import Counter
from pathlib import Path
from ..core import Budget, Failure, Store, now, sha, encoded
from ..providers import Providers, LAYOUTS, Q, task
from ..validation import parse
from .. import kis_manual
from ..experiment import engine
from ..experiment.storage import events
from . import policy


class PreparationBudget(Budget):
    def __init__(self,store,context,guard):
        super().__init__(store,calls=4,cumulative=4,seconds=240,interval=policy.INTERVAL)
        self.context,self.guard=context,guard;self.run=context['run_id']
    def check(self):
        policy.window(self.context);self.guard();super().check()
    def before(self,source,endpoint,reason):
        allowed=source=='KIS_MASTER' and endpoint in {LAYOUTS[m]['url'] for m in ('KOSPI','KOSDAQ')}
        allowed |= source=='KIS' and endpoint in ('/oauth2/tokenP',Q+'chk-holiday')
        if not allowed:raise Failure('regular_preparation_endpoint_forbidden')
        return super().before(source,endpoint,reason)


class MemorySession:
    """Only same-process, same-credential token transfer; never persisted."""
    def __init__(self,api):self.api=api;self.binding=self._binding()
    def __repr__(self):return '<MemorySession redacted>'
    @staticmethod
    def _binding():return hashlib.sha256(encoded([os.getenv('KIS_APP_KEY'),os.getenv('KIS_APP_SECRET')])).digest()
    def transfer(self,target):
        if not self.api.token or not hmac.compare_digest(self.binding,self._binding()):raise Failure('regular_memory_session_binding_changed')
        target.token=self.api.token


def prepare(folder,context,guard,*,opener=None,reference=None):
    store=Store(folder);budget=PreparationBudget(store,context,guard);api=Providers(store,budget,opener)
    if hasattr(guard,'register_api'):guard.register_api(api)
    if any(p.name!='writer.lock' for p in store.root.iterdir()):raise Failure('regular_preparation_state_cannot_reset')
    evidence=api.fetch(task('calendar',context['asof']))
    calendar=policy.calendar(store.raw(evidence),context,evidence,reference)
    store.write('calendar-evidence.json',evidence)
    store.write('calendar.json',calendar)
    if not calendar['open']:return dict(status='SKIPPED_CLOSED',calendar=calendar),None,budget,api
    universe=[];raw_evidence=[]
    for market in ('KOSPI','KOSDAQ'):
        e=api.fetch(task('master',context['asof'],market=market));universe.extend(parse(store.raw(e),e));raw_evidence.append(e)
    selected=policy.eligible(universe)
    baseline=2*len(selected)+3 # conservative existing full budget, including possible auth
    remaining=(policy.timestamp(context['expires_at'])-policy.utcnow()).total_seconds()
    if (baseline-1)*policy.INTERVAL+60>remaining:raise Failure('population_cannot_fit_remaining_execution_window')
    report=dict(status='PASS',asof=context['asof'],master_rows=len(universe),eligible=len(selected),excluded=len(universe)-len(selected),
        population_sha256=sha(encoded(universe)),evidence=raw_evidence,calendar=calendar,
        exclusion_reason_counts=dict(Counter(x for r in universe if not r['eligible'] for x in r['exclusion_reasons'])),
        full_http_conservative_baseline=baseline,full_http_limit=policy.FULL_HTTP,prep_http_limit=4,
        planned_total_with_same_process_token=2*len(selected)+6,physical_http=budget.used)
    store.write('universe.json',universe);store.write('master.json',report)
    return report,universe,budget,api


def run(folder,context,guard,*,opener=None,reference=None,phase=None):
    folder=Path(folder);phase=phase or (lambda _:None)
    phase('PREPARING')
    master,universe,budget,api=prepare(folder/'preparation',context,guard,opener=opener,reference=reference)
    if master['status']=='SKIPPED_CLOSED':return master,api
    policy.window(context);guard()
    # Keep 1.05s across the preparation/collection boundary too.
    delay=max(0,policy.INTERVAL-(time.monotonic()-budget.last));time.sleep(delay)
    policy.window(context);guard();phase('COLLECTING')
    r=kis_manual.run(folder/'collection',policy.control(context),universe,context['run_id'],opener=opener,
        policy=policy.RunPolicy(context,guard),memory_session=MemorySession(api))
    r.update(master=master,preparation_http=budget.used,total_physical_http=budget.used+r['physical_http'],
        generation_id=context['generation_id'],source_scope='KRX stock code J; index code U; NXT not observed',
        preparation_to_collection_wait_seconds=delay,collection_complete=r['status']=='PASS')
    verify(folder,context,r)
    r['internal_validation']='PASS' if r['status']=='PASS' else 'FAIL'
    r['status']='INTERNAL_PASS' if r['status']=='PASS' else 'FAILED'
    return r,api


def verify(folder,context,report):
    from ..core import Store
    import duckdb
    folder=Path(folder);prep=Store(folder/'preparation');master=report['master'];universe=prep.read('universe.json')
    ce=prep.read('calendar-evidence.json')
    if policy.calendar(prep.raw(ce),context,ce,master['calendar'].get('official_reference'))!=master['calendar']:
        raise Failure('regular_calendar_raw_mismatch')
    prep_ledger=prep.read('requests.json',[])
    if len(prep_ledger)!=4 or any(e.get('status')!='received' or e.get('http')!=200 for e in prep_ledger):raise Failure('regular_preparation_ledger_mismatch')
    recovered=[]
    for e in master['evidence']:recovered.extend(parse(prep.raw(e),e))
    if universe!=recovered or sha(encoded(universe))!=report['population_sha256']:raise Failure('regular_master_raw_mismatch')
    selected=policy.eligible(universe);expected={r['symbol']:r['market'] for r in selected}
    if report['target']!=len(expected) or report['target_symbols']!=sorted(expected):raise Failure('regular_population_mismatch')
    rows=report['rows'];store=Store(folder/'collection');cache=engine.replay(store,1,asof=context['asof'],generation_id=context['generation_id'])
    if len({r['symbol'] for r in rows})!=len(rows):raise Failure('regular_duplicate_stock_row')
    for row in rows:
        if expected.get(row['symbol'])!=row['market'] or row['asof']!=context['asof']:raise Failure('regular_stock_identity_mismatch')
        if engine.qualify_kis(row,cache,store,1,asof=context['asof'],generation_id=context['generation_id'])!=row:raise Failure('regular_row_raw_mismatch')
    entries=events(store.root/'http.jsonl');starts=[e for e in entries if e['event']=='started'];ends=[e for e in entries if e['event']=='finished']
    if len(starts)!=report['physical_http'] or len(starts)>policy.FULL_HTTP or [e['sequence'] for e in starts]!=list(range(1,len(starts)+1)):
        raise Failure('regular_http_ledger_mismatch')
    if any(e.get('generation_id')!=context['generation_id'] for e in starts):raise Failure('regular_http_generation_mismatch')
    if len({e['sequence'] for e in ends})!=len(ends) or any(e['sequence'] not in {s['sequence'] for s in starts} for e in ends):raise Failure('regular_http_end_sequence_mismatch')
    good=report['status'] in ('PASS','INTERNAL_PASS')
    if good and (len(rows)!=len(expected) or len(starts)!=len(ends) or report.get('db_json_match') is not True or len(report.get('indices',[]))!=2):raise Failure('regular_incomplete_marked_PASS')
    indices=[]
    for market in ('KOSPI','KOSDAQ'):
        e=cache.get(engine.identity(task('index',context['asof'],round=1,market=market,generation_id=context['generation_id'])))
        if e:indices.append(dict(**parse(store.raw(e),e)[0],observed_at=e['collected_at'],source_hash=e['source_hash']))
    if report.get('indices')!=indices:raise Failure('regular_index_raw_mismatch')
    path=(store.root/report['database_file']).resolve()
    if not path.is_relative_to(store.root):raise Failure('regular_unsafe_database_path')
    with duckdb.connect(str(path),read_only=True) as db:saved={s:json.loads(p) for s,p in db.execute('SELECT symbol,payload FROM observations').fetchall()}
    if saved!={r['symbol']:r for r in rows}:raise Failure('regular_DB_JSON_mismatch')
