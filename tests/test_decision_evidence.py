"""Synthetic calculations are isolated from preserved production inputs and outputs."""
import copy
import datetime as dt
import gzip
import json
from pathlib import Path

import duckdb
import pytest

from screener.decision_support.evidence import encoded, sha
from screener.decision_support.indicators import flow_metrics, technical
from screener.decision_evidence import metrics as m
from screener.decision_evidence import build as b
from screener.decision_evidence.events import event_evidence, consensus_surprise
from screener.decision_evidence.judgments import record, evaluate


def fixture(n=80):
    # Arbitrary synthetic session labels: not a fabricated production KRX calendar.
    days=[str(dt.date(2020,1,1)+dt.timedelta(days=i)) for i in range(n)]
    bars={d:dict(open=100+i,high=103+i,low=99+i,close=101+i,volume=1000+i,turnover=(101+i)*(1000+i)) for i,d in enumerate(days)}
    fcols=['trade_date','net_amount_krw','net_quantity_shares','institution_net_quantity_shares']
    pcols=['trade_date',*m.FIELDS,'validation_status','reasons']
    stock=dict(symbol='000720',name='synthetic',market='KOSPI',exchange='KRX',asof=days[-1],provider_finality='UNVERIFIED',
        validity={'historical_backtest_eligible':False},
        flow_series={'columns':fcols,'rows':[[d,10 if i>=n-5 else -2,1,-3] for i,d in enumerate(days)]},
        price_series={'columns':pcols,'rows':[[d,*[bars[d][k] for k in m.FIELDS],m.ACCEPTED,[]] for d in days], 'basis':'original'},
        _closed_price_days=days,_price_calendar=days,_benchmarks=dict.fromkeys(days,100))
    return days,bars,stock


def analyzed(n=80):
    days,bars,s=fixture(n)
    return m.analyze(s,days,days,s['_benchmarks'])


def test_exact_nonoverlapping_flow_windows_and_no_unit_invention():
    r=analyzed(20);f=r['foreign_flow_metrics'];p=r['flow_persistence_metrics']
    assert (f['net_3d']['value'],f['net_5d']['value'],f['previous_5d']['value'],f['change_5d']['value'],f['net_20d']['value'])==(30,50,-10,60,20)
    assert p['positive_days_5d']['value']==5 and p['positive_days_20d']['value']==5
    assert p['short_medium_direction']['value']==dict(short_5d='positive',medium_20d='positive')
    assert r['institution_flow_metrics']['net_quantity_5d']['value']==-15
    assert r['institution_flow_metrics']['net_1d']['value'] is None
    assert r['data_quality']['historical_backtest_eligible'] is False


@pytest.mark.parametrize('current,previous,state',[(1,-1,'new_net_buying'),(2,1,'buying_strengthening'),(1,1,'buying_continuing'),(1,2,'buying_slowing'),(-1,1,'new_net_selling'),(-2,-1,'selling_expanding'),(-1,-1,'selling_continuing'),(-1,-2,'selling_easing'),(0,1,'zero_net_flow'),(None,1,None)])
def test_transition_definitions(current,previous,state):
    assert m.transition(current,previous)['value']==state


def test_concentration_uses_positive_gross_sum_not_net_and_censors_streak():
    days,bars,_=fixture(20);amounts=dict.fromkeys(days,-2)
    amounts.update(dict(zip(days[-5:],[10,-5,20,-2,7])))
    r=m.persistence(days,amounts,bars,flow_metrics(days,amounts))
    assert r['max_positive_day_share_5d']['exact_ratio']==dict(numerator='20',denominator='37')
    assert r['mean_positive_day_net_amount_krw']['value']==pytest.approx(37/3)
    assert r['consecutive_net_buy_days']['value']==1
    amounts=dict.fromkeys(days,1);r=m.persistence(days,amounts,bars,flow_metrics(days,amounts))
    assert r['consecutive_net_buy_days']['value'] is None
    assert r['consecutive_net_buy_days_lower_bound']['value']==20 and not r['streak_is_exact']
    del amounts[days[-1]]
    r=m.persistence(days,amounts,bars,flow_metrics(days,amounts))
    assert r['positive_days_5d']['value'] is None and r['consecutive_net_buy_days_lower_bound']['value'] is None


