"""Record actual attempts, including failures; replay/skips never count as new-day success."""
import datetime as dt,gzip,json,os
from pathlib import Path
from zoneinfo import ZoneInfo

def make_record(env,bundle,ledger):
 day=env.get('DAY','');date=f'{day[:4]}-{day[4:6]}-{day[6:8]}' if len(day)==8 else None
 publication=ledger.get(date,{})
 mode=env.get('MODE','collect');same_run=publication.get('run_id')==env.get('GITHUB_RUN_ID')
 success=bool(env.get('JOB_STATUS')=='success' and mode=='collect' and same_run and publication.get('mode')=='collect' and bundle.get('status')=='complete')
 schedule=env.get('SCHEDULE','');time={'30 9 * * 1-5':'18:30:00','0 12 * * 1-5':'21:00:00','30 11 * * 1-5':'20:30:00','30 14 * * 1-5':'23:30:00'}.get(schedule)
 # Old cron labels are retained only to interpret historical operation records.
 return {'run_id':env.get('GITHUB_RUN_ID'),'attempt':env.get('GITHUB_RUN_ATTEMPT'),'mode':mode,'trade_date':date,
 'planned_start_at':date+'T'+time+'+09:00' if date and time and env.get('EVENT')=='schedule' else None,
 'actual_job_started_at':env.get('JOB_STARTED_AT'),'collection_started_at':bundle.get('started_at'),'collection_completed_at':bundle.get('completed_at'),
 'finished_at':dt.datetime.now(dt.timezone.utc).isoformat(),'job_status':env.get('JOB_STATUS'),
 'success_symbols':len(bundle['records']) if 'records' in bundle else None,'excluded_symbols':len(bundle['excluded']) if 'excluded' in bundle else None,
 'failed_symbols':len(bundle['failures']) if 'failures' in bundle else None,'failures':bundle.get('failures',{}),
 'collection_status':bundle.get('status','not_started_or_already_published'),'published_at':publication.get('published_at_utc') if same_run else None,
 'call_summary':bundle.get('call_summary',{}),
 'collection_attempts':bundle.get('attempts',[]),'phase_seconds':bundle.get('phase_seconds',{}),'checkpoint_cost':bundle.get('checkpoint_cost',{}),
 'two_call_successes':sum(r.get('quote_source')=='FHKST03010100.output1' for r in bundle.get('records',{}).values()),
 'third_call_successes':sum(r.get('quote_source')=='FHKST01010100.output' for r in bundle.get('records',{}).values()),
 'physical_request_count_current_attempt':len(bundle.get('metrics',[])),
 'new_trading_day_published':success,'url':'https://github.com/'+env.get('GITHUB_REPOSITORY','0baselabkim-debug/qweasd')+'/actions/runs/'+env.get('GITHUB_RUN_ID','')}

if __name__=='__main__':
 p=Path('daily-results/bundle.json.gz');b=json.loads(gzip.decompress(p.read_bytes())) if p.exists() else {}
 p=Path('snapshot/publication-ledger.json');ledger=json.loads(p.read_text()) if p.exists() else {}
 r=make_record(os.environ,b,ledger);p=Path('operations/market-runs')/(str(r['run_id'])+'-'+str(r['attempt'])+'.json');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in r.items() if k!='failures'},ensure_ascii=False))
