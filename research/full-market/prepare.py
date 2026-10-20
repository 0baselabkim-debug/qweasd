"""Read-only inventory and immutable research inputs; no market calls."""
import gzip,hashlib,json,tarfile
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parent; REPO=ROOT.parents[1]
FIELDS=['open','high','low','close','volume','turnover'];KIS=['stck_oprc','stck_hgpr','stck_lwpr','stck_clpr','acml_vol','acml_tr_pbmn']
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def normalize(rows):return {str(pd.Timestamp(r['stck_bsop_date']).date()):dict(zip(FIELDS,[float(r[k]) for k in KIS])) for r in rows if r['stck_bsop_date']<='20261019'}
def main():
 inp=ROOT/'inputs';base=pd.read_csv(inp/'baseline-screening.csv',dtype={'symbol':str}).set_index('symbol');cal=pd.read_csv(inp/'calendar.csv').trade_date.tolist();last60=cal[-60:]
 cp=json.loads(gzip.decompress((inp/'checkpoint-20261020.json.gz').read_bytes()))
 fresh={s:normalize(r['adjusted_prices']) for s,r in cp['records'].items() if s in base.index}
 baseline=set(base[base.research_price_eligible].index);pilot=json.loads((inp/'pilot/run.json').read_text())['records']
 sufficient=set(base[base.consecutive_60].index)|baseline|set(pilot)
 inv={x['path']:x for x in json.loads((REPO/'audit-archive/inventory.json').read_text())['files']}
 recovered={};scanned={};refs={};calendar_sources=[]
 for suffix in ['market','history','candidates','observations','pending','reference','reference-observations','provider-observations','ledgers']:
  archive=REPO/'audit-archive'/f'former-observations-{suffix}.tar.xz';n=0
  with tarfile.open(archive) as tf:
   for m in tf:
    if not m.isfile():continue
    raw=tf.extractfile(m).read();assert hashlib.sha256(raw).hexdigest()==inv[m.name]['sha256'];n+=1
    x=json.loads(gzip.decompress(raw) if m.name.endswith('.gz') else raw)
    if not isinstance(x,dict):continue
    if suffix.startswith('reference'):
     for r in x.get('rows',[]):
      key=r.get('ISU_CD','')+':'+r.get('BAS_DD','')
      refs.setdefault(key,[]).append({'row':r,'source':m.name,'observed_at':x.get('observed_at')})
    if x.get('calendar') and not calendar_sources:calendar_sources.append({'archive':str(archive.relative_to(REPO)),'member':m.name,'sha256':hashlib.sha256(raw).hexdigest(),'calendar':x['calendar'],'source':x.get('source')})
    code=x.get('symbol');bars=x.get('bars')
    if code not in fresh or code in sufficient or code in recovered or not bars or x.get('exchange')!='KRX' or x.get('adjustment')!='adjusted':continue
    old={b['trade_date']:{k:b[k] for k in FIELDS} for b in bars if b['trade_date']<='2026-10-19'}
    overlap=set(old)&set(fresh[code])
    if len(overlap)<20 or any(old[d]!=fresh[code][d] for d in overlap):continue
    joined={**old,**fresh[code]}
    if not all(d in joined for d in last60):continue
    recovered[code]={'bars':joined,'archive':m.name,'archive_sha256':hashlib.sha256(raw).hexdigest(),'archive_observed_at':x.get('observed_at'),'new_observed_at':cp['records'][code]['observed_at'],'overlap_days':len(overlap)}
  scanned[suffix]={'members':n,'sha256':digest(archive)}
  print(suffix,n,'recovered',len(recovered),flush=True)
 structural=set(base[base.prelisting_40>0].index)
 targets=[{'symbol':s,'name':r['name'],'market':r.market} for s,r in base.iterrows() if s not in sufficient|set(recovered)|structural]
 (inp/'recovered-existing.json.gz').write_bytes(gzip.compress(json.dumps(recovered,ensure_ascii=False).encode(),mtime=0))
 (inp/'official-reference.json.gz').write_bytes(gzip.compress(json.dumps(refs,ensure_ascii=False).encode(),mtime=0))
 (inp/'calendar-provenance.json').write_text(json.dumps(calendar_sources,ensure_ascii=False,indent=2))
 report={'universe':len(base),'baseline_eligible':len(baseline),'existing60':int(base.consecutive_60.sum()),'pilot_reused':len(pilot),'additional_existing_recovered':len(recovered),'structural_short_listing':len(structural),'targets':len(targets),'expected_base_requests':2*len(targets),'archives':scanned,'previous_snapshots_inspected':31,'new_api_calls':0}
 (ROOT/'plan.json').write_text(json.dumps({'asof':'2026-10-19','start':'2026-06-01','targets':targets,'summary':report},ensure_ascii=False,indent=2))
 (ROOT/'input-hashes.json').write_text(json.dumps({str(p.relative_to(ROOT)):digest(p) for p in inp.rglob('*') if p.is_file()},indent=2))
 print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