@pytest.mark.parametrize('numerator,denominator,positive',[(-1,100,True),(0,100,True),(1,0,False),(1,0.001,False),(None,1,False)])
def test_undefined_ratios_are_not_zero(numerator,denominator,positive):
    assert m.ratio(numerator,denominator,positive_net=positive)['value'] is None


def test_numeric_canonicalization_exact_no_rounding():
    t=m.table(dict(columns=['trade_date','close'],rows=[['2020-01-01','100.000']]))
    assert t['2020-01-01']['close']==100
    for invalid in ('100.01',float('nan'),True,float('inf')):
        with pytest.raises(ValueError):m.table(dict(columns=['trade_date','close'],rows=[['2020-01-01',invalid]]))


@pytest.mark.parametrize('series',['flow_series','price_series'])
def test_duplicate_dates_not_silently_removed(series):
    days,_,s=fixture();s[series]['rows'].append(s[series]['rows'][-1])
    with pytest.raises(ValueError,match='duplicate_trade_date'):m.analyze(s,days,days,s['_benchmarks'])


@pytest.mark.parametrize('mode',['missing','conflict','unfinished','invalid','adjustment'])
def test_unverified_latest_never_falls_back_or_zero_fills(mode):
    days,_,s=fixture()
    if mode=='missing':s['price_series']['rows'].pop()
    elif mode=='conflict':s['price_series']['rows'][-1][-2:] = ['conflict',['KIS_KRX_six_field_conflict']]
    elif mode=='unfinished':s['_closed_price_days']=days[:-1]
    elif mode=='invalid':s['price_series']['rows'][-1][1]=0
    else:s['price_series']['rows'][-1][-1]=['adjustment_pending']
    s['latest_verified_price']={'close':999999,'asof':days[-2]}
    r=m.analyze(s,days,days,s['_benchmarks'])
    assert r['price_trend_metrics']['close']['value'] is None
    assert r['price_trend_metrics']['sma_5']['value'] is None
    assert r['observation_tags']['foreign_buying_price_rising']['value'] is None
    assert r['data_quality']['stale_verified_price_used_as_current'] is False


def test_gap_does_not_bridge_price_return_or_relative_strength():
    days,bars,s=fixture();del bars[days[-3]]
    assert m.observed_return(days,bars,5)['value'] is None
    del s['_benchmarks'][days[-3]]
    r=m.analyze(s,days,days,s['_benchmarks'])
    assert r['relative_strength_metrics']['market_excess_5d_pp']['value'] is None
    assert r['relative_strength_metrics']['industry_excess_5d_pp']['value'] is None
    assert r['foreign_flow_metrics']['net_5d']['value']==50


def test_returns_require_six_and_twenty_one_closes_not_five_or_twenty():
    days,bars,_=fixture(21)
    assert m.observed_return(days[-5:],bars,5)['value'] is None
    assert m.observed_return(days[-20:],bars,20)['value'] is None
    assert m.observed_return(days,bars,20)['value']==pytest.approx((121/101-1)*100)
    assert m.observed_return(days,bars,5,5)['value']==pytest.approx((116/111-1)*100)


def test_held_history_is_not_counted_as_large_volume_absence():
    days,bars,_=fixture(25)
    r=m.structure(days,bars,technical(days,bars))
    assert r['large_volume_following_session']['value'] is None
    assert r['support_or_resistance_confirmed']['value'] is None


def test_pivots_use_high_low_and_need_two_later_sessions():
    days,bars,_=fixture(40)
    bars[days[34]]['high']=1000;bars[days[38]]['high']=2000
    r=m.structure(days,bars,technical(days,bars))
    pivots=r['confirmed_bar_swing_highs']['value']
    assert pivots[-1]==dict(price=1000,occurred_on=days[34],confirmed_on=days[36])
    assert all(p['occurred_on']!=days[38] for p in pivots)
    bars['2099-01-01']={'high':999999}
    assert m.structure(days,bars,technical(days,bars))==r


def test_zero_range_and_breakout_without_current_bar_are_unknown():
    days,bars,_=fixture(40);bars[days[-1]].update(open=100,high=100,low=100,close=100)
    assert m.structure(days,bars,technical(days,bars))['close_location_in_day_range']['value'] is None
    del bars[days[-1]]
    r=m.structure(days,bars,technical(days,bars))
    assert r['prior_breakout_close_lost']['value'] is None


