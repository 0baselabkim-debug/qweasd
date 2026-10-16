"""Private queue: collect once, verify exact payload, then evaluate current positions.

No private bundle or position is written to public artifacts or logs.
"""
import copy
import datetime as dt
from screener.technical import validate, sell_guide
from screener.technical_quality import daily_accepted
from scripts.technical_acceptance import verify_saved_or_live, accept_saved_responses
from scripts.technical_batch import make_bundle, krx_reference, read, CACHE, ROOT


def review(api, bundle, references):
    # Reuse provider response rows, never another payload's acceptance flag/hash.
    for path in [CACHE/(bundle['symbol']+'.json.gz'),
                 ROOT/'history'/bundle['asof']/(bundle['symbol']+'.json.gz')]:
        public=read(path)
        if (public and public.get('asof')==bundle['asof'] and daily_accepted(public)
                and public.get('flows')==bundle.get('flows')):
            e=public['daily_acceptance']
            return accept_saved_responses(bundle,references,e['raw_flow_rows'],
                [{'kind':'saved_public_KIS_response','observed_at':e['observed_at']}])
    return verify_saved_or_live(api,bundle,references)


def run(store, api, data, asof, targets, paged):
    references,_=krx_reference(asof)
    failed=0
    for code in targets:
        try:
            positions=store.positions(code)
            earliest=min((str(p['buy_date']) for p in positions),default=asof)
            count=min(5500,max(320,(dt.date.fromisoformat(asof)-dt.date.fromisoformat(earliest)).days+200))
            previous=store.cache(code)
            enough=previous and sum(r['trade_date']<earliest for r in previous.get('bars',[]))>=160
            if previous and (enough or previous.get('history_request_count',0)>=count) and previous.get('asof')==asof and previous.get('structure_valid'):
                bundle=copy.deepcopy(previous)
            else:
                calendar=[d for d,_ in paged(api,'0001',asof,index=True,count=count)]
                bundle=make_bundle(api,code,asof,calendar,data,previous if enough else None,paged,count=count)
            bundle['history_request_count']=count
            bundle['private_history_requirement']={'pre_purchase_days':sum(r['trade_date']<earliest for r in bundle['bars']),'required':160,'lookback_cap':5500}
            bundle=review(api,bundle,references)
            # Daily acceptance is not a corporate-action ledger or sell readiness.
            bundle['worker_verified']=not validate(bundle,asof) and bool(bundle.get('corporate_actions_verified')) and bundle['private_history_requirement']['pre_purchase_days']>=160
            store.publish(code,bundle)
            for position in positions:
                store.save_signal(position,sell_guide(bundle,position,position.get('risk') or {}))
        except Exception:
            failed+=1
            store.fail(code,'Collection or evidence verification failed; prior cache retained')
    print('Private worker finished; no identifiers or positions written to logs')
    if failed:raise SystemExit(1)
