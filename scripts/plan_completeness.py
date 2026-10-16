"""Estimate marginal indicator coverage from existing gap and offline audits."""
import json,sys,csv
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.technical_batch import read
from screener.technical_quality import daily_accepted

def plan(gaps,saved):
    if gaps['asof']!=saved['asof']:raise ValueError('Audit dates differ')
    reusable={(r['asof'],r['symbol']) for r in saved['tasks'] if r['status']=='SAVED_EVIDENCE_VALIDATED'}
    result=[]
    for s in gaps['sectors']:
        checks=s['remaining_checks'];codes=sorted({r['symbol'] for r in checks})
        if not checks:continue
        latest_needed=[];saved_latest=[]
        for code in codes:
            b=read(Path('technical/market')/(code+'.json.gz'),{})
            if daily_accepted(b):continue
            (saved_latest if (gaps['asof'],code) in reusable else latest_needed).append(code)
        saved_dates=sum((r['date'],r['symbol']) in reusable for r in checks)
        external_checks=len(checks)-saved_dates
        # Measured last phase: latest coherent rebuild 7, historical same-vintage 3.
        # Planning only: cannot promise recovery or replace any validation.
        estimated=3*external_checks+7*len(latest_needed)
        result.append({'market':s['market'],'sector':s['sector'],'members':s['members'],
          'complete_25d_symbols':s['complete_25d_symbols'],'potential_stock_gain':len(codes),
          'remaining_date_checks':len(checks),'saved_latest_candidates':saved_latest,
          'latest_needing_external_observation':latest_needed,'saved_date_candidates':saved_dates,
          'estimated_kis_market_requests':estimated,'upper_no_retry_market_requests':7*(external_checks+len(latest_needed)),
          'coverage_per_estimated_request':s['members']/estimated if estimated else None,
          'stock_gain_per_estimated_request':len(codes)/estimated if estimated else None,
          'auth_requests':'0 for offline-only run; otherwise up to 1 per run before renewal/retry',
          'krx_requests':'0 for offline-only run; otherwise 2 per distinct refreshed date, max 2 dates/run',
          'blocking_reasons':s['reason_symbol_counts'],'symbols':codes})
    return sorted(result,key=lambda r:-(r['coverage_per_estimated_request'] or 0))
if __name__=='__main__':
    gaps=json.loads(Path(sys.argv[1]).read_text());saved=json.loads(Path(sys.argv[2]).read_text());out=Path(sys.argv[3]);out.mkdir(parents=True,exist_ok=True)
    rows=plan(gaps,saved)
    (out/'priority-plan.json').write_text(json.dumps({'asof':gaps['asof'],'note':'Estimates exclude retries and are not promised success. Overlapping gaps are not additive.','sectors':rows},ensure_ascii=False,indent=2)+'\n')
    with (out/'priority-plan.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