def test_future_rows_cannot_change_observation_tags():
    days,_,s=fixture(40);old=m.analyze(s,days,days,s['_benchmarks'])
    s['flow_series']['rows'].append(['2099-01-01',99999,99999,99999])
    s['price_series']['rows'].append(['2099-01-01',*([99999]*6),m.ACCEPTED,[]])
    assert m.analyze(s,days,days,s['_benchmarks'])==old
    assert old['observation_tags']['recent_event_detected']['value'] is None


def event():
    return dict(event_type='supply_contract',event_date='2020-01-01',published_at='2020-01-01T09:00:00+09:00',
        source=dict(id='receipt',url='https://example.test/original',sha256='a'*64),relationship='direct',
        related_stock_codes=['000720'],verified_facts=['explicit source fact'],validation_status='verified')


def test_event_relation_fact_and_knowledge_boundaries():
    e=event();out=event_evidence(e,'000720','2020-01-02T00:00:00Z')
    assert out['directly_related'] and out['verified_facts']==e['verified_facts'] and out['price_impact'] is None
    with pytest.raises(ValueError,match='relationship_not_proven'):event_evidence(e,'064400','2020-01-02T00:00:00Z')
    e['relationship']='industry_peer'
    assert not event_evidence(e,'064400','2020-01-02T00:00:00Z')['directly_related']
    assert event_evidence(e,'000720','2019-12-31T00:00:00Z')['verified_facts']==[]
    e['published_at']=None
    assert not event_evidence(e,'000720','2020-01-02T00:00:00Z')['available_at_cutoff']
    e['published_at']='2020-01-01T09:00:00+09:00';e['validation_status']='UNVERIFIED'
    assert event_evidence(e,'000720','2020-01-02T00:00:00Z')['verified_facts']==[]


def test_consensus_is_preannouncement_verified_and_basis_matched():
    base=dict(period='2020Q1',accounting_scope='CFS',currency='KRW',metric='operating_profit',value=100,
              validation_status='verified',source_id='s',published_at='2020-01-01T00:00:00Z')
    actual={**base,'value':120}
    assert consensus_surprise(actual,base,'2020-01-02T00:00:00Z')['value']==20
    for changed in ({'period':'2020Q2'},{'value':0},{'published_at':'2020-01-03T00:00:00Z'},{'validation_status':'UNVERIFIED'}):
        assert consensus_surprise(actual,{**base,**changed},'2020-01-02T00:00:00Z')['value'] is None


def judgment_fixture():
    days,bars,_=fixture(25);asof=days[0];decision='2020-01-01T23:00:00+09:00'
    prices={d:{**v,'validation_status':m.ACCEPTED,'is_closed':True,'basis':'original',
        'observed_at':d+'T20:00:00+09:00','source_id':'price','trade_date':d,'stock_code':'000720','market':'KOSPI'} for d,v in bars.items()}
    index={d:dict(close=100,validation_status='verified',source_id='index',observed_at=d+'T20:00:00+09:00',trade_date=d,market='KOSPI') for d in days}
    flows={d:dict(net_amount_krw=1,validation_status='verified',source_id='flow',observed_at=d+'T20:00:00+09:00',trade_date=d,stock_code='000720',market='KOSPI') for d in days}
    payload=dict(stock_code='000720',market='KOSPI',asof=asof,decision_at=decision,historical_backtest_eligible=True,judgment='synthetic explicit hypothesis')
    calendar=dict(dates=days,source_id='synthetic_calendar',validation_status='verified',observed_at='2020-01-01T20:00:00+09:00')
    return payload,calendar,prices,index,flows


def test_judgment_is_explicit_immutable_and_does_not_requalify_source(tmp_path):
    evidence=dict(stock_code='000720',asof='2020-01-01',generated_at='2020-01-01T11:00:00Z',knowledge_cutoff='2020-01-01T10:00:00Z',data_quality={'historical_backtest_eligible':False})
    with pytest.raises(ValueError,match='explicit_GPT'):record(evidence,'','2020-01-01T12:00:00Z',[],tmp_path)
    with pytest.raises(ValueError,match='later_generated'):record(evidence,'explicit hypothesis','2020-01-01T09:00:00Z',[],tmp_path)
    a=record(evidence,'explicit hypothesis','2020-01-01T12:00:00Z',['review if false'],tmp_path)
    assert record(evidence,'explicit hypothesis','2020-01-01T12:00:00Z',['review if false'],tmp_path)==a
    assert not a['historical_backtest_eligible'] and len(list(tmp_path.iterdir()))==1
    Path(a['path']).write_text('tampered')
    with pytest.raises(ValueError,match='hash_mismatch'):record(evidence,'explicit hypothesis','2020-01-01T12:00:00Z',['review if false'],tmp_path)


