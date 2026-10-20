"""Read-only historical feasibility audit and finite-sample research output.
No HTTP clients, tokens, collection calls, production mutation or fabricated fills.
"""
from pathlib import Path
import collections, datetime as dt, gzip, hashlib, importlib.util, json, sys, tarfile
import math
import duckdb
import pandas as pd

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[1]
FULL=REPO/'research/full-market'
sys.path[:0]=[str(REPO),str(FULL)]
from screener.liquidity_quality import daily_accepted, blocking_conflicts
from screener.market_validation import validate
from engine import cohort, decision_gate, instant, next_session, digest, suppress_duplicates
spec=importlib.util.spec_from_file_location('preserved_full_census',FULL/'analyze.py')
census=importlib.util.module_from_spec(spec);spec.loader.exec_module(census)

def load(p):
    raw=Path(p).read_bytes()
    return json.loads(gzip.decompress(raw) if str(p).endswith('.gz') else raw)

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write_json(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def csv(name,rows,columns=None):pd.DataFrame(rows,columns=columns).to_csv(ROOT/name,index=False,encoding='utf-8-sig')

def bundle_times(b):
    e=b.get('daily_acceptance',{});r=e.get('krx_row',{})
    times=[b.get('observed_at'),e.get('observed_at'),r.get('_retrieved_at')]
    if any(not x for x in times):return None
    times += [x['observed_at'] for x in e.get('flow_sources',[]) if x.get('observed_at')]
    return times

def evaluate_bundle(b,calendar):
    reasons=validate(b,b['asof'],minimum_bars=40)
    if not daily_accepted(b):reasons.append('exact_daily_evidence_not_accepted')
    if blocking_conflicts(b):reasons.append('active_price_conflict')
    dates=[r['trade_date'] for r in b['bars']]
    if any(r['volume']<=0 or r['turnover']<=0 for r in b['bars'][-40:]):reasons.append('zero_trading_last40')
    # Preserve existing corporate-action hold; no new threshold or guessed factor.
    actions=b.get('corporate_actions',[])
    if actions and not b.get('corporate_actions_verified'):reasons.append('corporate_action_unverified')
    times=bundle_times(b)
    try:
        ok,why=decision_gate(b['asof'],calendar,times or [],max(times,key=instant) if times else b['observed_at'])
    except (ValueError,KeyError,TypeError):ok,why=False,'missing_or_invalid_observation_time'
    if not ok:reasons.append(why)
    return sorted(set(reasons))

def main():
    source=load(ROOT/'inputs/source.json')
    for n,h in source['files'].items():assert sha(ROOT/'inputs'/n)==h
    c=duckdb.connect(str(ROOT/'inputs/dashboard.duckdb'),read_only=True)
    ranges={t:c.execute(f'select cast(min(trade_date) as varchar),cast(max(trade_date) as varchar),count(distinct trade_date),count(*) from {t}').fetchone() for t in ['prices','flows','calendar','benchmarks']}
    latest=ranges['prices'][1]
    memberships=c.execute('select symbol,market,sector,cast(valid_from as varchar) valid_from,cast(valid_to as varchar) valid_to from memberships').fetchdf()
    instruments=c.execute('select symbol,name,market,cast(listing_date as varchar) listing_date from instruments').fetchdf().set_index('symbol')
    c.close()
    original=pd.read_csv(FULL/'results/all-stocks.csv',dtype={'symbol':str})
    calendar=load(FULL/'collection/calendar.json')['sessions']
    # Additional sessions are backed by the frozen calendar, never weekday guesses.
    c=duckdb.connect(str(ROOT/'inputs/dashboard.duckdb'),read_only=True)
    calendar=sorted(set(calendar)|{str(x[0]) for x in c.execute('select distinct trade_date from calendar').fetchall()});c.close()
    assert latest in calendar
    inventory={x['path']:x for x in load(REPO/'audit-archive/inventory.json')['files']}
    archive_audit=[]; selected={}; accepted_days=set(); observations=[]; all_dates=set(); accepted_before=0
    archive_hashes={}
    for suffix in ['market','history','observations','pending','candidates']:
        archive=REPO/'audit-archive'/f'former-observations-{suffix}.tar.xz'
        archive_hashes[str(archive.relative_to(REPO))]=sha(archive)
        with tarfile.open(archive) as tf:
            for m in tf:
                if not m.isfile() or not m.name.endswith(('.json','.gz')):continue
                raw=tf.extractfile(m).read(); h=hashlib.sha256(raw).hexdigest()
                assert inventory[m.name]['sha256']==h
                b=json.loads(gzip.decompress(raw) if m.name.endswith('.gz') else raw)
                if not isinstance(b,dict) or not b.get('bars'):continue
                day=b['asof'];code=b['symbol'];all_dates.update(r['trade_date'] for r in b['bars'])
                if b.get('observed_at'):observations.append(b['observed_at'])
                accepted=daily_accepted(b)
                if accepted:accepted_days.add(day)
                reasons=evaluate_bundle(b,calendar) if day in calendar else ['no_calendar_session']
                times=bundle_times(b)
                known=max(times,key=instant) if times else None
                archive_audit.append({'symbol':code,'asof':day,'archive':str(archive.relative_to(REPO)),'member':m.name,'sha256':h,
                  'observed_at':b.get('observed_at'),'evidence_known_at':known,'daily_evidence_accepted':accepted,
                  'pit_price_flow_eligible':not reasons,'reasons':';'.join(reasons)})
                if reasons:continue
                accepted_before+=1;key=(code,day)
                if key not in selected or instant(known)>instant(selected[key]['known']):
                    selected[key]={'bundle':b,'raw':raw,'sha256':h,'known':known,'archive':str(archive.relative_to(REPO)),'member':m.name}
    csv('archive-audit.csv',archive_audit)
    write_json(ROOT/'archive-hashes.json',archive_hashes)
    print('Archive audit complete:',len(archive_audit),'versions;',len(selected),'eligible stock/dates',flush=True)

    records=[]; detailed=[]; selected_dir=ROOT/'inputs/point-in-time';selected_dir.mkdir(exist_ok=True)
    original15=load(ROOT/'inputs/checkpoint-20261015.json.gz')
    dated_universe={r['symbol']:r for r in original15['universe']}
    assert instant(original15['updated_at'])<instant('2026-10-16T09:00:00+09:00')
    for (code,day), item in sorted(selected.items()):
        b=item['bundle']; name=dated_universe.get(code,{}).get('name',code)
        path=selected_dir/(item['sha256']+('.json.gz' if item['member'].endswith('.gz') else '.json'))
        if not path.exists():path.write_bytes(item['raw'])
        dates=[r['trade_date'] for r in b['bars']]; bars={r['trade_date']:r for r in b['bars']}
        events=census.price_events(dates,bars);state,e,_=census.current_state(dates,bars,events)
        flows=b['flows'][-20:];n5=sum(r['net_amount'] for r in flows[-5:]);prev=sum(r['net_amount'] for r in flows[-10:-5]);improving=n5>0 and n5>prev
        close=bars[day]['close'];group=cohort(state,improving,close,e['L2_price'] if e else None,e.get('H3_price') if e else None)
        detailed.append({'symbol':code,'asof':day,'name':name,'group':group,'price_structure':state,'flow_improving':improving,'known_at':item['known']})
        if group is None:continue
        confirmation=e['confirmed_date'] if e else None
        new_c=group!='C' or any(x['stage']=='확정' and x['confirmed_date']==day for x in events)
        official=b['daily_acceptance']['krx_row'];cap=None
        try:
            cap_value=float(official['MKTCAP'])
            if cap_value==float(official['TDD_CLSPRC'])*float(official['LIST_SHRS']) and cap_value>0:cap=cap_value
        except (KeyError,ValueError,TypeError):pass
        records.append({'signal_id':digest([day,code,item['sha256'],group]),'record_kind':'historical_partial_universe','signal_date':day,
          'confirmed_date':confirmation,'symbol':code,'name':name,'market':b['market'],'group':group,'price_structure':state,
          'rise_pct':(close/e['L2_price']-1)*100 if e else None,'L2_price':e['L2_price'] if e else None,'L2_date':e['L2_date'] if e else None,
          'L3_price':e.get('L3_price') if e else None,'L3_date':e.get('L3_date') if e else None,'H3_price':e.get('H3_price') if e else None,
          'net_5d_krw':n5,'previous_5d_krw':prev,'change_5d_krw':n5-prev,'net_20d_krw':sum(r['net_amount'] for r in flows),
          'flow_asof':day,'observed_at':b['observed_at'],'quality_known_at':item['known'],'frozen_at':None,
          'pit_eligible':new_c,'complete_market_universe':False,'data_version':item['sha256'],'source':str(path.relative_to(ROOT)),
          'status':'후속 관측 대기' if new_c else '기존 C 상태·신규 돌파 사건 아님','not_investable_reason':'' if new_c else 'C_requires_same_day_confirmed_cross',
          'raw_signal_close':close,'sector':dated_universe.get(code,{}).get('sector') if day=='2026-10-15' else None,
          'sector_known_at':original15['updated_at'] if day=='2026-10-15' else None,
          'market_cap_krw_at_signal':cap,'avg_turnover_20_at_signal':sum(x['turnover'] for x in b['bars'][-20:])/20,
          'matching_input_sha256':sha(ROOT/'inputs/checkpoint-20261015.json.gz'),
          'reference_scope':'archived_price_flow_verified_subset; not whole-market strategy'})
    csv('historical-subset.csv',detailed,columns=['symbol','asof','name','group','price_structure','flow_improving','known_at'])
    # Prior 36/15/20 are references only. The 10/19 flow must NEVER be applied to
    # earlier C confirmation dates or become a historical trade retroactively.
    reference_counts=collections.Counter()
    official_known={r['market']:r['observed_at'] for r in load(FULL/'collection/official-reference-probe.json')['records']}
    for r in original.to_dict('records'):
        if not r['trusted_candidate_data'] or not r['flow_improving']:continue
        group=cohort(r['price_structure'],True,r['close'],r['L2_price'] if pd.notna(r['L2_price']) else None,r['H3_price'] if pd.notna(r['H3_price']) else None)
        reference_counts[group]+=1
        def clean(k):return r[k] if pd.notna(r[k]) else None
        records.append({'signal_id':digest(['retrospective_reference','2026-10-19',r['symbol']]),'record_kind':'retrospective_reference',
         'signal_date':'2026-10-19','confirmed_date':clean('current_stage_confirmation_date'),'symbol':r['symbol'],'name':r['name'],
         'market':r['market'],'group':group,'price_structure':r['price_structure'],'rise_pct':clean('rise_pct'),
         **{k:clean(k) for k in ['L2_price','L2_date','L3_price','L3_date','H3_price','net_5d_krw','previous_5d_krw','change_5d_krw','net_20d_krw']},
         'flow_asof':'2026-10-19','observed_at':r['observed_at'],'quality_known_at':official_known[r['market']],'frozen_at':None,'pit_eligible':False,
         'complete_market_universe':False,'data_version':sha(FULL/'results/all-stocks.csv'),'source':'../full-market/results/all-stocks.csv',
         'status':'기존 사후 재구성 참고군','not_investable_reason':'retrospective_price_or_validation; not contemporaneously frozen',
         'raw_signal_close':None,'reference_scope':'2026-10-19 cross-section only'})
    assert reference_counts['A']==36 and reference_counts['B']==15 and reference_counts['C']==20
    csv('signals.csv',records)

    # Outcome availability: raw pair data may be acquired later for measurement,
    # but is never reused as earlier signal input. Preserve basis and quality hold.
    cp=load(FULL/'inputs/checkpoint-20261020.json.gz')['records']
    raw_outcomes={}; adjusted_outcomes={}
    checkpoint_inputs=[]
    for original_source in load(ROOT/'inputs/preserved-checkpoints.json'):
        p=ROOT/'inputs'/f"checkpoint-{original_source['day']}.json.gz"
        assert sha(p)==original_source['sha256'];d=load(p)
        checkpoint_inputs.append({'day':original_source['day'],'records':len(d['records']),'updated_at':d['updated_at'],'sha256':sha(p)})
        for code,r in d['records'].items():
            raw_outcomes.setdefault(code,{})[r['flow']['stck_bsop_date']]=r['flow']
    for code,r in cp.items():
        adjusted_outcomes[code]={x['stck_bsop_date']:x for x in r['adjusted_prices']}
        f=r['flow'];raw_outcomes.setdefault(code,{})[f['stck_bsop_date']]=f
    for code,pair in load(FULL/'collection/state.json')['records'].items():
        if 'raw' not in pair:continue
        p=FULL/'collection'/pair['raw']['file'];x=load(p)
        # Preserve the original dated checkpoint observation; a later requery
        # is another version, never a replacement for the entry-day evidence.
        for row in x['output2']:
            if row.get('stck_bsop_date'):
                raw_outcomes.setdefault(code,{}).setdefault(row['stck_bsop_date'],row)
    availability=[]
    historical=[r for r in records if r['record_kind']=='historical_partial_universe' and r['pit_eligible']]
    matches=[]
    for a in [s for s in historical if s['group']=='A']:
        peers=[s for s in historical if s['group']=='D' and s['market']==a['market'] and s.get('sector') and s.get('sector')==a.get('sector')]
        eligible=[s for s in peers if all((x.get('market_cap_krw_at_signal') or 0)>0 and x['avg_turnover_20_at_signal']>0 for x in [a,s])]
        ranked=sorted(eligible,key=lambda s:(abs(math.log(s['market_cap_krw_at_signal']/a['market_cap_krw_at_signal']))+abs(math.log(s['avg_turnover_20_at_signal']/a['avg_turnover_20_at_signal'])),s['symbol']))
        if not ranked:matches.append({'A_symbol':a['symbol'],'D_symbol':None,'rank':None,'status':'동일 시장·업종·시점 검증 재무규모/유동성 자료를 갖춘 대조군 없음'})
        for i,d in enumerate(ranked[:3],1):
            matches.append({'A_symbol':a['symbol'],'D_symbol':d['symbol'],'rank':i,'status':'성과 미관측·규모/유동성 유사도 순서만 산출',
              'sector':a['sector'],'market':a['market'],'cap_ratio_D_over_A':d['market_cap_krw_at_signal']/a['market_cap_krw_at_signal'],
              'turnover_ratio_D_over_A':d['avg_turnover_20_at_signal']/a['avg_turnover_20_at_signal']})
    csv('matching-feasibility.csv',matches,columns=['A_symbol','D_symbol','rank','status','sector','market','cap_ratio_D_over_A','turnover_ratio_D_over_A'])
    entry_audit=[]
    fields=['stck_oprc','stck_hgpr','stck_lwpr','stck_clpr','acml_vol','acml_tr_pbmn']
    for s in historical:
        entry=next_session(s['signal_date'],calendar);key=entry.replace('-','')
        raw=raw_outcomes.get(s['symbol'],{}).get(key);adj=adjusted_outcomes.get(s['symbol'],{}).get(key)
        changed=[k for k in fields if raw and adj and float(raw[k])!=float(adj[k])]
        entry_audit.append({'symbol':s['symbol'],'group':s['group'],'entry_date':entry,'raw_OHLC_present':bool(raw),
          'later_adjusted_OHLC_present':bool(adj),'changed_fields':','.join(changed),'raw_open':float(raw['stck_oprc']) if raw else None,
          'later_adjusted_open':float(adj['stck_oprc']) if adj else None,'execution_reconciled':False,
          'original_source_sha256':sha(ROOT/'inputs/checkpoint-20261016.json.gz'),
          'later_source_sha256':sha(FULL/'inputs/checkpoint-20261020.json.gz')})
    csv('entry-observation-audit.csv',entry_audit)
    trades=[]
    for horizon in [5,20,60]:
        chosen=suppress_duplicates(historical,horizon,calendar)
        for s in chosen:
            entry=next_session(s['signal_date'],calendar);ei=calendar.index(entry) if entry else len(calendar)
            elapsed=len(calendar)-ei
            # calendar is bounded to actual observed sessions, never projected weekdays.
            assert elapsed<horizon, 'Mature data detected: implement full validated outcome ingestion before publishing stats'
            raw=raw_outcomes.get(s['symbol'],{}).get(entry.replace('-','')) if entry else None
            adj=adjusted_outcomes.get(s['symbol'],{}).get(entry.replace('-','')) if entry else None
            for policy in ['fixed','L3']:
                if policy=='L3' and s['group']=='D':continue
                trades.append({'signal_id':s['signal_id'],'group':s['group'],'symbol':s['symbol'],'signal_date':s['signal_date'],
                 'entry_date':entry,'horizon':horizon,'exit_policy':policy,'status':'관측 대기','observed_sessions':elapsed,
                 'entry_open_candidate':float(raw['stck_oprc']) if raw else None,'entry_candidate_basis':'raw_unreconciled' if raw else ('adjusted_only_not_execution_price' if adj else 'missing'),
                 'entry_price':None,'exit_date':None,'exit_price':None,'cost_bps':None,'gross_return':None,'net_return':None,'mae':None,
                 'reason':'horizon_not_elapsed; execution_basis_and_exit_not_validated'})
        for g in 'ABCD':
            for policy in ['fixed','L3']:
                for bps in [0,10,30,60]:
                    availability.append({'group':g,'horizon':horizon,'exit_policy':policy,'roundtrip_cost_assumption_bps':bps,
                      'eligible_signals':sum(s['group']==g for s in chosen) if not(g=='D' and policy=='L3') else 0,'unique_symbols':len({s['symbol'] for s in chosen if s['group']==g}) if not(g=='D' and policy=='L3') else 0,
                      'completed_trades':0,'status':'적용 불가: D군 공통 L3 없음' if g=='D' and policy=='L3' else '데이터 부족으로 검증 불가',**{k:None for k in ['mean_gross_return','mean_net_return','median_net_return','win_rate','mean_mae','p10_net_return','mdd','kospi_excess','kosdaq_excess','sector_excess','sharpe','sortino','CI95_lower','CI95_upper']}})
    csv('trades.csv',trades,columns=['signal_id','group','symbol','signal_date','entry_date','horizon','exit_policy','status','observed_sessions','entry_open_candidate','entry_candidate_basis','entry_price','exit_date','exit_price','cost_bps','gross_return','net_return','mae','reason'])
    csv('performance.csv',availability)
    per_day=[]
    for day in calendar:
        if day < '2026-09-11':continue
        dated=memberships[(memberships.valid_from<=day)&(memberships.valid_to.fillna('9999-12-31')>=day)]
        count=sum(k[1]==day for k in selected)
        per_day.append({'date':day,'dated_membership_rows':len(dated),'point_in_time_price_flow_stocks':count,
         'eligible_signal_count':sum(s['signal_date']==day for s in historical),'whole_market_replay_verified':False,
         'remaining_actual_sessions':sum(d>day for d in calendar),'completed_5d':0,'completed_20d':0,'completed_60d':0})
    csv('date-coverage.csv',per_day)
    gaps=[{'scope':'stock','symbol':r['symbol'],'date':'2026-10-19','reason':r['reasons'],'status':r['price_data_status']} for r in original.to_dict('records') if not r['trusted_candidate_data']]
    gaps += [{'scope':'outcome_observation','symbol':x['symbol'],'date':x['entry_date'],
              'reason':'later_adjusted_vs_original_raw:'+x['changed_fields'],'status':'진입일 관측 충돌·성과 보류'}
             for x in entry_audit if x['changed_fields']]
    gaps += [{'scope':'outcome_observation','symbol':x['symbol'],'date':x['entry_date'],
              'reason':'later_adjusted_observation_missing','status':'후속 관측 미확보·성과 보류'}
             for x in entry_audit if not x['later_adjusted_OHLC_present']]
    gaps += [{'scope':'global','symbol':None,'date':latest,'reason':reason,'status':'미확보'} for reason in [
      'five_twenty_sixty_sessions_not_elapsed','no_full_historical_universe_before_2026-10-14',
      'sector_daily_index_missing_and_matched_control_sample_inadequate','historical_flow_rows_have_no_row_observed_at',
      'raw_execution_OHLC_and_action_interval_need_independent_validation','no_actual_cost_schedule_or_NAV_series',
      'October19_reference_candidates_are_posthoc_not_tradeable_signals']]
    csv('data_gaps.csv',gaps)
    cpcoverage=[]
    for r in records:
        if r['record_kind']!='retrospective_reference':continue
        raw=raw_outcomes.get(r['symbol'],{}).get('20261020')
        cpcoverage.append({'symbol':r['symbol'],'group':r['group'],'next_day':'2026-10-20','raw_OHLC_present':bool(raw),
                           'eligible_historical_trade':False,'observation':'reference_only; no simulated entry'})
    csv('reference-next-open-coverage.csv',cpcoverage)
    limits=[]
    for cap in [10,15,20]:
        for kind in ['retrospective_reference','historical_partial_universe']:
            subset=[r for r in records if r['record_kind']==kind and r['group'] in ['A','B'] and r['rise_pct'] is not None and 0<=r['rise_pct']<=cap+1e-10]
            limits.append({'record_kind':kind,'rise_cap_pct':cap,'middle_prebreak_stocks':len(subset),'completed_5d':0,'completed_20d':0,'completed_60d':0,'risk_advantage_verified':False})
    csv('threshold-feasibility.csv',limits)
    result={'verdict':'데이터 부족으로 검증 불가','latest_operating_date':latest,'operating_ranges':ranges,
      'archive_price_range':[min(all_dates),max(all_dates)],'prior_research_through':'2026-10-19',
      'first_preserved_bundle_observed':min(observations,key=instant),'last_preserved_bundle_observed':max(observations,key=instant),
      'archived_versions_scanned':len(archive_audit),'daily_accepted_asof_dates':sorted(accepted_days),
      'pit_eligible_versions':accepted_before,'pit_unique_stock_dates':len(selected),
      'pit_dates':sorted({d for _,d in selected}),'historical_signal_counts':dict(collections.Counter(s['group'] for s in historical)),
      'reference_counts':dict(reference_counts),'reference_raw_next_open_present':sum(x['raw_OHLC_present'] for x in cpcoverage),
      'historical_raw_entry_OHLC_present':sum(x['raw_OHLC_present'] for x in entry_audit),
      'historical_later_adjusted_entry_OHLC_present':sum(x['later_adjusted_OHLC_present'] for x in entry_audit),
      'historical_entry_later_observation_conflicts':sum(bool(x['changed_fields']) for x in entry_audit),
      'additional_preserved_checkpoints':checkpoint_inputs,
      'whole_market_replay_days':0,'mature_outcome_counts':{'5':0,'20':0,'60':0},'market_API_calls':0,
      'actual_trades_executed':0,'retrospective_references_used_as_trade_signals':0,'prospective_signals_frozen':0}
    write_json(ROOT/'summary.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    import socket
    def deny_network(*args,**kwargs):raise RuntimeError('Research audit is offline: network disabled')
    socket.socket.connect=deny_network
    socket.create_connection=deny_network
    main()
