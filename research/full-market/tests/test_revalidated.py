import copy
from revalidate_existing import candidate,FIELDS

def setup():
 import datetime as dt
 dates=[(dt.date(2026,9,28)+dt.timedelta(days=i)).isoformat() for i in range(22)]
 b=dict(open=100,high=110,low=90,close=105,volume=10,turnover=1000)
 old={d:dict(b) for d in dates};new=copy.deepcopy(old);new[dates[-1]]={**b,'close':106,'volume':11,'turnover':1100}
 scope={'market':'J','symbol':'000001','end':'20261020','adjustment':'0'};ref={tuple(new[dates[-1]][k] for k in FIELDS)}
 return old,new,dates[-1],ref,scope

def test_revalidated_observation_keeps_original_and_discards_future():
 old,new,asof,ref,scope=setup();saved=copy.deepcopy(old);new['2026-10-20']={k:999 for k in FIELDS}
 result=candidate(old,new,asof,ref,scope,'000001')
 assert result and result['matched_overlap']==21 and result['changed_dates']==[asof]
 assert old==saved and result['bars'][asof]['close']==106 and '2026-10-20' not in result['bars']

def test_unproven_revision_or_earlier_change_or_adjustment_remains_held():
 old,new,asof,ref,scope=setup()
 assert candidate(old,new,asof,None,scope,'000001') is None
 assert candidate(old,new,asof,ref,{**scope,'market':'UN'},'000001') is None
 assert candidate(old,new,asof,ref,scope,'000001',['2026-10-01']) is None
 new[min(new)]['close']=104
 assert candidate(old,new,asof,ref,scope,'000001') is None
