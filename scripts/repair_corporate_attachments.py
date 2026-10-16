"""Small verified attachment-mapping retry; append original receipt records only."""
import copy,datetime as dt,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.collect_corporate import Dart,process,BudgetExceeded
from scripts.corporate_documents import attachment_parent
from screener.corporate import load_state,save_state,build_database

def repair(api,state,receipts):
    results=[]
    for receipt in receipts:
        f=state['filings'][receipt];job=state['jobs'].setdefault(receipt,{})
        try:
            parent,record,evidence=attachment_parent(api,f)
            before=copy.deepcopy(job)
            if parent['receipt'] in state['filings'] and state['filings'][parent['receipt']]!=parent:raise ValueError('parent_metadata_revision_requires_review')
            state['filings'].setdefault(parent['receipt'],parent)
            new_finance=parent['receipt']+'_'+record['scope'] not in state['financials']
            state['financials'].setdefault(parent['receipt']+'_'+record['scope'],record)
            state['events'].append({'type':'attachment_parent_verified','receipt':receipt,'before_job':before,'evidence':evidence,'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()})
            job.update(linked_periodic_receipt=parent['receipt'],business_status='held_attachment_only',financial_status='held_attachment_only',attachment_review='Audit attachment content still requires separate review')
            for c in ['business','financial']:
                for suffix in ['error','retry_after','error_class']:job.pop(c+'_'+suffix,None)
            process(api,state,parent,None)
            results.append({'receipt':receipt,'parent':parent['receipt'],'new_financial':new_finance,'parent_job':state['jobs'].get(parent['receipt'])})
        except BudgetExceeded:raise
        except Exception as e:results.append({'receipt':receipt,'error':str(e) if isinstance(e,ValueError) else type(e).__name__})
    return results

if __name__=='__main__':
    state=load_state();api=Dart(max_calls=12,max_seconds=180)
    result=repair(api,state,['20260325000430','20260330001721'])
    report={'run_id':os.environ.get('GITHUB_RUN_ID'),'calls':api.calls,'seconds':round(time.monotonic()-api.started,3),'results':result}
    state['runs'].append(report);Path('corporate-candidate').mkdir(exist_ok=True)
    save_state(state,Path('corporate-candidate/state.json.gz'));build_database(state,Path('corporate-candidate/corporate.duckdb'))
    Path('corporate-run.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
