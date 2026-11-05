"""Observed official session sets, with independent supplier completeness checks.

Positive dated KRX responses establish each session. Stored KIS U-index pages
crosscheck missing sessions; this never calls a supplier calendar a KRX holiday
notice. No weekdays are synthesized, and a missing/extra day blocks continuity.
"""
from collections import defaultdict
import json
from pathlib import Path
from screener.decision_support.evidence import read,sha,date,encoded
from screener.decision_support.history import pages
from collector_v1.core import Store
from collector_v1.experiment.storage import events


def check_sessions(official, supplier):
    if set(official)!= {'KOSPI','KOSDAQ'} or set(supplier)!=set(official):raise ValueError('both_market_calendars_required')
    a,b=(set(official[m]) for m in ('KOSPI','KOSDAQ'))
    if not a or a!=b:raise ValueError('official_market_session_sets_disagree')
    first,last=min(a),max(a)
    for market in official:
        expected={d for d in supplier[market] if first<=d<=last}
        if a!=expected:raise ValueError('official_supplier_session_gap_or_conflict:'+market)
    return sorted(a)


def build(references,snapshot,sources):
    references=Path(references);snapshot=Path(snapshot);m=read(references/'source-manifest.json')
    official=defaultdict(set);supplier=defaultdict(set);hashes={}
    for x in m['references'].values():
        p=references/x['file']
        if sha(p.read_bytes())!=x['sha256']:raise ValueError('calendar_reference_hash_mismatch')
        obj=read(p);url=obj['url'];market='KOSPI' if '/stk_bydd_trd?' in url else 'KOSDAQ'
        if not obj['rows'] or any(date(r['BAS_DD'])!=obj['asof'] for r in obj['rows']):raise ValueError('calendar_official_response_date_mismatch')
        official[market].add(obj['asof']);hashes[str(p.resolve())]=x['sha256']
    inventory=read(snapshot/'decision-support/source-manifest.json');objects=defaultdict(list)
    for sid,x in inventory['auxiliary'].items():
        if ':index:' not in x['key']:continue
        p=snapshot/'decision-support'/x['file']
        if sha(p.read_bytes())!=x['sha256']:raise ValueError('calendar_index_hash_mismatch')
        objects[x['key']].append((sid,read(p)));hashes[str(p.resolve())]=x['sha256']
    for market,code in [('KOSPI','0001'),('KOSDAQ','1001')]:
        for asof in {k.split(':')[1] for k in objects if k.split(':')[2]==code}:
            rows,_,_=pages(objects,asof,code,'index');supplier[market].update(rows)
    for source in sources:
        if source['status']!='INTEGRITY_PASS':continue
        root=Path(source['directory']);folder=(root/source['proof']['report_path']).parent;store=Store(folder)
        for event in events(folder/'tasks.jsonl'):
            if event['status']!='received' or event['task']['kind']!='index':continue
            e=event['evidence'];obj=json.loads(store.raw(e));market=e['task']['market']
            if e['params']['FID_COND_MRKT_DIV_CODE']!='U' or e['params']['FID_INPUT_ISCD']!=('0001' if market=='KOSPI' else '1001'):raise ValueError('calendar_index_scope_mismatch')
            supplier[market].update(date(r['stck_bsop_date']) for r in obj['output2'])
            p=folder/'raw'/(e['source_hash']+'.gz');hashes[str(p.resolve())]=sha(p.read_bytes())
    days=check_sessions(official,supplier)
    return dict(status='OFFICIAL_POSITIVE_SESSIONS_INDEPENDENTLY_CROSSCHECKED',days=days,first=days[0],last=days[-1],
        official_source='KRX official dated stk_bydd_trd/ksq_bydd_trd responses',
        completeness_source='preserved KIS U index response sessions; exact set equality over the covered range',
        official_holiday_notice='NOT_ACQUIRED; this is an observed session calendar, not a statutory holiday list',
        source_hashes=hashes,generated_weekdays=False)
