"""Synthetic offline tests. These results are not market/API/strategy performance."""
import copy
import datetime as dt
import gzip
import json
from pathlib import Path
from fractions import Fraction

import duckdb
import pytest

from test_decision_evidence import fixture, synthetic_source, judgment_fixture
from screener.decision_evidence.metrics import analyze
from screener.decision_evidence import build as evidence_build
from screener.decision_support.evidence import encoded, sha
from screener.investment_v2 import contracts as c
from screener.investment_v2 import context as ctx
from screener.investment_v2 import assessment as a
from screener.investment_v2 import portfolio as p
from screener.investment_v2 import engine as e
from screener.investment_v2 import pipeline as pipe
from screener.investment_v2 import records as rec
from screener.investment_v2.briefing import render, PRICE_COLUMNS


def row(n=140):
    days, bars, stock = fixture(n)
    features = analyze(stock, days, days, stock['_benchmarks'])
    return dict(dataset_id='synthetic-verified-evidence', asof=days[-1], stock_code='000720', stock_name='합성종목',
        source_manifest_sha256='a'*64, source_generation_id='synthetic-source', exchange='KRX', market='KOSPI',
        generated_at=days[-1]+'T08:00:00Z', knowledge_cutoff=days[-1]+'T07:00:00Z', provider_finality='UNVERIFIED', **features)


def context_facts():
    facts = []
    for identifier, metric, val, start, end in (
        ('revenue_old', 'revenue', 100, '2018-01-01', '2018-12-31'),
        ('revenue_new', 'revenue', 120, '2019-01-01', '2019-12-31'),
        ('profit', 'operating_profit', 12, '2019-01-01', '2019-12-31'),
        ('debt', 'total_liabilities', 60, '2019-01-01', '2019-12-31'),
        ('equity', 'total_equity', 100, '2019-01-01', '2019-12-31')):
        facts.append(dict(id=identifier, kind='financial', stock_code='000720', metric=metric, value=val,
                          unit='KRW', accounting_scope='CFS', currency='KRW', period_start=start, period_end=end,
                          source_sha256='b'*64, source_id='synthetic'))
    facts.extend([dict(id='industry', kind='industry', metric='outlook_positive', stock_code='000720', value=True, unit='boolean', source_sha256='b'*64),
                  dict(id='macro', kind='macro', metric='US_DXY', stock_code=None, value=100, unit='index', source_sha256='b'*64),
                  dict(id='duration', kind='event', metric='adversity_duration', stock_code='000720', value='temporary', unit='classification', source_sha256='b'*64)])
    return dict(status='SYNTHETIC_TEST_ONLY', facts=facts, held=[], input_files={})


def full_row():
    r = row()
    def setv(group, key, val):
        r[group][key] = dict(value=val, status='valid', reason=None)
    for key, val in {'close':100, 'current_low':89, 'sma_20':94, 'sma_5':98, 'sma_20_slope_1d':1,
        'return_5d_pct':2, 'atr_14':2, 'current_volume_to_mean_5d_ratio':1.2, 'close_breakout_20':True,
        'confirmed_bar_swing_lows':[dict(price=90,occurred_on='2020-05-10',confirmed_on='2020-05-12')],
        'confirmed_bar_swing_highs':[dict(price=125,occurred_on='2020-05-08',confirmed_on='2020-05-10')]}.items():
        setv('price_trend_metrics',key,val)
    for key, val in {'net_5d':100, 'net_20d':400, 'change_5d':10}.items():setv('foreign_flow_metrics',key,val)
    for key, val in {'positive_days_5d':4,'positive_days_20d':15,'net_5d_to_turnover_ratio':.1}.items():setv('flow_persistence_metrics',key,val)
    for key, val in {'net_quantity_5d':10,'net_quantity_change_5d':5}.items():setv('institution_flow_metrics',key,val)
    setv('relative_strength_metrics','market_excess_5d_pp',1)
    return r


def cash_account():
    return dict(status='VALUED', holdings={}, assets_krw='100000', cash_krw='100000',
                total_planned_risk_ratio='0', constraints=[])


def reply(r):
    fid = 'foreign_flow_metrics.net_5d'
    narrative = dict(kind='HYPOTHESIS', text='수급의 지속성을 관찰한다', fact_ids=[fid])
    return dict(dataset_id=r['dataset_id'], asof=r['asof'], stock_code=r['stock_code'], grade='C', strategy='none', action='WATCH',
        claims=[dict(fact_id=fid, value=c.value(r,'foreign_flow_metrics','net_5d'), unit='KRW')],
        thesis=[narrative.copy()], counter_thesis=[narrative.copy()], risks=[narrative.copy()], invalidation=[narrative.copy()], holding_comparisons=[])


def batch_reply(r):
    return dict(dataset_id=r['dataset_id'],asof=r['asof'],judgments=[reply(r)],selected_stock_codes=[])


def event(identifier, kind, **kwargs):
    return dict(id=identifier, kind=kind, at='2020-01-01T10:00:00Z', user_confirmed=True, **kwargs)


def fill(identifier, kind, quantity=10, price='100', **kw):
    return event(identifier, kind, stock_code='000720', stock_name='합성종목', market='KOSPI',
                 quantity=quantity, price_krw=price, fee_krw='0', tax_krw='0', **kw)


def test_quantitative_missing_three_session_quantity_added_without_changing_amounts():
    r = row()
    assert c.value(r,'foreign_flow_metrics','net_quantity_3d') == 3
    assert c.value(r,'institution_flow_metrics','net_quantity_3d') == -9
    assert c.value(r,'institution_flow_metrics','net_quantity_previous_5d') == -15
    assert c.value(r,'institution_flow_metrics','net_quantity_change_5d') == 0


