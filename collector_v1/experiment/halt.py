"""Disable only this experiment and cancel its pending/running Actions jobs."""
import json,os,time
from pathlib import Path
from ..core import Failure,encoded,now
from . import runtime
WORKFLOWS=(runtime.WORKFLOW,'full-market-clock.yml','full-market-controller.yml')


def active_runs(workflow_id):
    runs=[]
    for page in range(1,11):
        batch=runtime.api(f'repos/{runtime.REPO}/actions/workflows/{workflow_id}/runs?per_page=100&page={page}')['workflow_runs']
        runs.extend(r for r in batch if r['status']!='completed' and str(r['id'])!=os.environ['GITHUB_RUN_ID'])
        if len(batch)<100:return runs
    raise Failure('stop_history_pagination_limit')


def stop():
    # Disable dispatch and cron first, before cancelling the independent clock.
    workflows=[]
    for name in WORKFLOWS:
        meta=runtime.api(f'repos/{runtime.REPO}/actions/workflows/{name}')
        if meta['path']!='.github/workflows/'+name:raise Failure('unexpected_stop_workflow')
        runtime.api(f'repos/{runtime.REPO}/actions/workflows/{meta["id"]}/disable','PUT')
        workflows.append(meta)
    cancelled=[]
    for meta in workflows:
        for run in active_runs(meta['id']):
            try:runtime.api(f'repos/{runtime.REPO}/actions/runs/{run["id"]}/cancel','POST')
            except Failure:
                state=runtime.api(f'repos/{runtime.REPO}/actions/runs/{run["id"]}')
                if state['status']!='completed':raise
            cancelled.append(run['id'])
    pending=[]
    for attempt in range(8):
        pending=[r for m in workflows for r in active_runs(m['id'])]
        if not pending:break
        if attempt==3:
            for run in pending:runtime.api(f'repos/{runtime.REPO}/actions/runs/{run["id"]}/force-cancel','POST')
        time.sleep(10)
    states={}
    for m in workflows:
        state=runtime.api(f'repos/{runtime.REPO}/actions/workflows/{m["id"]}')['state']
        states[m['path']]=state
        if state!='disabled_manually':raise Failure('experiment_workflow_not_disabled')
    result=dict(status='STOPPED' if not pending else 'STOP_PENDING',observed_at=now(),workflow_states=states,cancel_requested=cancelled,remaining_active_run_ids=[r['id'] for r in pending],provider_http_by_stop_job=0,existing_artifacts_preserved=True)
    folder=Path('data/experiment-admin');folder.mkdir(parents=True,exist_ok=True)
    (folder/'stop-receipt.json').write_bytes(encoded(result))
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('```json\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n```\n')
    print(json.dumps(result,ensure_ascii=False))
    if pending:raise Failure('experiment_cancellation_not_completed')
