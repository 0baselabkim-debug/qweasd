"""Public evidence only; synthetic mutations never enter production storage."""
import copy,gzip,json,sys
from pathlib import Path
import pandas as pd
import pytest
from screener.liquidity_quality import daily_accepted,digest
from screener.market_validation import validate
from screener.liquidity_evidence import permits,price_identity
from screener.store import load,PACKAGED_DB
from scripts.market_io import read

@pytest.fixture
def evidence():
 d=load(PACKAGED_DB)
 day,items=next(iter(d['metadata']['liquidity_price_evidence'].items()));code,e=next(iter(items.items()))
 row=d['prices'][(d['prices'].symbol==code)&(d['prices'].trade_date==pd.Timestamp(day))].iloc[0].to_dict()
 return row,copy.deepcopy(e),day

def test_migrated_evidence_has_no_trading_inputs(evidence):
 row,e,day=evidence;b=e['bundle']
 assert len(b['bars'])==25 and len(b['flows'])==20
 assert b['finality']=='UNVERIFIED' and daily_accepted(b) and not validate(b,day)
 assert permits(row,e,day)
 assert not set(b)&{'computed','observed_levels','observed_reward_risk','corporate_actions','guide'}

@pytest.mark.parametrize('mutation',[
 lambda b:b['bars'][-1].update(turnover=b['bars'][-1]['turnover']+1),
 lambda b:b['price_comparison'].update(kis_unadjusted=0),
 lambda b:b['daily_acceptance']['raw_flow_rows'][0].update(frgn_ntby_tr_pbmn='999999999'),
 lambda b:b.update(exchange='UN'),
 lambda b:b.update(asof='2026-10-13'),
 lambda b:b['daily_acceptance'].update(observed_at=b['asof']+'T06:00:00+00:00'),
 lambda b:b['bars'].pop(2),
 lambda b:b['bars'][-1].update(high=0),
])
def test_corrupt_or_wrong_date_evidence_stays_blocked(evidence,mutation):
 row,e,day=evidence;mutation(e['bundle']);e['bundle_sha256']=digest(e['bundle'])
 assert not permits(row,e,day)

def test_original_identity_and_historical_date_are_required(evidence):
 row,e,day=evidence
 assert not permits(row,e,'2026-10-01')
 row['turnover']+=1
 assert not permits(row,e,day)

def test_app_and_workflows_do_not_call_removed_features():
 import ast
 app=Path('app.py').read_text();ast.parse(app)
 for token in ['journal_ui','technical_guide','buy_guide','sell_guide','SUPABASE','투자 적격성']:
  assert token not in app
 for name in ['screener/technical.py','screener/journal_ui.py','technical_rules.json','.github/workflows/technical-refresh.yml','.github/workflows/technical-verify.yml']:
  assert not Path(name).exists()
 workflow=Path('.github/workflows/liquidity-recovery.yml').read_text()
 assert 'kis-validation-auth' in workflow and 'cancel-in-progress: false' in workflow
 assert 'recover_liquidity.py' in workflow and 'SUPABASE' not in workflow
 daily=Path('.github/workflows/daily-dashboard.yml').read_text()
 assert "cron: '30 9 * * 1-5'" in daily and "cron: '0 12 * * 1-5'" in daily
 assert "cron: '30 11 * * 1-5'" not in daily and "cron: '30 14 * * 1-5'" not in daily

def test_already_published_proof_does_not_request_any_api(evidence,tmp_path,monkeypatch):
 from scripts import recover_liquidity as recovery
 row,e,day=evidence;d=load(PACKAGED_DB)
 monkeypatch.setattr(recovery,'ROOT',tmp_path)
 class API:
  metrics=[]
  def request(self,*a,**k):raise AssertionError('verified same-date cache must not request')
 # Limit to a single already-published date, without introducing another dated task.
 d['metadata']['adjusted_price_conflicts']={day:[{'symbol':row['symbol']}]}
 r=recovery.run(d,api=API(),symbols={row['symbol']})
 assert not r['results'] and r['physical_requests']==0

def test_failed_recent_work_does_not_starve_older_tasks(evidence):
 from scripts.recover_liquidity import tasks_for
 row,e,day=evidence;d=load(PACKAGED_DB);now='2026-10-16T08:00:00+00:00'
 all_tasks=tasks_for(d,{'jobs':{}},now)
 assert len(all_tasks)>1
 key=all_tasks[0][1]
 remaining=tasks_for(d,{'jobs':{key:{'retry_after':'2099-01-01T00:00:00+00:00'}}},now)
 assert len(remaining)==len(all_tasks)-1 and all(x[1]!=key for x in remaining)

def test_short_window_candidate_is_checked_not_automatically_accepted(evidence):
 from scripts.recover_liquidity import candidate
 row,e,day=evidence;b=e['bundle'];bar=b['bars'][-1]
 class API:
  def request(self,*a,**k):return {'output2':[]}
 with pytest.raises(ValueError,match='incomplete_25_day_window'):candidate(API(),b,{b['symbol']:b['daily_acceptance']['krx_row']})

@pytest.mark.parametrize('bad_flow',[False,True])
def test_new_25_day_collection_requires_full_price_and_flow_evidence(evidence,bad_flow):
 from scripts.recover_liquidity import candidate
 row,e,day=evidence;b=e['bundle']
 fields={'open':'stck_oprc','high':'stck_hgpr','low':'stck_lwpr','close':'stck_clpr','volume':'acml_vol','turnover':'acml_tr_pbmn'}
 class API:
  def request(self,path,params,tr):
   if 'itemchart' in path:
    bars=b['bars'][-1:] if params['FID_ORG_ADJ_PRC']=='1' else b['bars']
    return {'output2':[{'stck_bsop_date':r['trade_date'].replace('-',''),**{v:str(r[k]) for k,v in fields.items()}} for r in bars]}
   raw=copy.deepcopy(b['daily_acceptance']['raw_flow_rows'])
   if bad_flow:raw[-1]['frgn_ntby_tr_pbmn']=str(int(raw[-1]['frgn_ntby_tr_pbmn'])+1)
   return {'output2':raw}
 base={k:copy.deepcopy(b[k]) for k in ['symbol','market','asof','calendar','exchange','adjustment','finality','flows']};base['price_comparison']={};base['price_conflicts']=[]
 proposed=candidate(API(),base,{b['symbol']:b['daily_acceptance']['krx_row']})
 assert daily_accepted(proposed) is not bad_flow
 assert bool(validate(proposed,day)) is bad_flow
 assert proposed['finality']=='UNVERIFIED' and len(proposed['bars'])==25

def test_app_reloads_stale_evidence_validator_on_hot_deployment(monkeypatch):
 import screener.liquidity_evidence as evidence_module
 from streamlit.testing.v1 import AppTest
 def removed(*a,**k):raise FileNotFoundError('removed legacy rule must not be called')
 monkeypatch.setattr(evidence_module,'validate',removed)
 app=AppTest.from_file(str(Path('app.py').resolve()),default_timeout=120).run()
 assert not app.exception
 assert evidence_module.validate is not removed
 app.sidebar.button[1].click().run()
 assert not app.exception