@pytest.mark.parametrize('v',[None,True,'NaN','Infinity','abc'])
def test_contracts_reject_non_numbers(v):
    with pytest.raises(ValueError):c.number(v)


@pytest.mark.parametrize('n',[20,59,119])
def test_long_technical_history_not_fabricated(n):
    r=row(n)
    assert c.value(r,'price_trend_metrics','sma_120') is None
    if n<60:assert c.value(r,'price_trend_metrics','sma_60') is None


def test_scoring_missing_categories_not_normalized_to_one_hundred():
    r=row();context=dict(status='NOT_RUN',facts=[])
    out=a.assess(r,context,p.review(None,{}))
    s=out['scorecard']
    assert s['total_score'] is None and s['missing_points']>=15 and s['normalized_score'] is None
    assert [x['maximum'] for x in s['categories'].values()]==[30,30,15,15,10]
    assert out['final_grade'] is None


def test_full_score_hypothesis_checks_and_reward_risk_separate():
    r=full_row();out=a.assess(r,context_facts(),cash_account())
    assert out['scorecard']['total_score']==100
    assert len(out['hypothesis_checks'])==7 and all(x['status']=='PASS' for x in out['hypothesis_checks'])
    plan=out['strategies']['trend_confirmation']
    assert Fraction(plan['reward_risk_exact'])==Fraction(5,2)
    assert plan['defense']==90 and plan['status']=='APPLICABLE_FOR_REVIEW'
    assert plan['automatic_order'] is False and 'NOT_AUTOMATIC' in plan['observation_target_action']


def test_expected_support_and_observed_test_not_future_guarantee():
    r=full_row();s=a.structure(r)
    assert s['support_candidates'][0]['state']=='EXPECTED_LEVEL' and s['support_test_confirmed'] is True
    r['price_trend_metrics']['current_low']['value']=99
    assert a.structure(r)['support_test_confirmed'] is False


def test_absorption_never_asserted_and_unverified_price_blocks_levels():
    r=row();r['data_quality']['latest_price_verified']=False
    r['price_trend_metrics']['close']['value']=None
    out=a.assess(r,dict(status='NOT_RUN',facts=[]),p.review(None,{}))
    assert out['technical_structure']['final_defense_candidate'] is None
    assert out['flow_price_divergence']['absorption']=='HYPOTHESIS_NOT_CONFIRMED'
    assert out['flow_price_divergence']['automatic_negative_conclusion'] is False


def test_reversal_add_requires_holding_recovery_and_known_event_character():
    r=full_row();acc=cash_account()
    assert a.assess(r,context_facts(),acc)['strategies']['flow_maintained_reversal_add']['status']!='APPLICABLE_FOR_REVIEW'
    acc['holdings']['000720']=dict(defense=None,defense_breached=False,market_value_krw='1000')
    r['price_trend_metrics']['close_change_1d_pct']['value']=2
    r['flow_price_relationship']['previous_5d_return_pct']['value']=-5
    assert a.assess(r,context_facts(),acc)['strategies']['flow_maintained_reversal_add']['status']=='APPLICABLE_FOR_REVIEW'
    assert a.assess(r,dict(status='NOT_RUN',facts=[]),acc)['strategies']['flow_maintained_reversal_add']['status']=='UNAVAILABLE'


def test_existing_defense_used_in_risk_plan_not_replaced_by_new_pivot():
    r=full_row();acc=cash_account();acc['holdings']['000720']=dict(defense={'price_krw':'95'},defense_breached=False,market_value_krw='1000')
    assert a.assess(r,context_facts(),acc)['strategies']['trend_confirmation']['defense']==95
    acc['holdings']['000720']['defense_breached']=True
    assert a.assess(r,context_facts(),acc)['strategies']['trend_confirmation']['status']!='APPLICABLE_FOR_REVIEW'


@pytest.fixture
def bundle(tmp_path):
    original=tmp_path/'original.json';original.write_bytes(encoded({'revenue':120,'news':'실제 원문 테스트 문자열'}))
    src=dict(file='original.json',sha256=sha(original.read_bytes()),validation_status='verified',published_at='2020-01-01T00:00:00Z',observed_at='2020-01-02T00:00:00Z')
    item=dict(id='test',kind='macro',metric='synthetic_macro',unit='index',value=120,source_id='src',pointer=['revenue'],stock_code=None,
              validation_status='verified',published_at=src['published_at'],observed_at=src['observed_at'])
    d=dict(schema_version='investment-context-v1',asof='2020-05-19',generated_at='2020-05-19T07:00:00Z',sources={'src':src},items=[item])
    path=tmp_path/'context.json';path.write_bytes(encoded(d))
    return path,d


def test_context_hash_pointer_and_exact_cutoff_validation(bundle):
    path,d=bundle
    assert ctx.load_bundle(path,d['asof'],'2020-05-19T08:00:00Z')['facts'][0]['value']==120
    d['items'][0]['value']=121;path.write_bytes(encoded(d))
    with pytest.raises(ValueError,match='not_equal'):ctx.load_bundle(path,d['asof'],'2020-05-19T08:00:00Z')


@pytest.mark.parametrize('change,reason',[
    ({'validation_status':'UNVERIFIED'},'UNVERIFIED_CONTEXT'),
    ({'published_at':None},'EXACT_AVAILABILITY_UNKNOWN'),
    ({'observed_at':'2021-01-01T00:00:00Z'},'NOT_AVAILABLE_AT_CUTOFF')])
def test_unverified_or_future_context_not_sent_as_facts(bundle,change,reason):
    path,d=bundle;d['items'][0].update(change);path.write_bytes(encoded(d))
    out=ctx.load_bundle(path,d['asof'],'2020-05-19T08:00:00Z')
    assert out['facts']==[] and out['held'][0]['reason']==reason


