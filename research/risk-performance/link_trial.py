"""Manual research replay. Network denied, production inputs read-only."""
from pathlib import Path
import collections,datetime as dt,gzip,json,subprocess,time,tarfile,hashlib
import pandas as pd
from engine import digest,instant
from forward_system import (Store,load,sha,utcnow,validate_publication,snapshot_rows,
                            official_next_session,FIELDS,KIS_FIELDS,KRX_FIELDS)
from price_link import link_history,classify_link,normalized,make_forward_bundle,six
from screener.liquidity_quality import digest as evidence_digest

ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1];FULL=ROOT.parent/'full-market'
OUT=ROOT/'price-link-study';SNAP=ROOT/'forward-study/inputs'

def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def table(name,rows):pd.DataFrame(rows).to_csv(OUT/name,index=False,encoding='utf-8-sig')
def iso(s):return dt.datetime.strptime(s,'%Y%m%d').date().isoformat()


def references(source_ids):
    p=SNAP/'reference-audit';result={};docs={};accepted=0
    for e in load(p/'catalog.json'):
        f=p/e['saved_file'];assert sha(f)==e['sha256'];x=load(f)
        if not isinstance(x,dict) or x.get('http')!=200 or not x.get('rows'):continue
        rows=x['rows']
        if x.get('rows_sha256')!=evidence_digest(rows):continue
        add_reference(x,e['sha256'],result,docs);accepted+=1
    # The previous successful KRX probe retains raw response and HTTP outcome separately.
    for rec in load(FULL/'collection/official-reference-probe.json')['records']:
        if rec['status']!='received_unreconciled':continue
        f=FULL/'collection'/rec['file'];assert sha(f)==rec['sha256'];x=load(f)
        wrapped={'url':x['url'],'rows':x['response']['OutBlock_1'],'observed_at':x['observed_at']}
        add_reference(wrapped,rec['sha256'],result,docs);accepted+=1
    source_ids.append({'file':str((p/'catalog.json').relative_to(REPO)),'sha256':sha(p/'catalog.json')})
    return result,docs,accepted


def add_reference(x,h,result,docs):
    for market,part in [('KOSPI','stk'),('KOSDAQ','ksq')]:
        prefix=f'https://data-dbg.krx.co.kr/svc/apis/sto/{part}_bydd_trd?basDd='
        if not x['url'].startswith(prefix):continue
        day=iso(x['url'][len(prefix):]);instant(x['observed_at'])
        rows=x['rows']
        if len({r['ISU_CD'] for r in rows})!=len(rows):raise ValueError('official_duplicate_symbol')
        for r in rows:
            if r['BAS_DD']!=day.replace('-',''):raise ValueError('official_wrong_date')
            if r.get('MKT_NM')!=market:raise ValueError('official_wrong_market')
            result.setdefault((r['ISU_CD'],day),set()).add(tuple(float(r[k]) for k in KRX_FIELDS))
        docs[h]={'url':x['url'],'observed_at':x['observed_at'],'market':market,'asof':day}


def saved_flow_evidence(cps,source_ids):
    """Keep distinct original versions; never manufacture API rows from DB values."""
    records={}
    def add(symbol,raw,identity,known):
        if not raw:return
        instant(known)
        for row in raw:
            if 'stck_bsop_date' not in row or 'frgn_ntby_tr_pbmn' not in row:continue
            day=iso(row['stck_bsop_date']);amount=int(row['frgn_ntby_tr_pbmn'])*1_000_000
            records.setdefault(symbol,{}).setdefault(day,{}).setdefault(amount,{'row':row,'source':identity,'observed_at':known})
    archive=REPO/'audit-archive/former-observations-market.tar.xz'
    inventory={r['path']:r['sha256'] for r in load(REPO/'audit-archive/inventory.json')['files']}
    archive_id={'file':str(archive.relative_to(REPO)),'sha256':sha(archive)}
    source_ids.append(archive_id)
    with tarfile.open(archive) as tf:
        for member in tf:
            if not member.isfile() or not member.name.endswith('.json.gz'):continue
            raw=tf.extractfile(member).read();h=hashlib.sha256(raw).hexdigest();assert inventory[member.name]==h
            b=json.loads(gzip.decompress(raw));e=b.get('daily_acceptance',{})
            if b.get('exchange')=='KRX' and e.get('flow_endpoint')=='KIS FHPTJ04160001 / J / million KRW to KRW':
                add(b['symbol'],e.get('raw_flow_rows',[]),{**archive_id,'member':member.name,'member_sha256':h},e.get('observed_at') or b['observed_at'])
    for sid,c in cps:
        for symbol,r in c['records'].items():add(symbol,[r['flow']],sid,r.get('observed_at') or c['updated_at'])
    return records