def test_future_outcomes_are_session_aligned_and_not_fabricated():
    p,c,prices,index,flows=judgment_fixture()
    r=evaluate(p,c,prices,index,flows,'2020-01-06T23:00:00+09:00')['outcomes']
    assert r['5']['return_pct']==pytest.approx((106/101-1)*100)
    assert r['5']['foreign_net_amount_after_judgment']['value']==5
    assert r['10']['reason']=='future_horizon_not_elapsed' and r['20']['status']=='unavailable'
    assert r['5']['invalidation_condition_result'] is None
    assert evaluate({**p,'historical_backtest_eligible':False},c,prices,index,flows,'2020-02-01T00:00:00Z')['status']=='unavailable'


@pytest.mark.parametrize('flag',['halted','delisted','adjustment_pending'])
def test_evaluation_does_not_skip_suspended_delisted_or_adjusted(flag):
    p,c,prices,index,flows=judgment_fixture();prices[c['dates'][3]][flag]=True
    assert evaluate(p,c,prices,index,flows,'2020-02-01T00:00:00Z')['outcomes']['5']['status']=='unavailable'


def test_base_price_must_have_been_known_at_decision_kst_midnight_boundary():
    p,c,prices,index,flows=judgment_fixture();p['decision_at']='2020-01-01T00:30:00+09:00'
    # UTC date is previous day, but KST target remains Jan 1; base is not known yet.
    r=evaluate(p,c,prices,index,flows,'2020-02-01T00:00:00Z')
    assert r['reason']=='reference_price_unverified'
    p['decision_at']='2020-01-01T23:30:00+09:00'
    assert evaluate(p,c,prices,index,flows,'2020-02-01T00:00:00Z')['outcomes']['20']['status'].startswith('observed')
    index[c['dates'][1]]['observed_at']='2099-01-01T00:00:00Z'
    assert evaluate(p,c,prices,index,flows,'2020-02-01T00:00:00Z')['outcomes']['5']['market_excess_pp']['value'] is None


@pytest.fixture
def synthetic_source(tmp_path,monkeypatch):
    days,_,s=fixture(80);snap=tmp_path/'source';snap.mkdir();(snap/'immutable').write_bytes(b'original')
    s['validity']['historical_backtest_eligible']=True  # Row claims cannot promote an unproven generation.
    index=dict(asof=days[-1],dataset_id='synthetic-source',validation_status='passed_with_metric_holds',provider_finality='UNVERIFIED',
        source_snapshot_sha256='a'*64,coverage={},units={'foreign_amount':'KRW'})
    value=dict(index=index,rows=[s],calendars={'KOSPI':days},input_file_hashes={'immutable':sha(b'original')},
        source_manifest_sha256='b'*64,input_index_sha256='c'*64,priority_symbols=['000720'],macro=[],
        official={},source_verification={'status':'PASS'},historical_backtest_eligible=False,known_source_observation_latest='2020-04-01T00:00:00Z',source_publication_time=None)
    monkeypatch.setattr(b,'load',lambda *args:copy.deepcopy(value))
    return snap,tmp_path/'derived'


def test_independent_build_idempotence_and_all_formats(synthetic_source):
    snap,out=synthetic_source;r=b.build(snap,out,'2020-04-02T00:00:00Z')
    directory=Path(r['directory']);manifest=json.loads((directory/'manifest.json').read_bytes())
    assert r['validation']['records']==1 and manifest['source_tree_unchanged']
    assert b.build(snap,out,'2020-04-02T00:00:00Z')['idempotent']
    assert (snap/'immutable').read_bytes()==b'original'
    market=json.loads(gzip.decompress((directory/'market.json.gz').read_bytes()))
    assert len(market['stocks'])==1 and not market['historical_backtest_eligible']
    assert market['stocks'][0]['data_quality']['historical_backtest_eligible'] is False
    briefing=json.loads((directory/'briefing.json').read_bytes())
    assert briefing['returned']==1 and briefing['investment_recommendations_generated'] is False
    (directory/'records.parquet').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='hash_mismatch'):b.verify_output(directory)
    with pytest.raises(ValueError,match='hash_mismatch'):b.build(snap,out,'2020-04-02T00:00:00Z')