def test_context_corrupt_original_and_unsafe_path_blocked(bundle):
    path,d=bundle;(path.parent/'original.json').write_text('{}')
    with pytest.raises(ValueError,match='hash'):ctx.load_bundle(path,d['asof'],'2020-05-19T08:00:00Z')
    d['sources']['src']['file']='../escape.json';path.write_bytes(encoded(d))
    with pytest.raises(ValueError,match='unsafe'):ctx.load_bundle(path,d['asof'],'2020-05-19T08:00:00Z')


def test_financial_growth_profitability_and_basis_mismatch():
    context=context_facts();out=ctx.financial_metrics(context,'000720')
    assert Fraction(out['revenue_growth_pct']['value'])==20
    assert Fraction(out['operating_margin_ratio']['value'])==Fraction(1,10)
    assert Fraction(out['debt_to_equity_ratio']['value'])==Fraction(3,5)
    context['facts'][0]['accounting_scope']='OFS'
    assert ctx.financial_metrics(context,'000720')['revenue_growth_pct']['value'] is None


def test_portfolio_fill_idempotence_past_snapshot_and_realized_unrealized(tmp_path):
    ledger=p.PortfolioLedger(tmp_path/'account')
    ledger.append(event('cash','DEPOSIT',amount_krw='10000'))
    buy=fill('buy','BUY');ledger.append(buy);ledger.append(buy)
    old=ledger.snapshot('2020-01-01T11:00:00Z')
    sell=fill('sell','SELL',quantity=5,price='120');sell['at']='2020-01-02T10:00:00Z';ledger.append(sell)
    assert ledger.snapshot('2020-01-01T11:00:00Z')==old
    latest=ledger.snapshot('2020-01-03T00:00:00Z')
    assert latest['realized_pnl_krw']=='100' and latest['holdings']['000720']['quantity']==5
    r=full_row();account=p.review(latest,{'000720':r})
    assert account['assets_krw']=='10100' and account['holdings']['000720']['unrealized_pnl_krw']=='0'
    assert len(ledger.events())==3


@pytest.mark.parametrize('bad',[
    {'user_confirmed':False},{'quantity':True},{'quantity':-1},{'fee_krw':'-1'},{'market':'NXT'}])
def test_portfolio_rejects_unconfirmed_invalid_or_insufficient_fills(tmp_path,bad):
    ledger=p.PortfolioLedger(tmp_path/'account');ledger.append(event('cash','DEPOSIT',amount_krw='10000'))
    v=fill('buy','BUY');v.update(bad)
    with pytest.raises(ValueError):ledger.append(v)
    assert len(ledger.events())==1


def test_defense_history_trigger_latches_and_cannot_be_lowered(tmp_path):
    ledger=p.PortfolioLedger(tmp_path/'account');ledger.append(event('cash','DEPOSIT',amount_krw='10000'));ledger.append(fill('buy','BUY'))
    defense=event('defense','DEFENSE',stock_code='000720',price_krw='90',reason='confirmed_structure',evidence_ids=['a'])
    ledger.append(defense)
    with pytest.raises(ValueError,match='lowered'):ledger.append({**defense,'id':'lower','price_krw':'80'})
    ledger.append(event('hit','DEFENSE_TRIGGER',stock_code='000720',observed_price_krw='89',origin='VERIFIED_OBSERVATION',evidence_ids=['b']))
    with pytest.raises(ValueError,match='retroactively'):ledger.append({**defense,'id':'new','price_krw':'91'})
    assert ledger.snapshot('2020-01-02T00:00:00Z')['holdings']['000720']['defense_triggered']


def test_portfolio_tampering_and_duplicate_event_replacement_detected(tmp_path):
    ledger=p.PortfolioLedger(tmp_path/'account');cash=event('cash','DEPOSIT',amount_krw='10000');ledger.append(cash)
    with pytest.raises(ValueError,match='reused'):ledger.append({**cash,'amount_krw':'20000'})
    file=next(ledger.root.glob('*.json'));d=json.loads(file.read_bytes());d['event']['amount_krw']='99999';file.write_bytes(encoded(d))
    with pytest.raises(ValueError,match='corrupt'):ledger.snapshot('2020-01-02T00:00:00Z')


def test_position_sizing_uses_cash_assets_existing_risk_and_weight_room():
    account=cash_account()
    out=p.sizing(account,'000720',100,90)
    assert out['quantity']==100
    account['total_planned_risk_ratio']='0.029'
    assert p.sizing(account,'000720',100,90)['quantity']==10
    account['total_planned_risk_ratio']=None
    assert p.sizing(account,'000720',100,90)['quantity'] is None


def test_engine_no_key_read_or_network_without_explicit_live_gate():
    engine=e.JudgmentEngine(key_provider=lambda:pytest.fail('must not read key'))
    r=row();out=a.assess(r,dict(facts=[]),p.review(None,{}))
    assert engine.judge(r,out,dict(facts=[]),p.review(None,{}),r['asof']+'T12:00:00Z')['status']=='NOT_RUN'
    assert engine.calls==0


