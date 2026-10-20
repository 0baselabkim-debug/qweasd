"""Isolated, fixed-2026-10-19 census. No HTTP; no production writes; no returns."""
from pathlib import Path
import collections,datetime as dt,gzip,hashlib,json,math
import duckdb,pandas as pd
from stages import stages_at
from strategy import pivots
ROOT=Path(__file__).resolve().parent;ASOF='2026-10-19'
FIELDS=['open','high','low','close','volume','turnover']
KIS=['stck_oprc','stck_hgpr','stck_lwpr','stck_clpr','acml_vol','acml_tr_pbmn']
KRX=['TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','TDD_CLSPRC','ACC_TRDVOL','ACC_TRDVAL']
def read(p):return json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def date(s):return str(pd.Timestamp(s).date())
def normalized(rows):
 result={}
 for r in rows:
  d=date(r['stck_bsop_date'])
  if d in result:raise ValueError('duplicate_date')
  result[d]={k:float(r[v]) for k,v in zip(FIELDS,KIS)}
 return result

def valid(b):
 return all(math.isfinite(b[k]) for k in FIELDS) and min(b[k] for k in FIELDS[:4])>0 and b['volume']>=0 and b['turnover']>=0 and b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']
def band(rise):
 if rise is None or not math.isfinite(rise):return '산출 불가'
 if rise<0:return '저점 대비 음수'
 if rise<=10+1e-10:return '0~10%'
 if rise<=15+1e-10:return '10% 초과~15%'
 if rise<=20+1e-10:return '15% 초과~20%'
 return '20% 초과'
def within(price,low,threshold):return low<=price<=low*(1+threshold/100)+abs(low)*1e-12

def price_events(dates,bars):
 """Emit original rules on observed price prefixes; never use later bars in pivots."""
 closes=[bars[d]['close'] for d in dates];events=[];seen=set()
 for t in range(39,len(dates)):
  if not all(valid(bars[d]) and bars[d]['volume']>0 and bars[d]['turnover']>0 for d in dates[t-39:t+1]):continue
  ps=pivots(closes[t-39:t+1],t-39)
  for stage,e in stages_at(closes,t).items():
   seq=ps[-4:] if stage=='조기' else ps[-6:]
   key=(stage,e['reference_low_index'],e.get('higher_low_index'))
   if key in seen:continue
   seen.add(key)
   item={'stage':stage,'confirmed_date':dates[t],'confirmation_index':t,'confirmation_close':closes[t],
         'reference_low':e['reference_low'],'reference_low_date':dates[e['reference_low_index']],
         'reference_low_index':e['reference_low_index'],'rise_pct':e['rise_pct'],
         'within10':within(closes[t],e['reference_low'],10),'within15':within(closes[t],e['reference_low'],15),
         'within20':within(closes[t],e['reference_low'],20),'rise_band':band(e['rise_pct']),
         'h3_over_l2_10':e.get('rebound_high',0)>e['reference_low']*1.10}
   for name,p in zip(['H1','L1','H2','L2','H3','L3'],seq):
    item[name+'_price']=p.price;item[name+'_date']=dates[p.index];item[name+'_confirmed_date']=dates[p.confirmed_at];item[name+'_index']=p.index
   events.append(item)
 return events

