"""Synthetic tests stay in memory/tmp; production exporter only reads real data."""
import copy
import datetime as dt
import json
from pathlib import Path
import pytest
from screener.decision_support.indicators import flow_metrics,technical,observations
from screener.decision_support.evidence import institution,accept_chart,bar,KIS_FIELDS,encoded,sha


def fixture(n=150):
    days=[str(dt.date(2020,1,1)+dt.timedelta(days=i)) for i in range(n)]
    bars={d:{'open':100+i,'high':103+i,'low':99+i,'close':101+i,'volume':1000+i,'turnover':(101+i)*(1000+i)} for i,d in enumerate(days)}
    return days,bars


def test_sma_slope_and_true_range_independent():
    days,bars=fixture();r=technical(days,bars)
    for n in (5,20,60,120):
        expected=(bars[days[-n]]['close']+bars[days[-1]]['close'])/2
        assert r[f'sma_{n}']['value']==expected
        assert r[f'sma_{n}_slope_1d']['value']==1
    assert r['atr_14']['value']==4
    assert r['sma_alignment']['value']=='ascending'


def test_atr_wilder_uses_gaps_and_explicit_seed():
    days,bars=fixture(17);bars[days[15]]={'open':180,'high':190,'low':175,'close':185,'volume':10,'turnover':1800}
    r=technical(days,bars);tr=[]
    for before,after in zip(days,days[1:]):
        p,b=bars[before],bars[after]
        tr.append(max(b['high'],p['close'])-min(b['low'],p['close']))
    expected=sum(tr[:14])/14
    for x in tr[14:]:expected+=(x-expected)/14
    assert r['atr_14']['value']==pytest.approx(expected)
    assert r['atr_seed_start']['value']==days[0]


def test_ichimoku_cloud_uses_26_session_displacement():
    days,b=fixture();r=technical(days,b)
    def mid(dates):return (max(b[d]['high'] for d in dates)+min(b[d]['low'] for d in dates))/2
    assert r['ichimoku_current_span_b']['value']==mid(days[-78:-26])
    assert r['ichimoku_current_span_a']['value']==(mid(days[-35:-26])+mid(days[-52:-26]))/2
    assert r['ichimoku_projected_span_b']['value']==mid(days[-52:])
    assert r['ichimoku_chikou_value']['value']==b[days[-1]]['close']
    assert r['ichimoku_chikou_plot_date']['value']==days[-27]
    assert r['ichimoku_current_span_b']!=r['ichimoku_projected_span_b']


@pytest.mark.parametrize('n',[0,1,9,14,26,51,77,119])
def test_short_history_has_no_fabricated_sma120(n):
    days,b=fixture(n);r=technical(days,b)
    assert r['sma_120']['value'] is None
    if n<78:assert r['ichimoku_current_span_b']['value'] is None
    if n<15:assert r['atr_14']['value'] is None


def test_missing_bar_not_interpolated_and_newer_data_not_used():
    days,b=fixture();small=technical(days[:80],b)
    changed=copy.deepcopy(b)
    for d in days[80:]:changed[d]['high']=999999
    assert technical(days[:80],changed)==small
    del b[days[-3]]
    r=technical(days,b)
    assert r['sma_5']['value'] is None and r['atr_14']['value'] is None


def test_breakout_excludes_today_and_relative_strength_uses_aligned_dates():
    days,b=fixture(26);bench={d:100 for d in days}
    r=technical(days,b,bench)
    expected=(b[days[-1]]['close']/b[days[-6]]['close']-1)*100
    assert r['market_excess_5d_pp']['value']==pytest.approx(expected)
    assert r['prior_high_20']['value']==max(b[d]['high'] for d in days[-21:-1])
    del bench[days[-3]]
    assert technical(days,b,bench)['market_excess_5d_pp']['value'] is None


def test_flow_windows_nonoverlap_zero_and_missing():
    days,_=fixture(20);amounts=dict.fromkeys(days,0)
    for d in days[-5:]:amounts[d]=10
    for d in days[-10:-5]:amounts[d]=-4
    r=flow_metrics(days,amounts)
    assert r['net_5d']['value']==50 and r['previous_5d']['value']==-20 and r['change_5d']['value']==70
    assert r['positive_days_20d']['value']==5
    amounts[days[-1]]=0
    assert flow_metrics(days,amounts)['net_1d']['value']==0
    del amounts[days[-2]]
    assert flow_metrics(days,amounts)['net_5d']['value'] is None