@pytest.mark.parametrize('mutation', ['number','unit','source','date','unknown_key','unreferenced','literal_number','url','buy_missing','missing_counter','hold_unknown'])
def test_engine_rejects_fabrication_bad_schema_and_missing_buy_evidence(mutation):
    r=row();context=dict(facts=[]);portfolio=p.review(None,{});out=a.assess(r,context,portfolio);j=reply(r)
    if mutation=='number':j['claims'][0]['value']+=1
    if mutation=='unit':j['claims'][0]['unit']='million_KRW'
    if mutation=='source':j['claims'][0]['fact_id']='invented.news'
    if mutation=='date':j['asof']='2099-01-01'
    if mutation=='unknown_key':j['new_price']=123
    if mutation=='unreferenced':j['thesis'][0]['fact_ids']=['unknown']
    if mutation=='literal_number':j['thesis'][0]['text']='수익률은 99%이다'
    if mutation=='url':j['thesis'][0]['text']='https://fake.test'
    if mutation=='buy_missing':j.update(grade='A',action='BUY_REVIEW',strategy='trend_confirmation')
    if mutation=='missing_counter':j['counter_thesis']=[]
    if mutation=='hold_unknown':j['action']='HOLD'
    with pytest.raises(ValueError):e.validate(j,r,out,c.facts_for(r,context),portfolio,r['asof']+'T12:00:00Z')


def test_engine_validates_full_buy_but_does_not_generate_or_submit_order():
    r=full_row();context=context_facts();account=cash_account();out=a.assess(r,context,account);j=reply(r)
    j.update(grade='A',action='BUY_REVIEW',strategy='trend_confirmation')
    result=e.validate(j,r,out,c.facts_for(r,context),account,r['asof']+'T12:00:00Z')
    assert result['automatic_order'] is False
    with pytest.raises(ValueError,match='stale'):e.validate(j,r,out,c.facts_for(r,context),account,'2020-05-20T12:00:00Z')


def test_mock_call_budget_and_no_retry_on_failure():
    r=row();context=dict(facts=[]);account=p.review(None,{});out=a.assess(r,context,account)
    engine=e.JudgmentEngine(model='synthetic-model',max_calls=1,transport=lambda request:reply(r))
    assert engine.judge(r,out,context,account,r['asof']+'T12:00:00Z')['status']=='MOCK_VALIDATED'
    with pytest.raises(ValueError,match='budget'):engine.judge(r,out,context,account,r['asof']+'T12:00:00Z')
    bad=e.JudgmentEngine(model='synthetic-model',max_calls=1,transport=lambda request:(_ for _ in ()).throw(RuntimeError('private error body')))
    with pytest.raises(ValueError,match='no_automatic_retry'):bad.judge(r,out,context,account,r['asof']+'T12:00:00Z')
    assert bad.calls==1 and 'private' not in json.dumps(bad.journal)


def test_briefing_price_table_layout_escaping_and_last_section():
    r=full_row();r['stock_name']='<script>alert(1)</script>'
    context=context_facts();account=cash_account();out=a.assess(r,context,account)
    html=render(dict(dataset_id=r['dataset_id'],asof=r['asof'],source_manifest_sha256='a'*64,provider_finality='UNVERIFIED',stocks=[r]),{'000720':out},{},context,account,['000720'])
    assert 'position:sticky' in html and 'overflow-x:auto' in html and 'white-space:nowrap' in html
    assert 'overflow:hidden;text-overflow:ellipsis' in html and 'title=' in html
    assert html.count('<col style=')==len(PRICE_COLUMNS) and '<script>' not in html
    assert html.endswith('</tbody></table></div></section></body></html>')
    assert '추세 재평가·유지 시 보유' in html


def built_input(synthetic_source):
    source,out=synthetic_source
    result=evidence_build.build(source,out,'2020-04-02T00:00:00Z')
    return Path(result['directory']),result['validation']['manifest_sha256']


def test_offline_e2e_real_modules_model_record_briefing_all_formats_and_idempotence(synthetic_source,tmp_path):
    directory,anchor=built_input(synthetic_source)
    source=json.loads(gzip.decompress((directory/'market.json.gz').read_bytes()))
    original=c.digest_tree(directory)
    rr=source['stocks'][0]
    engine=e.JudgmentEngine(model='synthetic-model',transport=lambda request:batch_reply(rr))
    result=pipe.run(directory,anchor,tmp_path/'integrated',engine=engine)
    assert result['validation']['rows']==1 and result['quality']['OpenAI_live_HTTP']==0
    assert result['quality']['OpenAI_mock_requests']==1 and result['quality']['market_HTTP']==0
    target=Path(result['directory']);b=json.loads((target/'briefing.json').read_bytes())
    assert b['decision_records'][0]['status']=='SIMULATED_RECORD'
    assert c.digest_tree(directory)==original and pipe.verify_bundle(target,result['validation']['manifest_sha256'])['status']=='PASS'
    again=pipe.run(directory,anchor,tmp_path/'integrated',decision_at=json.loads((target/'manifest.json').read_bytes())['decision_at'],
                   engine=e.JudgmentEngine(model='synthetic-model',transport=lambda request:pytest.fail('idempotent reuse cannot call model')))
    assert again['idempotent']
    record_path=target/b['decision_records'][0]['path']
    payload=rec.read_record(record_path,sha(record_path.read_bytes()))
    assert payload['historical_backtest_eligible'] is False
    evaluated=rec.review_outcomes(payload,{}, {},{}, {},'2026-10-31T00:00:00Z')
    assert evaluated['reason']=='point_in_time_or_historical_eligibility_not_verified'


def test_offline_pipeline_missing_context_account_and_OpenAI_is_normal(synthetic_source,tmp_path):
    directory,anchor=built_input(synthetic_source)
    result=pipe.run(directory,anchor,tmp_path/'integrated')
    assert result['quality']['OpenAI_live_HTTP']==0 and result['quality']['context_status']=='NOT_RUN'
    assert result['quality']['complete_scorecards']==0


