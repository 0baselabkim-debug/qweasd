"""Exact whole-bar selection across preserved vintages, for retrospective use only.

This is a new derived view, never a patch to a collector report. Every accepted
bar already passed the existing six-field, identity and corporate-action guard.
Equivalent accepted vintages retain all actual observation times. Conflicting
official versions remain held. No rounded matching or adjusted-price bridge.
"""
import datetime as dt
import gzip
import json
from pathlib import Path
from collections import Counter
import duckdb
import pandas as pd

from collector_v1.core import Store
from collector_v1.experiment.storage import events
from screener.decision_evidence.collector_source import saved_rows,references
from screener.decision_support.evidence import encoded,sha,read,timestamp,accept_chart,corporate_adjustment_pending
from . import VERSION,MODE


def key(code,day,market): return '|'.join((code,day,market))


def build(sources,reference_directory,destination,cutoff,*,legacy_snapshot=None):
    destination=Path(destination)
    if destination.exists(): raise ValueError('pool_destination_must_be_new')
    official,official_sources,errors,reference_hashes=references(reference_directory,timestamp(cutoff),asof='9999-12-31')
    book={};held=Counter();origins={};candidate_count=0;barriers={}
    for source in sources:
        if source['status']!='INTEGRITY_PASS':continue
        root=Path(source['directory']);proof=source['proof'];folder=(root/proof['report_path']).parent
        if sha((root/'manifest.json').read_bytes())!=proof['manifest_sha256']: raise ValueError('pool_source_anchor_mismatch')
        universe=read(root/proof['universe_path']);instruments={r['symbol']:r for r in universe}
        store=Store(folder);report=read(root/proof['report_path']);asof=report['asof']
        origins[source['artifact_id']]=proof
        for event in events(folder/'tasks.jsonl'):
            t=event['task']
            if event['status']!='received' or t['kind']!='price':continue
            e=event['evidence'];code=t['symbol'];market=t['market'];observed=e['collected_at']
            if timestamp(observed)>timestamp(cutoff):continue
            raw=saved_rows(json.loads(store.raw(e)),e,'price',code,market,asof=asof,generation_id=t.get('generation_id'),round_id=t['round'])
            barrier=max((d for d,r in raw.items() if corporate_adjustment_pending(r)),default=None)
            if barrier:barriers[(code,market)]=max(barrier,barriers.get((code,market),barrier))
            for day,row in raw.items():
                candidate_count+=1
                q=accept_chart(row,code,day,market,observed,official,instruments[code]['listing_date'],blocked=bool(barrier and day<=barrier))
                if not q['accepted']:held.update(q['reasons']);continue
                k=key(code,day,market)
                origin=dict(artifact_id=source['artifact_id'],source_hash=e['source_hash'],evidence_sha256=sha(encoded(e)),observed_at=observed,source_asof=asof)
                if k not in book:
                    book[k]=dict(symbol=code,trade_date=day,market=market,value=q['value'],raw_row=row,official_source_ids=q['official_source_ids'],sources=[])
                if book[k]['value']!=q['value']:raise ValueError('accepted_pool_values_disagree')
                if origin not in book[k]['sources']:book[k]['sources'].append(origin)
    legacy_hashes={}
    if legacy_snapshot:
        from collections import defaultdict
        from screener.decision_support.history import candidate
        snapshot=Path(legacy_snapshot);inventory=read(snapshot/'decision-support/source-manifest.json');objects=defaultdict(list)
        for sid,source in inventory.get('auxiliary',{}).items():
            if not source.get('key','').startswith('kis:'):continue
            path=snapshot/'decision-support'/source['file']
            if sha(path.read_bytes())!=source['sha256']:raise ValueError('legacy_price_source_hash_mismatch')
            objects[source['key']].append((sid,read(path)));legacy_hashes[str(path.resolve())]=source['sha256']
        with duckdb.connect(str(snapshot/'dashboard.duckdb'),read_only=True) as db:
            instruments={c:(m,str(d)) for c,m,d in db.execute('select symbol,market,listing_date from instruments').fetchall()}
        for code in sorted({k.split(':')[2] for k in objects if len(k.split(':')[2])==6}):
            market,listing=instruments[code]
            for asof in sorted({k.split(':')[1] for k in objects if k.split(':')[2]==code}):
                c=candidate(objects,code,market,asof,listing,official,{},[],{})
                if not c or c.get('error'):continue
                source_ids=c['source_ids']
                for row in c['series']:
                    if row['validation_status']!='operational_crosscheck_pass' or row['reasons']:continue
                    day=row['trade_date'];k=key(code,day,market);value={n:row[n] for n in ('open','high','low','close','volume','turnover')}
                    if k not in book:book[k]=dict(symbol=code,trade_date=day,market=market,value=value,raw_row_sha256=row['raw_row_sha256'],official_source_ids=row['official_source_ids'],sources=[])
                    if book[k]['value']!=value:raise ValueError('legacy_accepted_pool_values_disagree')
                    book[k]['sources'].append(dict(source_kind='PRESERVED_PAIRED_ADJUSTED_ORIGINAL_RAW_PAGES',source_evidence_ids=source_ids,
                        source_asof=asof,observed_at=c['observed_at'],whole_six_field_adjusted_original_equal=True))
    for k,v in list(book.items()):
        if v['trade_date']<=barriers.get((v['symbol'],v['market']),''):
            del book[k];held['cross_vintage_adjustment_barrier']+=1
    identity=dict(version=VERSION,analysis_mode=MODE,cutoff=cutoff,source_proofs=origins,reference_hashes=reference_hashes,legacy_source_hashes=legacy_hashes,
                  code_sha256={p.name:sha(p.read_bytes()) for p in Path(__file__).parent.glob('*.py')})
    rows=[dict(key=k,payload=encoded(v).decode()) for k,v in sorted(book.items())]
    destination.mkdir(parents=True)
    (destination/'bars.json.gz').write_bytes(gzip.compress(encoded(book),mtime=0))
    with duckdb.connect(str(destination/'prices.duckdb')) as db:
        frame=pd.DataFrame(rows,columns=['key','payload']);db.register('incoming',frame)
        db.execute('create table prices as select key, cast(payload as json) payload from incoming')
        db.execute('copy prices to ? (format parquet,compression zstd)',[str(destination/'prices.parquet')])
    files={p.name:sha(p.read_bytes()) for p in destination.iterdir() if p.is_file()}
    manifest=dict(**identity,files=files,bars=len(book),candidate_bars=candidate_count,hold_reasons=dict(held),official_reference_errors=len(errors),provider_finality='UNVERIFIED',historical_backtest_eligible=False)
    (destination/'manifest.json').write_bytes(encoded(manifest))
    return dict(directory=str(destination),manifest_sha256=sha(encoded(manifest)),bars=len(book),candidate_bars=candidate_count,hold_reasons=dict(held))


