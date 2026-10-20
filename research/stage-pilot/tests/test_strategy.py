"""Synthetic boundary fixtures only; never written to real-data result CSVs."""
import math
import pytest
from strategy import pivots,transition_at,signals,classify

def path(last=109,first_high=140):
    anchors={0:first_high-10,5:first_high,12:110,19:125,26:100,32:108,37:103,39:106,40:107,41:last}
    result=[]
    items=list(anchors.items())
    for (left,a),(right,b) in zip(items,items[1:]):
        result.extend(a+(b-a)*(i-left)/(right-left) for i in range(left,right))
    return result+[last]

def group(signal,**kw):
    return classify(price_eligible=kw.get('price',True),flow_eligible=kw.get('flow',True),
                    net5=kw.get('net',5),previous5=kw.get('prior',1),signal=signal)

def test_two_right_sessions_required():
    assert pivots([5,4,1,2])==[]
    p=pivots([5,4,1,2,3])[0]
    assert (p.kind,p.index,p.confirmed_at)==('L',2,4)

def test_equal_plateau_not_pivot():
    assert pivots([5,4,1,1,4,5])==[]

def test_equal_neighbor_rejects_extremum():
    assert pivots([5,4,1,2,1,4,5])==[]

def test_boundary_locations_not_confirmed():
    assert pivots([1,2,3,4,5])==[]

def test_consecutive_same_kind_extrema_keep_stronger():
    assert [p.index for p in pivots([1,2,5,3,3,4,6,4,3])]==[6]
    assert [p.index for p in pivots([1,2,5,3,3,3.5,4,3,2])]==[2]

def test_equal_strength_separated_extrema_keep_earlier():
    assert [p.index for p in pivots([1,2,5,3,3,4,5,4,3])]==[2]

def test_adjacent_alternating_extrema_confirm_separately():
    assert [(p.kind,p.index,p.confirmed_at) for p in pivots([3,2,5,1,4,3])]==[('H',2,4),('L',3,5)]

def test_real_structure_and_confirmation():
    event=transition_at(path(),41)
    assert event['reference_low_index']==26
    assert event['rebound_high_index']==32
    assert event['higher_low_index']==37
    assert event['higher_low_confirmed_index']==39
    assert event['rise_pct']==pytest.approx(9)
    assert group(event)=='A'

@pytest.mark.parametrize('last,expected',[(109,'A'),(110,'A'),(110.01,'B'),(111,'B')])
def test_ten_percent_inclusive(last,expected):
    assert group(transition_at(path(last),41))==expected

def test_breakout_needs_strict_close_above_high():
    assert transition_at(path(108),41) is None
    assert group(None)=='C'

def test_future_append_does_not_backdate_signals():
    original=path()
    expected=signals(original)
    future=original+[115,90,75,40,150,20,155]
    assert [e for e in signals(future) if e['confirmation_index']<len(original)]==expected
    assert signals(original[:41])==[]
    assert expected[0]['confirmation_index']==41

def test_data_after_analysis_time_not_read():
    assert transition_at(path()+[math.nan,0,999999],41)==transition_at(path(),41)

def test_above_breakout_not_repeated():
    assert len(signals(path()+[110,111,112]))==1

@pytest.mark.parametrize('bad',[None,math.nan,math.inf,0,-1])
def test_missing_or_invalid_price_blocks(bad):
    prices=path();prices[15]=bad
    assert transition_at(prices,41) is None

def test_short_history_not_replaced_by_shorter_window():
    assert transition_at(path()[-39:],38) is None
    assert group(None,price=False)=='판정 불가'

def test_missing_flow_never_zero():
    assert group(None,flow=False,net=None,prior=None)=='판정 불가'

@pytest.mark.parametrize('net,prior',[(0,-1),(-1,-5),(5,5),(4,5)])
def test_flow_strict_positive_and_improving(net,prior):
    assert group(None,net=net,prior=prior)=='수급 조건 미충족'

def test_unknown_price_not_c_even_with_improving_flow():
    assert group(None,price=False,net=100,prior=-10)=='판정 불가'

def test_not_downtrend():
    prices=path(first_high=120)
    assert transition_at(prices,41) is None