def test_institution_identity_and_unknown_unit():
    raw={'orgn_ntby_qty':'-2','orgn_shnu_vol':'3','orgn_seln_vol':'5','orgn_ntby_tr_pbmn':'0','orgn_shnu_tr_pbmn':'1','orgn_seln_tr_pbmn':'1'}
    r=institution(raw)
    assert r['net_quantity']==-2 and r['net_amount_provider_units']==0 and r['net_amount_krw'] is None
    raw['orgn_ntby_qty']='0'
    assert institution(raw)['net_quantity'] is None


def test_price_six_fields_and_action_gate_not_just_close():
    days,b=fixture(1);d=days[0];raw={v:str(b[d][k]) for k,v in KIS_FIELDS.items()}
    raw.update(stck_bsop_date=d.replace('-',''),flng_cls_code='00',mod_yn='N',revl_issu_reas='',prtt_rate='0.00')
    refs={('005930',d,'KOSPI'):[{'value':b[d],'source_id':'official'}]}
    args=('005930',d,'KOSPI','2020-01-02T01:00:00+00:00',refs,'1990-01-01')
    assert accept_chart(raw,*args)['accepted']==b[d]
    raw['acml_vol']='99'
    assert 'KIS_KRX_six_field_conflict' in accept_chart(raw,*args)['reasons']
    raw['acml_vol']=str(b[d]['volume']);raw['prtt_rate']='50'
    assert accept_chart(raw,*args)['accepted'] is None


def test_conflicting_official_versions_hold():
    days,b=fixture(1);d=days[0];raw={v:str(b[d][k]) for k,v in KIS_FIELDS.items()}
    raw.update(stck_bsop_date=d.replace('-',''),flng_cls_code='00',mod_yn='N',revl_issu_reas='',prtt_rate='0')
    refs={('005930',d,'KOSPI'):[{'value':b[d],'source_id':'a'},{'value':{**b[d],'volume':99},'source_id':'b'}]}
    assert accept_chart(raw,'005930',d,'KOSPI','2020-01-02T01:00:00+00:00',refs,'1990-01-01')['accepted'] is None


def test_descriptive_states_do_not_emit_orders_or_make_price_up():
    days,_=fixture(20);f=flow_metrics(days,{d:1 if i>=15 else -1 for i,d in enumerate(days)})
    o=observations(f,{})
    assert o['flow_state']['value']=='new_inflow'
    assert o['flow_price_divergence']['value'] is None
    assert o['order_signal'] is None


def test_auxiliary_workflow_has_no_schedule_credentials_or_main_db_staging():
    text=Path('.github/workflows/decision-support.yml').read_text()
    assert 'schedule:' not in text and 'cron:' not in text and 'secrets.' not in text
    assert 'workflow_run:' in text and 'continue-on-error: true' in text
    assert 'git add snapshot/decision-support' in text and 'git add snapshot/dashboard.duckdb' not in text
    assert "github.ref_name == 'main'" in text and "head_repository.full_name == github.repository" in text


def test_preserve_existing_daily_requests_while_saving_full_responses():
    text=Path('scripts/collect_daily_snapshot.py').read_text()
    assert "'flow_rows':flow.get('output2',[])" in text
    assert "'chart_observed_at':observations[1]['observed_at']" in text
    assert '1.05' in Path('scripts/kis_public_client.py').read_text()


def test_actual_dataset_independent_reconciliation_when_present():
    path=Path('snapshot/decision-support')
    if not (path/'index.json').exists():pytest.skip('Real sidecar not yet packaged')
    from scripts.build_decision_support import verify
    index=json.loads((path/'index.json').read_text());manifest=json.loads(Path('snapshot/manifest.json').read_text())
    if (index['asof'],index['source_snapshot_sha256'])!=(manifest['asof'],manifest['sha256']):
        with pytest.raises(ValueError,match='generation_mismatch'):verify(path,Path('snapshot'))
        return  # A new normal publication may legitimately precede its sidecar.
    r=verify(path,Path('snapshot'))
    assert r['symbols']>0 and r['foreign_value_checks']==r['symbols']*6


def test_failure_records_status_and_keeps_last_good_data(tmp_path,monkeypatch):
    import scripts.build_decision_support as b
    snap=tmp_path/'snapshot';out=snap/'decision-support';out.mkdir(parents=True)
    (snap/'manifest.json').write_text(json.dumps({'asof':'2026-10-21','sha256':'normal-db'}))
    old=b'{"dataset_id":"last-good"}';(out/'index.json').write_bytes(old)
    (snap/'dashboard.duckdb').write_bytes(b'untouched-source')
    def fail(*a,**kw):raise ValueError('normal_validation_missing')
    monkeypatch.setattr(b,'build',fail);monkeypatch.setattr(b,'ROOT',tmp_path)
    monkeypatch.setattr('sys.argv',['build','--snapshot',str(snap),'--install'])
    with pytest.raises(ValueError):b.main()
    assert (out/'index.json').read_bytes()==old
    assert (snap/'dashboard.duckdb').read_bytes()==b'untouched-source'
    assert json.loads((out/'status.json').read_bytes())['status']=='failed'


