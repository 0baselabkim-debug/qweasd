"""Read actual Actions events; never substitute a manual run for schedule evidence."""
import datetime as dt,json,subprocess,sys
from pathlib import Path
from zoneinfo import ZoneInfo

def report(day,out):
    target=dt.date.fromisoformat(day);now=dt.datetime.now(dt.timezone.utc);kst=ZoneInfo('Asia/Seoul')
    workflows=[]
    fields='databaseId,headSha,event,status,conclusion,createdAt,startedAt,updatedAt,url'
    for workflow in ['daily-dashboard.yml','liquidity-recovery.yml','corporate-refresh.yml']:
        runs=json.loads(subprocess.check_output(['gh','run','list','--repo','0baselabkim-debug/qweasd','--workflow',workflow,'--limit','100','--json',fields],text=True))
        for r in runs:
            r['created_at_kst']=dt.datetime.fromisoformat(r['createdAt'].replace('Z','+00:00')).astimezone(kst).isoformat()
        workflows.append({'workflow':workflow,'target_date_created_runs':[r for r in runs if r['created_at_kst'][:10]==day],
                          'latest_schedule_runs':[r for r in runs if r['event']=='schedule'][:6]})
    attempts=[]
    for p in Path('operations/market-runs').glob('*.json'):
        v=json.loads(p.read_text())
        if v.get('trade_date')==day:attempts.append(v)
    ledger=json.loads(Path('snapshot/publication-ledger.json').read_text())
    snapshot=json.loads(Path('snapshot/manifest.json').read_text())
    planned=[dt.datetime.combine(target,dt.time(h,m),kst) for h,m in [(20,30),(23,30)]]
    result={'checked_at_utc':now.isoformat(),'target_trade_date':day,'snapshot_asof':snapshot['asof'],
        'publication':ledger.get(day),'market_attempts':attempts,'workflows':workflows,
        'daily_slots':[{'planned_at_kst':t.isoformat(),'time_state':'NOT_DUE' if now<t else 'DUE_NOT_PROOF_OF_EXECUTION'} for t in planned],
        'note':'Actions process success, collection, validation, Git publication and public browser verification are separate. Created date alone does not prove the target trading date.'}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['checked_at_utc','target_trade_date','snapshot_asof','daily_slots']},ensure_ascii=False))
if __name__=='__main__':report(sys.argv[1],Path(sys.argv[2]))
