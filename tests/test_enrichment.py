"""Test-only fixtures never enter the production snapshot."""
import copy
import pandas as pd
import pytest
from test_dashboard import history
from screener.financials import select_account,parse_full_response,number
from screener.scoring import evaluate,_financial
from screener.themes import aggregate,members

def account(code,name,value,statement='CF',detail='-'):
    return dict(account_id=code,account_nm=name,thstrm_amount=value,frmtrm_amount='12',sj_div=statement,account_detail=detail)

def test_operating_cashflow_code_wins_over_similar_name():
    rows=[account('dart_NetCashflowsFromUsedInOperations','영업활동으로 창출된 현금','900'),
          account('ifrs-full_CashFlowsFromUsedInOperatingActivities','영업활동순현금흐름','(120)')]
    result=select_account(rows,'ocf')
    assert result['value']==-120 and result['method']=='standard_account_id'

def test_custom_cf_alias_and_missing_not_zero():
    assert select_account([account('custom','Ⅰ. 영업활동으로 인한 현금흐름','0')],'ocf')['value']==0
    assert select_account([account('custom','영업활동으로 창출된 현금','10')],'ocf')['value'] is None
    assert select_account([account('custom','영업활동 현금흐름','-')],'ocf')['value'] is None
    assert number('NaN') is None and number('1,000')==1000

def test_ambiguous_cf_and_segment_are_not_added_together():
    rows=[account('ifrs-full_CashFlowsFromUsedInOperatingActivities','영업활동현금흐름','100'),
          account('ifrs-full_CashFlowsFromUsedInOperatingActivities','영업활동현금흐름','200')]
    assert select_account(rows,'ocf')['ambiguous']
    assert select_account(rows,'ocf')['value'] is None
    rows[1]['account_detail']='사업부문'
    assert select_account(rows,'ocf')['value']==100

def test_comprehensive_income_cannot_become_parent_net_profit():
    row=account('ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent','지배기업소유주지분','999',statement='CIS')
    assert select_account([row],'parent_net_income')['value'] is None
    custom=account('custom','지배기업의 소유주에게 귀속되는 당기순이익','100',statement='IS')
    assert select_account([row,custom],'parent_net_income')['value']==100

@pytest.mark.parametrize('change',[{'scope':'OFS'},{'requested_receipt':'other'}])
def test_cfs_ofs_and_revision_are_never_blended(change):
    payload={'scope':'CFS','requested_receipt':'receipt','response':{'status':'000','list':[]},**change}
    with pytest.raises(ValueError,match='scope'):parse_full_response(payload,'receipt','CFS')

def test_currency_and_report_identity_are_required():
    row=account('ifrs-full_CashFlowsFromUsedInOperatingActivities','영업활동현금흐름','100')
    row.update(currency='USD',rcept_no='receipt',bsns_year='2025',reprt_code='11011')
    payload={'scope':'CFS','requested_receipt':'receipt','response':{'status':'000','list':[row]}}
    with pytest.raises(ValueError,match='currency'):parse_full_response(payload,'receipt','CFS')
    row['currency']='KRW';row['rcept_no']='different'
    with pytest.raises(ValueError,match='identity'):parse_full_response(payload,'receipt','CFS')

def test_reviewed_cf_override_is_bound_to_exact_filing():
    row=account('ifrs-full_OtherInflowsOutflowsOfCashClassifiedAsOperatingActivities','영업활동순현금흐름','-1908386327')
    row.update(currency='KRW',rcept_no='20260318001099',bsns_year='2025',reprt_code='11011')
    payload={'symbol':'294630','scope':'OFS','requested_receipt':row['rcept_no'],'response':{'status':'000','list':[row]}}
    assert parse_full_response(payload,row['rcept_no'],'OFS')['ocf']['value']==-1908386327
    row['thstrm_amount']='1'
    with pytest.raises(ValueError,match='override_changed'):parse_full_response(payload,row['rcept_no'],'OFS')

def test_missing_revenue_does_not_hide_independent_ratios(history):
    row=history['financials'].iloc[0].to_dict()|{'revenue':float('nan'),'market_cap':2000}
    f=_financial(row)
    assert f['roe']==pytest.approx(100/950)
    assert f['per']==20 and f['pbr']==2 and f['debt_ratio']==.5
    assert pd.isna(f['margin']) and '매출' in f['financial_hold']

def test_parent_basis_and_loss_are_not_replaced_by_total_profit(history):
    row=history['financials'].iloc[0].to_dict()|{'market_cap':2000,'parent_net_income':80,'parent_equity':800}
    assert _financial(row)['per']==25 and _financial(row)['pbr']==2.5
    row['parent_net_income']=-10
    assert pd.isna(_financial(row)['per'])
    assert '지배주주' in _financial(row)['financial_hold']

