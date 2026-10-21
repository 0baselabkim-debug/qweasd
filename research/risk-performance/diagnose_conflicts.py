"""Pinned offline investigation; output only in conflict-diagnosis, no API calls.
Run from repository root: python research/risk-performance/diagnose_conflicts.py
"""
from pathlib import Path
import collections
import datetime as dt
import gzip
import hashlib
import json
import socket
import subprocess
import time
import tarfile
import pandas as pd

import link_trial as old
from engine import digest, instant
from forward_system import Store, load, sha, utcnow, snapshot_rows, validate_publication, FIELDS, KIS_FIELDS
from price_link import normalized, six, classify_link, action_flag
from conflict_reconciliation import add_evidence, select_flow_replay, verify_replay_sources, research_snapshot_view
from screener.liquidity_quality import digest as evidence_digest

ROOT = old.ROOT; REPO = old.REPO; FULL = old.FULL
OUT = ROOT / 'conflict-diagnosis'
MAIN = '7ee2b7d533ddc08dc5eb316dc406fe0fbfdfdecc'


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def csv(name, rows):
    pd.DataFrame(rows).to_csv(OUT / name, index=False, encoding='utf-8-sig')


def changed(a, b):
    return ','.join(k for k in FIELDS if a[k] != b[k])


def flags(row):
    return {k: row.get(k) for k in ['prtt_rate', 'revl_issu_reas', 'mod_yn', 'flng_cls_code']}


