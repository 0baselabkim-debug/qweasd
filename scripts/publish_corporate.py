"""Atomically prepared corporate sidecar; baseline annual scores never overwritten."""
import argparse,hashlib,json,shutil,sys,datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.corporate import load_state

def validate(old,new):
 for table in ['filings','financials']:
  for key,row in old[table].items():
   if new[table].get(key)!=row:raise ValueError('existing_'+table+'_changed')
 if new['events'][:len(old['events'])]!=old['events']:raise ValueError('audit_history_changed')
 for r in new['financials'].values():
  if r['receipt'] not in new['filings'] or r['currency']!='KRW' or r['scope'] not in ['CFS','OFS']:raise ValueError('invalid_financial_identity')
  if r['disclosed_on']<r['period_end']:raise ValueError('disclosure_before_period_end')
 for r in new['reviews'].values():
  if r['status']=='confirmed' and (not r.get('excerpt') or not r.get('reviewer') or not r.get('document_sha256')):raise ValueError('unverified_theme')

def main():
 p=argparse.ArgumentParser();p.add_argument('--validate-only',action='store_true');a=p.parse_args()
 old=load_state();new=load_state('corporate-candidate/state.json.gz');validate(old,new)
 if a.validate_only:print('Corporate append-only validation passed');return
 latest=new['runs'][-1] if new['runs'] else {}
 manifest={'updated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'filings':len(new['filings']),'financial_versions':len(new['financials']),
 'business_companies':len({new['filings'][k]['symbol'] for k,j in new['jobs'].items() if j.get('business_status')=='success'}),'universe':2430,
 'last_run':latest,'annual_score_policy':'preserved_2025_baseline','quarterly_score_policy':'hold_until_comparable_TTM_validation',
 'state_sha256':hashlib.sha256(Path('corporate-candidate/state.json.gz').read_bytes()).hexdigest()}
 Path('snapshot').mkdir(exist_ok=True)
 shutil.copy2('corporate-candidate/state.json.gz','snapshot/corporate-state.json.gz');shutil.copy2('corporate-candidate/corporate.duckdb','snapshot/corporate.duckdb')
 Path('snapshot/corporate-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in manifest.items() if k!='last_run'},ensure_ascii=False))
if __name__=='__main__':main()
