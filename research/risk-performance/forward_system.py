"""Manual, offline research pipeline. Never writes operational files or schedules.
Records are append-only with atomic creation and content hashes, not physical WORM.
"""
import argparse,datetime as dt,gzip,hashlib,json,math,os,tempfile
from pathlib import Path
import duckdb
import forward
from engine import digest,instant,decision_gate,cohort,trade,nav_statistics,suppress_duplicates
from screener.liquidity_quality import digest as evidence_digest
from scripts.briefing_schema import payload as briefing_payload,digest as briefing_digest

ROOT=Path(__file__).resolve().parent
FIELDS=['open','high','low','close','volume','turnover']
KRX_FIELDS=['TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','TDD_CLSPRC','ACC_TRDVOL','ACC_TRDVAL']
KIS_FIELDS=['stck_oprc','stck_hgpr','stck_lwpr','stck_clpr','acml_vol','acml_tr_pbmn']

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p):
    raw=Path(p).read_bytes();return json.loads(gzip.decompress(raw) if str(p).endswith('.gz') else raw)
def utcnow():return dt.datetime.now(dt.timezone.utc)

class Store:
    """Atomic per-record identity; changed revisions rejected; all reads rehash.
    File owners can still delete/replace files. Git history/external retention is
    required for independent tamper evidence. No WORM/security guarantee.
    """
    def __init__(self,root):self.root=Path(root)
    def put(self,kind,key,payload):
        if kind not in ('signals','observations','assessments','runs','snapshots'):raise ValueError('invalid_record_kind')
        if not key or any(c not in '0123456789abcdef-' for c in key):raise ValueError('unsafe_identity')
        directory=self.root/kind;directory.mkdir(parents=True,exist_ok=True)
        path=directory/(key+'.json');envelope={'sha256':digest(payload),'payload':payload}
        raw=json.dumps(envelope,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
        fd,tmp=tempfile.mkstemp(prefix='.pending-',dir=directory)
        try:
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            try:os.link(tmp,path)
            except FileExistsError:
                existing=self.read(path)
                if existing!=payload:raise ValueError('immutable_identity_revision_conflict')
        finally:os.unlink(tmp)
        return path
    @staticmethod
    def read(path):
        x=load(path)
        if digest(x['payload'])!=x['sha256']:raise ValueError('record_hash_mismatch')
        return x['payload']
    def records(self,kind):return [self.read(p) for p in sorted((self.root/kind).glob('*.json'))]
    def verify_links(self):
        signals=self.records('signals')
        for signal in signals:
            for key in ['bundle_sha256','calendar_sha256']:
                item=self.read(self.root/'observations'/(signal[key]+'.json'))
                if digest(item['data'])!=signal[key]:raise ValueError('linked_evidence_hash_mismatch')
            publication=self.read(self.root/'snapshots'/(signal['snapshot_sha256']+'.json'))
            if publication['sha256']!=signal['snapshot_sha256']:raise ValueError('linked_snapshot_mismatch')
        return len(signals)

def validate_publication(directory):
    p=Path(directory);m=load(p/'manifest.json');v=load(p/'daily-validation.json');l=load(p/'publication-ledger.json');idx=load(p/'index.json')
    day=m['asof'];dbsha=sha(p/'dashboard.duckdb');entry=l.get(day,{})
    if not all(x==dbsha for x in [m.get('sha256'),v.get('sha256'),entry.get('sha256'),idx.get('source_snapshot_sha256')]):raise ValueError('publication_hash_mismatch')
    if v.get('trade_date')!=day or idx.get('asof')!=day or m.get('exchange')!='KRX':raise ValueError('publication_date_or_scope_mismatch')
    if not v.get('preservation_verified') or idx.get('validation_status')!='passed' or idx.get('publication_status')!='ready_for_briefing':raise ValueError('publication_not_validated')
    if idx.get('dataset_id')!='flow-v3-'+day+'-'+dbsha:raise ValueError('dataset_id_mismatch')
    if idx.get('index_content_sha256')!=briefing_digest(briefing_payload({k:v for k,v in idx.items() if k!='index_content_sha256'})):raise ValueError('briefing_index_content_hash_mismatch')
    if entry.get('validation',{}).get('sha256')!=dbsha or m.get('daily_validation',{}).get('sha256')!=dbsha:raise ValueError('nested_validation_revision_mismatch')
    c=duckdb.connect(str(p/'dashboard.duckdb'),read_only=True)
    try:
        count=c.execute('select count(*) from flows where trade_date=?',[day]).fetchone()[0]
        latest=str(c.execute('select max(trade_date) from flows').fetchone()[0])
    finally:c.close()
    if count!=v.get('records') or latest!=day:raise ValueError('publication_row_count_or_latest_date_mismatch')
    # Use this exact revision's known time, never the original older publication.
    times=[entry['published_at_utc'],idx['generated_at'],idx['published_at']]
    if (p/'source.json').exists():
        source=load(p/'source.json');times += [source[k] for k in ['commit_time','captured_at'] if source.get(k)]
    return {'asof':day,'sha256':dbsha,'dataset_id':idx['dataset_id'],'records':count,
            'known_at':max(times,key=instant),'provider_finality':m.get('finality'),
            'files':{n:sha(p/n) for n in ['dashboard.duckdb','manifest.json','daily-validation.json','publication-ledger.json','index.json']}}

def official_next_session(day,calendar,now):
    """Check saved official holiday response, including every intervening day.
    No inference from weekdays and no market request. Future calendar reception
    itself must predate this execution. The next session may be absent: hold.
    """
    h=calendar.get('holiday_response')
    if not h:raise ValueError('next_official_session_evidence_missing')
    if h.get('endpoint')!='https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/chk-holiday' or h.get('tr_id')!='CTCA0903R' or h.get('rt_cd')!='0':raise ValueError('invalid_official_calendar_source')
    if instant(h['observed_at'])>instant(now):raise ValueError('calendar_from_future')
    rows=h.get('output',[])
    if len({r['bass_dt'] for r in rows})!=len(rows):raise ValueError('calendar_duplicate_date')
    indexed={r['bass_dt']:r for r in rows};d=dt.date.fromisoformat(day)
    for _ in range(30):
        d+=dt.timedelta(days=1);r=indexed.get(d.strftime('%Y%m%d'))
        if r is None:raise ValueError('calendar_coverage_gap')
        if r.get('opnd_yn') not in ('Y','N') or r.get('tr_day_yn') not in ('Y','N'):raise ValueError('calendar_status_unknown')
        if r['opnd_yn']=='Y' and r['tr_day_yn']=='Y':return d.isoformat(),h['observed_at']
    raise ValueError('next_session_not_found')

def snapshot_rows(directory):
    p=Path(directory);m=load(p/'manifest.json');day=m['asof'];c=duckdb.connect(str(p/'dashboard.duckdb'),read_only=True)
    try:
        universe=c.execute('select f.symbol,i.name,i.market,i.listing_date from flows f join instruments i using(symbol) where f.trade_date=? order by f.symbol',[day]).fetchall()
        prices=c.execute('select symbol,cast(trade_date as varchar),close,adjusted_close,volume,turnover from prices order by symbol,trade_date').fetchall()
        flows=c.execute('select symbol,cast(trade_date as varchar),net_amount,exchange from flows order by symbol,trade_date').fetchall()
        sessions={m:[str(x[0]) for x in c.execute('select trade_date from calendar where market=? and trade_date<=? order by trade_date',[m,day]).fetchall()] for m in ['KOSPI','KOSDAQ']}
        excluded={x[0] for x in c.execute('select symbol from exclusions where trade_date=?',[day]).fetchall()}
    finally:c.close()
    ps={};fs={}
    for symbol,d,*values in prices:ps.setdefault(symbol,{})[d]=dict(zip(['close','adjusted_close','volume','turnover'],values))
    for symbol,d,value,exchange in flows:fs.setdefault(symbol,{})[d]=(value,exchange)
    return universe,ps,fs,sessions,excluded

def build_signal(bundle,publication,price_rows,flow_rows,sessions,calendar,now):
    day=publication['asof'];symbol=bundle['symbol']
    if bundle['asof']!=day:raise ValueError('bundle_not_latest_snapshot_date')
    next_day,calendar_known=official_next_session(day,calendar,now)
    calendar_days=sorted(set(sessions+[next_day]))
    reasons=forward.audit.evaluate_bundle(bundle,calendar_days)
    if reasons:raise ValueError(';'.join(reasons))
    bars=bundle['bars'];flows=bundle['flows'][-20:]
    if [r['trade_date'] for r in flows]!=sessions[-20:]:raise ValueError('snapshot_flow_calendar_mismatch')
    for r in flows:
        if flow_rows.get(r['trade_date'])!=(r['net_amount'],'KRX'):raise ValueError('snapshot_flow_mismatch')
    # Every overlapping snapshot observation must agree; a good last day cannot
    # silently unblock earlier price disagreements in the structure window.
    for r in bars[-40:]:
        if r['trade_date'] not in price_rows:continue
        q=price_rows[r['trade_date']]
        if any(q[k]!=r[k] for k in ['volume','turnover']) or q['adjusted_close']!=r['close']:raise ValueError('snapshot_price_overlap_conflict')
    if day not in price_rows or price_rows[day]['close']!=bars[-1]['close']:raise ValueError('snapshot_latest_raw_price_conflict')
    times=forward.audit.bundle_times(bundle)+[publication['known_at'],calendar_known]
    ok,why=decision_gate(day,calendar_days,times,max(times,key=instant),now)
    if not ok:raise ValueError(why)
    dates=[r['trade_date'] for r in bars];mapping={r['trade_date']:r for r in bars}
    events=forward.audit.census.price_events(dates,mapping);state,e,_=forward.audit.census.current_state(dates,mapping,events)
    n5=sum(r['net_amount'] for r in flows[-5:]);prev=sum(r['net_amount'] for r in flows[-10:-5]);close=bars[-1]['close']
    g=cohort(state,n5>0 and n5>prev,close,e['L2_price'] if e else None,e.get('H3_price') if e else None)
    if not g:raise ValueError('flow_condition_not_met')
    if g=='C' and not any(x['stage']=='확정' and x['confirmed_date']==day for x in events):raise ValueError('C_requires_new_confirmed_event')
    return {'record_kind':'prospective','schema':1,'symbol':symbol,'market':bundle['market'],'signal_date':day,'group':g,
       'confirmed_at':instant(now).isoformat(),'frozen_at':instant(now).isoformat(),'known_at':max(times,key=instant),
       'next_session':next_day,'price_structure':state,'structure':e,'structure_confirmed_date':e.get('confirmed_date') if e else None,
       'confirmed_date':day if g=='C' else (e.get('confirmed_date') if e else None),
       **{k:e.get(k) if e else None for k in ['L2_price','L2_date','L3_price','L3_date','H3_price','H3_date']},
       'rise_pct':100*(close/e['L2_price']-1) if e else None,'signal_close':close,'raw_signal_close':close,
       'net_1d_krw':flows[-1]['net_amount'],'net_5d_krw':n5,'previous_5d_krw':prev,'change_5d_krw':n5-prev,
       'net_20d_krw':sum(r['net_amount'] for r in flows),'positive_days_20':sum(r['net_amount']>0 for r in flows),
       'bundle_sha256':digest(bundle),'calendar_sha256':digest(calendar),'snapshot_sha256':publication['sha256'],
       'dataset_id':publication['dataset_id'],'bars_sha256':digest(bars),'flows_sha256':digest(bundle['flows']),
       'pit_eligible':True,'provider_finality':bundle.get('finality'),'L3_raw':None}

def generate(directory,bundle_directory,calendar,store,now=None):
    now=now or utcnow();publication=validate_publication(directory)
    universe,prices,flows,sessions,excluded=snapshot_rows(directory)
    if validate_publication(directory)!=publication:raise ValueError('snapshot_changed_during_read')
    store.verify_links()
    if len(universe)!=publication['records']:raise ValueError('instrument_join_loss')
    store.put('snapshots',publication['sha256'],publication)
    results=[];existing=store.records('signals')
    try:official_next_session(publication['asof'],calendar,now);calendar_reason=None
    except ValueError as e:calendar_reason=str(e)
    for symbol,name,market,listed in universe:
        reasons=[];day=publication['asof'];key=digest([day,symbol]);saved=store.root/'signals'/(key+'.json')
        if saved.exists():
            old=store.read(saved)
            results.append({'symbol':symbol,'status':'already_frozen','group':old['group'],'reason':'later_snapshot_revision_not_applied' if old['snapshot_sha256']!=publication['sha256'] else '', 'signal_sha256':digest(old)});continue
        required=sessions[market][-20:];f=flows.get(symbol,{})
        if len(required)<20 or any(d not in f or f[d][0] is None or f[d][1]!='KRX' for d in required):reasons.append('flow_20_sessions_missing')
        if symbol in excluded:reasons.append('excluded_or_suspended')
        if listed and str(listed)>day:reasons.append('pre_listing')
        price_count=len([d for d in prices.get(symbol,{}) if d<=day]);p=Path(bundle_directory)/(symbol+'.json.gz')
        if not p.exists():p=Path(bundle_directory)/(symbol+'.json')
        if not p.exists():reasons.append('validated_current_40_session_bundle_missing')
        if calendar_reason:reasons.append(calendar_reason)
        if not reasons:
            try:
                b=load(p)
                if b.get('symbol')!=symbol or b.get('market')!=market:raise ValueError('bundle_identity_mismatch')
                signal=build_signal(b,publication,prices.get(symbol,{}),f,sessions[market],calendar,now)
                if signal['group']!='D' and any(all(old.get(k)==signal.get(k) for k in ['symbol','group','L2_date','L3_date']) for old in existing):
                    results.append({'symbol':symbol,'name':name,'status':'repeat_pattern','group':signal['group'],'reason':'existing_same_group_pivot_pattern','snapshot_price_days':price_count});continue
                # Persist the exact input before its signal; hash verifies recovery.
                store.put('observations',digest(b),{'kind':'signal_bundle','data':b})
                store.put('observations',digest(calendar),{'kind':'official_calendar','data':calendar})
                store.put('signals',key,signal)
                existing.append(signal)
                results.append({'symbol':symbol,'name':name,'status':'frozen','group':signal['group'],'reason':'','signal_sha256':digest(signal),'snapshot_price_days':price_count});continue
            except (ValueError,KeyError,TypeError) as e:reasons.append(str(e))
        status='condition_not_met' if reasons==['flow_condition_not_met'] else ('no_new_event' if reasons==['C_requires_new_confirmed_event'] else 'hold')
        results.append({'symbol':symbol,'name':name,'status':status,'group':None,'reason':';'.join(sorted(set(reasons))),'snapshot_price_days':price_count})
    run={'publication':publication,'results':results,'recorded_at':instant(now).isoformat(),'calendar_sha256':digest(calendar)}
    store.put('runs',digest(run),run)
    return run

def compare_krx(observation,reference):
    """Independent 6-field raw price validation. Never diagnoses adjustment cause."""
    reasons=[];day=observation['trade_date'];symbol=observation['symbol'];market=observation['market'];part={'KOSPI':'stk','KOSDAQ':'ksq'}.get(market)
    if observation.get('exchange')!='KRX' or observation.get('basis')!='raw':reasons.append('execution_raw_KRX_basis_required')
    if reference is None:return {'status':'hold','reasons':reasons+['official_KRX_same_day_missing'],'matched':False}
    url=f'https://data-dbg.krx.co.kr/svc/apis/sto/{part}_bydd_trd?basDd='+day.replace('-','')
    if reference.get('url')!=url or reference.get('http')!=200:reasons.append('reference_scope_unverified')
    if reference.get('rows_sha256')!=evidence_digest(reference.get('rows',[])):reasons.append('reference_rows_hash_mismatch')
    try:instant(reference['observed_at'])
    except (KeyError,ValueError,TypeError):reasons.append('reference_observation_time_missing')
    rows=[r for r in reference.get('rows',[]) if r.get('BAS_DD')==day.replace('-','') and r.get('ISU_CD')==symbol]
    if len(rows)!=1:reasons.append('official_row_not_unique')
    else:
        try:
            values=[float(observation[k]) for k in FIELDS]
            if any(not math.isfinite(v) for v in values) or min(values[:4])<=0:reasons.append('invalid_OHLCV')
            if not values[2]<=min(values[0],values[3])<=max(values[0],values[3])<=values[1]:reasons.append('invalid_OHLC_geometry')
            reasons += ['KRX_mismatch_'+k for k,f in zip(FIELDS,KRX_FIELDS) if float(observation[k])!=float(rows[0][f])]
        except (KeyError,ValueError,TypeError):reasons.append('invalid_reference_fields')
    return {'status':'operational_price_match' if not reasons else 'hold','reasons':reasons,'matched':not reasons,'provider_finality':'UNVERIFIED'}

def observe(store,observation,reference=None):
    """All versions retained; conflicting observations do not overwrite first value."""
    old=[x for x in store.records('observations') if x.get('kind')=='outcome' and x['data']['symbol']==observation['symbol'] and x['data']['trade_date']==observation['trade_date']]
    conflicts=[digest(x['data']) for x in old if any(x['data'][k]!=observation[k] for k in FIELDS)]
    h=digest(observation);store.put('observations',h,{'kind':'outcome','data':observation})
    if reference:store.put('observations',digest(reference),{'kind':'official_reference','data':reference})
    assessment={'observation_sha256':h,'reference_sha256':digest(reference) if reference else None,
      'comparison':compare_krx(observation,reference),'conflicting_observations':conflicts,
      'execution_verified':False,'action_interval_verified':False,'reason':'corporate_action_interval_and_tradability_evidence_required'}
    store.put('assessments',digest(assessment),assessment)
    return assessment

def evaluate_signal(signal,verified_bars,calendar,benchmarks=None):
    """Caller must supply validated raw observations with corporate-action proof.
    This pure calculation adapter does not promote saved raw observations.
    """
    records=[]
    for horizon in [5,20,60]:
        for policy in ['fixed','L3']:
            if policy=='L3' and signal['group']=='D':continue
            for cost in [0,10,30,60]:
                t=trade(signal,verified_bars,calendar,horizon,policy,cost)
                excess=None
                if t['status']=='관측 완료' and benchmarks:
                    a=benchmarks.get(t['entry_date']);b=benchmarks.get(t['exit_date'])
                    if a and b and a.get('verified') and b.get('verified') and a.get('market')==signal['market']==b.get('market'):
                        excess=t['net_return']-(b[t['exit_basis']]/a['open']-1)
                records.append({'symbol':signal['symbol'],'group':signal['group'],'horizon':horizon,'policy':policy,'roundtrip_bps':cost,**t,'market_excess':excess})
    return records

def evaluate_registry(signals,bars_by_symbol,calendar,benchmarks=None):
    """Independent group/holding-period experiments, no overlapping same-stock
    entries within each. D daily flow candidates receive horizon-specific cooldown.
    A/B/C repeated patterns are suppressed even if imported from older registries.
    """
    records=[]
    eligible=[s for s in signals if s.get('record_kind')=='prospective' and s.get('pit_eligible')]
    for horizon in [5,20,60]:
        for signal in suppress_duplicates(eligible,horizon,calendar):
            records.extend(r for r in evaluate_signal(signal,bars_by_symbol.get(signal['symbol'],{}),calendar,benchmarks) if r['horizon']==horizon)
    return records

if __name__=='__main__':
    import socket
    def denied(*a,**k):raise RuntimeError('offline_research_no_network')
    socket.socket.connect=denied
    p=argparse.ArgumentParser();p.add_argument('--snapshot',required=True);p.add_argument('--bundles',required=True);p.add_argument('--calendar',required=True)
    a=p.parse_args();store=Store(ROOT/'forward-study/store')
    result=generate(a.snapshot,a.bundles,load(a.calendar),store)
    print(json.dumps({'asof':result['publication']['asof'],'frozen':sum(r['status']=='frozen' for r in result['results']),'hold':sum(r['status']=='hold' for r in result['results'])}))
