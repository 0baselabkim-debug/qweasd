"""Unauthenticated read, byte/hash and generation checks for the public v3 dataset."""
import argparse,json,time,urllib.request
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.briefing_schema import digest,payload
BASE='https://raw.githubusercontent.com/0baselabkim-debug/qweasd/main/snapshot/briefing/'

def check(root=Path('snapshot/briefing'),sample=3):
 root=Path(root);started=time.perf_counter();observations=[]
 def fetch(relative):
  expected=(root/relative).read_bytes();tick=time.perf_counter()
  for attempt in range(8):
   try:
    req=urllib.request.Request(BASE+relative,headers={'Cache-Control':'no-cache'})
    with urllib.request.urlopen(req,timeout=30) as r:status=r.status;raw=r.read()
    if raw==expected:break
   except Exception:
    if attempt==7:raise
   if attempt==7:raise ValueError('Public generation mismatch: '+relative)
   time.sleep(10)
  observations.append({'path':relative,'http_status':status,'sha256':digest(raw),'bytes':len(raw),'elapsed_seconds':time.perf_counter()-tick})
  return raw
 raw=fetch('index.json');index=json.loads(raw)
 if index['index_content_sha256']!=digest(payload({k:v for k,v in index.items() if k!='index_content_sha256'})):raise ValueError('Index content hash mismatch')
 if index['validation_status']!='passed' or index['publication_status']!='ready_for_briefing' or index['raw_flow_reconciliation']['status']!='passed':raise ValueError('Index unvalidated')
 entries=index['sectors']
 # Include both markets and a negative-flow sector where available.
 selected=[]
 for predicate in [lambda e:e['market']=='KOSPI',lambda e:e['market']=='KOSDAQ',lambda e:e['net_5d'] is not None and e['net_5d']<0]:
  candidate=next((e for e in entries if predicate(e) and e not in selected),None)
  if candidate:selected.append(candidate)
 if sample==0:selected=entries
 else:
  selected=(selected+[e for e in entries if e not in selected])[:sample]
 for entry in selected:
  raw=fetch(entry['path']);child=json.loads(raw)
  if digest(raw)!=entry['sha256'] or len(raw)!=entry['bytes']:raise ValueError('Public child hash mismatch')
  if any(child[k]!=index[k] for k in ['asof','dataset_id','source_snapshot_sha256']):raise ValueError('Public mixed generation')
  if child['stock_count']!=len(child['stocks']) or child['stock_count']!=entry['members']:raise ValueError('Public stock count mismatch')
 return {'status':'passed','asof':index['asof'],'dataset_id':index['dataset_id'],'index_url':BASE+'index.json','authorization_header_used':False,
  'checked_files':observations,'checked_sector_files':len(selected),'total_sector_files':len(entries),'elapsed_seconds':time.perf_counter()-started}

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--all',action='store_true');p.add_argument('--output',type=Path,default=Path('reports/market-briefing/public-check.json'));a=p.parse_args()
 result=check(sample=0 if a.all else 3);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
