"""Write the entire official-sector universe using already calculated dashboard frames."""
import json,shutil,tempfile,time,datetime as dt
from pathlib import Path
from scripts.briefing_schema import *
from scripts.briefing_raw import reference
from scripts.verify_market_briefing import verify_dataset

def install_dataset(source,target):
 """Local rollback on installation failure; callers publish all files in one Git commit."""
 source=Path(source);target=Path(target)
 if target.exists() and {str(p.relative_to(target)):p.read_bytes() for p in target.rglob('*.json')}=={str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*.json')}:return False
 target.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(dir=target.parent,prefix='.briefing-install-') as temp:
  temp=Path(temp);shutil.copytree(source,temp/'new')
  existed=target.exists()
  if existed:target.rename(temp/'old')
  try:(temp/'new').rename(target)
  except BaseException:
   if existed:(temp/'old').rename(target)
   raise
 return True

def export_dataset(snapshot,output,stocks,sectors,metadata):
 from scripts.export_daily_briefing import fields
 snapshot=Path(snapshot);output=Path(output);started=time.perf_counter()
 # One independent read of the DB for all stocks/sectors, also gives explicit exclusions.
 raw=reference(snapshot/'dashboard.duckdb',metadata['asof']);raw_seconds=time.perf_counter()-started
 raw_stocks,raw_sectors,groups,coverage=raw
 common={k:metadata[k] for k in ['asof','source_snapshot_sha256','generated_at','published_at','publication_status','validation_status','provider_finality']}
 common.update(schema_version=SCHEMA_VERSION,dataset_id=dataset_id(common['asof'],common['source_snapshot_sha256']),
  classification='official_sector',metric_definitions=DEFINITIONS,membership_basis='current_asof_membership_applied_to_history')
 # Same validated source keeps its original generation times and bytes on replay.
 old=output/'index.json'
 if old.exists():
  try:
   prior=json.loads(old.read_text())
   if prior.get('dataset_id')==common['dataset_id']:
    common.update({k:prior[k] for k in ['generated_at','published_at']})
  except (ValueError,KeyError):pass
 output.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(dir=output.parent,prefix='.briefing-candidate-') as temp:
  root=Path(temp);(root/'sectors').mkdir();entries=[];stock_seconds=0.;index_started=time.perf_counter()
  grouped={(market,sector):frame.sort_values('symbol') for (market,sector),frame in stocks.groupby(['market','sector'])}
  for _,r in sectors.sort_values(['market','sector']).iterrows():
   sid=sector_id(r.market,r.sector);members=grouped[r.market,r.sector]
   entry={'sector_id':sid,'market':r.market,'sector':r.sector,'official_sector':r.sector!=UNKNOWN,**fields(r,SECTOR_FIELDS)}
   entry['registered_members']=raw_sectors.get(sid,{}).get('registered_members')
   entry.update(metric_validity=validity(entry,SECTOR_FIELDS),quality=quality(entry,SECTOR_FIELDS))
   tick=time.perf_counter();rows=[]
   for _,s in members.iterrows():
    row={'symbol':s.symbol,'name':s['name'],'market':s.market,'sector_id':sid,**fields(s,STOCK_FIELDS)}
    row.update(metric_validity=validity(row,STOCK_FIELDS),quality=quality(row,STOCK_FIELDS));rows.append(row)
   relative='sectors/'+sid+'.json'
   child={**common,'path':relative,'sector_id':sid,'market':r.market,'sector':r.sector,'stock_count':len(rows),
          'raw_flow_reconciliation':{'status':'passed','stocks_checked':len(rows)},'stocks':rows}
   content=payload(child);(root/relative).write_bytes(content)
   entry.update(path=relative,sha256=digest(content),bytes=len(content));entries.append(entry)
   stock_seconds+=time.perf_counter()-tick
  index={**common,'path':'index.json','hash_contract':'index_content_sha256 = SHA256(canonical JSON excluding this field); child sha256 hashes exact file bytes',
   'collection_started_at':metadata['collection_started_at'],'collection_completed_at':metadata['collection_completed_at'],
   'snapshot_published_at':metadata['snapshot_published_at'],'freshness_policy':metadata['freshness_policy'],
   'coverage':coverage,'counts':{'groups':len(entries),'official_sectors':sum(e['official_sector'] for e in entries),'stocks':len(stocks),'sector_files':len(entries)},
   'raw_flow_reconciliation':{'status':'passed','sectors_checked':len(entries),'stocks_checked':len(stocks),'metric_checks':len(entries)*len(SECTOR_FIELDS)+len(stocks)*len(STOCK_FIELDS)},
   'sectors':entries}
  index['index_content_sha256']=digest(payload(index));(root/'index.json').write_bytes(payload(index))
  index_seconds=time.perf_counter()-index_started-stock_seconds
  tick=time.perf_counter();verify_dataset(root,snapshot,raw=raw);verification_seconds=raw_seconds+time.perf_counter()-tick
  if digest((snapshot/'dashboard.duckdb').read_bytes())!=common['source_snapshot_sha256']:raise ValueError('Source changed during full-market verification')
  sizes=[p.stat().st_size for p in root.rglob('*.json')]
  changed=install_dataset(root,output)
 report={'asof':common['asof'],'dataset_id':common['dataset_id'],'counts':index['counts'],'file_count':len(sizes),'total_bytes':sum(sizes),
  'sector_index_generation_seconds':index_seconds,'stock_file_generation_seconds':stock_seconds,'independent_validation_seconds':verification_seconds,
  'total_v3_seconds':time.perf_counter()-started,'changed':changed,'market_api_calls':0}
 return report
