"""Synthetic calendars/quotes stay in tests; real original bundle is read-only."""
import copy,datetime as dt,gzip,json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from engine import digest,cohort_nav
from forward_system import (Store,official_next_session,build_signal,compare_krx,observe,evaluate_signal,
                            validate_publication,load,FIELDS,KRX_FIELDS,evidence_digest)
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def real_bundle():
    import csv
    with (ROOT/'signals.csv').open(encoding='utf-8-sig') as f:
        s=next(r for r in csv.DictReader(f) if r['record_kind']=='historical_partial_universe' and r['group']=='A')
    return load(ROOT/s['source'])

def holiday():
    return {'holiday_response':{'endpoint':'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/chk-holiday',
      'tr_id':'CTCA0903R','rt_cd':'0','observed_at':'2026-10-15T12:00:00+00:00',
      'output':[{'bass_dt':'20261016','opnd_yn':'Y','tr_day_yn':'Y'}]}}

def build(b,**changes):
    prices={r['trade_date']:{**r,'adjusted_close':r['close']} for r in b['bars']}
    flows={r['trade_date']:(r['net_amount'],'KRX') for r in b['flows']}
    args={'publication':{'asof':'2026-10-15','sha256':'test-only','dataset_id':'test-only','known_at':'2026-10-15T23:40:03+00:00'},
      'price_rows':prices,'flow_rows':flows,'sessions':[r['trade_date'] for r in b['bars']],
      'calendar':holiday(),'now':dt.datetime.fromisoformat('2026-10-15T23:50:00+00:00')}
    args.update(changes);return build_signal(b,**args)

def test_append_only_atomic_concurrent_retry_and_revision(tmp_path):
    store=Store(tmp_path);payload={'symbol':'001234','value':1}
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:store.put('signals','a'*64,payload),range(12)))
    assert len(store.records('signals'))==1
    with pytest.raises(ValueError,match='revision_conflict'):store.put('signals','a'*64,{**payload,'value':2})
    assert not list((tmp_path/'signals').glob('.pending-*'))

def test_tampering_and_unsafe_identity_rejected(tmp_path):
    store=Store(tmp_path);p=store.put('signals','a',{'value':1});x=load(p);x['payload']['value']=2;p.write_text(json.dumps(x))
    with pytest.raises(ValueError,match='hash_mismatch'):store.read(p)
    with pytest.raises(ValueError):store.put('signals','../x',{})

def test_calendar_no_weekday_guess_or_gap():
    now=dt.datetime.fromisoformat('2026-10-15T23:50:00+00:00')
    with pytest.raises(ValueError,match='evidence_missing'):official_next_session('2026-10-15',{},now)
    h=holiday();h['holiday_response']['output'][0]['bass_dt']='20261017'
    with pytest.raises(ValueError,match='coverage_gap'):official_next_session('2026-10-15',h,now)
    assert official_next_session('2026-10-15',holiday(),now)[0]=='2026-10-16'

def test_real_structure_reused_with_isolated_calendar_fixture(real_bundle):
    s=build(real_bundle)
    assert s['group']=='A' and s['symbol']=='352820' and s['L2_price']==155300 and s['L3_price']==156500
    assert s['net_5d_krw']==2854000000 and s['change_5d_krw']==704000000
    assert s['confirmed_date']=='2026-10-14' and s['signal_date']=='2026-10-15'
    assert s['provider_finality']==real_bundle['finality']

@pytest.mark.parametrize('at',['2026-10-16T00:00:00+00:00','2026-10-20T00:00:00+00:00'])
def test_late_registration_never_backdates(real_bundle,at):
    with pytest.raises(ValueError,match='freeze_outside'):build(real_bundle,now=dt.datetime.fromisoformat(at))

def test_new_revision_known_after_open_rejects_original_publication_time(real_bundle):
    p={'asof':'2026-10-15','sha256':'x','dataset_id':'x','known_at':'2026-10-16T00:01:00+00:00'}
    with pytest.raises(ValueError,match='evidence_not_available'):build(real_bundle,publication=p)