@pytest.mark.parametrize('kind',['anchor','hash','db_json'])
def test_pipeline_blocks_source_or_derived_corruption(synthetic_source,tmp_path,kind):
    directory,anchor=built_input(synthetic_source)
    if kind=='anchor':
        with pytest.raises(ValueError):pipe.run(directory,'0'*64,tmp_path/'out')
    else:
        result=pipe.run(directory,anchor,tmp_path/'out');target=Path(result['directory'])
        if kind=='hash':(target/'briefing.html').write_text('modified')
        else:
            with duckdb.connect(str(target/'investment.duckdb')) as db:db.execute("update assessments set payload='{}'")
            m=json.loads((target/'manifest.json').read_bytes());m['files']['investment.duckdb']=sha((target/'investment.duckdb').read_bytes());(target/'manifest.json').write_bytes(encoded(m))
        with pytest.raises((ValueError,KeyError)):pipe.verify_bundle(target)


def test_pipeline_rejects_past_decision_and_stops_model_after_first_error(synthetic_source,tmp_path):
    directory,anchor=built_input(synthetic_source)
    with pytest.raises(ValueError,match='precedes'):pipe.run(directory,anchor,tmp_path/'out',decision_at='2020-01-01T00:00:00Z')
    engine=e.JudgmentEngine(model='synthetic-model',transport=lambda request:{'bad':'response'})
    result=pipe.run(directory,anchor,tmp_path/'out',engine=engine)
    assert result['status']=='GPT_REJECTED_WITH_DIAGNOSTICS' and result['quality']['engine_failed']
    assert engine.calls==1


def test_sma120_is_computed_only_when_sufficient_verified_history_exists():
    r=row(140)
    assert c.value(r,'price_trend_metrics','sma_120') is not None
    assert c.value(r,'price_trend_metrics','atr_14') is not None


def test_context_preserves_conflicting_versions_without_selecting_one(bundle):
    path,d=bundle
    duplicate={**d['items'][0],'id':'second-version'}
    d['items'].append(duplicate);path.write_bytes(encoded(d))
    out=ctx.load_bundle(path,d['asof'],'2020-05-19T08:00:00Z')
    assert not out['facts'] and len(out['held'])==2


def test_company_event_requires_real_source_fact_and_scheduled_event_is_distinct(bundle):
    path,d=bundle
    d['items'][0].update(kind='event',event_type='contract_cancellation',occurred_on='2021-01-01')
    path.write_bytes(encoded(d))
    with pytest.raises(ValueError,match='future_event'):ctx.load_bundle(path,d['asof'],'2020-05-19T08:00:00Z')
    d['items'][0]['event_type']='scheduled_event';path.write_bytes(encoded(d))
    assert ctx.load_bundle(path,d['asof'],'2020-05-19T08:00:00Z')['facts']


def test_empty_financials_and_different_scopes_do_not_invent_ratios():
    out=ctx.financial_metrics(dict(facts=[]),'000720')
    assert all(v['value'] is None for v in out.values())
    data=context_facts();data['facts'][2]['currency']='USD'
    assert ctx.financial_metrics(data,'000720')['operating_margin_ratio']['value'] is None


def test_portfolio_sell_unowned_negative_cash_and_later_reentry_keeps_closed_history(tmp_path):
    ledger=p.PortfolioLedger(tmp_path/'account')
    with pytest.raises(ValueError):ledger.append(fill('sell','SELL'))
    with pytest.raises(ValueError):ledger.append(fill('buy','BUY'))
    ledger.append(event('cash','DEPOSIT',amount_krw='10000'));ledger.append(fill('buy','BUY'))
    defense=event('d','DEFENSE',stock_code='000720',price_krw='90',reason='structure',evidence_ids=['p'])
    ledger.append(defense)
    ledger.append(event('hit','DEFENSE_TRIGGER',stock_code='000720',observed_price_krw='89',origin='VERIFIED_OBSERVATION',evidence_ids=['p']))
    ledger.append(fill('sell','SELL',price='89'))
    ledger.append(fill('reentry','BUY',price='95'))
    ledger.append({**defense,'id':'newcycle','price_krw':'88'})
    position=ledger.snapshot('2020-01-02T00:00:00Z')['holdings']['000720']
    assert position['lifecycle']==2 and position['closed_lifecycles'][0]['defense_triggered']
    assert len(position['trade_history'])==3 and not position['defense_triggered']


def test_missing_mark_does_not_create_zero_valuation_or_zero_risk(tmp_path):
    ledger=p.PortfolioLedger(tmp_path/'account');ledger.append(event('cash','DEPOSIT',amount_krw='10000'));ledger.append(fill('buy','BUY'))
    out=p.review(ledger.snapshot('2020-01-02T00:00:00Z'),{})
    assert out['assets_krw'] is None and out['total_planned_risk_ratio'] is None
    assert out['holdings']['000720']['market_value_krw'] is None


def test_maximum_position_and_weight_constraints_and_cash_in_asset_denominator(tmp_path):
    ledger=p.PortfolioLedger(tmp_path/'account');ledger.append(event('cash','DEPOSIT',amount_krw='10000'));ledger.append(fill('buy','BUY',quantity=50))
    snap=ledger.snapshot('2020-01-02T00:00:00Z');r=full_row();out=p.review(snap,{'000720':r})
    assert out['assets_krw']=='10000' and out['holdings']['000720']['weight_ratio']=='0.5'
    assert any(c['reason']=='MAXIMUM_40_PERCENT_EXCEEDED' for c in out['constraints'])
    r['data_quality']['latest_price_verified']=False
    assert p.review(snap,{'000720':r})['assets_krw'] is None


