"""Manual command line. No Git push, no operating snapshot writes, no scheduler."""
import argparse
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import Store, Budget, Failure, encoded, sha, collect_tasks, publish
from .providers import Providers, task, market_tasks, CREDENTIALS
from .validation import validate, parse
from screener.macro import catalog


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('data/collector-v1'))
    sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--date',required=True)
    init.add_argument('--mode',choices=['market','macro','corporate'],default='market')
    expand=sub.add_parser('select');expand.add_argument('--symbols',required=True)
    expand.add_argument('--scope',choices=['CFS','OFS'],default='CFS')
    collect=sub.add_parser('collect');collect.add_argument('--max-calls',type=int,default=20);collect.add_argument('--seconds',type=int,default=240)
    retry=sub.add_parser('retry');retry.add_argument('--task-key',required=True);retry.add_argument('--refresh',action='store_true')
    for name in ('status','stop','resume','validate','publish','credentials'):sub.add_parser(name)
    a=p.parse_args(argv);store=Store(a.root)
    if a.command=='credentials':
        print(json.dumps({source:{n:bool(__import__('os').getenv(n)) for n in names} for source,names in CREDENTIALS.items()}));return
    if a.command=='stop':(store.root/'STOP').touch();return
    with store.lock():
        if a.command=='init':
            dt.date.fromisoformat(a.date)
            if store.read('plan.json') is not None:raise Failure('plan_exists_use_separate_root')
            if a.mode=='market':tasks=[task('master',a.date,market=m) for m in ('KOSPI','KOSDAQ')]
            elif a.mode=='corporate':tasks=[task('corp_codes',a.date)]
            else:tasks=[task('macro',a.date,indicator=k) for k,v in catalog().items() if v['provider'] in ('ECOS','FRED')]
            store.write('plan.json',dict(schema=1,asof=a.date,mode=a.mode,coverage='pilot' if a.mode!='macro' else 'catalog',tasks=tasks))
        elif a.command=='resume':(store.root/'STOP').unlink(missing_ok=True)
        elif a.command=='status':
            print(json.dumps({k:store.read(k) for k in ('plan.json','state.json','last-run.json','quality.json','published.json')},ensure_ascii=False,indent=2))
        elif a.command=='retry':
            state=store.read('state.json',{'tasks':{}});entry=state['tasks'][a.task_key]
            if entry['collection_status']=='received' and not a.refresh:raise Failure('refresh_requires_explicit_flag')
            # Old evidence and failure history remain immutable on disk.
            store.write('task-history/'+sha(encoded(entry))+'.json',entry)
            del state['tasks'][a.task_key];store.write('state.json',state)
        else:
            plan=store.read('plan.json')
            if plan is None:raise Failure('initialize_first')
            if a.command=='select':
                codes=a.symbols.split(',')
                if len(codes)>3 or len(set(codes))!=len(codes):raise Failure('pilot_at_most_three_unique_symbols')
                state=store.read('state.json',{'tasks':{}})
                def evidence(t):return state['tasks'][sha(encoded(t))]['evidence']
                if plan['mode']=='market':
                    masters=[t for t in plan['tasks'] if t['kind']=='master']
                    universe=[r for t in masters for r in parse(store.raw(evidence(t)),evidence(t))]
                    selected=[r for r in universe if r['symbol'] in codes]
                    if len(selected)!=len(codes) or any(not r['eligible'] for r in selected):raise Failure('unknown_or_excluded_symbol')
                    tasks=masters+market_tasks(plan['asof'],selected)
                elif plan['mode']=='corporate':
                    initial=plan['tasks'][0];mapping={r['symbol']:r['corp_code'] for r in parse(store.raw(evidence(initial)),evidence(initial))}
                    listings=[task('filings',plan['asof'],symbol=s,corp_code=mapping[s]) for s in codes]
                    tasks=[initial]+listings
                    for t in listings:
                        e=state['tasks'].get(sha(encoded(t)),{}).get('evidence')
                        if e:
                            for filing in parse(store.raw(e),e):
                                if filing['report_code']:
                                    tasks.append(task('financial',plan['asof'],filing=filing,scope=a.scope))
                else:raise Failure('macro_needs_no_symbol_selection')
                plan.update(tasks=tasks,symbols=codes);store.write('plan.json',plan)
            elif a.command=='collect':
                budget=Budget(store,a.max_calls,a.seconds);api=Providers(store,budget)
                result=collect_tasks(store,plan['tasks'],api.fetch,budget)
                print(json.dumps(result,ensure_ascii=False))
                return 0 if result['collection_status']=='complete' else 1
            else:
                rows,report=validate(store,plan['tasks'],require_market=plan['mode']=='market')
                if plan['mode']=='market' and not plan.get('symbols'):
                    report['held']+=1;report['errors'].append({'reason':'select_pilot_symbols_first'});store.write('quality.json',report)
                if plan['mode']=='corporate' and not any(t['kind']=='financial' for t in plan['tasks']):
                    report['held']+=1;report['errors'].append({'reason':'financial_collection_not_prepared'});store.write('quality.json',report)
                report['coverage']=plan['coverage'];report['target_symbols']=plan.get('symbols',[])
                if a.command=='publish':
                    generation=sha(encoded({'plan':plan,'sources':report['source_hashes'],'policy':report['validation_policy']}))
                    report=publish(store,generation,rows,report)
                print(json.dumps(report,ensure_ascii=False))
                if report.get('held'):return 1
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Failure as e:
        print(json.dumps({'status':'blocked','reason':e.code}));raise SystemExit(2)