def rehash(directory):
    path=directory/'manifest.json';data=json.loads(path.read_bytes())
    data['files']={p.name:sha(p.read_bytes()) for p in directory.iterdir() if p.name!='manifest.json'}
    path.write_bytes(encoded(data))


@pytest.mark.parametrize('kind',['payload','columns','parquet'])
def test_independent_database_mismatch_caught_even_if_manifest_rehashed(synthetic_source,kind):
    snap,out=synthetic_source;directory=Path(b.build(snap,out,'2020-04-02T00:00:00Z')['directory'])
    if kind=='parquet':
        with duckdb.connect() as db:db.execute('copy (select 0 as source_order, \'x\' as stock_code, date \'2020-01-01\' as trade_date, \'x\' as dataset_id, \'{}\' as payload) to ? (format parquet)',[str(directory/'records.parquet')])
    else:
        with duckdb.connect(str(directory/'evidence.duckdb')) as db:
            db.execute("update evidence set payload='{}'" if kind=='payload' else "update evidence set stock_code='999999'")
    rehash(directory)
    with pytest.raises((ValueError,KeyError)):b.verify_output(directory)


def test_protected_paths_and_future_cutoff_rejected_before_loading(synthetic_source,monkeypatch):
    snap,out=synthetic_source
    monkeypatch.setattr(b,'load',lambda *args:pytest.fail('must fail before source load'))
    for path in (snap,snap/'nested',b.ROOT/'snapshot',b.ROOT/'reports'):
        with pytest.raises(ValueError,match='independent'):b.build(snap,path,'2020-04-02T00:00:00Z')
    with pytest.raises(ValueError,match='in_future'):b.build(snap,out,'2099-01-01T00:00:00Z')


def test_source_hash_rejection_is_not_bypassed(tmp_path,monkeypatch):
    from screener.decision_evidence.source import load
    import scripts.build_decision_support as original
    def fail(*args):raise ValueError('source_hash_mismatch')
    monkeypatch.setattr(original,'verify',fail)
    with pytest.raises(ValueError,match='source_hash_mismatch'):load(tmp_path,'2020-01-01T00:00:00Z')


def test_no_candidates_for_incomplete_price_and_existing_priority_preserved():
    r=analyzed();rows=[]
    for i in range(25):
        rows.append(dict(stock_code=f'{i:06}',stock_name='synthetic',**copy.deepcopy(r),missing_fields=[],source_references={}))
    out=b.candidates(rows,['000024','000023'])
    assert out['returned']==20 and out['stocks'][0]['stock_code']=='000024' and out['eligible_candidates']==25
    rows[24]['price_trend_metrics']['return_5d_pct']['value']=None
    assert b.candidates(rows,['000024'])['stocks'][0]['stock_code']=='000000'
    with pytest.raises(ValueError):b.candidates(rows,[],21)


@pytest.mark.parametrize('days,asof',[([], '2020-01-01'),(['2020-01-01','2020-01-01'],'2020-01-01'),(['2020-01-02','2020-01-01'],'2020-01-01'),(['2020-01-01'],'2020-01-02')])
def test_calendar_is_exact_no_synthetic_gap_fill(days,asof):
    with pytest.raises(ValueError):m.checked_days(days,asof)


def test_nonpositive_flow_concentration_and_missing_turnover_stay_null():
    days,bars,_=fixture(20);amounts=dict.fromkeys(days,-10);amounts[days[-1]]=1
    r=m.persistence(days,amounts,bars,flow_metrics(days,amounts))
    for key in ('max_positive_day_share_5d','mean_positive_day_turnover_ratio','net_5d_to_turnover_ratio'):
        assert r[key]['value'] is None
    amounts=dict.fromkeys(days,1);del bars[days[-2]]
    assert m.persistence(days,amounts,bars,flow_metrics(days,amounts))['net_5d_to_turnover_ratio']['value'] is None


def test_exact_relative_strength_equal_is_not_improvement_from_float_noise():
    days,_,s=fixture(30)
    prices=m.table(s['price_series'])
    # Identical stock/index series yield mathematically zero excess in both windows.
    benchmarks={d:float(prices[d]['close']) for d in days}
    r=m.analyze(s,days,days,benchmarks)
    assert r['observation_tags']['relative_strength_improving']['value'] is False
    benchmarks[days[-3]]=None
    assert m.analyze(s,days,days,benchmarks)['observation_tags']['relative_strength_improving']['value'] is None


