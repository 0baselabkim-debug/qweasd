"""Prepare briefing before installing the validated snapshot for one Git commit."""
import datetime as dt,json,os,shutil
from pathlib import Path
from scripts.export_daily_briefing import export

def stage(candidate=Path('daily-candidate'),snapshot=Path('snapshot'),run_id=None,mode='collect'):
    candidate=Path(candidate);snapshot=Path(snapshot)
    if not (candidate/'manifest.json').exists():return False
    new=json.loads((candidate/'manifest.json').read_text());old=json.loads((snapshot/'manifest.json').read_text())
    if new['asof']<old['asof']:raise ValueError('Refuse date rollback')
    ledger=json.loads((snapshot/'publication-ledger.json').read_text())
    day=new['asof']
    if day in ledger:return False
    ledger[day]={'run_id':run_id,'mode':mode,'published_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'sha256':new['sha256'],'validation':new['daily_validation']}
    (candidate/'publication-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
    shutil.copy2(candidate/'validation.json',candidate/'daily-validation.json')
    # Any export error occurs before touching the last good publication.
    export(day,candidate/'daily_briefing.json',candidate)
    archive=snapshot/'publication-history'/old['sha256']
    archive.mkdir(parents=True,exist_ok=True)
    for name in ['dashboard.duckdb','manifest.json','daily-validation.json','publication-ledger.json','daily_briefing.json']:
        if (snapshot/name).exists() and not (archive/name).exists():shutil.copy2(snapshot/name,archive/name)
    for name in ['dashboard.duckdb','manifest.json','daily-validation.json','publication-ledger.json','daily_briefing.json']:
        shutil.copy2(candidate/name,snapshot/name)
    return True

if __name__=='__main__':
    try:
        if stage(run_id=os.environ['GITHUB_RUN_ID'],mode=os.environ.get('MODE','collect')):
            source=Path('daily-results/calendar-check.json')
            if source.exists():shutil.copy2(source,'snapshot/calendar-check.json')
    except Exception as e:
        Path('briefing-failure.json').write_text(json.dumps({'status':'failed','reason':str(e),'publication_aborted':True},ensure_ascii=False)+'\n')
        raise
