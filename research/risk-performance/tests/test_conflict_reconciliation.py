"""Original-source replay and exact-date research overlays, no provider calls."""
import copy
import json
from pathlib import Path
import pytest
from conflict_reconciliation import (flow_rows, add_evidence, select_flow_replay,
    verify_replay_sources, research_snapshot_view, official_rows)
from forward_system import FIELDS, KRX_FIELDS, sha
from screener.liquidity_quality import digest as evidence_digest

NOW='2026-10-21T04:00:00+00:00'


def history():
    return {'mode':'history','analysis_date':'20261014','exchange':'KRX','run_id':'test-only',
      'records':{'001234':{'symbol':'001234','received_at_utc':'2026-10-14T16:00:00+00:00',
         'metric':{'http':200,'code':'MCA00000'},
         'rows':[{'stck_bsop_date':'20261013','frgn_ntby_tr_pbmn':'0','stck_clpr':'100'}]}}}


def test_full_export_schema_replays_real_zero_without_filling_dates():
    x=history();raw,known,kind=flow_rows(x,'001234',NOW)
    assert raw==x['records']['001234']['rows'] and known=='2026-10-14T16:00:00+00:00'
    index={};add_evidence(index,'001234',x,{'file':'test-only'},NOW)
    replay=select_flow_replay(index,'001234',['2026-10-12','2026-10-13'],{'2026-10-12':(0,'KRX'),'2026-10-13':(0,'KRX')})
    assert len(replay['rows'])==1 and replay['status']=='hold'
    assert replay['errors']==[{'date':'2026-10-12','reason':'original_fields_missing'}]


@pytest.mark.parametrize('change,reason',[
    ('exchange','wrong_or_missing_exchange'),('http','history_identity_or_response_invalid'),
    ('date','history_request_date_mismatch'),('symbol','history_identity_or_response_invalid'),
    ('duplicate','duplicate_dates'),('contract','unknown_history_export_contract')])
def test_bad_archive_cannot_be_used(change,reason):
    x=history();r=x['records']['001234']
    if change=='exchange':x['exchange']='UN'
    if change=='http':r['metric']['http']=403
    if change=='date':r['rows'][0]['stck_bsop_date']='20261015'
    if change=='symbol':r['symbol']='009999'
    if change=='duplicate':r['rows']*=2
    if change=='contract':x['analysis_date']='20261015'
    rows,_,err=flow_rows(x,'001234',NOW)
    assert not rows and err==reason


def test_pilot_missing_timestamp_uses_capture_bound_not_asof():
    x=history();r=x['records']['001234'];del r['received_at_utc']
    r['history_metric']=r.pop('metric');r['history_request_date']='20261013';r['exchange']='KRX'
    _,known,kind=flow_rows(x,'001234',NOW)
    assert known==NOW and kind.endswith('timestamp_upper_bound')


def test_conflicting_amount_not_selected_just_because_it_matches_db():
    x=history();index={};add_evidence(index,'001234',x,{'file':'a'},NOW)
    x['records']['001234']['rows'][0]['frgn_ntby_tr_pbmn']='2'
    add_evidence(index,'001234',x,{'file':'b'},NOW)
    r=select_flow_replay(index,'001234',['2026-10-13'],{'2026-10-13':(0,'KRX')})
    assert r['errors'][0]['reason']=='original_amount_versions_conflict' and not r['rows']


def test_quantity_is_preferred_when_available_but_conflicts_are_held():
    x=history();index={};add_evidence(index,'001234',x,{'file':'a'},NOW)
    x['records']['001234']['rows'][0]['frgn_ntby_qty']='10'
    add_evidence(index,'001234',x,{'file':'b'},NOW)
    r=select_flow_replay(index,'001234',['2026-10-13'],{'2026-10-13':(0,'KRX')})
    assert r['rows'][0]['frgn_ntby_qty']=='10'
    x['records']['001234']['rows'][0]['frgn_ntby_qty']='11'
    add_evidence(index,'001234',x,{'file':'c'},NOW)
    r=select_flow_replay(index,'001234',['2026-10-13'],{'2026-10-13':(0,'KRX')})
    assert not r['rows'] and r['errors'][0]['reason']=='original_quantity_versions_conflict'