def current_state(dates,bars,events):
 """Display policy separate from EVENT definitions: same L2, last-40 structure.
 L2 breach invalidates all; L3 breach removes middle/confirmed; a close back below
 H3 removes current confirmed status. This is not a trading/return rule.
 """
 t=len(dates)-1;close=bars[dates[-1]]['close'];valid_events=[]
 # Current structure is observable even if its original event preceded the first
 # available 40-session window. Do not invent an event/confirmation date for it.
 ps_now=pivots([bars[d]['close'] for d in dates[-40:]],t-39)
 structures=[]
 for i in range(len(ps_now)-3):
  seq=ps_now[i:i+4];h1,l1,h2,l2=seq
  if ''.join(p.kind for p in seq)!='HLHL' or not(h2.price<h1.price and l2.price<l1.price):continue
  stage='조기'
  following=ps_now[i+4:i+6]
  if len(following)==2 and ''.join(p.kind for p in following)=='HL' and l2.price<following[1].price<following[0].price:
   seq=seq+following;stage='중간'
  e={'stage':stage,'confirmed_date':None,'confirmation_index':t,'reference_low':l2.price,'reference_low_index':l2.index,'current_structure_only':True}
  for name,pivot in zip(['H1','L1','H2','L2','H3','L3'],seq):
   e[name+'_price']=pivot.price;e[name+'_date']=dates[pivot.index];e[name+'_index']=pivot.index;e[name+'_confirmed_date']=dates[pivot.confirmed_at]
  structures.append(e)
 for e in [*events,*structures]:
  if e['H1_index']<t-39:continue
  l2=e['L2_price'];after=[bars[d]['close'] for d in dates[e['L2_index']+1:]]
  if close<=l2 or (after and min(after)<l2):continue
  if e['stage'] in ['중간','확정']:
   after_l3=[bars[d]['close'] for d in dates[e['L3_index']+1:]]
   if after_l3 and min(after_l3)<e['L3_price']:continue
  if e['stage']=='확정' and close<=e['H3_price']:continue
  valid_events.append(e)
 if valid_events:
  # A middle event itself proves the preceding lower-high/lower-low structure.
  last_l2=max(e['reference_low_index'] for e in valid_events)
  same=[e for e in valid_events if e['reference_low_index']==last_l2]
  e=max(same,key=lambda e:({'조기':1,'중간':2,'확정':3}[e['stage']],e['confirmed_date'] is not None,e['confirmation_index']))
  return {'조기':'조기 반등 관찰','중간':'중간 전환','확정':'상승 전환 확정'}[e['stage']],e,same
 if structures:
  return '하락 구조 진행',max(structures,key=lambda e:e['reference_low_index']),[]
 ps=pivots([bars[d]['close'] for d in dates[-40:]])
 for i in range(len(ps)-3):
  h1,l1,h2,l2=ps[i:i+4]
  if ''.join(p.kind for p in ps[i:i+4])=='HLHL' and h2.price<h1.price and l2.price<l1.price:return '하락 구조 진행',None,[]
 return '해당 단계 사건 없음',None,[]

def classify_candidate(state,e,current_close,improving,structural_ok,flow_ok,trusted):
 group='조건 미충족'
 if not structural_ok or not flow_ok:group='판정 불가'
 elif improving and e:
  if state=='중간 전환':
   # Preserve original middle EVENTS, but do not call a price already above H3
   # a 'before-breakout' middle candidate. No confirmed event is invented.
   if current_close>e['H3_price']:group='중간 이력·현재 H3 초과·확정 사건 미확인'
   else:group='핵심 연구 후보' if within(current_close,e['reference_low'],10) else ('확장 연구 후보' if within(current_close,e['reference_low'],20) else '중간 전환 20% 초과')
  elif state=='상승 전환 확정':group='확정 전환 연구군'
  elif state=='조기 반등 관찰':group='조기 관찰군'
 if not trusted and group!='판정 불가':group='검증 보류' if group=='조건 미충족' else '잠정 · '+group
 return group

def reference_resolves(candidate,raw,reference):
 if candidate is None or raw is None or reference is None or len(reference)!=1:return False
 if not valid(candidate) or not valid(raw):return False
 values=tuple(candidate[k] for k in FIELDS)
 return values==tuple(raw[k] for k in FIELDS) and values in reference

