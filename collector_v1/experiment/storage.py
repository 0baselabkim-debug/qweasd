"""Append-only HTTP and task journals. Reservations survive ordinary process failure."""
import gzip,json,os,time
from pathlib import Path
from ..core import Failure,Stop,Store,atomic,encoded,now,sha
from .policy import INTERVAL,PROBE_LIMIT,ROUND_LIMIT,window


def events(path):
    if not path.exists():return []
    raw=path.read_bytes()
    if raw and not raw.endswith(b'\n'):raise Failure('incomplete_journal_requires_review')
    return [json.loads(line) for line in raw.splitlines()]


def append(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('ab') as f:f.write(encoded(value)+b'\n');f.flush();os.fsync(f.fileno())


class Metrics:
    def __init__(self):self.seconds={};self.counts={}
    def add(self,key,seconds):
        self.seconds[key]=self.seconds.get(key,0)+seconds;self.counts[key]=self.counts.get(key,0)+1
    def result(self):return dict(seconds=self.seconds,counts=self.counts)


class Evidence(Store):
    def __init__(self,root,metrics,level=9):super().__init__(root);self.metrics=metrics;self.level=level
    def preserve(self,raw,metadata):
        t=time.monotonic()
        for name in ('KIS_APP_KEY','KIS_APP_SECRET','KRX_API_KEY','DART_API_KEY','ECOS_API_KEY','FRED_API_KEY'):
            secret=os.getenv(name)
            if secret and secret.encode() in raw:raise Failure('credential_echo')
        h=sha(raw);p=self.root/'raw'/(h+'.gz')
        self.metrics.add('source_hash',time.monotonic()-t)
        t=time.monotonic()
        if p.exists():
            if sha(gzip.decompress(p.read_bytes()))!=h:raise Failure('source_hash_mismatch')
        else:atomic(p,gzip.compress(raw,compresslevel=self.level,mtime=0))
        entry=dict(metadata,source_hash=h)
        self.write('observations/'+sha(encoded(entry))+'.json',entry)
        self.metrics.add('raw_compression_and_storage',time.monotonic()-t)
        return entry


class ExperimentBudget:
    def __init__(self,store,round_id,metrics,run_id):
        self.store=store;self.round_id=round_id;self.metrics=metrics;self.run_id=run_id
        self.path=store.root/'http.jsonl';self.entries=events(self.path)
        self.used=sum(e['event']=='started' for e in self.entries);self.initial=self.used;self.last=0.;self.current=None
        self.limit=PROBE_LIMIT;self.started=time.monotonic()
    def check(self):
        window(self.round_id)
        if (self.store.root/'STOP').exists():raise Stop('operator_stop')
        if self.used>=self.limit:raise Stop('experiment_http_budget_exhausted')
    def promote(self,proof):
        if proof.get('status')!='PASS' or proof.get('round')!=self.round_id:raise Failure('live_probe_not_passed')
        self.limit=ROUND_LIMIT
    def before(self,source,endpoint,reason):
        self.check();t=time.monotonic()
        time.sleep(max(0,INTERVAL-(time.monotonic()-self.last)));self.metrics.add('rate_wait',time.monotonic()-t)
        self.check();self.last=time.monotonic();self.used+=1
        entry=dict(event='started',sequence=self.used,round=self.round_id,source=source,endpoint=endpoint,reason=reason,source_run=self.run_id,started_at=now())
        append(self.path,entry);self.entries.append(entry);self.current=entry
        return entry
    def finish(self,entry,status,**metadata):
        outcome=dict(event='finished',sequence=entry['sequence'],status=status,completed_at=now(),**metadata)
        append(self.path,outcome);self.entries.append(outcome)
    def remaining_seconds(self):
        from .policy import utcnow
        return (window(self.round_id)[1]-utcnow()).total_seconds()
