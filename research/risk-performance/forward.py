"""Manual append-only prospective research storage. Does not collect data.

Usage: python research/risk-performance/forward.py freeze BUNDLE CALENDAR_JSON
CALENDAR_JSON: {"sessions": [actual official sessions, including next session]}.
No --date/--now override in the CLI. Old reference candidates cannot be frozen.
"""
from pathlib import Path
import argparse,datetime as dt,gzip,hashlib,importlib.util,json,os
from engine import cohort,decision_gate,digest,instant

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('risk_audit',ROOT/'run.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

def exclusive_json(directory,key,payload):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/(key+'.json')
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n'
    try:
        with path.open('x') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    except FileExistsError:
        saved=json.loads(path.read_text())
        # Repeated identical input retains the original freeze time and record.
        if saved.get('input_sha256')!=payload.get('input_sha256'):
            raise ValueError('immutable_record_conflict')
    return path

def freeze(bundle_path,calendar,root=None,now=None):
    """now/root injection is for isolated tests only; CLI uses actual UTC clock."""
    raw=Path(bundle_path).read_bytes();b=json.loads(gzip.decompress(raw) if str(bundle_path).endswith('.gz') else raw)
    now=now or dt.datetime.now(dt.timezone.utc)
    reasons=audit.evaluate_bundle(b,calendar)
    times=audit.bundle_times(b)
    if reasons:raise ValueError(';'.join(reasons))
    ok,why=decision_gate(b['asof'],calendar,times,max(times,key=instant),now)
    if not ok:raise ValueError(why)
    dates=[x['trade_date'] for x in b['bars']];bars={x['trade_date']:x for x in b['bars']}
    events=audit.census.price_events(dates,bars);state,e,_=audit.census.current_state(dates,bars,events)
    f=b['flows'][-20:];net=sum(x['net_amount'] for x in f[-5:]);prev=sum(x['net_amount'] for x in f[-10:-5])
    close=bars[b['asof']]['close'];g=cohort(state,net>0 and net>prev,close,e['L2_price'] if e else None,e.get('H3_price') if e else None)
    if g is None:raise ValueError('flow_condition_not_met')
    if g=='C' and not any(x['stage']=='확정' and x['confirmed_date']==b['asof'] for x in events):
        raise ValueError('C_requires_new_confirmed_event')
    h=hashlib.sha256(raw).hexdigest()
    payload={'record_kind':'prospective','symbol':b['symbol'],'signal_date':b['asof'],'group':g,'frozen_at':now.isoformat(),
      'input_sha256':h,'known_at':max(times,key=instant),'bars_sha256':digest(b['bars']),'flows_sha256':digest(b['flows']),
      'price_structure':state,'structure':e,'net_5d_krw':net,'previous_5d_krw':prev,'pit_eligible':True,
      'corporate_action_and_execution_basis':'must be checked again for actual entry and holding interval',
      'L3_raw':None,'raw_signal_close':close,'provider_finality':b.get('finality')}
    key=digest([b['symbol'],b['asof'],g,h]);destination=Path(root or ROOT/'forward-records')
    evidence=destination/'evidence';evidence.mkdir(parents=True,exist_ok=True)
    p=evidence/(h+('.json.gz' if str(bundle_path).endswith('.gz') else '.json'))
    if not p.exists():
        with p.open('xb') as f:f.write(raw)
    elif hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('evidence_hash_conflict')
    return exclusive_json(destination/'signals',key,payload)

def record_observation(source_path,destination):
    """Separate raw observations; saving alone never certifies execution prices."""
    raw=Path(source_path).read_bytes();h=hashlib.sha256(raw).hexdigest()
    out=Path(destination)/'observations';out.mkdir(parents=True,exist_ok=True)
    target=out/(h+'.bin')
    if not target.exists():
        with target.open('xb') as f:f.write(raw)
    elif hashlib.sha256(target.read_bytes()).hexdigest()!=h:raise ValueError('observation_hash_conflict')
    return exclusive_json(out,h,{'input_sha256':h,'recorded_at':dt.datetime.now(dt.timezone.utc).isoformat(),'quality_status':'UNVERIFIED','source_file':Path(source_path).name})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['freeze','observe']);parser.add_argument('source');parser.add_argument('calendar',nargs='?')
    args=parser.parse_args()
    if args.action=='freeze':
        if not args.calendar:parser.error('official session calendar required')
        print(freeze(args.source,json.loads(Path(args.calendar).read_text())['sessions']))
    else:print(record_observation(args.source,ROOT/'forward-records'))
