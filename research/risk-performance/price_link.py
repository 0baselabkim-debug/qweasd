"""Offline price stitching, with explicit separation from prospective acceptance.
No provider clients. Inputs remain immutable; mismatches never overwrite either side.
"""
import datetime as dt
import math
from copy import deepcopy
from engine import digest, instant, cohort
from forward_system import FIELDS, KIS_FIELDS, KRX_FIELDS
from screener.liquidity_quality import POLICY, digest as evidence_digest, daily_accepted
import forward


def normalized(rows):
    result={}
    for r in rows:
        s=r['stck_bsop_date'];day=dt.datetime.strptime(s,'%Y%m%d').date().isoformat()
        if day in result:raise ValueError('duplicate_price_date')
        b={k:float(r[v]) for k,v in zip(FIELDS,KIS_FIELDS)}
        if not valid(b):raise ValueError('invalid_ohlcv')
        result[day]=b
    return result


def valid(b):
    try:
        return (all(math.isfinite(b[k]) for k in FIELDS)
            and min(b[k] for k in FIELDS[:4])>0 and b['volume']>=0 and b['turnover']>=0
            and b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high'])
    except (KeyError,TypeError):return False


def action_flag(r):
    # Do not infer that an event did not occur simply because a flag is blank.
    try:ratio=float(r.get('prtt_rate') or 0)
    except ValueError:return True
    return not math.isfinite(ratio) or ratio!=0 or bool(str(r.get('revl_issu_reas','')).strip())


def six(b):return tuple(b[k] for k in FIELDS)


def link_history(symbol,market,day,listing,old,record,sessions,prior_reasons,
                 raw_observations,official,source_ids,known_at,quarantined=False):
    """Return a candidate only if all evidence in this 40-session window agrees.
    official maps date -> distinct official raw OHLCV tuples. An old observation
    may be superseded in the research candidate only by unique matching official
    evidence, while original values remain in the conflict audit. Corporate action
    flags / changed adjustment factors have no automatic override.
    """
    reasons=[];conflicts=[];resolved=[];overlap=[];raw_latest=[]
    calendar=sorted(set(d for d in sessions if d<=day));required=calendar[-40:]
    if len(required)!=40 or not required or required[-1]!=day:reasons.append('official_40_session_calendar_missing')
    expected=set(required)
    scope=record.get('request_scope',{})
    if scope!={'market':'J','symbol':symbol,'end':day.replace('-',''),'adjustment':'0'}:reasons.append('checkpoint_request_scope_mismatch')
    if record.get('chart_header',{}).get('stck_shrn_iscd')!=symbol:reasons.append('checkpoint_response_symbol_mismatch')
    try:new=normalized(record.get('adjusted_prices',[]))
    except (ValueError,KeyError,TypeError):new={};reasons.append('invalid_checkpoint_prices')
    if any(d>day or d not in calendar or d<listing for d in new):reasons.append('checkpoint_date_or_listing_mismatch')
    if any(d>day for d in old):reasons.append('old_series_future_date')
    if any(not valid(b) for d,b in old.items() if d in expected):reasons.append('invalid_old_prices')
    overlap=sorted(expected&set(old)&set(new))
    if not overlap:reasons.append('no_overlap_to_validate')
    for d in overlap:
        if six(old[d])!=six(new[d]):
            conflicts.append({'date':d,'source':'adjusted_overlap','left':old[d],'right':new[d]})
    if conflicts:reasons.append('adjusted_overlap_conflict')
    # Never replace an existing bar, even where official evidence might resolve it.
    merged={d:dict(b) for d,b in old.items() if d in set(calendar) and d<=day}
    merged.update({d:dict(b) for d,b in new.items() if d in set(calendar) and d not in merged})
    missing=sorted(expected-set(merged))
    if missing:reasons.append('history_short_40')
    if any(d<listing for d in required):reasons.append('listing_short_40')
    if any(merged[d]['volume']<=0 or merged[d]['turnover']<=0 for d in expected&set(merged)):reasons.append('zero_trading_last40')
    # Preserve earlier unresolved price/corporate-action evidence, not old flow gaps.
    reasons.extend('prior_'+r for r in prior_reasons if r and not r.startswith('flow_'))
    if any(action_flag(r) and dt.datetime.strptime(r['stck_bsop_date'],'%Y%m%d').date().isoformat() in expected for r in record.get('adjusted_prices',[])):
        reasons.append('new_corporate_action_flag_unverified')
    for observation in raw_observations:
        d=observation['date'];raw=observation['values']
        if d==day:raw_latest.append(raw)
        if d not in merged or d not in expected:continue
        if not valid(raw):reasons.append('invalid_raw_observation');continue
        if six(raw)!=six(merged[d]):
            refs=official.get(d,set())
            item={'date':d,'source':observation['source'],'left':raw,'right':merged[d]}
            if refs=={six(merged[d])}:resolved.append(item)
            else:conflicts.append(item);reasons.append('raw_adjusted_conflict_unresolved')
    for d in expected&set(official)&set(merged):
        if official[d]!={six(merged[d])}:reasons.append('official_reference_conflict')
    if not raw_latest:reasons.append('latest_raw_price_missing')
    if quarantined:reasons.append('oct16_37_outcome_quarantine')
    try:instant(known_at);instant(record['observed_at'])
    except (ValueError,KeyError,TypeError):reasons.append('observation_time_missing')
    reasons=sorted(set(reasons))
    # Keep the original prefix for event confirmation dates and old C states.
    # Truncating to exactly 40 would relabel older breakout states as new D signals.
    suffix=[]
    for d in reversed(calendar):
        if d not in merged or not valid(merged[d]):break
        suffix.append(d)
    suffix=list(reversed(suffix))
    return {'schema':'research-linked-40-v2','symbol':symbol,'market':market,'asof':day,
      'listing_date':listing,'status':'linked_history_validated' if not reasons else 'hold','reasons':reasons,
      'bars':[{'trade_date':d,**merged[d]} for d in suffix],
      'validated_window':required,'preserved_prefix_scope':'original research context; latest 40 revalidated',
      'calendar':calendar,'overlap_days':len(overlap),'missing_dates':missing,
      'conflicts':conflicts,'official_resolved_observations':resolved,
      'observed_at':known_at,'source_identifiers':source_ids,'provider_finality':'UNVERIFIED',
      'raw_latest':raw_latest,'scope':'KRX','basis':'adjusted','corporate_action_status':'no_unresolved_observed_adjustment' if not any('action' in r or 'adjustment' in r for r in reasons) else 'hold',
      'execution_action_interval_verified':False,'prospective_signal':False}


def classify_link(link,flows):
    """As-of structure analysis only; not a registration or past tradable signal."""
    if link['status']!='linked_history_validated':return {'status':'unclassifiable','group':None,'reason':';'.join(link['reasons'])}
    required=link['calendar'][-20:]
    if len(required)!=20 or any(d not in flows or flows[d][0] is None or not math.isfinite(float(flows[d][0])) or flows[d][1]!='KRX' for d in required):
        return {'status':'unclassifiable','group':None,'reason':'flow_20_sessions_missing'}
    bars={r['trade_date']:r for r in link['bars']};dates=sorted(bars)
    events=forward.audit.census.price_events(dates,bars);state,e,_=forward.audit.census.current_state(dates,bars,events)
    n5=sum(flows[d][0] for d in required[-5:]);prev=sum(flows[d][0] for d in required[-10:-5]);close=bars[dates[-1]]['close']
    group=cohort(state,n5>0 and n5>prev,close,e['L2_price'] if e else None,e.get('H3_price') if e else None)
    reason='';status='research_classified'
    if group is None:status='condition_not_met';reason='flow_condition_not_met'
    if group=='C' and not any(x['stage']=='확정' and x['confirmed_date']==link['asof'] for x in events):
        group=None;status='no_new_event';reason='C_requires_new_confirmed_event'
    return {'status':status,'group':group,'reason':reason,'price_structure':state,'structure':e,
      'net_5d_krw':n5,'previous_5d_krw':prev,'change_5d_krw':n5-prev,'net_20d_krw':sum(flows[d][0] for d in required),
      'rise_pct':100*(close/e['L2_price']-1) if e else None,'prospective_signal':False}


def make_forward_bundle(link,flows,raw_flow_rows,flow_sources,reference,verified_at):
    """Promotion adapter: keep the EXISTING daily_accepted rules, no fake finality.
    reference must be the preserved official full response wrapper, not a chosen
    numeric value or a successful research-only overlap flag.
    """
    if link.get('schema')!='research-linked-40-v2':raise ValueError('legacy_link_requires_revalidation')
    if link['status']!='linked_history_validated':raise ValueError('link_not_validated')
    if reference is None:raise ValueError('latest_official_KRX_evidence_missing')
    if not link.get('raw_latest') or any(six(r)!=six(link['bars'][-1]) for r in link['raw_latest']):raise ValueError('latest_raw_price_not_reconciled')
    market=link['market'];day=link['asof'];symbol=link['symbol']
    url='https://data-dbg.krx.co.kr/svc/apis/sto/'+{'KOSPI':'stk','KOSDAQ':'ksq'}[market]+'_bydd_trd?basDd='+day.replace('-','')
    if reference.get('url')!=url or reference.get('http')!=200 or reference.get('rows_sha256')!=evidence_digest(reference.get('rows',[])):raise ValueError('official_reference_envelope_invalid')
    matches=[r for r in reference['rows'] if r.get('BAS_DD')==day.replace('-','') and r.get('ISU_CD')==symbol]
    if len(matches)!=1:raise ValueError('official_reference_not_unique')
    if tuple(float(matches[0][k]) for k in KRX_FIELDS)!=six(link['bars'][-1]):raise ValueError('latest_official_price_mismatch')
    required=link['calendar'][-20:]
    if len(raw_flow_rows)!=20:raise ValueError('twenty_original_flow_rows_required')
    expected=[{'trade_date':d,'net_amount':flows[d][0]} for d in required]
    if any(flows[d][1]!='KRX' for d in required):raise ValueError('flow_market_mismatch')
    row={**matches[0],'_source_url':url,'_retrieved_at':reference['observed_at']}
    b={'symbol':symbol,'market':market,'asof':day,'exchange':'KRX','adjustment':'adjusted','finality':'UNVERIFIED',
       'bars':deepcopy(link['bars']),'flows':expected,'calendar':link['calendar'],
       'observed_at':max([link['observed_at'],verified_at,reference['observed_at']],key=instant),
       'price_conflicts':[],'corporate_actions':[],'price_comparison':{'kis_unadjusted':link['raw_latest'][0]['close']},
       'linked_history_sha256':digest(link),'linked_history_source_ids':link['source_identifiers']}
    b['daily_acceptance']={'policy':POLICY,'asof':day,'symbol':symbol,'observed_at':verified_at,
       'krx_url':url,'krx_row':row,'flow_endpoint':'KIS FHPTJ04160001 / J / million KRW to KRW',
       'flow_check_kind':'saved_api_response_replay','raw_flow_rows':raw_flow_rows,'flow_sources':flow_sources,
       'checked_flows':expected,'bars_sha256':evidence_digest(b['bars']),'flows_sha256':evidence_digest(expected)}
    if not daily_accepted(b):raise ValueError('existing_daily_acceptance_failed')
    return b


def verify_flow_sources(raw,symbol,repo):
    """Every submitted API row must occur verbatim in hash-checked saved input."""
    if raw.get('schema')=='research-raw-flow-replay-v2':
        from conflict_reconciliation import verify_replay_sources
        if raw.get('symbol')!=symbol or raw.get('status')!='replay_ready' or len(raw.get('rows',[]))!=20:
            raise ValueError('raw_flow_replay_incomplete_or_wrong_symbol')
        verify_replay_sources(raw,repo)
        return
    import gzip,hashlib,json,tarfile
    from forward_system import load,sha
    allowed=set()
    if not raw.get('sources'):raise ValueError('raw_flow_sources_missing')
    for source in raw['sources']:
        path=(repo/source['file']).resolve()
        if not path.is_relative_to(repo) or sha(path)!=source['sha256']:raise ValueError('raw_flow_source_hash_mismatch')
        if source.get('member'):
            with tarfile.open(path) as tf:data=tf.extractfile(source['member']).read()
            if hashlib.sha256(data).hexdigest()!=source['member_sha256']:raise ValueError('raw_flow_member_hash_mismatch')
            obj=json.loads(gzip.decompress(data) if data[:2]==b'\x1f\x8b' else data)
        else:obj=load(path)
        if 'records' in obj:
            record=obj['records'].get(symbol,{})
            if obj.get('exchange')!='KRX':raise ValueError('raw_flow_source_market_mismatch')
            rows=[record['flow']];known=record.get('observed_at') or obj['updated_at']
        else:
            if obj.get('symbol')!=symbol or obj.get('exchange')!='KRX':raise ValueError('raw_flow_source_identity_mismatch')
            e=obj['daily_acceptance']
            if e.get('flow_endpoint')!='KIS FHPTJ04160001 / J / million KRW to KRW':raise ValueError('raw_flow_endpoint_mismatch')
            rows=e['raw_flow_rows'];known=e.get('observed_at') or obj['observed_at']
        if instant(source['observed_at'])<instant(known):raise ValueError('raw_flow_source_backdated')
        allowed.update(digest(r) for r in rows)
    if any(digest(r) not in allowed for r in raw['rows']):raise ValueError('raw_flow_row_not_in_original_source')


def stage_bundle(link_path,snapshot_directory,raw_flow_path,reference_path,output_directory):
    """Manual adapter into existing forward_system.generate; no registration here.
    All evidence files must already exist. Never contacts an API or guesses a date.
    Use a new output version directory when evidence changes.
    """
    from pathlib import Path
    import gzip,json,os,tempfile
    from forward_system import Store,load,sha,utcnow,validate_publication,snapshot_rows
    repo=Path(__file__).resolve().parents[2]
    saved=Store.read(link_path)
    if saved.get('kind')!='validated_linked_history':raise ValueError('wrong_link_record_kind')
    link=saved['data']
    for source in link['source_identifiers']:
        path=(repo/source['file']).resolve()
        if not path.is_relative_to(repo) or sha(path)!=source['sha256']:raise ValueError('linked_input_hash_mismatch')
    pub=validate_publication(snapshot_directory)
    if link['asof']!=pub['asof']:raise ValueError('linked_bundle_not_current')
    universe,prices,flows,sessions,excluded=snapshot_rows(snapshot_directory)
    symbol=link['symbol'];raw=load(raw_flow_path);reference=load(reference_path)
    if symbol in excluded:raise ValueError('excluded_or_suspended')
    if raw.get('symbol')!=symbol or raw.get('asof')!=link['asof']:raise ValueError('raw_flow_identity_mismatch')
    verify_flow_sources(raw,symbol,repo)
    b=make_forward_bundle(link,flows[symbol],raw['rows'],raw['sources'],reference,utcnow().isoformat())
    b['linked_history_record_sha256']=sha(link_path)
    b['input_evidence_hashes']={'link':sha(link_path),'raw_flow':sha(raw_flow_path),'reference':sha(reference_path),'snapshot':pub['sha256']}
    out=Path(output_directory).resolve()
    if not out.is_relative_to(Path(__file__).resolve().parent):raise ValueError('research_output_required')
    out.mkdir(parents=True,exist_ok=True);target=out/(symbol+'.json.gz')
    if target.exists():
        existing=load(target)
        if existing.get('input_evidence_hashes')!=b['input_evidence_hashes']:raise ValueError('staged_bundle_revision_conflict')
        if not daily_accepted(existing):raise ValueError('staged_bundle_tampered')
        return target
    payload=gzip.compress(json.dumps(b,ensure_ascii=False,allow_nan=False,sort_keys=True).encode(),mtime=0)
    fd,tmp=tempfile.mkstemp(dir=out,prefix='.pending-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
        os.link(tmp,target)
    finally:os.unlink(tmp)
    return target


if __name__=='__main__':
    import argparse,socket
    def denied(*args,**kwargs):raise RuntimeError('offline_price_link_no_network')
    socket.socket.connect=denied
    parser=argparse.ArgumentParser(description='Stage existing validated evidence; never register a retroactive signal')
    for name in ['link','snapshot','raw-flow','reference','output']:parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    print(stage_bundle(args.link,args.snapshot,args.raw_flow,args.reference,args.output))
