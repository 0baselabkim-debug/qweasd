"""Explicit local/CI batch entrypoint; never issues provider requests or publishes.

JSON job file: jobs [{id, directory, source_kind, proof}], reference_directory,
cutoff, output_root. Both collector and publication sources require external
anchors. Historical GPT calls are a separate explicit run_investment_v2 action.
"""
import argparse
import concurrent.futures
import datetime as dt
import json
import sys
import re
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.decision_evidence.build import build,verify_output
from screener.investment_v2.pipeline import run,verify_bundle
from screener.decision_support.evidence import encoded,sha


def execute(item):
    job,config=item
    if not re.fullmatch('[A-Za-z0-9_-]{1,100}',job['id']):raise ValueError('unsafe_job_identity')
    from screener.investment_v2.contracts import independent
    output=independent(config['output_root'],[job['directory'],config['reference_directory']])
    root=output/job['id'];root.mkdir(parents=True,exist_ok=True)
    repo=Path(__file__).resolve().parents[1]
    code={str(p.relative_to(repo)):sha(p.read_bytes()) for group in ('decision_evidence','decision_support','historical_v2','investment_v2') for p in (repo/'screener'/group).glob('*') if p.suffix in ('.py','.md')}
    request_sha=sha(encoded(dict(job=job,cutoff=config['cutoff'],reference_directory=config['reference_directory'],code=code)))
    receipt=root/'result.json'
    if receipt.exists():
        r=json.loads(receipt.read_bytes())
        if r.get('job_request_sha256')!=request_sha:raise ValueError('batch_job_or_analysis_version_changed_use_new_generation')
        if r.get('status')=='PASS_OFFLINE_REPROCESSING':
            verify_output(r['evidence']['directory'],r['evidence']['validation']['manifest_sha256'])
            verify_bundle(r['investment']['directory'],r['investment']['validation']['manifest_sha256'])
            return r
        raise ValueError('failed_job_requires_explicit_new_output_generation')
    try:
        e=build(job['directory'],root/'evidence',config['cutoff'],source_kind=job['source_kind'],reference_directory=config['reference_directory'],source_proof=job['proof'])
        i=run(e['directory'],e['validation']['manifest_sha256'],root/'investment')
        r=dict(status='PASS_OFFLINE_REPROCESSING',job=job['id'],asof=job['proof']['asof'],source_kind=job['source_kind'],analysis_mode='RETROSPECTIVE_REANALYSIS',evidence=e,investment=i,market_HTTP=0,OpenAI_HTTP=0,job_request_sha256=request_sha,code_sha256=code)
    except Exception as error:
        import traceback
        r=dict(status='FAIL',job=job['id'],reason=str(error),traceback=traceback.format_exc())
    with receipt.open('xb') as f:f.write(encoded(r))
    return r


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('jobs');p.add_argument('--workers',type=int,default=1);a=p.parse_args()
    if not 1<=a.workers<=2:raise ValueError('offline_batch_workers_limit_two')
    config=json.loads(Path(a.jobs).read_bytes());jobs=config['jobs']
    if len({j['id'] for j in jobs})!=len(jobs):raise ValueError('duplicate_batch_job')
    if any(j['source_kind'] not in ('historical-collector','historical-publication') for j in jobs):raise ValueError('historical_sources_only')
    results=[]
    with concurrent.futures.ProcessPoolExecutor(max_workers=a.workers) as pool:
        for r in pool.map(execute,[(j,config) for j in jobs]):
            results.append(r);print(json.dumps({k:r.get(k) for k in ['job','asof','status','reason']},ensure_ascii=False),flush=True)
            Path(config['output_root']).joinpath('batch-results.json').write_bytes(encoded(dict(results=results,completed=len(results),total=len(jobs),market_HTTP=0,OpenAI_HTTP=0)))
    if any(r['status']!='PASS_OFFLINE_REPROCESSING' for r in results):raise SystemExit(1)


if __name__=='__main__':main()
