"""Original inline rehearsal; must run inside kis_shared.legacy Session."""
import sys,json,os
from pathlib import Path
sys.path.insert(0,'scripts')
from kis_public_client import KIS
api=KIS();day=os.environ['DAY']
response=api.request('/uapi/domestic-stock/v1/quotations/chk-holiday',{'BASS_DT':day,'CTX_AREA_FK':'','CTX_AREA_NK':''},'CTCA0903R')
rows=[r for r in response['output'] if r.get('bass_dt')==day]
assert len(rows)==1 and rows[0]['opnd_yn']=='Y','Official calendar target mismatch'
Path('daily-results/calendar-check.json').write_text(json.dumps({'trade_date':day,'endpoint':'CTCA0903R','actual_response':rows[0]},ensure_ascii=False,indent=2)+'\n')
print('Official trading-day calendar validated')