def test_sector_flow_needs_20_flow_days_not_25(history):
    dates=history['calendar'].trade_date.iloc[-20:]
    history['flows']=history['flows'][history['flows'].trade_date.isin(dates)]
    stocks,sectors,_=evaluate(history)
    assert sectors.score_foreign_intensity.notna().all()
    assert sectors.score_foreign_persistence.notna().all()
    assert sectors.score_activity.notna().all()
    assert (stocks.flow_observations==20).all()

def test_sector_activity_does_not_require_flow_history(history):
    history['flows']=history['flows'][history['flows'].trade_date==history['calendar'].trade_date.max()]
    _,sectors,_=evaluate(history)
    assert sectors.score_activity.notna().all()
    assert sectors.score_foreign_intensity.isna().all()
    assert sectors.total_score.isna().all()

def test_theme_overlap_union_and_unconfirmed_exclusion():
    stocks=pd.DataFrame([{'symbol':'000001','net_amount':100},{'symbol':'000002','net_amount':200}])
    base={'status':'confirmed','valid_from':'2026-10-14','valid_to':None,'disclosed_on':'2026-03-01','evidence_url':'https://example.invalid/test-only','excerpt':'TEST ONLY'}
    mapping={'themes':[{'name':'A'},{'name':'B'}],'memberships':[
        base|{'symbol':'000001','theme':'A'},base|{'symbol':'000001','theme':'A'},
        base|{'symbol':'000001','theme':'B'},base|{'symbol':'000002','theme':'B','status':'needs_review'}]}
    table,_,stats=aggregate(stocks,'2026-10-14',mapping)
    assert table.net_amount.tolist()==[100,100]
    assert stats['unique_net_amount']==100 and stats['unique_members']==1 and stats['overlap_count']==1
    assert members('2026-10-13',mapping).empty

def test_live_theme_selector_preserves_official_view():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app_legacy.py'),default_timeout=120).run()
    assert not app.exception
    next(w for w in app.radio if w.label=='분류체계').set_value('투자 테마');app.run()
    assert not app.exception
    next(w for w in app.selectbox if w.label=='테마 선택').select('원전');app.run()
    assert not app.exception
    assert '034020' in next(w for w in app.selectbox if w.label=='종목 상세정보').options[0] or any('두산에너빌리티' in str(v) for v in next(w for w in app.selectbox if w.label=='종목 상세정보').options)
    next(w for w in app.radio if w.label=='분류체계').set_value('공식 업종');app.run()
    assert not app.exception
    assert any(w.label=='섹터 선택' for w in app.selectbox)

@pytest.mark.parametrize('timestamp',['2026-10-14T05:00:00Z','2026-10-15T00:00:00Z',None])
def test_latest_quote_cannot_be_assigned_to_an_unverified_day(timestamp):
    from screener.market_quality import quote_cap
    quote={'lstn_stcn':'10000','hts_avls':'10','stck_prpr':'100000','acml_vol':'10'}
    cap,reasons=quote_cap(quote,{'close':100000,'volume':10},'10','2026-10-14',timestamp)
    assert cap is None and reasons


def test_cap_units_and_alignment_require_all_checks():
    from screener.market_quality import quote_cap
    q={'lstn_stcn':'10000','hts_avls':'10','stck_prpr':'100000','acml_vol':'10'}
    cap,reasons=quote_cap(q,{'close':100000,'volume':10},'10','2026-10-14','2026-10-14T15:08:44Z')
    assert cap==1000000000 and not reasons
    cap,reasons=quote_cap(q,{'close':100000,'volume':11},'10','2026-10-14','2026-10-14T15:08:44Z')
    assert cap is None and reasons==['volume_mismatch']


def test_duplicate_history_is_rejected_instead_of_counted_twice(history):
    history['flows']=pd.concat([history['flows'],history['flows'].iloc[[0]]],ignore_index=True)
    with pytest.raises(ValueError,match='Duplicate'):evaluate(history)


