"""Read-only inventory; reports existing coverage without importing old data."""
import collections
import gzip
import json
from pathlib import Path
import re
import duckdb


def audit(root):
    root=Path(root)
    files=[p for p in root.rglob('*') if p.is_file() and not any(x in p.relative_to(root).parts for x in ('.git','.venv','__pycache__','.pytest_cache','data'))]
    sources=[p for p in files if p.suffix in ('.py','.yml','.yaml','.toml','.md','.txt')]
    source_digest=__import__('hashlib').sha256()
    for p in sorted(sources):source_digest.update(p.read_bytes())
    schedules=[]
    for p in (root/'.github/workflows').glob('*.yml'):
        if re.search(r'^\s+(schedule:|(?:-\s*)?cron:)',p.read_text(),re.M):schedules.append(str(p.relative_to(root)))
    coverage={}
    with duckdb.connect(str(root/'snapshot/dashboard.duckdb'),read_only=True) as db:
        for table,column in [('flows','trade_date'),('prices','trade_date'),('benchmarks','trade_date'),('financials','disclosed_on'),('memberships','valid_from')]:
            lo,hi,n,days=db.execute(f'SELECT min({column}), max({column}), count(*), count(distinct {column}) FROM {table}').fetchone()
            coverage[table]=dict(first=str(lo),last=str(hi),rows=n,distinct_dates=days)
        coverage['market_budget_estimate']={}
        for market,n in db.execute('SELECT market,count(*) FROM instruments GROUP BY market').fetchall():coverage['market_budget_estimate'][market]=n
    refs=collections.Counter()
    for p in list((root/'liquidity/reference').rglob('*.json.gz'))+list((root/'auxiliary-reference-imports').glob('*.json.gz')):
        obj=json.loads(gzip.decompress(p.read_bytes()));refs[obj.get('asof','unknown')]+=1
    return dict(inventory_files=len(files),source_and_document_files_read=len(sources),source_inventory_sha256=source_digest.hexdigest(),active_schedule_definitions=schedules,preserved_coverage=coverage,reference_files_by_date=dict(sorted(refs.items())),new_market_collection_count=0,note='Date bounds do not establish continuous or validated historical coverage. No old rows imported.')


if __name__=='__main__':
    print(json.dumps(audit(Path(__file__).resolve().parents[1]),ensure_ascii=False,indent=2))