def load(directory,manifest_sha,cutoff):
    directory=Path(directory);raw=(directory/'manifest.json').read_bytes()
    if sha(raw)!=manifest_sha:raise ValueError('price_pool_anchor_mismatch')
    manifest=json.loads(raw)
    if manifest.get('analysis_mode')!=MODE or manifest.get('historical_backtest_eligible') is not False:raise ValueError('pool_not_retrospective')
    hashes={str((directory/'manifest.json').resolve()):manifest_sha}
    for n,h in manifest['files'].items():
        p=(directory/n).resolve()
        if not p.is_relative_to(directory.resolve()) or sha(p.read_bytes())!=h:raise ValueError('price_pool_hash_mismatch')
        hashes[str(p)]=h
    if timestamp(manifest['cutoff'])>timestamp(cutoff):raise ValueError('price_pool_after_cutoff')
    book=read(directory/'bars.json.gz')
    with duckdb.connect(str(directory/'prices.duckdb'),read_only=True) as db:
        rows=db.execute('select key,payload from prices order by key').fetchall()
        parquet=db.execute('select key,payload from read_parquet(?) order by key',[str(directory/'prices.parquet')]).fetchall()
    if rows!=parquet or {k:json.loads(v) for k,v in rows}!=book or len(rows)!=manifest['bars']:raise ValueError('price_pool_DB_JSON_Parquet_mismatch')
    for k,v in book.items():
        if k!=key(v['symbol'],v['trade_date'],v['market']) or not v['sources'] or not v['official_source_ids']:raise ValueError('price_pool_missing_provenance')
        if any(timestamp(s['observed_at'])>timestamp(cutoff) for s in v['sources']):raise ValueError('pool_observation_after_cutoff')
    return book,hashes
