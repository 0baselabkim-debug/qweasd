"""Independent read-only SQL/raw-response audit, with no API imports."""
import csv,datetime as dt,gzip,hashlib,json,collections
from pathlib import Path
import duckdb
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1];OUT=ROOT/'official-crosscheck'
def read(p):
 b=p.read_bytes();return json.loads(gzip.decompress(b) if p.suffix=='.gz' else b)
def rows(p):return list(csv.DictReader(p.open(encoding='utf-8-sig')))
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 meta=read(OUT/'analysis-inputs.json')
 for rel,want in {**meta['sources'],**meta['protected_sources']}.items():assert h(REPO/rel)==want,(rel,'source_modified')
 ledger=read(OUT/'API_EXECUTION.json');assert len(ledger['requests'])<=8
 keys=set();previous=None;official={};krx_fields=['TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','TDD_CLSPRC','ACC_TRDVOL','ACC_TRDVAL'];fields=['open','high','low','close','volume','turnover']
 for r in ledger['requests']:
  assert r['key'] not in keys;keys.add(r['key']);assert not r['retry']
  started=dt.datetime.fromisoformat(r['started_at']);assert not 18<=started.astimezone(dt.timezone(dt.timedelta(hours=9))).hour<23
  if previous is not None:assert (started-previous).total_seconds()>=1.05
  previous=started
  if 'file' not in r:assert r['authentication'];continue
  raw=gzip.decompress((OUT/r['file']).read_bytes());assert hashlib.sha256(raw).hexdigest()==r['response_sha256'];assert h(OUT/r['file'])==r['file_sha256'];x=json.loads(raw)
  if r['key']=='holiday':continue
  market,d=r['key'].split('-');assert d in ['20261016','20261020'];seen=set()
  for b in x['OutBlock_1']:
   assert b['BAS_DD']==d and b['MKT_NM']==market and b['ISU_CD'] not in seen;seen.add(b['ISU_CD'])
   official[b['ISU_CD'],d]=tuple(float(b[k]) for k in krx_fields)
 counts=collections.Counter()
 for r in rows(OUT/'krx-price-comparison.csv'):
  o=official[r['symbol'],'20261016'];a=tuple(float(r['original_'+k]) for k in fields);b=tuple(float(r['later_'+k]) for k in fields)
  state='original_matches' if a==o else 'later_matches' if b==o else 'neither_matches';assert r['result']==state
  assert tuple(float(r['official_'+k]) for k in fields)==o;counts[state]+=1
 for r in rows(OUT/'latest-all-price-comparison.csv'):
  o=official[r['symbol'],'20261020'];a=tuple(float(r['kis_adjusted_'+k]) for k in fields)
  assert (r['candidate_six_match']=='True')==(a==o)
 coverage=rows(OUT/'updated-coverage.csv');assert len(coverage)==len({r['symbol'] for r in coverage})==2429
 with duckdb.connect(str(ROOT/'conflict-diagnosis/inputs/latest-snapshot/dashboard.duckdb'),read_only=True) as c:
  sql={(s,str(d)):(a,q) for s,d,a,q in c.execute('select symbol,trade_date,net_amount,net_quantity from flows').fetchall()}
 amount=quantity=0
 for r in coverage:
  assert r['registration_eligible']=='False' and r['registered_signal']=='False'
  if r['explicit_adjustment_hold']=='True' or r['prior37_quarantine']=='True':assert r['link_status']=='hold'
  if r['original20_flow_replay_ready']!='True':continue
  replay=read(OUT/r['flow_evidence']);assert len(replay['rows'])==20 and len({v['stck_bsop_date'] for v in replay['rows']})==20
  for v in replay['rows']:
   d=dt.datetime.strptime(v['stck_bsop_date'],'%Y%m%d').date().isoformat();a,q=sql[r['symbol'],d]
   assert int(v['frgn_ntby_tr_pbmn'])*1000000==a;amount+=1
   assert int(v['frgn_ntby_qty'])==q;quantity+=1
 prior={r['symbol']:r for r in rows(ROOT/'conflict-diagnosis/research-classification.csv')};common=0;differences=[]
 for r in coverage:
  if r['symbol'] not in prior or r['link_status']!='linked_history_validated':continue
  common+=1;p=prior[r['symbol']]
  for key in ['net_5d_krw','change_5d_krw']:
   assert float(r[key])==float(p[key])
  if r['group']!=p['group']:differences.append(r['symbol'])
 assert not differences
 result={'status':'passed','original_price_and_source_hashes_unchanged':True,'protected_files':len(meta['protected_sources']),'independent_oct16_counts':dict(counts),'latest_all_comparisons':len(rows(OUT/'latest-all-price-comparison.csv')),'original_net_amount_checks':amount,'original_net_quantity_checks':quantity,'previous227_surviving_group_regressions':common,'previous_group_differences':differences,'operating_files_changed':0,'actual_registered':0,'physical_market_and_auth_requests':len(keys)}
 out=OUT/'validation';out.mkdir(exist_ok=True);(out/'independent-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
