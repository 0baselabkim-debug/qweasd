"""Apply audited reviews using saved official business excerpts, without an API call."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.corporate import apply_review,load_state,save_state,build_database

def main():
 p=argparse.ArgumentParser();p.add_argument('--decisions',required=True,type=Path);p.add_argument('--business-cache',type=Path);p.add_argument('--state',default='corporate-candidate/state.json.gz');p.add_argument('--db',default='corporate-candidate/corporate.duckdb');a=p.parse_args()
 s=load_state(a.state)
 for decision in json.loads(a.decisions.read_text()):
  text=None
  if a.business_cache:
   source=a.business_cache/(decision['receipt']+'.txt')
   if source.exists():text=source.read_text()
  apply_review(s,decision,text)
 save_state(s,a.state);build_database(s,a.db)
 print('Reviews recorded with before/after history; publication validation still required')
if __name__=='__main__':main()