def test_source_hash_verbatim_fields_and_observation_time(tmp_path):
    p=tmp_path/'history.json';p.write_text(json.dumps(history()))
    index={};add_evidence(index,'001234',history(),{'file':p.name,'sha256':sha(p)},NOW)
    r=select_flow_replay(index,'001234',['2026-10-13'],{'2026-10-13':(0,'KRX')})
    verify_replay_sources(r,tmp_path)
    wrong=copy.deepcopy(r);wrong['rows'][0]['stck_clpr']='101'
    with pytest.raises(ValueError,match='row_not_in_original_source'):verify_replay_sources(wrong,tmp_path)
    wrong=copy.deepcopy(r);wrong['sources'][0]['observed_at']='2026-10-14T01:00:00+00:00'
    with pytest.raises(ValueError,match='time_mismatch'):verify_replay_sources(wrong,tmp_path)
    p.write_text('{}')
    with pytest.raises(ValueError,match='source_hash'):verify_replay_sources(r,tmp_path)


def test_archived_member_compressed_cache_and_hash(tmp_path):
    import gzip,hashlib,io,tarfile
    obj={'symbol':'001234','exchange':'KRX','observed_at':NOW,'daily_acceptance':{
        'flow_endpoint':'KIS FHPTJ04160001 / J / million KRW to KRW',
        'raw_flow_rows':history()['records']['001234']['rows']}}
    data=gzip.compress(json.dumps(obj).encode());p=tmp_path/'archive.tar.xz'
    with tarfile.open(p,'w:xz') as tf:
        info=tarfile.TarInfo('observations/001234.json.gz');info.size=len(data);tf.addfile(info,io.BytesIO(data))
    identity={'file':p.name,'sha256':sha(p),'member':'observations/001234.json.gz','member_sha256':hashlib.sha256(data).hexdigest()}
    index={};add_evidence(index,'001234',obj,identity,NOW)
    r=select_flow_replay(index,'001234',['2026-10-13'],{'2026-10-13':(0,'KRX')})
    cache={};verify_replay_sources(r,tmp_path,cache)
    assert isinstance(cache[(str(p),identity['member'])][1],bytes)
    r['sources'][0]['member_sha256']='wrong'
    with pytest.raises(ValueError,match='member_hash'):verify_replay_sources(r,tmp_path,cache)


def test_adapter_requires_complete_twenty_day_replay(tmp_path):
    from price_link import verify_flow_sources
    replay={'schema':'research-raw-flow-replay-v2','symbol':'001234','status':'hold','rows':[],'sources':[]}
    with pytest.raises(ValueError,match='incomplete_or_wrong_symbol'):
        verify_flow_sources(replay,'001234',tmp_path)


@pytest.fixture
def overlay():
    b=dict(zip(FIELDS,[100.,110.,90.,105.,12.,1200.]));day='2026-10-19'
    link={'schema':'research-linked-40-v2','symbol':'001234','market':'KOSPI','asof':'2026-10-20',
          'status':'linked_history_validated','bars':[{'trade_date':day,**b}]}
    snapshot={day:{'close':104.,'adjusted_close':105.,'volume':10.,'turnover':1000.}}
    cp={day:{'exchange':'KRX','trade_date':'20261019','records':{'001234':{'flow':{'stck_bsop_date':'20261019',
      'stck_clpr':'104','acml_vol':'10','acml_tr_pbmn':'1000'},'observed_at':'2026-10-19T10:00:00+00:00'}}}}
    rows=[{'ISU_CD':'001234','BAS_DD':'20261019','MKT_NM':'KOSPI',**dict(zip(KRX_FIELDS,b.values()))}]
    ref={'url':'https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20261019','http':200,
        'rows':rows,'rows_sha256':evidence_digest(rows),'observed_at':NOW}
    return dict(link=link,snapshot_prices=snapshot,checkpoint=cp,references={day:[ref]},snapshot_sha256='test-only')


