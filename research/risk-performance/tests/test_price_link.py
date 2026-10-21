"""Synthetic fixtures remain in pytest temporary paths; never published as market data."""
import copy,datetime as dt
from pathlib import Path
import pytest
from price_link import link_history,classify_link,make_forward_bundle,six
from forward_system import FIELDS,KIS_FIELDS,KRX_FIELDS,Store,build_signal,evidence_digest

@pytest.fixture
def case():
    dates=[(dt.date(2026,7,1)+dt.timedelta(days=i)).isoformat() for i in range(40)]
    b=dict(zip(FIELDS,[100.,110.,90.,105.,10.,1000.]))
    old={d:dict(b) for d in dates[:-1]}
    rows=[{'stck_bsop_date':d.replace('-',''),**{k:str(b[f]) for f,k in zip(FIELDS,KIS_FIELDS)},'prtt_rate':'0.00','revl_issu_reas':''} for d in dates[1:]]
    record={'adjusted_prices':rows,'request_scope':{'market':'J','symbol':'001234','end':dates[-1].replace('-',''),'adjustment':'0'},'chart_header':{'stck_shrn_iscd':'001234'},'observed_at':'2026-08-10T01:00:00+00:00'}
    return dict(symbol='001234',market='KOSPI',day=dates[-1],listing='2000-01-01',old=old,record=record,sessions=dates,prior_reasons=[],raw_observations=[{'date':dates[-1],'values':b,'source':'test-only'}],official={},source_ids=[{'sha256':'test-only'}],known_at='2026-08-10T01:00:00+00:00')

def test_contiguous_join_preserves_old_and_adds_one_day(case):
    original=copy.deepcopy(case);r=link_history(**case)
    assert r['status']=='linked_history_validated' and len(r['bars'])==40 and r['overlap_days']==38
    assert case==original and not r['prospective_signal'] and r['provider_finality']=='UNVERIFIED'

@pytest.mark.parametrize('field',FIELDS)
def test_overlap_six_fields_never_overwrite(case,field):
    case['old'][case['sessions'][3]][field]+=1
    r=link_history(**case)
    assert r['status']=='hold' and 'adjusted_overlap_conflict' in r['reasons']
    assert r['bars'][3][field]==case['old'][case['sessions'][3]][field]

def test_gap_not_filled_even_with_many_rows(case):
    d=case['sessions'][15];del case['old'][d]
    case['record']['adjusted_prices']=[r for r in case['record']['adjusted_prices'] if r['stck_bsop_date']!=d.replace('-','')]
    r=link_history(**case)
    assert r['missing_dates']==[d] and 'history_short_40' in r['reasons']

def test_missing_old_stock_does_not_crash(case):
    case['old']={};case['prior_reasons']=['previous_research_symbol_missing']
    r=link_history(**case)
    assert r['status']=='hold' and len(r['bars'])==39

def test_adjustment_flag_and_new_listing_hold(case):
    case['record']['adjusted_prices'][-1]['prtt_rate']='50.00'
    case['listing']=case['sessions'][10]
    r=link_history(**case)
    assert 'new_corporate_action_flag_unverified' in r['reasons'] and 'listing_short_40' in r['reasons']

def test_only_exact_date_official_evidence_resolves_raw_difference(case):
    old=copy.deepcopy(case['raw_observations'][0]);old['values']['close']=104.;case['raw_observations'].append(old)
    r=link_history(**case);assert 'raw_adjusted_conflict_unresolved' in r['reasons']
    case['official'][case['sessions'][-2]]={six(case['raw_observations'][0]['values'])}
    assert link_history(**case)['status']=='hold'
    case['official'][case['day']]={six(case['raw_observations'][0]['values'])}
    r=link_history(**case);assert r['status']=='linked_history_validated' and len(r['official_resolved_observations'])==1
    # Mixed raw versions still cannot be promoted to an execution bundle.
    with pytest.raises(ValueError,match='raw_price_not_reconciled'):
        make_forward_bundle(r,{},[],[],{},'2026-08-10T01:00:00+00:00')

def test_quarantine_is_per_stock_not_global(case):
    r=link_history(**case,quarantined=True)
    assert 'oct16_37_outcome_quarantine' in r['reasons']
    assert link_history(**case)['status']=='linked_history_validated'

def test_unknown_and_condition_failure_are_distinct(case):
    r=link_history(**case);flows={d:(0,'KRX') for d in case['sessions'][-20:]}
    assert classify_link(r,flows)['status']=='condition_not_met'
    del flows[case['sessions'][-2]]
    assert classify_link(r,flows)['status']=='unclassifiable'

def test_missing_KRX_never_promotes_successful_link(case):
    r=link_history(**case)
    with pytest.raises(ValueError,match='legacy_link_requires_revalidation'):
        make_forward_bundle({**r,'schema':'research-linked-40-v1'},{},[],[],None,'2026-08-10T01:00:00+00:00')
    with pytest.raises(ValueError,match='official_KRX_evidence_missing'):
        make_forward_bundle(r,{},[],[],None,'2026-08-10T01:00:00+00:00')