def main():
    started = time.monotonic(); run_at = utcnow().isoformat(); OUT.mkdir(exist_ok=True)
    # Retain the actual first capture upper bound for unchanged saved inputs.
    # Replays do not shift source versions or fabricate earlier knowledge times.
    previous_manifest=load(OUT/'input-manifest.json') if (OUT/'input-manifest.json').exists() else None
    if previous_manifest:
        for rel,h in previous_manifest['sources'].items():assert sha(REPO/rel)==h
    now=previous_manifest['known_at'] if previous_manifest else run_at
    ids = {}
    def identify(p):
        rel = str(p.relative_to(REPO)); ids[rel] = sha(p)
        return {'file': rel, 'sha256': ids[rel]}

    baseline = pd.read_csv(old.OUT / 'all-stocks.csv', dtype={'symbol': str}).fillna('')
    comparisons = pd.read_csv(old.OUT / 'observation-comparison.csv', dtype={'symbol': str})
    identify(old.OUT / 'all-stocks.csv'); identify(old.OUT / 'observation-comparison.csv')
    records = baseline.set_index('symbol').to_dict('index')
    protected = {str(p.relative_to(REPO)): sha(p) for p in (REPO/'snapshot').rglob('*') if p.is_file()}
    protected.update({str(p.relative_to(REPO)): sha(p) for p in old.OUT.rglob('*') if p.is_file()})
    # Read newer main without changing branch, worktree, or previous study inputs.
    latest = OUT / 'inputs/latest-snapshot'; latest.mkdir(parents=True, exist_ok=True)
    for name, rel in [('dashboard.duckdb','snapshot/dashboard.duckdb'), ('manifest.json','snapshot/manifest.json'),
                      ('daily-validation.json','snapshot/daily-validation.json'), ('publication-ledger.json','snapshot/publication-ledger.json'),
                      ('index.json','snapshot/briefing/index.json')]:
        payload = subprocess.check_output(['git', 'show', MAIN+':'+rel], cwd=REPO)
        p = latest/name
        if p.exists(): assert p.read_bytes() == payload
        else: p.write_bytes(payload)
        identify(p)
    pub = validate_publication(latest)
    u, latest_prices, latest_flows, latest_sessions, excluded = snapshot_rows(latest)
    _, previous_prices, previous_flows, sessions, _ = snapshot_rows(old.SNAP)
    assert pub['asof'] == '2026-10-20' and len(u) == 2429
    assert latest_flows == previous_flows, 'latest publication flow changes require new study'
    cps = {}; cp_sources = {}
    for day, p in [('2026-10-15',ROOT/'inputs/checkpoint-20261015.json.gz'),
                   ('2026-10-16',ROOT/'inputs/checkpoint-20261016.json.gz'),
                   ('2026-10-19',FULL/'inputs/checkpoint-20261019.json.gz'),
                   ('2026-10-20',FULL/'inputs/checkpoint-20261020.json.gz')]:
        cps[day] = load(p); cp_sources[day] = identify(p)
    all_prices = pd.read_parquet(FULL/'results/research-prices.parquet'); identify(FULL/'results/research-prices.parquet')
    origins = {(r['symbol'],r['trade_date']):r for r in all_prices.to_dict('records')}
    pairs = load(FULL/'collection/state.json')['records']; pilot = load(FULL/'inputs/pilot/run.json')['records']
    parsed_pairs = {}
    for symbol in records:
        pair = pilot.get(symbol) or pairs.get(symbol)
        if not pair: continue
        base = FULL/'inputs/pilot' if symbol in pilot else FULL/'collection'
        parsed = {}
        for kind, e in pair.items():
            p = base/e['file']; assert sha(p) == e['sha256']; identify(p); obj = load(p)
            assert obj['params']['FID_INPUT_ISCD'] == symbol and obj['params']['FID_COND_MRKT_DIV_CODE'] == 'J'
            assert obj['params']['FID_ORG_ADJ_PRC'] == {'raw':'1','adjusted':'0'}[kind]
            assert obj['rt_cd']=='0' and obj['output1']['stck_shrn_iscd']==symbol
            parsed[kind] = {'rows': {old.iso(r['stck_bsop_date']):r for r in obj['output2']},
                            'normalized':normalized(obj['output2']), 'observed_at':obj['observed_at'],
                            'params':obj['params'], 'sha256':e['sha256']}
        parsed_pairs[symbol] = parsed
    detail=[]
    for r in comparisons.to_dict('records'):
        s=r['symbol'];d=r['date'];src=r['comparison_source']; meta=records[s]
        left={k:r['original_'+k] for k in FIELDS};right={k:r['candidate_'+k] for k in FIELDS}
        origin=origins.get((s,d),{});pair=parsed_pairs.get(s,{})
        row={**r,'market':meta['market'],'changed_fields':changed(left,right),
             'candidate_observed_at':origin.get('series_observed_at') or origin.get('observed_at'),
             'candidate_source':origin.get('source'),'candidate_basis':'adjusted', 'scope':'J/KRX',
             'type':'raw_chart_vs_adjusted_chart' if '/' not in src and src!='adjusted_overlap' else 'adjusted_chart_observation_drift' if src=='adjusted_overlap' else 'investor_blank_option_vs_adjusted_chart',
             'left_observed_at':None,'left_adjustment_option':None,'old_chart_equals_old_investor':None,
             'later_raw_equals_later_adjusted':None,'later_raw_equals_candidate':None,
             'left_flags':None,'right_flags':None,'adjustment_difference_is_proven_cause':False}
        if 'checkpoint-' in src:
            checkpointday=next(day for day in cps if day.replace('-','') in src)
            rec=cps[checkpointday]['records'][s];chart=next((v for v in rec['adjusted_prices'] if v['stck_bsop_date']==d.replace('-','')),None)
            row.update(left_observed_at=rec.get('observed_at'),left_adjustment_option='blank (official required; raw/adjusted semantics unspecified)',
                       left_flags=flags(rec['flow']),old_chart_flags=flags(chart or {}))
            if chart:row['old_chart_equals_old_investor']=six(normalized([chart])[d])==six(left)
        elif src=='adjusted_overlap':
            row.update(left_observed_at=row['candidate_observed_at'],left_adjustment_option='0',
                       candidate_source=cp_sources['2026-10-20']['file'],
                       candidate_observed_at=cps['2026-10-20']['records'][s]['observed_at'])
        elif 'raw' in pair:
            row.update(left_observed_at=pair['raw']['observed_at'],left_adjustment_option='1',
                       left_flags=flags(pair['raw']['rows'].get(d,{})))
        if 'raw' in pair and 'adjusted' in pair and d in pair['raw']['rows'] and d in pair['adjusted']['rows']:
            raw=pair['raw']['normalized'][d]; adj=pair['adjusted']['normalized'][d]
            row['later_raw_equals_later_adjusted']=six(raw)==six(adj)
            row['later_raw_equals_candidate']=six(raw)==six(right)
            row['later_pair_changed_fields']=changed(raw,adj)
            row['right_flags']=flags(pair['adjusted']['rows'][d])
            row['later_raw_pair_observed_at']=pair['raw']['observed_at']
            row['later_adjusted_pair_observed_at']=pair['adjusted']['observed_at']
        if src=='adjusted_overlap':
            latest_row=next(v for v in cps['2026-10-20']['records'][s]['adjusted_prices'] if v['stck_bsop_date']==d.replace('-',''))
            row['right_flags']=flags(latest_row)
        detail.append(row)
    csv('observation-detail.csv',detail)
    df=pd.DataFrame(detail); active=df[~df.official_resolved]
    grouped=active.groupby(['type','date','market','changed_fields'],dropna=False).agg(observations=('symbol','size'),stocks=('symbol','nunique')).reset_index()
    grouped.to_csv(OUT/'conflict-by-date-field-market.csv',index=False,encoding='utf-8-sig')

    # Compare explicit 0 vs 1 responses, independently of the catch-all reason name.
    pair_diffs=[]
    for s,p in parsed_pairs.items():
        if not {'raw','adjusted'} <= p.keys():continue
        for d in sorted(set(p['raw']['rows']) & set(p['adjusted']['rows'])):
            if d<'2026-08-20' or d>'2026-10-20':continue
            a=p['raw']['normalized'][d];b=p['adjusted']['normalized'][d]
            if six(a)==six(b):continue
            pair_diffs.append({'symbol':s,'name':records[s]['name'],'market':records[s]['market'],'date':d,
                 'fields':changed(a,b),'raw_observed_at':p['raw']['observed_at'],'adjusted_observed_at':p['adjusted']['observed_at'],
                 'raw_sha256':p['raw']['sha256'],'adjusted_sha256':p['adjusted']['sha256'],
                 'raw_flags':flags(p['raw']['rows'][d]),'adjusted_flags':flags(p['adjusted']['rows'][d]),
                 'raw_close':a['close'],'adjusted_close':b['close'],'factor':b['close']/a['close'],
                 'verified_corporate_action_factor':False})
    csv('explicit-adjustment-differences.csv',pair_diffs)

    # All known official versions for Oct19, with their preserved probe outcome.
    official_by_market={'KOSPI':{},'KOSDAQ':{}}
    probe=load(FULL/'collection/official-reference-probe.json'); identify(FULL/'collection/official-reference-probe.json')
    for r in probe['records']:
        if r['status']!='received_unreconciled':continue
        p=FULL/'collection'/r['file'];assert sha(p)==r['sha256'];identify(p);x=load(p)
        assert r['http']==200 and r['url']==x['url'] and r['observed_at']==x['observed_at']
        market=x['market'];rows=x['response']['OutBlock_1']
        envelope={'url':x['url'],'http':r['http'],'rows':rows,'rows_sha256':evidence_digest(rows),
                  'observed_at':x['observed_at'],'response_source':{'file':str(p.relative_to(REPO)),'sha256':r['sha256']},
                  'http_outcome_source':{'file':str((FULL/'collection/official-reference-probe.json').relative_to(REPO)),
                                         'sha256':sha(FULL/'collection/official-reference-probe.json'),'status':r['status']}}
        official_by_market[market].setdefault('2026-10-19',[]).append(envelope)
    # Earlier stored references are not silently ignored if they disagree.
    refs,refdocs,_=old.references([])
    overlap_reference=[]
    for r in active[active.type=='adjusted_chart_observation_drift'].to_dict('records'):
        official=refs.get((r['symbol'],r['date']),set())
        left=tuple(r['original_'+k] for k in FIELDS);right=tuple(r['candidate_'+k] for k in FIELDS)
        overlap_reference.append({'symbol':r['symbol'],'name':r['name'],'date':r['date'],
            'old_close':r['original_close'],'new_close':r['candidate_close'],
            'official_matches_old_all_six':official=={left},'official_matches_new_all_six':official=={right},
            'all_existing_link_holds_preserved':records[r['symbol']]['link_reasons']})
    csv('overlap-23-reference.csv',overlap_reference)
    linked=baseline[baseline.link_status=='linked_history_validated']
    snapshot_audit=[]; research_rows=[]; overlays={}
    for r in linked.to_dict('records'):
        p=old.OUT/'store/observations'/(r['link_sha256']+'.json');identify(p);link=Store.read(p)['data'];s=r['symbol']
        assert digest(link)==r['link_sha256']
        overlay=research_snapshot_view(link,latest_prices[s],cps,official_by_market[r['market']],pub['sha256'])
        for a in overlay['audit']:
            assert refs[(s,a['date'])]=={six(a['candidate'])},'another official version conflicts'
            f=cps[a['date']]['records'][s]['flow'];b=a['candidate'];q=a['original']
            snapshot_audit.append({'symbol':s,'name':r['name'],'market':r['market'],'date':a['date'],
               'db_raw_close':q['close'],'db_adjusted_close':q['adjusted_close'],'candidate_close':b['close'],
               'db_volume':q['volume'],'candidate_volume':b['volume'],'db_turnover':q['turnover'],'candidate_turnover':b['turnover'],
               'old_flow_volume':float(f['acml_vol']),'old_flow_turnover':float(f['acml_tr_pbmn']),
               'official_six_fields_match':True,'snapshot_adjusted_close_unchanged':q['adjusted_close']==b['close'],
               'source_collection_time':a['checkpoint_observed_at'],'official_observed_at':a['official_proofs'][0]['observed_at'],
               'cause':a['reason'],'prospective':False})
        overlays[s]=overlay
        c=classify_link(link,latest_flows[s])
        research_rows.append({'symbol':s,'name':r['name'],'market':r['market'],'price_link_status':link['status'],
             'comparison_status':overlay['status'],'official_reconciled_dates':','.join(x['date'] for x in overlay['audit']),
             **c,'link_sha256':r['link_sha256'],'signal_registered':False})
    csv('snapshot-31-comparison.csv',snapshot_audit)
    dump('research-overlays.json',{'snapshot_sha256':pub['sha256'],'retrospective_only':True,'stocks':overlays})

    # Expand the source search to all archived versions AND saved history exports.
    index={}; inventory={r['path']:r for r in load(REPO/'audit-archive/inventory.json')['files']};identify(REPO/'audit-archive/inventory.json')
    archive_audit=[];targets=set(linked.symbol)
    for archive in sorted((REPO/'audit-archive').glob('*.tar.xz')):
        if archive.name=='previous-publications.tar.xz':continue # normalized DB is not original flow evidence
        identity=identify(archive);found=0;stocks=set();objects=0
        with tarfile.open(archive) as tf:
            for member in tf:
                if not member.isfile() or not member.name.endswith(('.json','.json.gz')):continue
                raw=tf.extractfile(member).read();h=hashlib.sha256(raw).hexdigest()
                assert inventory[member.name]['sha256']==h
                x=json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
                if not isinstance(x,dict) or x.get('symbol') not in targets:continue
                objects+=1;s=x['symbol'];n,kind=add_evidence(index,s,x,{**identity,'member':member.name,'member_sha256':h},now)
                found+=n
                if n:stocks.add(s)
        archive_audit.append({'file':identity['file'],'sha256':identity['sha256'],'target_objects':objects,'original_rows':found,'target_stocks':len(stocks)})
    for day,c in cps.items():
        for s in targets:add_evidence(index,s,c,cp_sources[day],now)
    history=REPO/'staging/kis_history_20261014.json.gz';replay=REPO/'research/daily-replay.json.gz'
    for p in [history,replay]:
        identity=identify(p);x=load(p)
        for s in targets:add_evidence(index,s,x,identity,now)
    for rel in ['.github/scripts/kis_enrich_collect.py','.github/scripts/kis_history_probe.py',
                'scripts/build_daily_replay.py','scripts/publish_snapshot.py','screener/daily.py',
                'reports/taewon-20261020/investor_trade_by_stock_daily.py','research/full-market/docs/official_itemchart.py']:
        identify(REPO/rel)
    flow_audit=[];cache={};folder=OUT/'flow-evidence';folder.mkdir(exist_ok=True)
    for r in research_rows:
        s=r['symbol'];days=latest_sessions[r['market']][-20:]
        replay=select_flow_replay(index,s,days,latest_flows[s]);verify_replay_sources(replay,REPO,cache)
        r['original_flow_replay_ready']=replay['status']=='replay_ready'
        was=records[s]['original_flow_replay_ready']
        flow_audit.append({'symbol':s,'name':r['name'],'previous_snapshot_compatible':records[s]['snapshot_compatible'],
            'previous_replay_ready':was,'replay_status':replay['status'],'rows_replayed':len(replay['rows']),
            'missing_dates':','.join(x['date'] for x in replay['errors']),
            'reasons':';'.join(sorted({x['reason'] for x in replay['errors']})),
            'sources':json.dumps(replay['sources'],ensure_ascii=False),
            'timestamp_upper_bound_used':any('upper_bound' in x['representation'] for x in replay['sources'])})
        path=folder/(digest(replay)+'.json.gz')
        raw=gzip.compress(json.dumps(replay,ensure_ascii=False,sort_keys=True,allow_nan=False).encode(),mtime=0)
        if not path.exists():path.write_bytes(raw)
        else:assert path.read_bytes()==raw
        r['flow_evidence']=str(path.relative_to(OUT));r['registration_status']='hold_missing_current_KRX_and_next_session_evidence'
    csv('flow-restoration.csv',flow_audit);csv('research-classification.csv',research_rows);csv('archive-search.csv',archive_audit)
    a16=active[active.comparison_source.str.contains('checkpoint-20261016')]
    samepair=a16[a16.later_raw_equals_later_adjusted==True]
    partitions=active.groupby('symbol').type.apply(lambda s:';'.join(sorted(set(s))))
    csv('conflict-types-by-stock.csv',[{'symbol':s,'name':records[s]['name'],'market':records[s]['market'],
         'types':types,'prior_37_quarantine':records[s]['prior_37_quarantine']} for s,types in partitions.items()])
    requests=[]
    for r in a16[['symbol','name','market']].drop_duplicates().to_dict('records'):
        requests.append({**r,'date':'2026-10-16','purpose':'resolve_old_and_later_OHLCV_versions','request_group':r['market']+'-20261016'})
    for r in linked.to_dict('records'):
        requests.append({k:r[k] for k in ['symbol','name','market']}|{'date':pub['asof'],
            'purpose':'current_day_official_acceptance','request_group':r['market']+'-20261020'})
    csv('official-price-targets.csv',requests)
    # Independent SQL comparison includes quantities, which the original amount
    # replay gate does not use. No normalization from DB is written to evidence.
    import duckdb
    with duckdb.connect(str(latest/'dashboard.duckdb'),read_only=True) as c:
        sql={(s,str(d)):(a,q) for s,d,a,q in c.execute('select symbol,trade_date,net_amount,net_quantity from flows').fetchall()}
    amount_checks=quantity_checks=0
    for r in research_rows:
        wrapper=load(OUT/r['flow_evidence'])
        for v in wrapper['rows']:
            a,q=sql[r['symbol'],old.iso(v['stck_bsop_date'])]
            assert int(v['frgn_ntby_tr_pbmn'])*1_000_000==a;amount_checks+=1
            if 'frgn_ntby_qty' in v:assert int(v['frgn_ntby_qty'])==q;quantity_checks+=1
    summaries={
        'asof':pub['asof'],'main_read_commit':MAIN,'snapshot_sha256':pub['sha256'],'population':len(baseline),
        'unresolved_conflict_stocks':active.symbol.nunique(),
        'legacy_raw_adjusted_label':int(baseline.link_reasons.str.contains('raw_adjusted_conflict_unresolved').sum()),
        'explicit_raw_adjusted_pair_stocks':len({r['symbol'] for r in pair_diffs}),
        'source_type_unique_counts':active.groupby('type').symbol.nunique().to_dict(),
        'exclusive_type_partitions':partitions.value_counts().to_dict(),
        'oct16_stocks':a16.symbol.nunique(),'oct16_market_counts':a16.groupby('market').symbol.nunique().to_dict(),
        'oct16_original_flow_equals_original_chart':int((a16.old_chart_equals_old_investor==True).sum()),
        'oct16_later_pair_available':int(a16.later_raw_equals_later_adjusted.notna().sum()),
        'oct16_later_raw_adjusted_identical':samepair.symbol.nunique(),
        'oct16_pair_identical_and_candidate_equal':samepair[samepair.later_raw_equals_candidate==True].symbol.nunique(),
        'oct16_changed_volume_or_turnover':a16[a16.changed_fields.str.contains('volume|turnover')].symbol.nunique(),
        'oct16_field_patterns':a16.changed_fields.value_counts().to_dict(),
        'oct16_old_chart_adjustment_flags':a16.old_chart_flags.map(str).value_counts().to_dict(),
        'adjusted_overlap_stocks':active[active.type=='adjusted_chart_observation_drift'].symbol.nunique(),
        'overlap23_new_matches_official':sum(r['official_matches_new_all_six'] for r in overlap_reference),
        'linked_history_validated_unchanged':len(linked),'prior_quarantine37_unchanged':int(baseline.prior_37_quarantine.sum()),
        'snapshot31_officially_reconciled':len(snapshot_audit),'research_comparison_passed':sum(r['comparison_status']=='research_comparison_passed' for r in research_rows),
        'previous196_missing50_restored':sum((not r['previous_replay_ready']) and r['previous_snapshot_compatible'] and r['replay_status']=='replay_ready' for r in flow_audit),
        'all_linked_original20_replay_ready':sum(r['replay_status']=='replay_ready' for r in flow_audit),
        'independent_original_amount_checks':amount_checks,'independent_original_quantity_checks':quantity_checks,
        'research_groups':dict(collections.Counter(r['group'] for r in research_rows if r['group'])),
        'registered_signals':0,'market_API_calls':0,'new_schedules':0,
        'latest_vs_previous_snapshot_changed_rows':sum(previous_prices.get(s,{}).get(d)!=v for s,days in latest_prices.items() for d,v in days.items()),
        'latest_vs_previous_snapshot_flow_equal':latest_flows==previous_flows,
        'source_observation_times':{d:{'first':min(str(r.get('observed_at','')) for r in cp['records'].values()),
                                     'last':max(str(r.get('observed_at','')) for r in cp['records'].values())} for d,cp in cps.items()},
        'run_observed_at':run_at,'evidence_captured_at':now,'elapsed_seconds':time.monotonic()-started,
        'result_scope':'retrospective_research_only; no real signal or price finality promotion'}
    for name,h in protected.items():assert sha(REPO/name)==h,'protected input changed: '+name
    dump('input-manifest.json',{'sources':ids,'known_at':now,'main_commit':MAIN,'publication':pub})
    dump('protection.json',{'protected_hashes':protected,'all_unchanged':True,'main_branch_not_modified':True})
    dump('SUMMARY.json',summaries);print(json.dumps(summaries,ensure_ascii=False,indent=2))


if __name__=='__main__':
    def denied(*args,**kwargs):raise RuntimeError('No network or market API permitted in offline diagnosis')
    socket.socket.connect=denied
    main()