def test_official_exact_six_fields_reconcile_volume_only_without_mutating_sources(overlay):
    before=copy.deepcopy(overlay);r=research_snapshot_view(**overlay)
    assert overlay==before and r['status']=='research_comparison_passed'
    assert r['prices']['2026-10-19']['volume']==12
    assert r['prices']['2026-10-19']['close']==104 # original raw close is not overwritten
    assert r['provider_finality']=='UNVERIFIED' and not r['prospective_signal_eligible']


@pytest.mark.parametrize('field',FIELDS)
def test_any_official_six_field_mismatch_keeps_hold(overlay,field):
    ref=overlay['references']['2026-10-19'][0]
    ref['rows'][0][KRX_FIELDS[FIELDS.index(field)]]+=1
    ref['rows_sha256']=evidence_digest(ref['rows'])
    r=research_snapshot_view(**overlay)
    assert r['status']=='hold' and r['prices']==overlay['snapshot_prices']


def test_no_official_or_only_latest_day_cannot_clear_historical_conflict(overlay):
    overlay['references']={'2026-10-20':overlay['references']['2026-10-19']}
    assert research_snapshot_view(**overlay)['status']=='hold'


def test_conflicting_official_versions_never_pick_matching_one(overlay):
    ref=copy.deepcopy(overlay['references']['2026-10-19'][0]);ref['rows'][0]['ACC_TRDVOL']+=1
    ref['rows_sha256']=evidence_digest(ref['rows']);overlay['references']['2026-10-19'].append(ref)
    assert research_snapshot_view(**overlay)['status']=='hold'


def test_adjusted_close_revision_or_latest_day_requires_full_review(overlay):
    overlay['snapshot_prices']['2026-10-19']['adjusted_close']=104
    assert research_snapshot_view(**overlay)['status']=='hold'
    overlay['snapshot_prices']['2026-10-19']['adjusted_close']=105
    overlay['link']['asof']='2026-10-19'
    assert research_snapshot_view(**overlay)['status']=='hold'


@pytest.mark.parametrize('change',['market','date','hash','duplicate','checkpoint'])
def test_wrong_proof_identity_never_passes(overlay,change):
    ref=overlay['references']['2026-10-19'][0]
    if change=='market':ref['rows'][0]['MKT_NM']='KOSDAQ'
    if change=='date':ref['rows'][0]['BAS_DD']='20261020'
    if change=='hash':ref['rows_sha256']='wrong'
    if change=='duplicate':ref['rows']*=2
    if change=='checkpoint':
        overlay['checkpoint']['2026-10-19']['exchange']='UN'
        assert research_snapshot_view(**overlay)['status']=='hold';return
    if change!='hash':ref['rows_sha256']=evidence_digest(ref['rows'])
    with pytest.raises(ValueError):research_snapshot_view(**overlay)


def test_real_missing_50_export_rows_and_BYC_official_match():
    import pandas as pd
    from forward_system import load,Store
    root=Path(__file__).resolve().parents[1];repo=root.parents[1];full=root.parent/'full-market'
    a=pd.read_csv(root/'price-link-study/all-stocks.csv',dtype={'symbol':str})
    targets=a[(a.link_status=='linked_history_validated')&a.snapshot_compatible&~a.original_flow_replay_ready]
    assert len(targets)==50
    history_source=load(repo/'staging/kis_history_20261014.json.gz')
    for s in targets.symbol:
        rows,_,_=flow_rows(history_source,s,NOW)
        assert len(rows)>=20
    row=a[a.symbol=='001460'].iloc[0];link=Store.read(root/'price-link-study/store/observations'/(row.link_sha256+'.json'))['data']
    candidate=next(b for b in link['bars'] if b['trade_date']=='2026-10-19')
    probe=load(full/'collection/official-reference-probe.json')
    ref=next(x for x in probe['records'] if x['market']=='KOSPI')
    assert sha(full/'collection'/ref['file'])==ref['sha256']
    official=next(r for r in load(full/'collection'/ref['file'])['response']['OutBlock_1'] if r['ISU_CD']=='001460')
    assert tuple(candidate[k] for k in FIELDS)==tuple(float(official[k]) for k in KRX_FIELDS)