def test_promotion_replays_existing_rules_then_expired_freeze_blocked(case):
    r=link_history(**case);dates=case['sessions'];b=r['bars'][-1]
    flows={d:(2_000_000 if i>=15 else 1_000_000,'KRX') for i,d in enumerate(dates[-20:])}
    raw=[{'stck_bsop_date':d.replace('-',''),'frgn_ntby_tr_pbmn':str(v[0]//1_000_000)} for d,v in flows.items()]
    official=[{'BAS_DD':case['day'].replace('-',''),'ISU_CD':'001234',**{k:str(b[f]) for f,k in zip(FIELDS,KRX_FIELDS)}}]
    ref={'url':'https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd='+case['day'].replace('-',''),'http':200,'observed_at':'2026-08-10T01:00:00+00:00','rows':official,'rows_sha256':evidence_digest(official)}
    bundle=make_forward_bundle(r,flows,raw,[{'observed_at':case['known_at'],'source_sha256':'test-only'}],ref,case['known_at'])
    assert bundle['finality']=='UNVERIFIED'
    raw[-1]['frgn_ntby_tr_pbmn']='0'
    with pytest.raises(ValueError,match='daily_acceptance_failed'):
        make_forward_bundle(r,flows,raw,[{'observed_at':case['known_at']}],ref,case['known_at'])
    raw[-1]['frgn_ntby_tr_pbmn']='2'
    next_day='2026-08-11'
    calendar={'holiday_response':{'endpoint':'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/chk-holiday','tr_id':'CTCA0903R','rt_cd':'0','observed_at':case['known_at'],'output':[{'bass_dt':'20260810','opnd_yn':'N','tr_day_yn':'N'},{'bass_dt':'20260811','opnd_yn':'Y','tr_day_yn':'Y'}]}}
    args=dict(publication={'asof':case['day'],'sha256':'test-only','dataset_id':'test-only','known_at':case['known_at']},price_rows={b['trade_date']:{**b,'adjusted_close':b['close']} for b in bundle['bars']},flow_rows=flows,sessions=dates,calendar=calendar)
    s=build_signal(bundle,now=dt.datetime.fromisoformat('2026-08-10T02:00:00+00:00'),**args)
    assert s['group']=='D' and s['next_session']==next_day
    with pytest.raises(ValueError,match='freeze_outside'):
        build_signal(bundle,now=dt.datetime.fromisoformat('2026-08-11T00:00:00+00:00'),**args)


def test_flow_source_verbatim_hash_and_time(tmp_path):
    import json
    from forward_system import sha
    from price_link import verify_flow_sources
    row={'stck_bsop_date':'20261020','frgn_ntby_tr_pbmn':'2'}
    p=tmp_path/'original.json';p.write_text(json.dumps({'exchange':'KRX','updated_at':'2026-10-20T12:00:00+00:00','records':{'001234':{'flow':row}}}))
    source={'file':'original.json','sha256':sha(p),'observed_at':'2026-10-20T12:00:00+00:00'}
    wrapper={'rows':[row],'sources':[source]};verify_flow_sources(wrapper,'001234',tmp_path)
    wrapper['rows']=[{**row,'frgn_ntby_tr_pbmn':'3'}]
    with pytest.raises(ValueError,match='row_not_in_original'):verify_flow_sources(wrapper,'001234',tmp_path)
    wrapper['rows']=[row];source['observed_at']='2026-10-20T11:00:00+00:00'
    with pytest.raises(ValueError,match='source_backdated'):verify_flow_sources(wrapper,'001234',tmp_path)


def test_original_prefix_preserves_real_confirmation_date():
    import csv
    from forward_system import load
    root=Path(__file__).resolve().parents[1]
    with (root/'signals.csv').open(encoding='utf-8-sig') as stream:
        source=next(r for r in csv.DictReader(stream) if r['record_kind']=='historical_partial_universe' and r['group']=='A')
    b=load(root/source['source']);bars=b['bars'];symbol=b['symbol'];day=b['asof']
    record={'request_scope':{'market':'J','symbol':symbol,'end':day.replace('-',''),'adjustment':'0'},'chart_header':{'stck_shrn_iscd':symbol},'observed_at':b['observed_at'],
      'adjusted_prices':[{'stck_bsop_date':r['trade_date'].replace('-',''),**{k:str(r[f]) for f,k in zip(FIELDS,KIS_FIELDS)}} for r in bars[-39:]]}
    link=link_history(symbol,b['market'],day,'2000-01-01',{r['trade_date']:{k:r[k] for k in FIELDS} for r in bars[:-1]},record,b['calendar'],[],[{'date':day,'values':{k:bars[-1][k] for k in FIELDS},'source':'test-readonly-real-bundle'}],{},[],b['observed_at'])
    assert link['status']=='linked_history_validated' and len(link['bars'])==len(bars)>40
    result=classify_link(link,{r['trade_date']:(r['net_amount'],'KRX') for r in b['flows']})
    assert result['group']=='A' and result['structure']['confirmed_date']=='2026-10-14'