def test_profit_observation_is_not_exit_and_triggered_defense_cannot_be_cancelled():
    r=full_row();account=cash_account()
    account['holdings']['000720']=dict(defense=None,defense_breached=False,unrealized_pnl_krw='100')
    context=context_facts();out=a.assess(r,context,account);j=reply(r);j['action']='PARTIAL_PROFIT'
    with pytest.raises(ValueError,match='not_take_profit'):e.validate(j,r,out,c.facts_for(r,context),account,r['asof']+'T12:00:00Z')
    r['price_trend_metrics']['sma_20_slope_1d']['value']=-1
    assert e.validate(j,r,out,c.facts_for(r,context),account,r['asof']+'T12:00:00Z')['action']=='PARTIAL_PROFIT'
    account['holdings']['000720']['defense_breached']=True;j['action']='HOLD'
    with pytest.raises(ValueError,match='cannot_cancel'):e.validate(j,r,out,c.facts_for(r,context),account,r['asof']+'T12:00:00Z')


def test_engine_missing_key_does_not_attempt_HTTP():
    r=row();account=p.review(None,{});context=dict(facts=[])
    engine=e.JudgmentEngine(model='synthetic',allow_live=True,key_provider=lambda:None)
    assert engine.judge(r,a.assess(r,context,account),context,account,r['asof']+'T12:00:00Z')['status']=='NOT_RUN'
    assert engine.calls==0


def test_grounded_holding_comparisons_include_actual_confirmed_position_fact():
    r=full_row();account=cash_account();account['holdings']['000720']=dict(defense=None,defense_breached=False,mark_krw='100')
    context=context_facts();out=a.assess(r,context,account)
    request,facts,_=e.request_for(r,out,context,account,r['asof']+'T12:00:00Z','synthetic')
    j=reply(r);fid='portfolio.000720.mark_krw'
    j['claims'].append(dict(fact_id=fid,value='100',unit='KRW'))
    j['holding_comparisons']=[dict(stock_code='000720',relative_attractiveness='uncertain',fact_ids=[fid])]
    assert e.validate(j,r,out,facts,account,r['asof']+'T12:00:00Z')['holding_comparisons']
    assert request['store'] is False and 'memories' not in request


def test_outcomes_all_horizons_original_defense_and_unknown_later_events():
    payload,calendar,prices,index,flows=judgment_fixture()
    payload['judgment']=json.dumps({'strategy':'trend_confirmation'})
    payload['evidence']={'investment_assessment':{'strategies':{'trend_confirmation':{'defense':104}}}}
    trades=[fill('entry','BUY',price='103'),fill('add','BUY',price='105',intent='ADD'),fill('exit','SELL',price='108',intent='PARTIAL_PROFIT')]
    for t in trades:t['at']='2020-01-03T10:00:00Z'
    later=dict(facts=[dict(id='later-news',kind='event',stock_code='000720',event_type='news',published_at='2020-01-03T00:00:00Z',observed_at='2020-01-03T01:00:00Z')])
    before=copy.deepcopy(payload)
    result=rec.review_outcomes(payload,calendar,prices,index,flows,'2020-02-01T00:00:00Z',user_trades=trades,subsequent_context=later)
    assert set(result['outcomes'])=={'5','10','20'}
    assert result['outcomes']['5']['fixed_original_defense_close_breach']['triggered']
    assert result['review_axes']['additional_buy']['status'].startswith('OBSERVED')
    assert result['review_axes']['exit_timing']['status'].startswith('OBSERVED')
    assert result['review_axes']['previously_unknown_events']['events'][0]['id']=='later-news'
    assert payload==before and result['historical_record_modified'] is False


@pytest.mark.parametrize('failure',['future_cutoff','unconfirmed_fill','missing_price'])
def test_outcome_guards_never_override_original_verification(failure):
    payload,calendar,prices,index,flows=judgment_fixture();payload['evidence']={}
    cutoff='2020-02-01T00:00:00Z';trades=[]
    if failure=='future_cutoff':cutoff='2099-01-01T00:00:00Z'
    if failure=='unconfirmed_fill':trades=[{**fill('x','BUY'),'user_confirmed':False}]
    if failure=='missing_price':prices.pop(calendar['dates'][2])
    if failure!='missing_price':
        with pytest.raises(ValueError):rec.review_outcomes(payload,calendar,prices,index,flows,cutoff,user_trades=trades)
    else:assert rec.review_outcomes(payload,calendar,prices,index,flows,cutoff)['outcomes']['5']['status']=='unavailable'


def test_full_e2e_with_context_portfolio_and_saved_guarded_outcome(synthetic_source,tmp_path):
    directory,anchor=built_input(synthetic_source)
    source=json.loads(gzip.decompress((directory/'market.json.gz').read_bytes()));r=source['stocks'][0]
    context_root=tmp_path/'context-input';context_root.mkdir();facts=context_facts()['facts']
    originals={f['id']:f['value'] for f in facts}
    original=context_root/'original.json';original.write_bytes(encoded(originals))
    when='2020-01-01T00:00:00Z';items=[]
    for f in facts:
        item={**f,'source_id':'local','pointer':[f['id']],'published_at':when,'observed_at':when,'validation_status':'verified'}
        if item['kind']=='event':item.update(event_type='news',occurred_on='2020-01-01')
        items.append(item)
    bundle=context_root/'context.json';bundle.write_bytes(encoded(dict(schema_version='investment-context-v1',asof=r['asof'],generated_at=when,
        sources={'local':dict(file='original.json',sha256=sha(original.read_bytes()),published_at=when,observed_at=when,validation_status='verified')},items=items)))
    account=p.PortfolioLedger(tmp_path/'account');account.append(event('cash','DEPOSIT',amount_krw='100000'));account.append(fill('buy','BUY'))
    result=pipe.run(directory,anchor,tmp_path/'out',context_path=bundle,portfolio_directory=account.root,
                    engine=e.JudgmentEngine(model='synthetic',transport=lambda request:batch_reply(r)))
    target=Path(result['directory']);brief=json.loads((target/'briefing.json').read_bytes())
    assert brief['portfolio']['status']=='VALUED' and brief['context_status']=='LOCAL_VERIFIED'
    record_path=target/brief['decision_records'][0]['path'];record_hash=sha(record_path.read_bytes())
    future=tmp_path/'future.json';future.write_bytes(encoded(dict(schema_version='investment-outcome-input-v1',stock_code=r['stock_code'],asof=r['asof'],
        record_sha256=record_hash,evidence_dataset_id=r['dataset_id'],evidence_sha256=json.loads(record_path.read_bytes())['evidence_sha256'],
        calendar={},prices={},market={},foreign_flows={},cutoff='2026-10-31T00:00:00Z')))
    # This current record was generated now, so use a current evaluation cutoff.
    doc=json.loads(future.read_bytes());doc['cutoff']=dt.datetime.now(dt.timezone.utc).isoformat();future.write_bytes(encoded(doc))
    evaluated=rec.evaluate_saved_record(record_path,record_hash,future,sha(future.read_bytes()),tmp_path/'outcomes')
    assert evaluated['status']=='unavailable' and sha(record_path.read_bytes())==record_hash
    assert pipe.verify_bundle(target,result['validation']['manifest_sha256'])['status']=='PASS'


