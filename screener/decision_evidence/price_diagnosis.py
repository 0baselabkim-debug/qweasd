"""Compare the old cohort only against actual same-date reobservations; no causal guesses."""
import csv
import gzip
import io
import json
from collections import Counter
from pathlib import Path
from collector_v1.core import Store
from collector_v1.providers import task
from collector_v1.experiment.engine import replay,identity
from screener.decision_support.evidence import encoded,sha,bar,KIS_FIELDS,read,timestamp,corporate_adjustment_pending,accept_chart
from .metrics import table,FIELDS
from .source import load as load_published,differences
from .collector_artifact import ASOF,GENERATION
from .collector_source import saved_rows


def paired_old_basis(snapshot, asof):
    directory=Path(snapshot)/'decision-support';inventory=read(directory/'source-manifest.json');seen={}
    for sid,v in inventory.get('auxiliary',{}).items():
        if v.get('asof')!=asof or v.get('key','').startswith('krx:'):continue
        obj=read(directory/v['file']);p=obj.get('params',{});option=p.get('FID_ORG_ADJ_PRC');code=obj.get('symbol')
        if option not in ('0','1') or p.get('FID_COND_MRKT_DIV_CODE')!='J' or p.get('FID_INPUT_ISCD')!=code:continue
        for raw in obj.get('response',{}).get('output2',[]):
            if raw.get('stck_bsop_date')!=asof.replace('-',''):continue
            try:value=bar(raw,KIS_FIELDS)
            except ValueError:continue
            seen.setdefault(code,{}).setdefault(option,[]).append(dict(value=value,source_id=sid,observed_at=obj['observed_at']))
    return seen


def diagnose(collector_root,published_snapshot,cutoff):
    old=load_published(published_snapshot,cutoff);asof=old['index']['asof'];pairs=paired_old_basis(published_snapshot,asof)
    store=Store(Path(collector_root)/GENERATION/'collection');cache=replay(store,1,asof=ASOF,generation_id=GENERATION)
    report=read(Path(collector_root)/'report.json');current={r['symbol']:r for r in report['rows']};details=[];counts=Counter();same_date_approvals=0
    instruments={r['symbol']:r for r in read(Path(collector_root)/GENERATION/'preparation/universe.json')}
    cohort=Counter()
    for stock in old['rows']:
        code=stock['symbol'];market=stock['market'];before=table(stock['price_series']).get(asof)
        if code in current:
            e=cache[identity(task('price',ASOF,round=1,symbol=code,market=market,generation_id=GENERATION))]
            history=saved_rows(json.loads(store.raw(e)),e,'price',code,market);raw=history.get(asof)
            barriers=[d for d,v in history.items() if corporate_adjustment_pending(v)]
            barrier=max(barriers) if barriers else None
            approved=False
            if raw:
                q=accept_chart(raw,code,asof,market,e['collected_at'],old['official'],instruments[code]['listing_date'],blocked=bool(barrier and asof<=barrier))
                approved=q['accepted'] is not None
            was_approved=before is not None and before.get('validation_status')=='operational_crosscheck_pass' and not before.get('reasons')
            cohort['common_stocks']+=1;cohort['before_approved']+=was_approved;cohort['later_approved']+=approved
            cohort['newly_approved']+=approved and not was_approved;cohort['lost_approvals']+=was_approved and not approved
        if not differences(stock,old['official']):continue
        refs=old['official'].get((code,asof,market),[]);variants={encoded(r['value']) for r in refs}
        item=dict(stock_code=code,comparison_trade_date=asof,original_generation=old['index']['dataset_id'],
            new_source_generation=GENERATION,new_observation_source_hash=None,old_price={k:before.get(k) for k in FIELDS},
            saved_KRX_price=None,later_original_price=None,classification=None,cause='UNRESOLVED',price_approved_for_20261029=False)
        if len(variants)!=1 or not refs[0].get('value'):item['classification']='OFFICIAL_VERSIONS_CONFLICT_OR_INVALID'
        elif code not in current:item['classification']='NOT_IN_NEW_POPULATION'
        else:
            item.update(new_observation_source_hash=e['source_hash'],observed_at=e['collected_at'],saved_KRX_price=refs[0]['value'],request_market='J_KRX',request_price_basis='1_ORIGINAL',
                official_source_ids=[r['source_id'] for r in refs],later_same_date_strict_approved=approved)
            if raw is None:item['classification']='SAME_DATE_REOBSERVATION_MISSING'
            else:
                try:after=bar(raw,KIS_FIELDS)
                except ValueError:after=None
                item.update(later_original_price=after,adjustment_fields={k:raw.get(k) for k in ('flng_cls_code','prtt_rate','revl_issu_reas','mod_yn')})
                if after==refs[0]['value']:
                    item['classification']='LATER_ORIGINAL_SIX_FIELDS_MATCH_SAVED_KRX'
                    if raw.get('mod_yn')=='N' and not corporate_adjustment_pending(raw):same_date_approvals+=1
                    options=pairs.get(code,{})
                    matches={o:[v for v in options.get(o,[]) if v['value']==item['old_price']] for o in ('0','1')}
                    witnesses=[(a,b) for a in matches['0'] for b in matches['1'] if abs((timestamp(a['observed_at'])-timestamp(b['observed_at'])).total_seconds())<=900]
                    if witnesses:
                        a,b=witnesses[0];item.update(cause='SAME_SCOPE_ORIGINAL_PRICE_VINTAGE_REVISION_CONFIRMED',old_original_adjusted_equal_witnesses=[a['source_id'],b['source_id']])
                elif after==item['old_price']:item['classification']='SIX_FIELD_CONFLICT_PERSISTS'
                else:item['classification']='LATER_PRICE_CHANGED_CONFLICT_UNRESOLVED'
        counts[item['classification']]+=1;details.append(item)
    summary=dict(status='OBSERVATION_EVIDENCE_ONLY',original_asof=asof,latest_asof=ASOF,original_conflict_stocks=len(details),
        classification_counts=dict(counts),confirmed_price_vintage_revision=sum(r['cause']=='SAME_SCOPE_ORIGINAL_PRICE_VINTAGE_REVISION_CONFIRMED' for r in details),
        later_same_date_exact_matches_without_adjustment_flags=same_date_approvals,
        same_date_common_population_strict_approval=dict(cohort),comparison_asof=asof,
        original_records_changed=False,latest_20261029_approval_inferred=False,
        cause_policy='A later original-price match alone does not distinguish adjusted/original basis from publication revision. A paired old original/adjusted witness is required for confirmed vintage revision.',
        NXT_cause='NOT_PROVEN; official KIS sample defines J=KRX, NX=NXT, UN=integrated. Only J requests observed.',
        volume_turnover_aggregation_cause='NOT_PROVEN; preserved field-level differences are insufficient to establish aggregation semantics.',
        old_same_date_confirmation_is_not_latest_date_approval=True)
    return summary,details


def save(directory,summary,details):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    (directory/'legacy-price-diagnosis.json').write_bytes(encoded(summary))
    (directory/'legacy-price-details.json.gz').write_bytes(gzip.compress(encoded(details),mtime=0))
