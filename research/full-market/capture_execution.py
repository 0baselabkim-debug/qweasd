"""Read GitHub execution metadata only; does not dispatch or collect market data."""
from pathlib import Path
import datetime as dt,json,subprocess
ROOT=Path(__file__).resolve().parent
def gh(*args):return json.loads(subprocess.check_output(['gh',*args]))
def main():
 ledger=json.loads((ROOT/'collection/ledger.json').read_text());runs=[]
 for b in ledger['batches']:
  run=b['run'].split('-')[0]
  x=gh('run','view',run,'--repo','0baselabkim-debug/qweasd','--json','databaseId,event,headSha,status,conclusion,createdAt,updatedAt,jobs,url')
  assert x['status']=='completed' and x['conclusion']=='success' and x['event']=='workflow_dispatch'
  for job in x['jobs']:
   seconds=(dt.datetime.fromisoformat(job['completedAt'])-dt.datetime.fromisoformat(job['startedAt'])).total_seconds()
   assert seconds<=900;job['actual_job_seconds']=seconds
  runs.append(x)
 starts=[dt.datetime.fromisoformat(r['createdAt']) for r in runs]
 ends=[dt.datetime.fromisoformat(j['completedAt']) for r in runs for j in r['jobs']]
 result={'runs':runs,'first_dispatch':min(starts).isoformat(),'last_job_completed':max(ends).isoformat(),'total_wall_seconds':(max(ends)-min(starts)).total_seconds(),'scheduled_runs_created':0,'scope':'manual research only; stock batches plus bounded calendar/reference validation'}
 (ROOT/'results/execution-records.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='runs'}))
if __name__=='__main__':main()