@pytest.mark.parametrize('code',['0120G0','0008Z0','000720'])
def test_official_master_alphanumeric_symbols_preserved_in_consumer_and_record(code,tmp_path):
    r=row();r['stock_code']=code
    assert c.symbol(code)==code
    result=dict(status='MOCK_VALIDATED',judgment=reply(r))
    saved=rec.record_judgment(r,{},result,r['asof']+'T12:00:00Z',tmp_path/'decisions',p.review(None,{}))
    assert json.loads(Path(saved['path']).read_bytes())['stock_code']==code
    assert not saved['historical_backtest_eligible']


@pytest.mark.parametrize('code',['../123','abc123','1234567','12345','00072/'])
def test_invalid_stock_identifiers_rejected_without_changing_universe(code):
    with pytest.raises(ValueError):c.symbol(code)


def test_GPT_batch_selection_is_explicit_global_order_not_first_four_scores():
    r=full_row();other=copy.deepcopy(r);other.update(stock_code='000721',stock_name='두번째 합성종목')
    context=context_facts();context['facts'] += [{**f,'id':'other_'+f['id'],'stock_code':'000721'} for f in list(context['facts']) if f.get('stock_code')=='000720']
    account=cash_account();rows=[r,other];assessments={x['stock_code']:a.assess(x,context,account) for x in rows}
    responses=[{**reply(x),'grade':'A','action':'BUY_REVIEW','strategy':'trend_confirmation'} for x in rows]
    batch=dict(dataset_id=r['dataset_id'],asof=r['asof'],judgments=responses,selected_stock_codes=['000721','000720'])
    engine=e.JudgmentEngine(model='synthetic',transport=lambda request:batch)
    result=engine.judge_batch(rows,assessments,context,account,r['asof']+'T12:00:00Z',['000720','000721'])
    assert result['selected_stock_codes']==['000721','000720'] and engine.calls==1


@pytest.mark.parametrize('bad',['unknown_selection','duplicate_selection','missing_stock','unqualified_selection','wrong_generation'])
def test_batch_rejects_unqualified_selection_and_population_mismatch(bad):
    r=row();context=dict(facts=[]);account=p.review(None,{});assessments={'000720':a.assess(r,context,account)};response=batch_reply(r)
    if bad=='unknown_selection':response['selected_stock_codes']=['999999']
    if bad=='duplicate_selection':response['selected_stock_codes']=['000720','000720']
    if bad=='missing_stock':response['judgments']=[]
    if bad=='unqualified_selection':response['selected_stock_codes']=['000720']
    if bad=='wrong_generation':response['dataset_id']='other'
    engine=e.JudgmentEngine(model='synthetic',transport=lambda request:response)
    with pytest.raises(ValueError,match='no_automatic_retry'):engine.judge_batch([r],assessments,context,account,r['asof']+'T12:00:00Z',['000720'])
    assert engine.calls==1


def test_opening_position_without_fill_history_does_not_fabricate_trade(tmp_path):
    ledger=p.PortfolioLedger(tmp_path/'account')
    ledger.append(event('opening','OPENING_BALANCE',cash_krw='10000',positions=[dict(stock_code='000720',stock_name='합성',market='KOSPI',quantity=10,average_cost_krw='100')]))
    snap=ledger.snapshot('2020-01-02T00:00:00Z');position=snap['holdings']['000720']
    assert position['average_cost_krw']=='100' and position['trades']==[]
    assert 'PRIOR_REALIZED_UNKNOWN' in snap['realized_pnl_scope']
    with pytest.raises(ValueError,match='first_event'):ledger.append(event('second','OPENING_BALANCE',cash_krw='20000',positions=[]))


def test_all_sell_hold_types_have_applicable_and_missing_conditions():
    r=full_row();account=cash_account();context=context_facts()
    nohold=a.assess(r,context,account)
    assert set(nohold['sell_hold_policies'])=={'hold','partial_profit','reduce','final_defense','thesis_invalidated'}
    assert all(v['status']=='INAPPLICABLE' for v in nohold['sell_hold_policies'].values())
    account['holdings']['000720']=dict(defense=None,defense_breached=False,unrealized_pnl_krw='100',weight_ratio='.2')
    held=a.assess(r,context,account)
    assert held['sell_hold_policies']['hold']['status']=='REVIEW_APPLICABLE'
    assert held['sell_hold_policies']['partial_profit']['status']=='INAPPLICABLE'
    account['holdings']['000720']['defense_breached']=True
    assert a.assess(r,context,account)['sell_hold_policies']['final_defense']['status']=='REVIEW_APPLICABLE'