def main():
    start=time.monotonic();OUT.mkdir(exist_ok=True);store=Store(OUT/'store')
    pub=validate_publication(SNAP);day=pub['asof'];now=utcnow()
    # Pin the newly checked latest main, and prove publication inputs unchanged.
    commit='e376ea00f4904d3ead312198267ca5736e3767b7'
    source_ids=[]
    def source(p):
        r={'file':str(p.relative_to(REPO)),'sha256':sha(p)};source_ids.append(r);return r
    for name,path in [('dashboard.duckdb','snapshot/dashboard.duckdb'),('manifest.json','snapshot/manifest.json'),('daily-validation.json','snapshot/daily-validation.json'),('publication-ledger.json','snapshot/publication-ledger.json'),('index.json','snapshot/briefing/index.json')]:
        import hashlib
        assert hashlib.sha256(subprocess.check_output(['git','show',commit+':'+path],cwd=REPO)).hexdigest()==sha(SNAP/name)
        source(SNAP/name)
    hashes=load(FULL/'results/artifact-hashes.json')
    for rel in ['results/research-prices.parquet','results/all-stocks.csv','collection/calendar.json']:
        assert sha(FULL/rel)==hashes[rel];source(FULL/rel)
    previous=pd.read_csv(FULL/'results/all-stocks.csv',dtype={'symbol':str}).fillna('').set_index('symbol')
    prices=pd.read_parquet(FULL/'results/research-prices.parquet')
    price_groups={s:g for s,g in prices.groupby('symbol')}
    cps=[]
    for p in [ROOT/'inputs/checkpoint-20261015.json.gz',ROOT/'inputs/checkpoint-20261016.json.gz',FULL/'inputs/checkpoint-20261019.json.gz',FULL/'inputs/checkpoint-20261020.json.gz']:
        cps.append((source(p),load(p)))
    cp=cps[-1][1];assert cp['trade_date']==day.replace('-','')
    remote_cp='c9e24ddc6a597233fc46da5c1e2f4eeeaa13da6b'
    assert hashlib.sha256(subprocess.check_output(['git','show',remote_cp+':daily-results/bundle.json.gz'],cwd=REPO)).hexdigest()==cps[-1][0]['sha256']
    universe,snapshot_prices,flowmap,db_sessions,excluded=snapshot_rows(SNAP)
    official_calendar=load(FULL/'collection/calendar.json')
    assert official_calendar['status']=='passed'
    actual_sessions={'KOSPI':set(),'KOSDAQ':set()}
    for e in official_calendar['observations']:
        f=FULL/'collection'/e['file'];assert sha(f)==e['sha256'];source(f)
        response=load(f);m=e['market']
        assert response['market']==m and response['params']['FID_COND_MRKT_DIV_CODE']=='U'
        assert response['params']['FID_INPUT_ISCD']=={'KOSPI':'0001','KOSDAQ':'1001'}[m]
        assert response['response']['rt_cd']=='0'
        actual_sessions[m].update(iso(r['stck_bsop_date']) for r in response['response']['output2'])
    for m in actual_sessions:
        assert actual_sessions[m]==set(official_calendar['market_sessions'][m])
        assert set(db_sessions[m])-actual_sessions[m]<={day}
    assert cp['holiday']['bass_dt']==day.replace('-','') and cp['holiday']['opnd_yn']=='Y' and cp['holiday']['tr_day_yn']=='Y'
    sessions={m:sorted(set(official_calendar['market_sessions'][m]+db_sessions[m])) for m in ['KOSPI','KOSDAQ']}
    calendar={**official_calendar,'sessions':sorted(set(sum(sessions.values(),[])))}
    # One retained holiday row for the current day cannot prove the next session.
    try:official_next_session(day,calendar,now);calendar_reason=''
    except ValueError as e:calendar_reason=str(e)
    refs,refdocs,refcount=references(source_ids)
    raw_flow_evidence=saved_flow_evidence(cps,source_ids)
    quarantined=set(pd.read_csv(ROOT/'forward-study/krx-crosscheck-37.csv',dtype={'symbol':str}).symbol)
    pilot=load(FULL/'inputs/pilot/run.json')['records'];pairs=load(FULL/'collection/state.json')['records']
    rows=[];conflict_rows=[];classified=[];source_base=list(source_ids);preparation_errors=collections.Counter()
    for symbol,name,market,listing in universe:
        prior=previous.loc[symbol] if symbol in previous.index else pd.Series({'reasons':'previous_research_symbol_missing','observed_at':pub['known_at']});g=price_groups.get(symbol)
        old={r['trade_date']:{k:r[k] for k in FIELDS} for r in g.to_dict('records')} if g is not None else {}
        observations=[];ids=list(source_base);prior_reasons=prior.reasons.split(';');record=cp['records'].get(symbol,{})
        for sid,c in cps:
            r=c['records'].get(symbol)
            if not r:continue
            f=r['flow'];d=iso(f['stck_bsop_date'])
            observations.append({'date':d,'values':{k:float(f[v]) for k,v in zip(FIELDS,KIS_FIELDS)},'source':sid['file']+':'+sid['sha256']})
            if d in flowmap.get(symbol,{}) and flowmap[symbol][d]!=(int(f['frgn_ntby_tr_pbmn'])*1_000_000,'KRX'):
                prior_reasons.append('checkpoint_snapshot_flow_mismatch')
        # Verify preserved request identity and raw/adjusted pairs; no requery.
        pair=pilot.get(symbol) or pairs.get(symbol);base=FULL/'inputs/pilot' if symbol in pilot else FULL/'collection'
        if pair:
            parsed={}
            for kind,e in pair.items():
                f=base/e['file'];assert sha(f)==e['sha256'];x=load(f);ids.append({'file':str(f.relative_to(REPO)),'sha256':e['sha256']})
                p=x.get('params',{})
                if p.get('FID_INPUT_ISCD')!=symbol or p.get('FID_COND_MRKT_DIV_CODE')!='J' or p.get('FID_ORG_ADJ_PRC')!=('0' if kind=='adjusted' else '1') or x.get('rt_cd')!='0' or x.get('output1',{}).get('stck_shrn_iscd')!=symbol:
                    prior_reasons.append('raw_pair_scope_mismatch');continue
                parsed[kind]=normalized(x['output2'])
            if 'raw' in parsed:
                observations.extend({'date':d,'values':b,'source':next(e['sha256'] for k,e in pair.items() if k=='raw')} for d,b in parsed['raw'].items())
            else:prior_reasons.append('raw_pair_missing')
        official={d:values for (s,d),values in refs.items() if s==symbol}
        known=max([pub['known_at'],str(prior.observed_at),record.get('observed_at',pub['known_at']),*[r['observed_at'] for r in refdocs.values()]],key=instant)
        link=link_history(symbol,market,day,str(listing),old,record,sessions[market],prior_reasons,observations,official,ids,known,symbol in quarantined)
        if symbol in excluded:link['reasons']=sorted(set(link['reasons']+['excluded_or_suspended']));link['status']='hold'
        classification=classify_link(link,flowmap.get(symbol,{}));snapshot_conflicts=[]
        for b in link['bars'][-40:]:
            q=snapshot_prices.get(symbol,{}).get(b['trade_date'])
            if q and (q['adjusted_close']!=b['close'] or any(q[k]!=b[k] for k in ['volume','turnover'])):snapshot_conflicts.append(b['trade_date'])
        flow_replay_ok=all(len(raw_flow_evidence.get(symbol,{}).get(d,{}))==1 and flowmap.get(symbol,{}).get(d)==(next(iter(raw_flow_evidence[symbol][d])),'KRX') for d in sessions[market][-20:])
        # These are different from overlap consistency: registration keeps existing gates.
        blockers=[]
        if link['status']!='linked_history_validated':blockers.extend(link['reasons'])
        if classification['status']=='unclassifiable':blockers.append(classification['reason'])
        if snapshot_conflicts:blockers.append('snapshot_price_overlap_conflict')
        if (symbol,day) not in refs:blockers.append('latest_official_KRX_evidence_missing')
        if calendar_reason:blockers.append(calendar_reason)
        if not flow_replay_ok:blockers.append('exact_20_raw_flow_replay_missing_or_conflicting')
        if not blockers:blockers.append('exact_20_raw_flow_replay_and_registration_gate_required')
        item={'symbol':symbol,'name':name,'market':market,'asof':day,'link_status':link['status'],
          'price_days':len(link['bars']),'validated_window_days':len(link['validated_window']),'overlap_days':link['overlap_days'],'missing_dates':','.join(link['missing_dates']),
          'link_reasons':';'.join(link['reasons']),'unresolved_price_conflict':bool(link['conflicts']),
          'unresolved_conflict_dates':','.join(sorted({r['date'] for r in link['conflicts']})),
          'official_resolved_count':len(link['official_resolved_observations']),
          'snapshot_conflict_dates':','.join(snapshot_conflicts),'prior_37_quarantine':symbol in quarantined,
          'classification_status':classification['status'],'research_group':classification['group'],
          'classification_reason':classification['reason'],'registration_status':'hold','registration_blockers':';'.join(sorted(set(blockers))),
          'link_sha256':digest(link),'signal_registered':False,'flow_evidence_file':None,'original_flow_replay_ready':flow_replay_ok,
          'snapshot_compatible':not snapshot_conflicts,
          'integrated_research_status':classification['status'] if not snapshot_conflicts else 'unclassifiable',
          'integrated_research_group':classification['group'] if not snapshot_conflicts else None}
        for key in ['price_structure','net_5d_krw','previous_5d_krw','change_5d_krw','net_20d_krw','rise_pct']:item[key]=classification.get(key)
        if link['status']=='linked_history_validated':
            store.put('observations',digest(link),{'kind':'validated_linked_history','data':link})
            raw_records=[];raw_sources=[]
            if flow_replay_ok:
                chosen=[next(iter(raw_flow_evidence[symbol][d].values())) for d in sessions[market][-20:]]
                raw_records=[r['row'] for r in chosen]
                source_map={digest({**r['source'],'observed_at':r['observed_at']}):{**r['source'],'observed_at':r['observed_at']} for r in chosen}
                raw_sources=list(source_map.values())
                wrapper={'symbol':symbol,'asof':day,'rows':raw_records,'sources':raw_sources}
                # saved_flow_evidence already hash-checked each source and extracted
                # these exact rows in one archive pass. Do not decompress per stock.
                assert all(raw_flow_evidence[symbol][iso(r['stck_bsop_date'])][int(r['frgn_ntby_tr_pbmn'])*1_000_000]['row']==r for r in raw_records)
                folder=OUT/'flow-evidence';folder.mkdir(exist_ok=True)
                path=folder/(digest(wrapper)+'.json.gz')
                payload=gzip.compress(json.dumps(wrapper,ensure_ascii=False,sort_keys=True).encode(),mtime=0)
                if path.exists():assert path.read_bytes()==payload
                else:
                    with path.open('xb') as stream:stream.write(payload)
                item['flow_evidence_file']=str(path.relative_to(OUT))
            # Exact-date official data are absent in this pinned trial. Exercise the
            # preparation gate rather than promoting overlap success by assertion.
            assert (symbol,day) not in refs
            try:make_forward_bundle(link,flowmap[symbol],raw_records,raw_sources,None,now.isoformat())
            except ValueError as error:preparation_errors[str(error)]+=1
            else:raise AssertionError('missing official evidence was accepted')

            classified.append({'symbol':symbol,'name':name,**classification,'link_sha256':digest(link)})
        for resolved,key in [(False,'conflicts'),(True,'official_resolved_observations')]:
            for c in link[key]:conflict_rows.append({'symbol':symbol,'name':name,'date':c['date'],'comparison_source':c['source'],'official_resolved':resolved,**{'original_'+k:c['left'][k] for k in FIELDS},**{'candidate_'+k:c['right'][k] for k in FIELDS}})
        rows.append(item)
    assert len(rows)==2429 and len({r['symbol'] for r in rows})==2429
    table('all-stocks.csv',rows);table('observation-comparison.csv',conflict_rows);table('research-classification.csv',classified)
    stats={'asof':day,'main_read_commit':commit,'checkpoint_commit':remote_cp,'population':len(rows),
       'price_40_covered':sum(r['price_days']>=40 for r in rows),
       'linked_history_validated':sum(r['link_status']=='linked_history_validated' for r in rows),
       'linked_hold':sum(r['link_status']=='hold' for r in rows),
       'adjusted_overlap_conflict':sum('adjusted_overlap_conflict' in r['link_reasons'].split(';') for r in rows),
       'unresolved_price_conflict_stocks':sum(r['unresolved_price_conflict'] for r in rows),
       'raw_adjusted_unresolved':sum('raw_adjusted_conflict_unresolved' in r['link_reasons'].split(';') for r in rows),
       'prior_37_still_quarantined':sum(r['prior_37_quarantine'] for r in rows),
       'classification_states':dict(collections.Counter(r['classification_status'] for r in rows)),
       'research_only_group_counts':dict(collections.Counter(r['research_group'] for r in rows if r['research_group'])),
       'snapshot_compatible_linked':sum(r['link_status']=='linked_history_validated' and r['snapshot_compatible'] for r in rows),
       'integrated_research_states':dict(collections.Counter(r['integrated_research_status'] for r in rows)),
       'integrated_research_groups':dict(collections.Counter(r['integrated_research_group'] for r in rows if r['integrated_research_group'])),
       'raw_flow_replay_ready':sum(r['original_flow_replay_ready'] for r in rows),
       'linked_raw_flow_replay_ready':sum(r['original_flow_replay_ready'] and r['link_status']=='linked_history_validated' for r in rows),
       'preparation_gate_errors':dict(preparation_errors),'registration_eligible':0,'registered_signals':0,
       'link_reason_counts':dict(collections.Counter(reason for r in rows for reason in r['link_reasons'].split(';') if reason)),
       'official_reference_dates':sorted({d for _,d in refs}),'reference_files_used':refcount,
       'calendar_blocker':calendar_reason,'elapsed_seconds':time.monotonic()-start,
       'market_API_calls':0,'new_schedules':0,'known_at':now.isoformat(),'all_results_are_retrospective_research':True}
    dump(OUT/'SUMMARY.json',stats);dump(OUT/'input-manifest.json',{'publication':pub,'main_commit':commit,'checkpoint_commit':remote_cp,'sources':source_ids,'official_reference_sources':refdocs,'known_at':now.isoformat()})
    store.put('runs',digest(stats),stats)
    print(json.dumps(stats,ensure_ascii=False,indent=2))

if __name__=='__main__':
    import socket
    def denied(*args,**kwargs):raise RuntimeError('No network/API permitted')
    socket.socket.connect=denied
    main()
