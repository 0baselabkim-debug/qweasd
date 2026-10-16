"""Read-only exact-date saved-evidence preflight; never makes network requests."""
import copy,datetime as dt,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.technical_quality import digest,daily_accepted,blocking_conflicts
from screener.technical import validate
from scripts.technical_acceptance import reference_ready,verify_saved_or_live,selected_observations

class MissingSavedEvidence(Exception):pass
class NoNetwork:
    def request(self,*a,**k):raise MissingSavedEvidence('saved_flow_response_unavailable')

def reference_index(root):
    from scripts.technical_batch import read
    from scripts.technical_reference import classify_rows
    chosen={};errors=[]
    paths=list((root/'reference').glob('**/*.json.gz'))+list((root/'reference-observations').glob('*/*/*.json.gz'))
    for p in paths:
        c=read(p,{})
        try:
            day=c['asof'];market=p.parent.name if 'reference-observations' in p.parts else p.name.split('.')[0]
            part={'KOSPI':'stk','KOSDAQ':'ksq'}[market]
            url=f'https://data-dbg.krx.co.kr/svc/apis/sto/{part}_bydd_trd?basDd='+day.replace('-','')
            if c['url']!=url or classify_rows({'OutBlock_1':c['rows']},day)[0]!='RECEIVED':raise ValueError('reference_identity')
            if c.get('rows_sha256') and c['rows_sha256']!=digest(c['rows']):raise ValueError('reference_hash')
            if 'reference-observations' in p.parts and p.name.split('.')[0]!=digest(c):raise ValueError('observation_hash')
            observed=dt.datetime.fromisoformat(c['observed_at'])
            if observed.tzinfo is None:raise ValueError('reference_time')
            key=(day,market)
            if key not in chosen or observed>chosen[key][0]:chosen[key]=(observed,c,p)
        except (KeyError,ValueError,TypeError) as e:errors.append({'path':str(p),'error':type(e).__name__})
    index={}
    for (day,market),(_,c,p) in chosen.items():
        for r in c['rows']:
            index[(day,r['ISU_CD'])]={'row':{**r,'_retrieved_at':c['observed_at'],'_source_url':c['url']},'source':str(p),'sha256':digest(c)}
    return index,errors

def preflight(bundle,index):
    """Return a separately validated candidate, or the precise evidence gap."""
    b=copy.deepcopy(bundle);e=b.get('daily_acceptance',{});entry=index.get((b['asof'],b['symbol']))
    info={'symbol':b['symbol'],'asof':b['asof'],'original_sha256':digest(bundle),
          'previous_reasons':blocking_conflicts(bundle),'physical_requests':0,
          'saved_evidence':{'raw_flow_rows':len(e.get('raw_flow_rows',[])),
              'raw_close':b.get('price_comparison',{}).get('kis_unadjusted'),
              'bars':len(b.get('bars',[]))},'status':'ADDITIONAL_OBSERVATION_REQUIRED'}
    def fail(reason):info['new_request_reason']=reason;return None,info
    if not entry:return fail('exact_date_official_reference_missing')
    refs={b['symbol']:entry['row']};info['saved_evidence']['reference']=entry
    if not reference_ready(b,refs):return fail('official_reference_identity_or_observation_time')
    if not b.get('bars') or b['bars'][-1].get('trade_date')!=b['asof']:
        return fail('saved_adjusted_bar_date_mismatch_or_missing')
    if b.get('price_comparison',{}).get('kis_unadjusted') is None:
        return fail('saved_same_date_unadjusted_price_missing')
    from screener.technical_quality import FIELDS
    row=entry['row']
    try:
        matching=(b['bars'][-1]['trade_date']==b['asof']
            and all(b['bars'][-1][k]==float(row[v]) for k,v in FIELDS.items())
            and b['price_comparison'].get('kis_unadjusted')==float(row['TDD_CLSPRC'])
            and b['price_comparison'].get('kis_adjusted')==float(row['TDD_CLSPRC']))
    except (KeyError,TypeError,ValueError,IndexError):matching=False
    if not matching:return fail('saved_price_values_conflict_cause_unconfirmed')
    if e.get('bars_sha256')!=digest(b['bars']) or e.get('flows_sha256')!=digest(b['flows']):return fail('matching_saved_payload_hashes_missing')
    try:proposed=verify_saved_or_live(NoNetwork(),b,refs)
    except MissingSavedEvidence:return fail('exact_date_raw_flow_response_missing_or_mismatch')
    except (KeyError,ValueError,TypeError,IndexError) as exc:return fail('invalid_saved_response_'+type(exc).__name__)
    info['validation_reasons']=validate(proposed,b['asof'])
    if not daily_accepted(proposed) or info['validation_reasons']:return fail('full_validation_failed')
    info.update(status='SAVED_EVIDENCE_VALIDATED',new_request_reason=None,candidate_sha256=digest(proposed))
    return proposed,info

def audit(root,asof,out):
    from scripts.technical_batch import read
    index,errors=reference_index(root);rows=[]
    paths=list((root/'market').glob('*.json.gz'))+list((root/'history').glob('*/*.json.gz'))+list((root/'pending').glob('*/*.json.gz'))
    ledger=read(root/'recovery-state.json',{})
    for _,b in selected_observations(paths,asof):
        _,r=preflight(b,index);r['next_retry_at']=ledger.get('jobs',{}).get(b['asof']+'/'+b['symbol'],{}).get('retry_after');rows.append(r)
    from collections import Counter
    result={'asof':asof,'physical_requests':0,'promoted':0,'publication':'READ_ONLY_NOT_PUBLISHED',
      'saved_eligible':sum(r['status']=='SAVED_EVIDENCE_VALIDATED' for r in rows),'reasons':dict(Counter(r['new_request_reason'] for r in rows)),
      'reference_errors':errors,'tasks':rows}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='tasks'},ensure_ascii=False))
if __name__=='__main__':
    from screener.store import load,PACKAGED_DB
    audit(Path('technical'),str(load(PACKAGED_DB)['flows'].trade_date.max().date()),Path(sys.argv[1]))