def test_any_overlap_conflict_and_flow_mismatch_block(real_bundle):
    prices={r['trade_date']:{**r,'adjusted_close':r['close']} for r in real_bundle['bars']};prices[real_bundle['bars'][-9]['trade_date']]['adjusted_close']+=1
    with pytest.raises(ValueError,match='overlap_conflict'):build(real_bundle,price_rows=prices)
    flows={r['trade_date']:(r['net_amount'],'KRX') for r in real_bundle['flows']};flows[real_bundle['flows'][-8]['trade_date']]=(0,'KRX')
    with pytest.raises(ValueError,match='flow_mismatch'):build(real_bundle,flow_rows=flows)

def observation():
    return {'symbol':'001234','market':'KOSPI','trade_date':'2026-10-16','exchange':'KRX','basis':'raw','open':100.,'high':110.,'low':90.,'close':105.,'volume':10.,'turnover':1000.}

def reference(o):
    rows=[{'BAS_DD':'20261016','ISU_CD':'001234',**{f:str(o[k]) for k,f in zip(FIELDS,KRX_FIELDS)}}]
    return {'url':'https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20261016','http':200,'observed_at':'2026-10-17T00:00:00+00:00','rows':rows,'rows_sha256':evidence_digest(rows)}

def test_KRX_six_fields_unit_date_scope_and_hash():
    o=observation();r=reference(o);assert compare_krx(o,r)['matched']
    for field in FIELDS:
        changed={**o,field:o[field]+1};assert not compare_krx(changed,r)['matched']
    assert not compare_krx({**o,'basis':'adjusted'},r)['matched']
    assert not compare_krx({**o,'trade_date':'2026-10-15'},r)['matched']
    r['rows'][0]['TDD_CLSPRC']='106';assert 'reference_rows_hash_mismatch' in compare_krx(o,r)['reasons']

def test_observations_keep_versions_and_no_auto_finality(tmp_path):
    s=Store(tmp_path);o=observation();first=observe(s,o,reference(o));second=observe(s,{**o,'close':104},None)
    assert first['comparison']['matched'] and not first['execution_verified']
    assert second['conflicting_observations'] and not second['execution_verified']
    assert len([r for r in s.records('observations') if r['kind']=='outcome'])==2

def test_publication_actual_snapshot_and_hash_failure(tmp_path):
    p=ROOT/'forward-study/inputs';r=validate_publication(p);assert r['asof']=='2026-10-20' and r['records']==2429
    import shutil
    for name in ['dashboard.duckdb','manifest.json','daily-validation.json','publication-ledger.json','index.json']:
        shutil.copyfile(p/name,tmp_path/name)
    m=load(tmp_path/'manifest.json');m['sha256']='wrong';(tmp_path/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='hash_mismatch'):validate_publication(tmp_path)

def test_missing_followup_never_produces_returns_or_MDD():
    s={'symbol':'001234','market':'KOSPI','group':'A','pit_eligible':True,'record_kind':'prospective','signal_date':'2026-10-15'}
    results=evaluate_signal(s,{},['2026-10-15','2026-10-16'])
    assert len(results)==24 and all(r['net_return'] is None and r['market_excess'] is None for r in results)
    assert cohort_nav([],{},[])['mdd'] is None

def test_equal_cash_cohort_MDD_does_not_chain_trade_returns():
    d=['2026-10-16','2026-10-19'];t=[{'symbol':'001234','status':'관측 완료','entry_date':d[0],'exit_date':d[1],'entry_price':100,'exit_price':110}]
    bars={'001234':{d[0]:{'close':80,'execution_verified':True,'action_interval_verified':True,'basis':'raw'}}}
    result=cohort_nav(t,bars,d);assert result['mdd']==pytest.approx(-.2) and result['nav'][-1][1]==pytest.approx(1.1)
    bars['001234'][d[0]]['execution_verified']=False;assert cohort_nav(t,bars,d)['mdd'] is None

