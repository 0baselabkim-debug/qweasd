"""Reprocess three already received official XMLs; zero market/provider requests."""
import copy,datetime as dt,gzip,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.corporate import load_state,save_state,build_database
from scripts.collect_corporate import process
from scripts.publish_corporate import validate
from screener.technical_quality import digest

def audit_facts(response,filing):
    rows=response.get('list',[])
    if response.get('status')!='000' or not rows:return None
    if any(r.get('corp_code')!=filing['corp_code'] or r.get('rcept_no')!=filing['receipt'] or r.get('stlm_dt')!=filing['period_end'] for r in rows):return None
    current=[r for r in rows if '(당기)' in ''.join(r.get('bsns_year','').split())]
    if not current or any(not r.get('adt_opinion') for r in current):return None
    return {'receipt':filing['receipt'],'symbol':filing['symbol'],'disclosed_on':filing['disclosed_on'],'period_end':filing['period_end'],
      'source':'OpenDART accnutAdtorNmNdAdtOpinion.json','response_sha256':digest(response),'raw_current_rows':current,
      'scope':'Provider does not label CFS/OFS in these rows; retain all current-period rows',
      'material_financial_risk':'unverified','eligibility_rule_applied':False}

def run():
    evidence=Path('reports/recovery-operations');diagnosis=json.loads((evidence/'missing-xml-diagnosis.json').read_text())
    old=load_state();state=copy.deepcopy(old);results=[]
    for item in diagnosis['filings']:
        f=item['requested_filing'];receipt=f['receipt'];raw=gzip.decompress((evidence/'source-documents'/(receipt+'.document.gz')).read_bytes())
        assert state['filings'][receipt]==f and item['document']['transport']['http']==200
        assert hashlib.sha256(raw).hexdigest()==item['document']['sha256']
        job=state['jobs'][receipt];before=copy.deepcopy(job)
        class Saved:
            def get(self,path,params):
                if path!='document.xml' or params!={'rcept_no':receipt}:raise AssertionError('Unexpected recollection')
                return raw
        if job.get('business_status')!='success':
            job['business_retry_after']='2000-01-01'
            state['events'].append({'type':'document_size_parser_replay','receipt':receipt,'before_job':before,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'source_run':'38837357481'})
            process(Saved(),state,f,None)
        audit=audit_facts(item.get('audit_opinion_response',{}),f)
        if audit and receipt not in state.setdefault('audit_facts',{}):
            state['audit_facts'][receipt]=audit
            state['events'].append({'type':'audit_opinion_observed','receipt':receipt,'facts':audit,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()})
        results.append({'receipt':receipt,'symbol':f['symbol'],'business_status':job['business_status'],'business_error':job.get('business_error'),'audit_facts_verified':bool(audit)})
    validate(old,state);out=Path('/tmp/recovery-corporate-candidate');out.mkdir(exist_ok=True)
    run={'run_id':'saved-document-replay','calls':0,'results':results};state['runs'].append(run)
    save_state(state,out/'state.json.gz');build_database(state,out/'corporate.duckdb')
    (evidence/'saved-document-replay.json').write_text(json.dumps(run,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(run,ensure_ascii=False))
if __name__=='__main__':run()