def test_wrong_generation_rejected_before_reading_shards(tmp_path,monkeypatch):
    import scripts.build_decision_support as b
    d=tmp_path/'decision';s=tmp_path/'snapshot';d.mkdir();s.mkdir()
    (d/'index.json').write_text(json.dumps({'asof':'2026-10-20','source_snapshot_sha256':'old'}))
    (s/'manifest.json').write_text(json.dumps({'asof':'2026-10-21','sha256':'new'}))
    monkeypatch.setattr(b,'coherent',lambda p:True)
    with pytest.raises(ValueError,match='generation_mismatch'):b.verify(d,s)


def test_malformed_ohlcv_and_zero_volume_blocked():
    raw={v:'10' for v in KIS_FIELDS.values()};raw['stck_lwpr']='11'
    with pytest.raises(ValueError,match='invalid_ohlc'):bar(raw,KIS_FIELDS)
    raw['stck_lwpr']='9';raw['acml_vol']='0'
    with pytest.raises(ValueError,match='no_trade'):bar(raw,KIS_FIELDS)


def test_changed_inventory_cannot_pass_verification(tmp_path,monkeypatch):
    import scripts.build_decision_support as b
    d=tmp_path/'decision';s=tmp_path/'snapshot';d.mkdir();s.mkdir()
    identity={'asof':'2026-10-21','source_snapshot_sha256':'same'}
    (d/'index.json').write_bytes(encoded({**identity,'source_inventory_sha256':'wrong'}))
    (s/'manifest.json').write_bytes(encoded({'asof':'2026-10-21','sha256':'same'}))
    (d/'source-manifest.json').write_bytes(encoded({'checkpoints':{},'references':{}}))
    monkeypatch.setattr(b,'coherent',lambda p:True)
    with pytest.raises(ValueError,match='source_inventory_hash_mismatch'):b.verify(d,s)


def test_modified_raw_observation_cannot_pass_verification(tmp_path,monkeypatch):
    import scripts.build_decision_support as b
    d=tmp_path/'decision';s=tmp_path/'snapshot';d.mkdir();s.mkdir()
    inv={'checkpoints':{'a':{'file':'raw.json','sha256':sha(b'original')}}}
    (d/'index.json').write_bytes(encoded({'asof':'2026-10-21','source_snapshot_sha256':'same','source_inventory_sha256':sha(encoded(inv))}))
    (s/'manifest.json').write_bytes(encoded({'asof':'2026-10-21','sha256':'same'}))
    (d/'source-manifest.json').write_bytes(encoded(inv));(d/'raw.json').write_bytes(b'changed')
    monkeypatch.setattr(b,'coherent',lambda p:True)
    with pytest.raises(ValueError,match='evidence_hash_mismatch'):b.verify(d,s)


def test_install_failure_rolls_back_entire_previous_generation(tmp_path,monkeypatch):
    import scripts.build_decision_support as b
    out=tmp_path/'out';dest=tmp_path/'decision';out.mkdir();dest.mkdir()
    (out/'index.json').write_bytes(encoded({'asof':'2026-10-21','source_snapshot_sha256':'new','dataset_id':'new'}))
    (out/'data.json').write_bytes(b'new-data')
    (dest/'index.json').write_bytes(b'old-index');(dest/'data.json').write_bytes(b'old-data')
    monkeypatch.setattr(b,'verify',lambda *a:None)
    replace=b.os.replace
    def interrupted(src,dst):
        if Path(src).name=='ready':raise OSError('simulated disk failure')
        return replace(src,dst)
    monkeypatch.setattr(b.os,'replace',interrupted)
    with pytest.raises(OSError):b.install(out,dest,tmp_path)
    assert (dest/'index.json').read_bytes()==b'old-index'
    assert (dest/'data.json').read_bytes()==b'old-data'


def test_daily_price_changes_and_swings_never_use_future():
    days,b=fixture();r=technical(days,b)
    assert r['close_change_1d']['value']==b[days[-1]]['close']-b[days[-2]]['close']
    assert r['intraday_range']['value']==b[days[-1]]['high']-b[days[-1]]['low']
    assert all(x['confirmed_on']<=days[-1] for k in ('high','low') for x in r[f'confirmed_swing_{k}s']['value'])
    prefix=technical(days[:-2],b)
    b[days[-1]]['close']=999999
    assert technical(days[:-2],b)==prefix
    b.pop(days[-2])
    held=technical(days[:-1],b)
    assert held['close']['value'] is None and held['sma_5']['value'] is None
