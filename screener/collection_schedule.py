"""Korean collection attempts, independent of provider finality claims."""
import datetime as dt
from zoneinfo import ZoneInfo
ZONE=ZoneInfo('Asia/Seoul')
PRIMARY=dt.time(18,30)
RETRY=dt.time(21)
CRONS={'30 9 * * 1-5':PRIMARY,'0 12 * * 1-5':RETRY}

def target_date(now,created_at=None):
    # Bind delayed schedule jobs to their creation day, not the runner start day.
    stamp=created_at or now
    stamp=stamp.astimezone(ZONE)
    return (stamp-dt.timedelta(days=1)).date() if stamp.hour<9 else stamp.date()

def window(day,now):
    now=now.astimezone(ZONE)
    planned=dt.datetime.combine(day,PRIMARY,ZONE)
    cutoff=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(9),ZONE)
    if day.weekday()>4 or not planned<=now<cutoff:
        raise ValueError('Outside after-close collection window (18:30 KST; trading calendar also required)')
    return planned,cutoff
