"""Aggregate actual bounded recovery reports, not theoretical scheduled capacity."""
import collections,datetime as dt,json,sys
from pathlib import Path

def summarize(root):
    runs=[];totals=collections.Counter()
    for path in sorted(root.glob('run-[0-9]*.json')):
        r=json.loads(path.read_text());calls=r['physical_request_attempts'];promoted=sum(x['promoted'] for x in r['results'])
        row={'run_id':r['run_id'],'event':r['event'],'requested_limit':r['limit'],'date_symbol_attempts':len(r['results']),'promoted_candidates':promoted,
             'physical_kis_requests':calls,'auth_requests':r['authentication_attempts'],'retry_attempts':r['retry_attempts'],
             'http_or_api_errors':sum(m.get('http')!=200 or m.get('code') not in ('','0','MCA00000') for m in r['kis_metrics']),
             'saved_page_responses_reused':r['cached_responses_reused'],'saved_flow_response_replays':sum(x.get('saved_flow_response_replay',False) for x in r['results']),
             'recovery_phase_seconds':(dt.datetime.fromisoformat(r['finished_at'])-dt.datetime.fromisoformat(r['started_at'])).total_seconds(),
             'promotions_per_10_kis_requests':10*promoted/calls if calls else None,
             'url':f"https://github.com/0baselabkim-debug/qweasd/actions/runs/{r['run_id']}"}
        for key in ['date_symbol_attempts','promoted_candidates','physical_kis_requests','auth_requests','retry_attempts','http_or_api_errors','saved_page_responses_reused','saved_flow_response_replays']:totals[key]+=row[key]
        audit_path=root/f"publication-{r['run_id']}.json"
        if audit_path.exists():
            a=json.loads(audit_path.read_text());row['indicator_counts_before']=a.get('before');row['indicator_counts_after']=a.get('after')
        runs.append(row)
    totals['promotions_per_10_kis_requests']=10*totals['promoted_candidates']/totals['physical_kis_requests'] if totals['physical_kis_requests'] else None
    totals['requests_per_date_symbol_attempt']=totals['physical_kis_requests']/totals['date_symbol_attempts'] if totals['date_symbol_attempts'] else None
    out={'runs':runs,'totals':dict(totals),'counting':'Date-symbol promotions differ from newly accepted unique stocks. Only matching publication audits establish liquidity gain. No future schedule success inferred.'}
    (root/'run-efficiency.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');return out
if __name__=='__main__':print(json.dumps(summarize(Path(sys.argv[1] if len(sys.argv)>1 else 'reports/completeness-phase2'))['totals']))