def test_parent_identity_requires_both_explicit_components():
    rows=[account('ifrs-full_ProfitLoss','당기순이익','100',statement='IS'),
          account('ifrs-full_ProfitLossAttributableToNoncontrollingInterests','비지배지분순이익','20',statement='IS'),
          account('ifrs-full_Equity','자본총계','1000',statement='BS'),
          account('ifrs-full_NoncontrollingInterests','비지배지분','200',statement='BS')]
    for r in rows:r.update(currency='KRW',rcept_no='receipt',bsns_year='2025',reprt_code='11011')
    payload={'scope':'CFS','requested_receipt':'receipt','response':{'status':'000','list':rows}}
    result=parse_full_response(payload,'receipt','CFS')
    assert result['parent_net_income']['value']==80 and result['parent_equity']['value']==800
    assert result['parent_equity']['components']=={'total':1000,'noncontrolling':200}
    payload['response']['list']=[r for r in rows if 'Noncontrolling' not in r['account_id']]
    result=parse_full_response(payload,'receipt','CFS')
    assert result['parent_net_income']['value'] is None and result['parent_equity']['value'] is None


def test_bare_parent_label_in_comprehensive_statement_is_ambiguous():
    rows=[account('custom','지배기업소유주지분','100',statement='CIS')]
    assert select_account(rows,'parent_net_income')['value'] is None
    assert select_account([account('custom','지배기업의 소유주 귀속 당기순이익','100',statement='CIS')],'parent_net_income')['value']==100


def test_total_profit_can_be_recovered_only_from_two_explicit_interests():
    rows=[account('ifrs-full_ProfitLossAttributableToOwnersOfParent','지배기업소유주지분','80',statement='CIS'),
          account('ifrs-full_ProfitLossAttributableToNoncontrollingInterests','비지배지분','20',statement='CIS')]
    for r in rows:r.update(currency='KRW',rcept_no='receipt',bsns_year='2025',reprt_code='11011')
    p={'scope':'CFS','requested_receipt':'receipt','response':{'status':'000','list':rows}}
    assert parse_full_response(p,'receipt','CFS')['net_income']['value']==100
    p['response']['list']=rows[:1]
    assert parse_full_response(p,'receipt','CFS')['net_income']['value'] is None
    assert select_account([account('ifrs-full_ProfitLossFromOperatingActivities','영업이익','100',statement='CIS')],'operating_profit')['value']==100


def test_legacy_import_preserves_enriched_rows_and_accepts_new_dates(tmp_path):
    from screener.store import connect,insert_frame
    with connect(tmp_path/'test-only.duckdb') as c:
        for field in ['market_cap_source','market_cap_quality','market_cap_observed_at']:
            c.execute(f'ALTER TABLE prices ADD COLUMN IF NOT EXISTS {field} VARCHAR')
        old=dict(symbol='000001',trade_date=pd.Timestamp('2026-10-14'),close=100,adjusted_close=None,turnover=200,volume=2,market_cap=None,source='TEST ONLY legacy',exchange='KRX')
        enriched=old|{'market_cap':10000,'market_cap_source':'TEST ONLY verified','market_cap_quality':'EXPLICIT_DATE','market_cap_observed_at':None}
        insert_frame(c,'prices',pd.DataFrame([enriched]))
        insert_frame(c,'prices',pd.DataFrame([old]),preserve_existing=True)
        assert c.execute('select market_cap,market_cap_source from prices').fetchone()==(10000,'TEST ONLY verified')
        insert_frame(c,'prices',pd.DataFrame([old|{'trade_date':pd.Timestamp('2026-10-15')}]),preserve_existing=True)
        assert c.execute("select market_cap from prices where trade_date='2026-10-15'").fetchone()==(None,)
        assert c.execute('select count(*) from prices').fetchone()[0]==2


def test_running_app_refreshes_an_old_imported_calculator(monkeypatch):
    import streamlit as st
    import screener.scoring as scoring
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    st.cache_data.clear()
    def stale(*args,**kwargs):
        raise AssertionError('previous deployment calculator was retained')
    monkeypatch.setattr(scoring,'evaluate',stale)
    monkeypatch.setattr(scoring,'_dashboard_loaded_version','old-deployment',raising=False)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app_legacy.py'),default_timeout=90).run()
    assert not app.exception
    # This verified financial-count fixture belongs to October 14, not the latest date.
    next(w for w in app.selectbox if w.label=='데이터 기준일').select('2026-10-14');app.run()
    assert not app.exception
    assert any(m.value=='2,415종목' for m in app.metric)


def test_historical_date_does_not_backdate_theme_membership():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app_legacy.py'),default_timeout=90).run()
    next(w for w in app.selectbox if w.label=='데이터 기준일').select('2026-10-13');app.run()
    next(w for w in app.radio if w.label=='분류체계').set_value('투자 테마');app.run()
    assert not app.exception
    assert any('테마 구성 이력이 없습니다' in w.value for w in app.info)
    assert not any(w.label=='테마 선택' for w in app.selectbox)
