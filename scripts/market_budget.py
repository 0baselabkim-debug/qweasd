import copy,datetime as dt,time
from screener.liquidity_quality import digest
from scripts import market_io as batch
class RecoveryBudget(Exception):pass

class BoundedAPI:
    """Physical-boundary caps for KIS, conservative fallback for other adapters."""
    def __init__(self,api,max_calls=60,seconds=240):
        self.api=api;self.start=time.monotonic();self.max_calls=max_calls;self.seconds=seconds
        self.used=0;self.observations=[];self.physical=[];self.logical=0;self.cache_hits=0
        self.reserved=0;self.refunded=0;self.sink=None;self.resume=[];self.anchors=[];self.generation=None;self.legacy_generation=None
        target=api.__dict__.get('factory',type(api))
        self.physical_supported=bool(getattr(target,'physical_budget_supported',False))
    def check(self):
        if self.used>=self.max_calls or time.monotonic()-self.start>=self.seconds:raise RecoveryBudget()
    def before_http(self,path,attempt):
        self.check()
        # One slot reserved and consumed at the actual transport boundary. No unused retries.
        self.used+=1;self.reserved+=1
        self.physical.append({'kind':'authentication' if path=='/oauth2/tokenP' else 'market','attempt':attempt+1})
    def begin(self,observations,sink):
        self.resume=observations;self.anchors=[];self.sink=sink;self.generation=None
        self.legacy_generation=None
        if len(observations)>=2 and not any(o.get('anchor_generation') for o in observations):
            first=observations[:2];keys=[self.signature(o['path'],o['params']) for o in first]
            if len(set(keys))==2 and all(sum(self.signature(o['path'],o['params'])==k for o in observations)==1 for k in keys):
                self.legacy_generation=self.anchor_digest(first)
    @staticmethod
    def anchor_digest(anchors):
        return digest([{'path':o['path'],'params':o['params'],'output2':o['output'].get('output2')} for o in anchors])
    @staticmethod
    def signature(path,params):return digest({'path':path,'params':params})
    def request(self,*args,**kwargs):
        from scripts.kis_public_client import REQUEST_BUDGET
        path=args[0];params=args[1] if len(args)>1 else kwargs.get('params',{})
        key=self.signature(path,params);self.logical+=1
        # Always reobserve the adjusted overlap and same-date raw anchor first.
        # A cache is usable only when BOTH anchors match the interrupted vintage.
        if len(self.anchors)==2:
            matches=[o for o in self.resume if self.signature(o['path'],o['params'])==key and o.get('anchor_generation',self.legacy_generation)==self.generation]
            if matches:
                value=copy.deepcopy(matches[-1]);self.cache_hits+=1
                value['anchor_generation']=self.generation
                self.observations.append(value)
                if self.sink:self.sink(value)
                return copy.deepcopy(value['output'])
        self.check()
        if not self.physical_supported:
            reserve=6 if self.api.__dict__.get('client',True) is None else 3
            if self.used+reserve>self.max_calls:raise RecoveryBudget()
            self.used+=reserve;self.reserved+=reserve
        token=REQUEST_BUDGET.set(self) if self.physical_supported else None
        try:response=self.api.request(*args,**kwargs)
        finally:
            if token is not None:REQUEST_BUDGET.reset(token)
        observation={'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'path':path,'params':params,
                     'output':{k:response[k] for k in ['output','output1','output2'] if k in response}}
        if len(self.anchors)<2:
            self.anchors.append(observation)
            if len(self.anchors)==2:self.generation=self.anchor_digest(self.anchors)
        if self.generation:observation['anchor_generation']=self.generation
        self.observations.append(observation)
        if self.sink:self.sink(observation) # survive process termination after any completed response
        return response
    def prices(self,symbol,start,end):return batch.chart(self,symbol,start,end)

def fair_tasks(tasks,ledger,now):
    """Rotate dates, then oldest eligible attempt; paused work has a resume lane."""
    dates=sorted({t[3]['asof'] for t in tasks})
    last=ledger.get('last_processed_date','')
    dates=[d for d in dates if d>last]+[d for d in dates if d<=last]
    queues={d:[] for d in dates}
    for t in tasks:
        job=ledger['jobs'].setdefault(t[1],{})
        job.setdefault('first_seen_at',t[3].get('seed_provenance',{}).get('queued_at') or now)
        queues[t[3]['asof']].append(t)
    for q in queues.values():
        q.sort(key=lambda t:(ledger['jobs'][t[1]].get('last_result',{}).get('error_type')!='RecoveryBudget',
                             t[0] or ledger['jobs'][t[1]]['first_seen_at'],t[1]))
    while any(queues.values()):
        for d in dates:
            if queues[d]:yield queues[d].pop(0)
