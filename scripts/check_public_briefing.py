"""Fetch public JSON without an Authorization header; do not infer today's freshness."""
import datetime as dt,hashlib,json,time,urllib.request
from pathlib import Path
URL='https://raw.githubusercontent.com/0baselabkim-debug/qweasd/main/snapshot/daily_briefing.json'
expected=Path('snapshot/daily_briefing.json').read_bytes()
for attempt in range(8):
    try:
        req=urllib.request.Request(URL,headers={'Cache-Control':'no-cache'})
        with urllib.request.urlopen(req,timeout=30) as r:status=r.status;raw=r.read()
        if raw==expected:break
    except Exception:
        if attempt==7:raise
    if attempt==7:raise ValueError('Public JSON differs from verified publication')
    time.sleep(10)
d=json.loads(raw)
assert d['validation_status']=='passed' and d['publication_status']=='ready_for_briefing'
p=Path('reports/briefing-automation/public-check.json');p.parent.mkdir(parents=True,exist_ok=True)
p.write_text(json.dumps({'url':URL,'http_status':status,'asof':d['asof'],'sha256':hashlib.sha256(raw).hexdigest(),'source_snapshot_sha256':d['source_snapshot_sha256'],'exact_match':True,'authorization_header_used':False,'checked_at':dt.datetime.now(dt.timezone.utc).isoformat()},indent=2)+'\n')
print(p.read_text())