def test_market_comparison_uses_stop_exit_open_not_later_close():
    days=['2026-10-15','2026-10-16','2026-10-19','2026-10-20','2026-10-21','2026-10-22']
    s={'symbol':'001234','market':'KOSPI','group':'A','pit_eligible':True,'record_kind':'prospective','signal_date':days[0],'L3_raw':95}
    bars={d:{'open':100,'high':110,'low':85,'close':90 if i==0 else 105,'volume':10,'basis':'raw','execution_verified':True,'action_interval_verified':True,'tradable':True} for i,d in enumerate(days[1:])}
    index={d:{'market':'KOSPI','verified':True,'open':100 if i==0 else 102,'close':120} for i,d in enumerate(days[1:])}
    result=next(r for r in evaluate_signal(s,bars,days,index) if r['horizon']==5 and r['policy']=='L3' and r['roundtrip_bps']==0)
    assert result['exit_basis']=='open' and result['market_excess']==pytest.approx(-.02)

def test_whole_pipeline_freezes_once_and_keeps_original_input(real_bundle,tmp_path):
    """Small synthetic publication around a preserved bundle; never production."""
    import duckdb
    from forward_system import generate,sha,briefing_payload,briefing_digest
    p=tmp_path/'publication';p.mkdir();b=real_bundle;day=b['asof'];symbol=b['symbol'];market=b['market']
    c=duckdb.connect(str(p/'dashboard.duckdb'))
    c.execute('create table instruments(symbol varchar,name varchar,market varchar,listing_date date)');c.execute('insert into instruments values (?,?,?,?)',[symbol,'TEST',market,'2020-01-01'])
    c.execute('create table flows(symbol varchar,trade_date date,net_amount bigint,exchange varchar)')
    c.executemany('insert into flows values (?,?,?,?)',[(symbol,r['trade_date'],r['net_amount'],'KRX') for r in b['flows'][-20:]])
    c.execute('create table prices(symbol varchar,trade_date date,close double,adjusted_close double,volume bigint,turnover double)')
    c.executemany('insert into prices values (?,?,?,?,?,?)',[(symbol,r['trade_date'],r['close'],r['close'],r['volume'],r['turnover']) for r in b['bars']])
    c.execute('create table calendar(market varchar,trade_date date)');c.executemany('insert into calendar values (?,?)',[(market,r['trade_date']) for r in b['bars']])
    c.execute('create table exclusions(symbol varchar,trade_date date)');c.close();h=sha(p/'dashboard.duckdb')
    v={'trade_date':day,'records':1,'preservation_verified':True,'sha256':h}
    idx={'asof':day,'source_snapshot_sha256':h,'dataset_id':'flow-v3-'+day+'-'+h,'validation_status':'passed','publication_status':'ready_for_briefing','generated_at':'2026-10-15T23:40:03+00:00','published_at':'2026-10-15T23:40:03+00:00'}
    idx['index_content_sha256']=briefing_digest(briefing_payload(idx))
    docs={'manifest.json':{'asof':day,'sha256':h,'exchange':'KRX','daily_validation':v},'daily-validation.json':v,'publication-ledger.json':{day:{'sha256':h,'validation':v,'published_at_utc':idx['published_at']}},'index.json':idx}
    for name,x in docs.items():(p/name).write_text(json.dumps(x))
    bundles=tmp_path/'bundles';bundles.mkdir();(bundles/(symbol+'.json')).write_text(json.dumps(b))
    store=Store(tmp_path/'store');first=generate(p,bundles,holiday(),store,dt.datetime.fromisoformat('2026-10-15T23:50:00+00:00'))
    assert first['results'][0]['status']=='frozen';before=store.records('signals')[0]
    second=generate(p,bundles,holiday(),store,dt.datetime.fromisoformat('2026-10-16T00:30:00+00:00'))
    assert second['results'][0]['status']=='already_frozen' and store.records('signals')==[before]
    assert len(store.records('observations'))==2 and before['bundle_sha256']==digest(b)
    (store.root/'observations'/(before['calendar_sha256']+'.json')).unlink()
    with pytest.raises(FileNotFoundError):
        generate(p,bundles,holiday(),store,dt.datetime.fromisoformat('2026-10-16T00:31:00+00:00'))
