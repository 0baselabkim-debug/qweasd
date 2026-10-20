"""Reconcile every v3 file and metric against independent SQL/raw observations."""
import json
from pathlib import Path
from scripts.briefing_schema import (SCHEMA_VERSION,UNKNOWN,STOCK_FIELDS,SECTOR_FIELDS,DEFINITIONS,
 sector_id,dataset_id,payload,digest,validity,quality)
from scripts.briefing_raw import reference
from scripts.verify_daily_briefing import equal
from screener.publication import coherent

COMMON=('schema_version','asof','source_snapshot_sha256','dataset_id','classification',
 'publication_status','validation_status','provider_finality','generated_at','published_at','metric_definitions','membership_basis')
def verify_dataset(root,snapshot,raw=None):
 root=Path(root);snapshot=Path(snapshot)
 if not coherent(snapshot):raise ValueError('Snapshot coherence failed')
 manifest=json.loads((snapshot/'manifest.json').read_text())
 if not manifest.get('daily_validation',{}).get('preservation_verified'):raise ValueError('Unvalidated source')
 index=json.loads((root/'index.json').read_text())
 sha=digest((snapshot/'dashboard.duckdb').read_bytes());asof=manifest['asof']
 if index['schema_version']!=SCHEMA_VERSION or index['asof']!=asof or index['source_snapshot_sha256']!=sha or index['dataset_id']!=dataset_id(asof,sha):raise ValueError('Dataset source identity mismatch')
 if index['index_content_sha256']!=digest(payload({k:v for k,v in index.items() if k!='index_content_sha256'})):raise ValueError('Index content hash mismatch')
 if index['path']!='index.json':raise ValueError('Index path mismatch')
 if index['metric_definitions']!=DEFINITIONS:raise ValueError('Units/definitions changed')
 if index['publication_status']!='ready_for_briefing' or index['validation_status']!='passed' or index['provider_finality']!='UNVERIFIED':raise ValueError('Invalid validation/finality status')
 if index['classification']!='official_sector' or index['membership_basis']!='current_asof_membership_applied_to_history':raise ValueError('Classification mismatch')
 # Timestamps are informational, never the trading date.
 import datetime as dt
 for k in ['generated_at','published_at']:
  if dt.datetime.fromisoformat(index[k]).tzinfo is None:raise ValueError('Timestamp timezone missing')
 stocks,sectors,groups,coverage=raw or reference(snapshot/'dashboard.duckdb',asof)
 if index['coverage']!=coverage:raise ValueError('Population/omission mismatch')
 entries=index['sectors'];ids=[s['sector_id'] for s in entries]
 if len(ids)!=len(set(ids)) or set(ids)!=set(groups):raise ValueError('Sector coverage/duplicate mismatch')
 checks=0;seen=[];files={'index.json'}
 def record(actual,expected,fields):
  nonlocal checks
  for field in fields:equal(actual[field],expected[field],field);checks+=1
  if actual['metric_validity']!=validity(expected,fields) or actual['quality']!=quality(expected,fields):raise ValueError('Metric validity/missing flags mismatch')
 for entry in entries:
  sid=entry['sector_id'];expected=sectors[sid]
  if sid!=sector_id(entry['market'],entry['sector']) or (entry['market'],entry['sector'])!=(expected['market'],expected['sector']):raise ValueError('Sector identity mismatch')
  if entry['official_sector']!=(entry['sector']!=UNKNOWN):raise ValueError('Unclassified status mismatch')
  record(entry,expected,SECTOR_FIELDS)
  if entry['registered_members']!=expected['registered_members']:raise ValueError('Registered membership mismatch')
  relative='sectors/'+sid+'.json'
  if entry['path']!=relative:raise ValueError('Unsafe/unexpected sector path')
  content=(root/relative).read_bytes();files.add(relative)
  if digest(content)!=entry['sha256'] or len(content)!=entry['bytes']:raise ValueError('Sector file hash/size mismatch')
  child=json.loads(content)
  if any(child[k]!=index[k] for k in COMMON):raise ValueError('Mixed generation metadata')
  if child['sector_id']!=sid or child['market']!=entry['market'] or child['sector']!=entry['sector'] or child['path']!=relative:raise ValueError('Child identity mismatch')
  codes=[s['symbol'] for s in child['stocks']]
  if len(codes)!=len(set(codes)) or set(codes)!=set(groups[sid]) or len(codes)!=child['stock_count'] or len(codes)!=entry['members']:raise ValueError('Stock count/coverage mismatch')
  if child['raw_flow_reconciliation']!={'status':'passed','stocks_checked':len(codes)}:raise ValueError('Child reconciliation mismatch')
  for stock in child['stocks']:
   expected_stock=stocks[stock['symbol']]
   if any(stock[k]!=expected_stock[k] for k in ['symbol','name','market','sector_id']):raise ValueError('Stock identity mismatch')
   record(stock,expected_stock,STOCK_FIELDS);seen.append(stock['symbol'])
  # Raw sector metrics above require every member on every date; no partial totals.
 if len(seen)!=len(set(seen)) or set(seen)!=set(stocks):raise ValueError('Global stock coverage mismatch')
 if {str(p.relative_to(root)) for p in root.rglob('*.json')}!=files:raise ValueError('Unexpected/missing dataset files')
 expected_counts={'groups':len(groups),'official_sectors':sum(s['sector']!=UNKNOWN for s in sectors.values()),'stocks':len(stocks),'sector_files':len(groups)}
 if index['counts']!=expected_counts:raise ValueError('Dataset counts mismatch')
 summary={'status':'passed','sectors_checked':len(sectors),'stocks_checked':len(stocks),'metric_checks':checks}
 if index['raw_flow_reconciliation']!=summary:raise ValueError('Reconciliation totals mismatch')
 return summary