def test_judgment_missing_calendar_or_unpublished_future_price_is_not_evaluated():
    p,c,prices,index,flows=judgment_fixture()
    assert evaluate({**p,'judgment':''},c,prices,index,flows,'2020-02-01T00:00:00Z')['reason']=='explicit_GPT_judgment_missing'
    assert evaluate(p,c['dates'],prices,index,flows,'2020-02-01T00:00:00Z')['reason']=='verified_calendar_provenance_missing'
    prices[c['dates'][2]]['observed_at']='2099-01-01T00:00:00Z'
    assert evaluate(p,c,prices,index,flows,'2020-02-01T00:00:00Z')['outcomes']['5']['status']=='unavailable'


def test_read_only_builder_failure_leaves_inputs_and_no_published_output(synthetic_source,monkeypatch):
    snap,out=synthetic_source
    def fail(*args):raise RuntimeError('isolated_postprocessor_failure')
    monkeypatch.setattr(b,'analyze',fail)
    with pytest.raises(RuntimeError,match='isolated_postprocessor_failure'):b.build(snap,out,'2020-04-02T00:00:00Z')
    assert (snap/'immutable').read_bytes()==b'original' and not list(out.glob('decision-*'))


def test_changed_input_tree_cannot_silently_reuse_existing_generation(synthetic_source,monkeypatch):
    snap,out=synthetic_source;first=b.build(snap,out,'2020-04-02T00:00:00Z')
    old=b.load
    def revised(*args):
        value=old(*args);value['input_file_hashes']['immutable']=sha(b'revised observation');return value
    (snap/'immutable').write_bytes(b'revised observation');monkeypatch.setattr(b,'load',revised)
    second=b.build(snap,out,'2020-04-02T00:00:00Z')
    assert first['dataset_id']!=second['dataset_id'] and Path(first['directory']).exists()


def test_exact_official_differences_not_tolerated_or_cause_invented():
    from screener.decision_evidence.source import differences
    days,bars,s=fixture();reference=dict(bars[days[-1]]);reference['close']-=1
    official={(s['symbol'],s['asof'],s['market']):[dict(value=reference,source_id='krx')]}
    result=differences(s,official)
    assert len(result)==1 and result[0]['delta']==1 and result[0]['status']=='conflict'
    assert result[0]['cause']=='UNRESOLVED_NOT_ATTRIBUTED_TO_NXT_OR_ADJUSTMENTS'
    official[(s['symbol'],s['asof'],s['market'])].append(dict(value=bars[days[-1]],source_id='newer_krx'))
    assert differences(s,official)[0]['reason']=='official_reference_versions_conflict_or_invalid'


@pytest.mark.parametrize('kind',['wrong_stock','wrong_market','wrong_date','before_close'])
def test_future_evaluation_checks_identity_and_completed_observation(kind):
    p,c,prices,index,flows=judgment_fixture();row=prices[c['dates'][3]]
    if kind=='wrong_stock':row['stock_code']='064400'
    elif kind=='wrong_market':row['market']='KOSDAQ'
    elif kind=='wrong_date':row['trade_date']=c['dates'][2]
    else:row['observed_at']=c['dates'][3]+'T10:00:00+09:00'
    assert evaluate(p,c,prices,index,flows,'2020-02-01T00:00:00Z')['outcomes']['5']['status']=='unavailable'


def test_separately_saved_manifest_anchor_detects_manifest_replacement(synthetic_source):
    snap,out=synthetic_source;built=b.build(snap,out,'2020-04-02T00:00:00Z');directory=Path(built['directory'])
    anchor=built['validation']['manifest_sha256']
    assert b.verify_output(directory,anchor)['status']=='PASS'
    manifest=directory/'manifest.json';manifest.write_bytes(manifest.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='anchor_mismatch'):b.verify_output(directory,anchor)


def test_empty_source_rejected_without_publishing(synthetic_source,monkeypatch):
    snap,out=synthetic_source;original=b.load
    def empty(*args):
        value=original(*args);value['rows']=[];return value
    monkeypatch.setattr(b,'load',empty)
    with pytest.raises(ValueError,match='empty_source_population'):b.build(snap,out,'2020-04-02T00:00:00Z')
    assert not out.exists()
