"""Observed publication evidence only; future dates and rehearsals never count."""
import datetime as dt,json
from pathlib import Path
from zoneinfo import ZoneInfo

def summarize_runs(rows,today=None):
 today=today or str(dt.datetime.now(ZoneInfo('Asia/Seoul')).date())
 success={r['trade_date'] for r in rows if r.get('new_trading_day_published') and r.get('mode')=='collect' and r.get('published_at') and r.get('trade_date','9999')<=today}
 return {'actual_success_days':sorted(success),'verified_three_days':len(success)>=3,'attempts':len(rows)}

def read_runs(root=None):
 root=Path(root or Path(__file__).resolve().parents[1]/'operations/market-runs')
 return [json.loads(p.read_text()) for p in sorted(root.glob('*.json'))]
