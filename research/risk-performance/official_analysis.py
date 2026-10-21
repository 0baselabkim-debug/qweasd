"""Reproducible offline revalidation; no market clients or signal registration."""
import collections,datetime as dt,gzip,json,time,tarfile,hashlib,subprocess
from pathlib import Path
import pandas as pd
import link_trial as old
from forward_system import Store,load,sha,utcnow,validate_publication,snapshot_rows,official_next_session,FIELDS,KIS_FIELDS,KRX_FIELDS
from engine import digest,instant
from price_link import normalized,link_history,classify_link,six,make_forward_bundle
from conflict_reconciliation import official_rows,research_snapshot_view,add_evidence,select_flow_replay,verify_replay_sources
from official_reconcile import compare_six,candidate_context
from screener.liquidity_quality import digest as evidence_digest
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1];FULL=ROOT.parent/'full-market';OUT=ROOT/'official-crosscheck';DIAG=ROOT/'conflict-diagnosis';SNAP=DIAG/'inputs/latest-snapshot'
def dump(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def table(name,rows):pd.DataFrame(rows).to_csv(OUT/name,index=False,encoding='utf-8-sig')
def read_csv(p):return pd.read_csv(p,dtype={'symbol':str}).fillna('')
def main():
 started=time.monotonic();manifest=OUT/'analysis-inputs.json';ids={}
 known=load(manifest)['captured_at'] if manifest.exists() else utcnow().isoformat()
 if manifest.exists():
  for rel,want in load(manifest)['protected_sources'].items():assert sha(REPO/rel)==want,'previous_study_source_changed'
  for rel,want in load(manifest)['sources'].items():
   if not rel.endswith('.py'):assert sha(REPO/rel)==want,'captured_data_source_changed'
 def identify(p):
  rel=str(p.relative_to(REPO));ids[rel]=sha(p);return {'file':rel,'sha256':ids[rel]}
 protected={str(p.relative_to(REPO)):sha(p) for base in [REPO/'snapshot',ROOT/'price-link-study',DIAG,ROOT/'forward-study'] for p in base.rglob('*') if p.is_file()}
 pub=validate_publication(SNAP);u,snapshot_prices,flows,db_sessions,excluded=snapshot_rows(SNAP);day=pub['asof'];assert day=='2026-10-20' and len(u)==2429
 for p in SNAP.glob('*'):identify(p)
 ledger=load(OUT/'API_EXECUTION.json');identify(OUT/'API_EXECUTION.json')
 assert len(ledger['requests'])<=8
 for r in ledger['requests']:
  assert r['physical_request'] and r['status']!='reserved'
  h=instant(r['started_at']).astimezone(dt.timezone(dt.timedelta(hours=9))).hour
  assert not 18<=h<23
 refs,docs,_=old.references([]);envelopes={};response_audit=[];calendar={}
 for r in ledger['requests']:
  if 'file' not in r:continue
  p=OUT/r['file'];assert sha(p)==r['file_sha256'];raw=gzip.decompress(p.read_bytes());assert hashlib.sha256(raw).hexdigest()==r['response_sha256'];identify(p)
  if r['http']!=200:continue
  x=json.loads(raw)
  if r['key']=='holiday':
   calendar={'holiday_response':{'endpoint':r['url'],'tr_id':'CTCA0903R','observed_at':r['completed_at'],**x},'request':r['params'],'response_sha256':r['response_sha256'],'tr_cont':r['tr_cont']};continue
  market,date=r['key'].split('-');date=old.iso(date)
  e={'http':r['http'],'url':r['url']+'?basDd='+r['params']['basDd'],'observed_at':r['completed_at'],'rows':x.get('OutBlock_1',[]),'rows_sha256':evidence_digest(x.get('OutBlock_1',[])),'raw_response_sha256':r['response_sha256']}
  values=official_rows(e,market,date)
  for s,b in values.items():refs.setdefault((s,date),set()).add(six(b))
  envelopes[market,date]=e;response_audit.append({'market':market,'date':date,'rows':len(values),'http':200,'schema_date_market_unique_pass':True,'response_sha256':r['response_sha256']})
  dump(OUT/'references'/f'{market}-{date}.json',e)
 # Include all preserved Oct19 reference versions, not only a convenient one.
 prior_probe=load(FULL/'collection/official-reference-probe.json')
 for r in prior_probe['records']:
  if r.get('http')!=200:continue
  p=FULL/'collection'/r['file'];assert sha(p)==r['sha256'];identify(p);x=load(p);date='2026-10-19'
  e={'http':200,'url':x['url'],'observed_at':x['observed_at'],'rows':x['response']['OutBlock_1'],'rows_sha256':evidence_digest(x['response']['OutBlock_1'])}
  official_rows(e,x['market'],date);envelopes[x['market'],date]=e
 dump(OUT/'official-calendar.json',calendar)
 try:next_day,calendar_known=official_next_session(day,calendar,known);calendar_error=''
 except ValueError as error:next_day=None;calendar_known=None;calendar_error=str(error)
 cps={};cp_ids={}
 for d,p in [('2026-10-15',ROOT/'inputs/checkpoint-20261015.json.gz'),('2026-10-16',ROOT/'inputs/checkpoint-20261016.json.gz'),('2026-10-19',FULL/'inputs/checkpoint-20261019.json.gz'),('2026-10-20',FULL/'inputs/checkpoint-20261020.json.gz')]:cps[d]=load(p);cp_ids[d]=identify(p)
 comparison=[];detail=read_csv(DIAG/'observation-detail.csv');identify(DIAG/'observation-detail.csv')
 for r in detail[detail.comparison_source.str.contains('checkpoint-20261016')].to_dict('records'):
  a={k:r['original_'+k] for k in FIELDS};b={k:r['candidate_'+k] for k in FIELDS};values=refs.get((r['symbol'],'2026-10-16'),set());o=dict(zip(FIELDS,next(iter(values)))) if len(values)==1 else None
  comparison.append({'symbol':r['symbol'],'market':r['market'],'date':r['date'],'original_observed_at':r['left_observed_at'],'later_observed_at':r['candidate_observed_at'],'result':compare_six(a,b,o),**{'original_'+k:a[k] for k in FIELDS},**{'later_'+k:b[k] for k in FIELDS},**{'official_'+k:o[k] if o else None for k in FIELDS}})
 assert len(comparison)==2159 and len({r['symbol'] for r in comparison})==2159
 table('krx-price-comparison.csv',comparison)
 q37=read_csv(ROOT/'forward-study/krx-crosscheck-37.csv');identify(ROOT/'forward-study/krx-crosscheck-37.csv');quarantined=set(q37.symbol);qrows=[]
 for r in q37.to_dict('records'):
  vals=refs.get((r['symbol'],r['date']),set());o=dict(zip(FIELDS,next(iter(vals)))) if len(vals)==1 else None
  result=compare_six({k:r['original_'+k] for k in FIELDS},{k:r['later_adjusted_'+k] for k in FIELDS},o)
  qrows.append({**r,'official_KRX_available':o is not None,'result':result,'six_field_version_resolved':result in ['original_matches','later_matches'],'outcome_quarantine':'maintained; corporate_action_interval_and_executable_price_not_verified','performance_eligible':False})
 table('quarantine-37.csv',qrows)
 original=read_csv(ROOT/'price-link-study/all-stocks.csv').set_index('symbol');identify(ROOT/'price-link-study/all-stocks.csv')
 previous=read_csv(FULL/'results/all-stocks.csv').set_index('symbol');identify(FULL/'results/all-stocks.csv')
 prices=pd.read_parquet(FULL/'results/research-prices.parquet');identify(FULL/'results/research-prices.parquet');groups={s:g for s,g in prices.groupby('symbol')}
 sessions={m:sorted(set(load(FULL/'collection/calendar.json')['market_sessions'][m]+db_sessions[m])) for m in db_sessions};identify(FULL/'collection/calendar.json')
 pairs=load(FULL/'collection/state.json')['records'];pilot=load(FULL/'inputs/pilot/run.json')['records'];explicit=set(read_csv(DIAG/'explicit-adjustment-differences.csv').symbol)
 common_sources=[identify(p) for p in [FULL/'results/research-prices.parquet',FULL/'results/all-stocks.csv',FULL/'collection/calendar.json',ROOT/'forward-study/inputs/reference-audit/catalog.json',DIAG/'explicit-adjustment-differences.csv',OUT/'official-calendar.json',OUT/'API_EXECUTION.json']]
 for p in sorted((OUT/'references').glob('*.json')):common_sources.append(identify(p))
 for p in [ROOT/'official_analysis.py',ROOT/'verify_official_study.py',ROOT/'official_reconcile.py',ROOT/'price_link.py',ROOT/'engine.py',FULL/'stages.py']:identify(p)
 store=Store(OUT/'store');coverage=[];links={};audits=[];views={};latest_comparison=[];latest_all=[]
 for symbol,name,market,listing in u:
  prior=previous.loc[symbol] if symbol in previous.index else pd.Series({'reasons':'previous_research_symbol_missing'});g=groups.get(symbol);oldbars={r['trade_date']:{k:r[k] for k in FIELDS} for r in g.to_dict('records')} if g is not None else {}
  record=cps[day]['records'].get(symbol,{});observations=[];sources=list(cp_ids.values())+common_sources;prior_reasons=prior.reasons.split(';')
  for d,cp in cps.items():
   r=cp['records'].get(symbol)
   if r:
    f=r['flow'];observations.append({'date':old.iso(f['stck_bsop_date']),'values':{k:float(f[v]) for k,v in zip(FIELDS,KIS_FIELDS)},'source':cp_ids[d]['file']})
    if flows[symbol].get(d)!=(int(f['frgn_ntby_tr_pbmn'])*1000000,'KRX'):prior_reasons.append('checkpoint_snapshot_flow_mismatch')
  pair=pilot.get(symbol) or pairs.get(symbol);base=FULL/'inputs/pilot' if symbol in pilot else FULL/'collection'
  if pair:
   for kind,e in pair.items():
    p=base/e['file'];assert sha(p)==e['sha256'];sources.append(identify(p));x=load(p);params=x['params']
    assert params['FID_INPUT_ISCD']==symbol and params['FID_COND_MRKT_DIV_CODE']=='J' and params['FID_ORG_ADJ_PRC']==('0' if kind=='adjusted' else '1') and x['rt_cd']=='0' and x['output1']['stck_shrn_iscd']==symbol
    if kind=='raw':observations.extend({'date':d,'values':b,'source':e['sha256']} for d,b in normalized(x['output2']).items())
  official={d:v for (s,d),v in refs.items() if s==symbol}
  candidate,changes=candidate_context(oldbars,record,official,symbol in explicit)
  # Explicit adjustment uncertainty remains independently blocking even if one date agrees.
  if symbol in explicit:prior_reasons.append('explicit_adjustment_factor_unverified')
  link=link_history(symbol,market,day,str(listing),candidate,record,sessions[market],prior_reasons,observations,official,sources,known,symbol in quarantined)
  if symbol in excluded:link['reasons']=sorted(set(link['reasons']+['excluded_or_suspended']));link['status']='hold'
  link['official_candidate_audit']=changes;link['original_series_sha256']=digest(oldbars)
  audits.extend({'symbol':symbol,**c} for c in changes)
  cls=classify_link(link,flows[symbol]);overlay=None
  if link['status']=='linked_history_validated':
   links[symbol]=link;store.put('observations',digest(link),{'kind':'validated_linked_history','data':link})
   refbyday={d:[e] for (m,d),e in envelopes.items() if m==market}
   overlay=research_snapshot_view(link,snapshot_prices[symbol],cps,refbyday,pub['sha256']);views[symbol]=overlay
  vals=official.get(day,set());b=link['bars'][-1] if link['bars'] else None;q=snapshot_prices.get(symbol,{}).get(day)
  full_match=b is not None and vals=={six(b)}
  if len(vals)==1 and b is not None:
   official_bar=dict(zip(FIELDS,next(iter(vals))))
   raw_bar=normalized([record['flow']])[day]
   latest_all.append({'symbol':symbol,'name':name,'market':market,'date':day,'checkpoint_observed_at':record['observed_at'],'official_observed_at':envelopes.get((market,day),{}).get('observed_at'),'investor_blank_adjusted_six_equal':six(raw_bar)==six(b),'explicit_raw_option1_latest_available':False,'candidate_six_match':full_match,'changed_fields':','.join(k for k in FIELDS if b[k]!=official_bar[k]),**{'kis_investor_blank_'+k:raw_bar[k] for k in FIELDS},**{'kis_adjusted_'+k:b[k] for k in FIELDS},**{'official_'+k:official_bar[k] for k in FIELDS}})
  snap_match=q is not None and len(vals)==1 and (q['close'],q['adjusted_close'],q['volume'],q['turnover'])==(next(iter(vals))[3],next(iter(vals))[3],next(iter(vals))[4],next(iter(vals))[5])
  if original.loc[symbol,'link_status']=='linked_history_validated':latest_comparison.append({'symbol':symbol,'name':name,'market':market,'candidate_6fields_match':full_match,'snapshot_available_4fields_match':snap_match,'still_linked':symbol in links,'raw_latest_matches':bool(b and all(six(v)==six(b) for v in link['raw_latest'])),'reasons':';'.join(link['reasons'])})
  coverage.append({'symbol':symbol,'name':name,'market':market,'asof':day,'previous_link_status':original.loc[symbol,'link_status'],'link_status':link['status'],'reasons':';'.join(link['reasons']),'unresolved_conflict_dates':';'.join(sorted({c['date'] for c in link['conflicts']})),'price_days':len(link['bars']),'officially_resolved_observations':len(link['official_resolved_observations']),'separate_candidate_bar_changes':len(changes),'explicit_adjustment_hold':symbol in explicit,'prior37_quarantine':symbol in quarantined,'latest_6fields_official_match':full_match,'snapshot_latest_available_fields_match':snap_match,'research_snapshot_comparison':overlay['status'] if overlay else 'not_evaluated','classification_status':cls['status'],'group':cls['group'],'classification_reason':cls['reason'],'net_5d_krw':cls.get('net_5d_krw'),'change_5d_krw':cls.get('change_5d_krw'),'original20_flow_replay_ready':False,'bundle_preparation':'not_evaluated','registration_eligible':False,'registered_signal':False,'registration_blocker':'asof_20261020_retrospective_only; next_open_deadline_passed','link_sha256':digest(link)})
 print('price links',len(links),flush=True)
 # Reuse all original archived observations. No API; source identifiers verified.
 index={};inventory={r['path']:r for r in load(REPO/'audit-archive/inventory.json')['files']};identify(REPO/'audit-archive/inventory.json');targets=set(links)
 for archive in sorted((REPO/'audit-archive').glob('*.tar.xz')):
  if archive.name=='previous-publications.tar.xz':continue
  sid=identify(archive)
  with tarfile.open(archive) as tf:
   for member in tf:
    if not member.isfile() or not member.name.endswith(('.json','.json.gz')):continue
    raw=tf.extractfile(member).read();h=hashlib.sha256(raw).hexdigest();assert inventory[member.name]['sha256']==h
    x=json.loads(gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
    if isinstance(x,dict) and x.get('symbol') in targets:add_evidence(index,x['symbol'],x,{**sid,'member':member.name,'member_sha256':h},known)
 for d,c in cps.items():
  for s in targets:add_evidence(index,s,c,cp_ids[d],known)
 for p in [REPO/'staging/kis_history_20261014.json.gz',REPO/'research/daily-replay.json.gz']:
  sid=identify(p);x=load(p)
  for s in targets:add_evidence(index,s,x,sid,known)
 cache={};flow_rows_checked=0;bundle_counts=collections.Counter()
 for r in coverage:
  s=r['symbol']
  if s not in links:continue
  replay=select_flow_replay(index,s,sessions[r['market']][-20:],flows[s]);verify_replay_sources(replay,REPO,cache);r['original20_flow_replay_ready']=replay['status']=='replay_ready'
  path=OUT/'flow-evidence'/(digest(replay)+'.json.gz');path.parent.mkdir(exist_ok=True);data=gzip.compress(json.dumps(replay,ensure_ascii=False,sort_keys=True,allow_nan=False).encode(),mtime=0)
  if path.exists():assert path.read_bytes()==data
  else:path.write_bytes(data)
  r['flow_evidence']=str(path.relative_to(OUT))
  if r['original20_flow_replay_ready']:flow_rows_checked+=20
  try:
   bundle=make_forward_bundle(links[s],flows[s],replay['rows'],replay['sources'],envelopes.get((r['market'],day)),known)
   r['bundle_preparation']='passed_offline_acceptance_only';store.put('observations',digest(bundle),{'kind':'prepared_research_bundle','data':bundle})
  except ValueError as e:r['bundle_preparation']=str(e)
  bundle_counts[r['bundle_preparation']]+=1
 table('latest-all-price-comparison.csv',latest_all);table('updated-coverage.csv',coverage);table('latest-227-comparison.csv',latest_comparison);table('candidate-revisions.csv',audits)
 resolution=[{'symbol':r['symbol'],'previous_link_status':r['previous_link_status'],'new_link_status':r['link_status'],'resolved_observations':r['officially_resolved_observations'],'remaining_reasons':r['reasons'],'prospective_eligible':False} for r in coverage]
 table('conflict-resolution.csv',resolution);dump(OUT/'research-overlays.json',{'snapshot_sha256':pub['sha256'],'prospective_eligible':False,'stocks':views})
 assert len(coverage)==len({r['symbol'] for r in coverage})==2429
 assert all(r['link_status']=='hold' for r in coverage if r['explicit_adjustment_hold'] or r['prior37_quarantine'])
 assert all(sha(REPO/p)==h for p,h in protected.items())
 stats={'asof':day,'observed_at':known,'population':len(coverage),'physical_requests':sum(r['physical_request'] for r in ledger['requests']),'market_requests':sum(not r['authentication'] for r in ledger['requests']),'authentication_requests':sum(r['authentication'] for r in ledger['requests']),'retries':0,'official_responses':response_audit,'oct16_comparison':dict(collections.Counter(r['result'] for r in comparison)),'prior37_comparison':dict(collections.Counter(r['result'] for r in qrows)),'prior37_outcome_eligible':0,'prior227_still_linked':sum(r['still_linked'] for r in latest_comparison),'prior227_latest_official_match':sum(r['candidate_6fields_match'] for r in latest_comparison),'prior227_snapshot_match':sum(r['snapshot_available_4fields_match'] for r in latest_comparison),'latest_all_official_match':sum(r['candidate_six_match'] for r in latest_all),'latest_all_official_different':sum(not r['candidate_six_match'] for r in latest_all),'latest_all_field_patterns':dict(collections.Counter(r['changed_fields'] for r in latest_all if not r['candidate_six_match'])),'hold_reason_counts':dict(collections.Counter(reason for r in coverage for reason in r['reasons'].split(';') if reason)),'price_links':len(links),'new_price_links':sum(r['previous_link_status']!='linked_history_validated' and r['link_status']=='linked_history_validated' for r in coverage),'price_holds':2429-len(links),'explicit_adjustment_holds':len(explicit),'separate_full_bar_candidates':len(audits),'original20_replays':sum(r['original20_flow_replay_ready'] for r in coverage),'flow_rows_independently_checked':flow_rows_checked,'snapshot_research_comparison_passed':sum(r['research_snapshot_comparison']=='research_comparison_passed' for r in coverage),'research_groups':dict(collections.Counter(r['group'] for r in coverage if r['group'])),'classification_states':dict(collections.Counter(r['classification_status'] for r in coverage)),'bundle_preparation':dict(bundle_counts),'next_official_session':next_day,'calendar_known_at':calendar_known,'calendar_error':calendar_error,'registration_eligible':0,'actual_registered':0,'publication_sha256':pub['sha256'],'dataset_id':pub['dataset_id'],'all_retrospective_research':True,'protected_files_unchanged':len(protected),'elapsed_seconds':time.monotonic()-started}
 dump(OUT/'SUMMARY.json',stats);dump(manifest,{'captured_at':known,'sources':ids,'publication':pub,'protected_sources':protected});print(json.dumps(stats,ensure_ascii=False),flush=True)
if __name__=='__main__':
 import socket
 def denied(*args,**kwargs):raise RuntimeError('offline_analysis_network_denied')
 socket.socket.connect=denied;socket.create_connection=denied
 main()