def test_external_portfolio_head_anchor_detects_tail_deletion_but_allows_future_append(tmp_path):
    ledger=p.PortfolioLedger(tmp_path/'account');first=ledger.append(event('cash','DEPOSIT',amount_krw='10000'))
    original=ledger.snapshot('2020-01-01T11:00:00Z',first['hash'])
    later={**fill('buy','BUY'),'at':'2020-01-02T10:00:00Z'};last=ledger.append(later)
    assert ledger.snapshot('2020-01-01T11:00:00Z',first['hash'])==original
    (ledger.root/f'00000001-{last["hash"]}.json').unlink()
    with pytest.raises(ValueError,match='history_missing'):ledger.snapshot('2020-01-03T00:00:00Z',last['hash'])


@pytest.mark.parametrize('code',[401,429,500])
def test_live_HTTP_adapter_auth_rate_limit_and_server_error_have_no_retry_or_secret_echo(monkeypatch,code):
    import urllib.error
    attempts=[]
    class FakeOpener:
        def open(self,request,timeout):
            attempts.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url,code,'synthetic private response',{},None)
    monkeypatch.setattr(e.urllib.request,'build_opener',lambda *args:FakeOpener())
    r=row();context=dict(facts=[]);account=p.review(None,{})
    engine=e.JudgmentEngine(model='synthetic',allow_live=True,key_provider=lambda:'synthetic-unit-test-key')
    with pytest.raises(ValueError,match='no_automatic_retry') as error:
        engine.judge_batch([r],{'000720':a.assess(r,context,account)},context,account,r['asof']+'T12:00:00Z',['000720'])
    assert attempts==['https://api.openai.com/v1/responses'] and engine.calls==1
    assert 'private' not in str(error.value) and 'synthetic-unit-test-key' not in json.dumps(engine.journal)


def test_live_adapter_complete_response_is_structured_and_redirects_are_blocked(monkeypatch):
    r=row();response=batch_reply(r)
    class FakeResponse:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return encoded(dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(response))])]))
    class FakeOpener:
        def open(self,request,timeout):
            assert request.full_url=='https://api.openai.com/v1/responses' and timeout==60
            return FakeResponse()
    monkeypatch.setattr(e.urllib.request,'build_opener',lambda *args:FakeOpener())
    assert e.live_transport({'model':'synthetic'},'synthetic-unit-test-key')==response
    assert e.NoRedirect().redirect_request(None,None,302,'',{},'https://other.test') is None


def test_financial_growth_finds_matching_year_period_instead_of_adjacent_quarter():
    context=context_facts()
    context['facts'].append(dict(context['facts'][1],id='interleaved-quarter',value=30,period_start='2019-07-01',period_end='2019-09-30'))
    assert Fraction(ctx.financial_metrics(context,'000720')['revenue_growth_pct']['value'])==20


def test_GPT_does_not_invalidate_a_thesis_the_user_never_provided():
    r=full_row();account=cash_account();account['holdings']['000720']=dict(defense=None,defense_breached=False)
    context=context_facts();out=a.assess(r,context,account);j=reply(r);j['action']='THESIS_INVALIDATED'
    with pytest.raises(ValueError,match='unknown_thesis'):e.validate(j,r,out,c.facts_for(r,context),account,r['asof']+'T12:00:00Z')


def test_independent_saved_market_index_does_not_require_unverified_stock_price():
    r=row();r['data_quality']['latest_price_verified']=False;r['price_trend_metrics']['close']['value']=None
    facts=c.facts_for(r,dict(facts=[]))
    assert 'relative_strength_metrics.market_return_5d_pct' in facts
    assert 'relative_strength_metrics.stock_return_5d_pct' not in facts
    account=p.review(None,{});context=dict(status='NOT_RUN',facts=[])
    html=render(dict(dataset_id=r['dataset_id'],asof=r['asof'],source_manifest_sha256='a'*64,provider_finality='UNVERIFIED',stocks=[r]),
                {'000720':a.assess(r,context,account)},{},context,account,[])
    assert '국내 지수 KOSPI' in html and '공식 최종 확정으로 표시하지 않습니다' in html


def test_decision_record_retains_exact_context_used_by_model(tmp_path):
    r=row();context=context_facts();account=p.review(None,{})
    result=dict(status='MOCK_VALIDATED',judgment=reply(r))
    saved=rec.record_judgment(r,a.assess(r,context,account),result,r['asof']+'T12:00:00Z',tmp_path/'decisions',account,context)
    payload=json.loads(Path(saved['path']).read_bytes())
    assert payload['evidence']['verified_context_at_decision']==context
    assert sha(encoded(payload['evidence']))==payload['evidence_sha256']


def test_briefing_displays_GPT_selection_order_holdings_judgment_and_cited_values():
    r=full_row();context=context_facts();account=cash_account()
    account['holdings']['000720']=dict(stock_name='합성종목',quantity=10,average_cost_krw='90',defense=None,defense_breached=False)
    assessment=a.assess(r,context,account);judgment=reply(r);judgment['action']='HOLD'
    result=dict(status='MOCK_VALIDATED',judgment=judgment)
    dataset=dict(dataset_id=r['dataset_id'],asof=r['asof'],source_manifest_sha256='a'*64,provider_finality='UNVERIFIED',stocks=[r])
    html=render(dataset,{'000720':assessment},{'000720':result},context,account,['000720'],['000720'])
    assert 'GPT 우선 검토 순서: 합성종목 (000720)' in html
    assert 'GPT 보유 판단 MOCK_VALIDATED · HOLD' in html and 'foreign_flow_metrics.net_5d' in html
    assert html.endswith('</tbody></table></div></section></body></html>')