def inspect_response(x,code,kind,listing,calendar):
 reasons=[];p=x.get('params',{});expected={'FID_INPUT_ISCD':code,'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_DATE_2':'20261019','FID_INPUT_DATE_1':'20260601','FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'0' if kind=='adjusted' else '1'}
 if any(p.get(k)!=v for k,v in expected.items()):reasons.append('request_scope_mismatch')
 if x.get('rt_cd')!='0':reasons.append('provider_response_rejected')
 if x.get('output1',{}).get('stck_shrn_iscd')!=code:reasons.append('response_symbol_unverified')
 try:b=normalized(x['output2'])
 except (KeyError,TypeError,ValueError,OverflowError):return {},reasons+['malformed_or_duplicate_rows']
 if not b:return b,reasons+['empty_response']
 if any(d>ASOF or d<'2026-06-01' for d in b):reasons.append('response_date_out_of_range')
 if any(d<listing for d in b):reasons.append('prelisting_rows')
 if any(d not in calendar for d in b):reasons.append('not_official_observed_session')
 if any(not valid(v) for v in b.values()):reasons.append('invalid_ohlcv')
 if not x.get('observed_at'):reasons.append('observation_time_missing')
 return b,reasons

def main():
 inp=ROOT/'inputs';out=ROOT/'results';out.mkdir(exist_ok=True)
 base=pd.read_csv(inp/'baseline-screening.csv',dtype={'symbol':str}).set_index('symbol')
 oldprices=pd.read_parquet(inp/'baseline-prices.parquet')
 bar_provenance={(r['symbol'],r['trade_date']):r for r in oldprices.to_dict('records')}
 baselinebars={s:{r['trade_date']:{k:r[k] for k in FIELDS} for r in g.to_dict('records')} for s,g in oldprices.groupby('symbol')}
 calendar=pd.read_csv(inp/'calendar.csv').trade_date.tolist();calendar=[d for d in calendar if d<=ASOF];expected=set(calendar[-40:]);required60=set(calendar[-60:])
 cp19=read(inp/'checkpoint-20261019.json.gz')['records'];cp20=read(inp/'checkpoint-20261020.json.gz')['records']
 pilot=read(inp/'pilot/run.json')['records'];col=read(ROOT/'collection/state.json') if (ROOT/'collection/state.json').exists() else {'records':{},'failures':{}}
 refs=read(inp/'official-reference.json.gz');original_ref={}
 for key,values in refs.items():
  for v in values:
   r=v['row'];s=r.get('ISU_CD');d=date(r['BAS_DD'])
   original_ref.setdefault((s,d),set()).add(tuple(float(str(r[k]).replace(',','')) for k in KRX))
 extra_ref_ids=[]
 probe_path=ROOT/'collection/official-reference-probe.json'
 if probe_path.exists():
  for observation in read(probe_path).get('records',[]):
   if observation.get('status')!='received_unreconciled':continue
   rp=ROOT/'collection'/observation['file'];assert sha(rp)==observation['sha256'];rx=read(rp)
   extra_ref_ids.append(str(rp.relative_to(ROOT))+':'+observation['sha256'])
   for r in rx['response']['OutBlock_1']:
    assert r['BAS_DD']=='20261019'
    original_ref.setdefault((r['ISU_CD'],ASOF),set()).add(tuple(float(str(r[k]).replace(',','')) for k in KRX))
 with duckdb.connect(str(inp/'asof19.duckdb'),read_only=True) as c:
  inst=c.execute('select * from instruments').fetchdf().set_index('symbol')
  flows=c.execute("select symbol,trade_date,net_amount,exchange from flows where trade_date<=?",[ASOF]).fetchdf()
  caps=c.execute('select symbol,market_cap,market_cap_quality from prices where trade_date=?',[ASOF]).fetchdf().set_index('symbol')
 assert not flows.duplicated(['symbol','trade_date']).any() and set(flows.exchange)=={'KRX'}
 flowmap={(s,str(d.date())):n for s,d,n in flows[['symbol','trade_date','net_amount']].itertuples(index=False,name=None)}
 results=[];events_all=[];differences=[];price_dataset=[];adjustments=[];baseline_checks=[];coverage=[];response_coverage=[]
 panels={p['symbol']:p for p in read(inp/'previous-study/results/as-observed-panels.json.gz') if p['date']==ASOF}
 baseline_ids=['inputs/'+name+':'+sha(inp/name) for name in ['baseline-prices.parquet','checkpoint-20261019.json.gz']]
 for code,row in base.iterrows():
  listing=date(inst.loc[code,'listing_date']);bars=baselinebars[code];source='baseline';observed=row.latest_observed_at;source_ids=[str(row.source),*baseline_ids];reasons=[];warnings=[];rawbars=None;pair={};response_issues=[]
  # Preserve original 701 series even when a later pilot contains revised observations.
  entries=pilot.get(code) or col['records'].get(code)
  entrydir=inp/'pilot' if code in pilot else ROOT/'collection'
  for kind,e in (entries or {}).items():
   p=entrydir/e['file'];assert sha(p)==e['sha256'];x=read(p)
   b,errors=inspect_response(x,code,kind,listing,set(calendar));pair[kind]=(b,x,e);response_issues.extend(errors)
   response_coverage.append({'symbol':code,'kind':kind,'file':str(p.relative_to(ROOT)),'sha256':e['sha256'],'observed_at':x.get('observed_at'),'rows':len(b),'first':min(b) if b else None,'last':max(b) if b else None,'missing40':len(expected-set(b)),'missing60':len(required60-set(b)),'validation_issues':';'.join(errors),'reused_pilot':code in pilot})
   source_ids.append(str(p.relative_to(ROOT))+':'+e['sha256'])
  if not bool(row.research_price_eligible) and 'adjusted' in pair:
   bars=pair['adjusted'][0];source='pilot_requery' if code in pilot else 'full_requery';observed=pair['adjusted'][1]['observed_at']
  if 'raw' in pair:rawbars=pair['raw'][0]
  reasons.extend(response_issues)
  if entries and set(pair)!={'adjusted','raw'}:reasons.append('adjusted_raw_pair_incomplete')
  missing40=sorted(expected-set(bars));prelisting40=[d for d in expected if d<listing]
  zero40=[d for d in expected&set(bars) if bars[d]['volume']<=0 or bars[d]['turnover']<=0]
  invalid40=[d for d in expected&set(bars) if not valid(bars[d])]
  if missing40:reasons.append('history_short_40')
  if prelisting40:reasons.append('listing_short_40')
  if zero40:reasons.append('zero_volume_or_turnover_40')
  if invalid40:reasons.append('invalid_ohlcv_40')
  # All versions remain distinct. Compare old/new/reused daily observations, not only closes.
  observations=[('asof19_checkpoint',normalized(cp19[code]['adjusted_prices']))]
  if code in cp20:observations.append(('asof20_checkpoint',normalized(cp20[code]['adjusted_prices'])))
  if 'adjusted' in pair:observations.append(('pilot' if code in pilot else 'new_batch',pair['adjusted'][0]))
  conflict_dates=set();close_conflict_dates=set()
  for label,other in observations:
   for d in sorted(set(bars)&set(other)):
    changed=[k for k in FIELDS if bars[d][k]!=other[d][k]]
    if not changed:continue
    differences.append({'symbol':code,'name':row['name'],'date':d,'basis_source':source,'comparison_source':label,'fields':','.join(changed),'inside_40':d in expected,**{'basis_'+k:bars[d][k] for k in FIELDS},**{'comparison_'+k:other[d][k] for k in FIELDS}})
    if d in expected:
     conflict_dates.add(d)
     if 'close' in changed:close_conflict_dates.add(d)
  resolved_dates={d for d in conflict_dates if reference_resolves(bars.get(d),(rawbars or {}).get(d),original_ref.get((code,d)))}
  unresolved_dates=conflict_dates-resolved_dates
  if unresolved_dates:reasons.append('unresolved_observation_conflict')
  if extra_ref_ids:source_ids.extend(extra_ref_ids)
  action_dates=set(str(row.corporate_action_flag_dates).split(',')) if pd.notna(row.corporate_action_flag_dates) else set()
  for raw in cp19[code]['adjusted_prices']:
   if str(raw.get('prtt_rate','0')).strip() not in ['0','0.0','0.00',''] or str(raw.get('revl_issu_reas','')).strip():action_dates.add(date(raw['stck_bsop_date']))
  for kind,(b,x,e) in pair.items():
   for r in x['output2']:
    if str(r.get('prtt_rate','0')).strip() not in ['0','0.0','0.00',''] or str(r.get('revl_issu_reas','')).strip():action_dates.add(date(r['stck_bsop_date']))
  adjustment_dates=[]
  if 'adjusted' in pair and 'raw' in pair:
   adj=pair['adjusted'][0]
   if set(adj)!=set(rawbars):reasons.append('adjusted_raw_date_mismatch')
   for d in sorted(set(adj)&set(rawbars)):
    different=[k for k in FIELDS if adj[d][k]!=rawbars[d][k]]
    if different:
     adjustment_dates.append(d);adjustments.append({'symbol':code,'date':d,'fields':','.join(different),'adjusted_close':adj[d]['close'],'raw_close':rawbars[d]['close'],'inside_40':d in expected,'official_action_basis':'미확인'})
  if (action_dates|set(adjustment_dates))&expected:reasons.append('corporate_action_adjustment_unverified')
  reference_bad=[];reference_count=0
  for d in expected&set(bars):
   ref=original_ref.get((code,d))
   if not ref:continue
   reference_count+=1;compare=(rawbars or bars).get(d)
   if compare is None or len(ref)!=1 or tuple(compare[k] for k in FIELDS) not in ref:reference_bad.append(d)
  if reference_bad:reasons.append('official_reference_conflict')
  if not bool(row.research_price_eligible) and source=='baseline':
   if row.price_conflict_evidence=='unresolved_or_reference_disagreement':reasons.append('prior_unresolved_conflict')
   if pd.notna(row.krx_reference_mismatch_dates):reasons.append('prior_reference_conflict')
  def net(ds):return sum(flowmap[code,d] for d in ds) if all((code,d) in flowmap and pd.notna(flowmap[code,d]) for d in ds) else None
  n1=net(calendar[-1:]);n5=net(calendar[-5:]);p5=net(calendar[-10:-5]);n20=net(calendar[-20:]);change=n5-p5 if n5 is not None and p5 is not None else None
  flow_ok=n5 is not None and p5 is not None;improving=bool(flow_ok and n5>0 and change>0)
  if not flow_ok:reasons.append('flow_missing_required_10')
  if n20 is None:warnings.append('flow_missing_optional_20')
  positive20=sum(flowmap[code,d]>0 for d in calendar[-20:]) if n20 is not None else None
  cap=caps.loc[code,'market_cap'];cap_quality=caps.loc[code,'market_cap_quality']
  intensity=n5/cap*100 if n5 is not None and pd.notna(cap) and cap>0 and cap_quality=='CLOSE_VOLUME_DATED_MASTER_SHARES_MATCH' else None
  # No inserting/filling gaps. Only the contiguous suffix ending on the fixed date.
  suffix=[]
  for d in reversed(calendar):
   if d not in bars or not valid(bars[d]):break
   suffix.append(d)
  suffix=list(reversed(suffix));structural_ok=len(suffix)>=40 and not invalid40 and not prelisting40 and not zero40 and not response_issues
  found=price_events(suffix,bars) if structural_ok else []
  state,e,same=current_state(suffix,bars,found) if structural_ok else ('판정 불가',None,[])
  current_close=bars.get(ASOF,{}).get('close');rise=(current_close/e['reference_low']-1)*100 if e else None
  trusted=not reasons
  if 'unresolved_observation_conflict' in reasons or any('reference_conflict' in r or 'unresolved_conflict' in r for r in reasons):data_status='가격 관측값 충돌'
  elif any('adjustment' in r for r in reasons):data_status='수정주가 검증 보류'
  elif prelisting40 or zero40:data_status='신규상장·거래정지 등 구조적 예외'
  elif missing40:data_status='가격 이력 부족'
  elif not flow_ok:data_status='수급 데이터 결측'
  elif reasons:data_status='기타 판정 불가'
  else:data_status='가격·수급 모두 정상'
  group=classify_candidate(state,e,current_close,improving,structural_ok,flow_ok,trusted)
  new=[x for x in found if x['confirmed_date']==ASOF]
  item={'asof':ASOF,'symbol':code,'name':row['name'],'market':row.market,'sector':row.sector,'price_data_status':data_status,'price_rows':len(bars),'earliest_price_date':min(bars) if bars else None,'last_price_date':max(bars) if bars else None,'first_evaluable_event_date':suffix[39] if len(suffix)>=40 else None,'continuous_sessions':len(suffix),'continuous_40':len(suffix)>=40,'continuous_60':len(suffix)>=60,'baseline_701':bool(row.research_price_eligible),'selected_source':source,'observed_at':observed,'knowledge_basis':'당시 보존 관측' if source=='baseline' else '사후 재조회·당시 관측 아님','provider_finality':'UNVERIFIED','close':current_close,'price_structure':state,'current_close_above_H3':bool(current_close>e['H3_price']) if e and e.get('H3_price') else None,'middle_before_breakout':bool(state=='중간 전환' and current_close<=e['H3_price']) if e and e.get('H3_price') else False,'new_events':','.join(x['stage'] for x in new),'new_event_count':len(new),'current_stage_confirmation_date':e['confirmed_date'] if e else None,'current_structure_only':bool(e and e.get('current_structure_only')), 'rise_pct':rise,'rise_band':band(rise),'within10':within(current_close,e['reference_low'],10) if e else None,'within15':within(current_close,e['reference_low'],15) if e else None,'within20':within(current_close,e['reference_low'],20) if e else None,'net_1d_krw':n1,'net_5d_krw':n5,'previous_5d_krw':p5,'change_5d_krw':change,'net_20d_krw':n20,'positive_days_20':positive20,'market_cap_krw':cap,'market_cap_quality':cap_quality,'intensity_5d_pct':intensity,'flow_state':'수급 개선 충족' if improving else ('수급 개선 미충족' if flow_ok else '자료 부족'),'flow_improving':improving,'trusted_candidate_data':trusted,'final_group':group,'reasons':';'.join(sorted(set(reasons))),'warnings':';'.join(warnings),'conflicting_dates':','.join(sorted(conflict_dates)),'unresolved_conflicting_dates':','.join(sorted(unresolved_dates)),'reference_resolved_dates':','.join(sorted(resolved_dates)),'close_conflicting_dates':','.join(sorted(close_conflict_dates)),'adjustment_different_dates':len(adjustment_dates),'official_reference_days':reference_count,'official_reference_bad_dates':','.join(reference_bad),'source_identifiers':' | '.join(source_ids),'h3_above_l2_10':e.get('H3_price',0)>e['L2_price']*1.10 if e else None,'breakout_within10_impossible':e.get('H3_price',0)>=e['L2_price']*1.10 if e and e.get('H3_price') else None}
  for name in ['H1','L1','H2','L2','H3','L3']:
   for k in ['price','date','confirmed_date']:item[name+'_'+k]=e.get(name+'_'+k) if e else None
  for label,stage in [('early','조기'),('middle','중간'),('confirmed','확정')]:
   matches=[v for v in same if v['stage']==stage and v['confirmed_date'] is not None];item[label+'_confirmation_date']=matches[-1]['confirmed_date'] if matches else None
  results.append(item)
  coverage.append({'symbol':code,'missing_40':','.join(missing40),'prelisting_40':','.join(prelisting40),'zero_volume_40':','.join(zero40),'invalid_40':','.join(invalid40),'reasons':item['reasons'],'response_issues':';'.join(response_issues)})
  for event in found:events_all.append({'symbol':code,'name':row['name'],'market':row.market,'sector':row.sector,'selected_source':source,'observed_at':observed,'asof_data_trusted':trusted,'event_date_quality_verified':bool(trusted and event['confirmed_date']==ASOF),'flow_improving_asof':improving,'event_new_asof':event['confirmed_date']==ASOF,'point_in_time_observation_available':pd.Timestamp(observed)<=pd.Timestamp(event['confirmed_date']+'T23:59:59+09:00'),**event})
  for d,b in sorted(bars.items()):
   prov=bar_provenance.get((code,d),{}) if source=='baseline' else {}
   original_observed=(prov.get('update_observed_at') if str(prov.get('value_source','')).startswith('checkpoint:') else prov.get('base_observed_at')) if prov else observed
   price_dataset.append({'symbol':code,'trade_date':d,'source':source,'observed_at':original_observed,'series_observed_at':observed,'original_value_source':prov.get('value_source'),**b})
  if bool(row.research_price_eligible):
   prior=[panels[code]]
   assert len(prior)==1
   current=stages_at([bars[d]['close'] for d in calendar[-40:]],39);baseline_result=stages_at(prior[0]['closes'],39)
   assert current==baseline_result and n5==row.net_5d and p5==row.previous_5d,(code,'baseline changed')
   baseline_checks.append({'symbol':code,'prices_and_events_identical':True,'net5_and_previous5_identical':True,'new_evidence_conflict':bool(conflict_dates)})
 f=pd.DataFrame(results);ev=pd.DataFrame(events_all)
 assert len(f)==2429 and f.symbol.nunique()==2429 and len(baseline_checks)==701
 f.to_csv(out/'all-stocks.csv',index=False,encoding='utf-8-sig')
 previous_events=pd.read_csv(inp/'baseline-stage-events.csv',dtype={'symbol':str})
 previous_nine=previous_events[(previous_events.date==ASOF)&(previous_events.stage=='중간')&previous_events.flow_improving&previous_events.within_10]
 assert len(previous_nine)==9
 f[f.symbol.isin(previous_nine.symbol)].to_csv(out/'previous-nine-followup.csv',index=False,encoding='utf-8-sig')
 f[f.trusted_candidate_data & f.flow_improving & (f.price_structure=='중간 전환') & f.current_close_above_H3.fillna(False)].to_csv(out/'middle-boundary-cases.csv',index=False,encoding='utf-8-sig')
 candidate=f[f.final_group.isin(['핵심 연구 후보','확장 연구 후보','확정 전환 연구군'])].copy()
 candidate['selection_reason']=candidate.apply(lambda r:f"{r.price_structure}; 5일 순매수 {r.net_5d_krw:.0f}원; 직전5일 개선 {r.change_5d_krw:.0f}원; L2 대비 {r.rise_pct:.4f}%",axis=1)
 candidate['next_check']='같은 L2/L3 유지·H3 종가 돌파·가격 충돌 재발 여부; 수익률 성과 미검증'
 candidate.to_csv(out/'verified-candidates.csv',index=False,encoding='utf-8-sig')
 for label,name in [('핵심 연구 후보','core-candidates'),('확장 연구 후보','expanded-candidates'),('확정 전환 연구군','confirmed-candidates')]:candidate[candidate.final_group==label].to_csv(out/(name+'.csv'),index=False,encoding='utf-8-sig')
 f[f.final_group.str.startswith('잠정')].to_csv(out/'provisional-only.csv',index=False,encoding='utf-8-sig')
 ev.to_csv(out/'stage-events.csv',index=False,encoding='utf-8-sig');ev[ev.event_new_asof].to_csv(out/'new-events-asof.csv',index=False,encoding='utf-8-sig')
 for name,rows in [('observation-conflicts',differences),('adjustment-differences',adjustments),('coverage',coverage),('baseline-regression',baseline_checks),('response-coverage',response_coverage)]:pd.DataFrame(rows).to_csv(out/(name+'.csv'),index=False,encoding='utf-8-sig')
 prices=pd.DataFrame(price_dataset);prices.to_parquet(out/'research-prices.parquet',index=False)
 with duckdb.connect(str(out/'research.duckdb')) as c:
  c.execute('create or replace table prices as select * from prices');c.execute('create or replace table screening as select * from f');c.execute('create or replace table events as select * from ev');c.execute('create or replace table frozen_flows as select * from flows')
 selected=f[f.flow_improving & f.trusted_candidate_data]
 cross=f.groupby(['trusted_candidate_data','price_structure','flow_state','rise_band']).size().reset_index(name='stocks');cross.to_csv(out/'cross-tabulation.csv',index=False,encoding='utf-8-sig')
 thresholds=[]
 for trusted in [True,False]:
  for stage,g in f[(f.trusted_candidate_data==trusted)&f.flow_improving].groupby('price_structure'):
   thresholds.append({'trusted':trusted,'stage':stage,'before_limit':len(g),'rise_available':int(g.rise_pct.notna().sum()),'rise_unavailable':int(g.rise_pct.isna().sum()),'within10':int(g.within10.fillna(False).sum()),'within15':int(g.within15.fillna(False).sum()),'within20':int(g.within20.fillna(False).sum())})
 pd.DataFrame(thresholds).to_csv(out/'threshold-comparison.csv',index=False,encoding='utf-8-sig')
 candidate.groupby(['market','sector','final_group']).size().reset_index(name='stocks').to_csv(out/'candidate-sectors.csv',index=False,encoding='utf-8-sig')
 summary={'asof':ASOF,'population':len(f),'baseline_701_identical':len(baseline_checks),'structural_price_evaluable':int((f.price_structure!='판정 불가').sum()),'trusted_data_stocks':int(f.trusted_candidate_data.sum()),'reused_baseline_rows':int((f.selected_source=='baseline').sum()),'pilot_selected':int((f.selected_source=='pilot_requery').sum()),'new_selected':int((f.selected_source=='full_requery').sum()),'continuous40':int(f.continuous_40.sum()),'continuous60':int(f.continuous_60.sum()),'data_states':f.price_data_status.value_counts().to_dict(),'current_price_states':f.price_structure.value_counts().to_dict(),'flow_improving':int(f.flow_improving.sum()),'final_groups':f.final_group.value_counts().to_dict(),'new_events_raw':ev[ev.event_new_asof].stage.value_counts().to_dict(),'new_events_trusted':ev[ev.event_new_asof & ev.asof_data_trusted].stage.value_counts().to_dict(),'core_candidates':candidate[candidate.final_group=='핵심 연구 후보'][['symbol','name','rise_pct','net_5d_krw','change_5d_krw']].to_dict('records'),'thresholds':thresholds,'baseline_new_conflicts':sum(r['new_evidence_conflict'] for r in baseline_checks),'reason_counts':dict(collections.Counter(r for line in f.reasons for r in line.split(';') if r)),'future_returns_computed':False,'fresh_flow_requests':0}
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
 print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
