import json,gzip
from pathlib import Path
import pandas as pd
from stages import stages_at
from strategy import transition_at

ROOT=Path(__file__).resolve().parents[1]

def shaped(anchors):
    result=[];points=sorted(anchors.items())
    for (i,a),(j,b) in zip(points,points[1:]):
        result.extend(a+(b-a)*(k-i)/(j-i) for k in range(i,j))
    return result+[points[-1][1]]

def test_early_requires_two_right_days():
    p=shaped({0:150,10:160,18:130,26:145,38:100,40:103})
    assert '조기' not in stages_at(p,39)
    event=stages_at(p,40)['조기']
    assert event['confirmation_index']==40 and event['reference_low_index']==38 and event['within_10']

def test_middle_can_be_on_same_day_as_confirmation():
    p=shaped({0:130,5:140,12:110,19:125,26:100,32:108,37:103,39:109})
    events=stages_at(p,39)
    assert '중간' in events and '확정' in events
    assert events['중간']['within_10'] and events['확정']['within_10']

def test_added_future_cannot_change_current_stages():
    p=shaped({0:150,10:160,18:130,26:145,38:100,40:103})
    assert stages_at(p,40)==stages_at(p+[500,1,900,0],40)

def test_real_confirmed_rule_is_unchanged():
    panel=json.loads(gzip.decompress((ROOT/'results/as-observed-panels.json.gz').read_bytes()))
    for p in panel:
        result=stages_at(p['closes'],39)
        old=transition_at(p['closes'],39)
        assert ('확정' in result)==(old is not None)
        if old:assert result['확정']['reference_low']==old['reference_low']

def test_observation_dates_not_future():
    panel=json.loads(gzip.decompress((ROOT/'results/as-observed-panels.json.gz').read_bytes()))
    for p in panel:
        cutoff=pd.Timestamp(p['date']+'T23:59:59+09:00')
        assert pd.Timestamp(p['base_observed_at'])<=cutoff
        assert pd.Timestamp(p['checkpoint_observed_at'])<=cutoff
        assert max(p['dates'])==p['date']

def test_real_19th_event_counts():
    f=pd.read_csv(ROOT/'results/stage-events.csv',dtype={'symbol':str})
    selected=f[(f.date=='2026-10-19')&f.flow_improving&f.within_10]
    assert selected.groupby('stage').size().to_dict()=={'조기':96,'중간':9}
    final=f[(f.date=='2026-10-19')&(f.stage=='확정')&f.flow_improving]
    assert set(final.symbol)=={'364950','372170'}
    assert (final.rise_pct>10).all()

def test_missing_15th_is_unknown_not_no_signal():
    f=pd.read_csv(ROOT/'results/stage-coverage.csv')
    assert (~f[f.date=='2026-10-15'].evaluable).all()
    assert f[f.date=='2026-10-15'].reasons.notna().all()
