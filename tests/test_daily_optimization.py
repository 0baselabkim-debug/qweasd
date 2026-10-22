"""No synthetic fixture is stored in production snapshots."""
import copy,datetime as dt,json,sys
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from screener.collection_schedule import window,target_date,CRONS
from daily_checkpoint import Journal

KST=ZoneInfo('Asia/Seoul')
@pytest.mark.parametrize('stamp,accepted',[
 ('2026-10-16T18:29:59',False),('2026-10-16T18:30:00',True),
 ('2026-10-16T21:00:00',True),('2026-10-17T08:59:59',True),
 ('2026-10-17T09:00:00',False),('2026-10-16T08:00:00',False)])
def test_collection_window(stamp,accepted):
 now=dt.datetime.fromisoformat(stamp).replace(tzinfo=KST)
 if accepted:assert window(dt.date(2026,10,16),now)[0].hour==18
 else:
  with pytest.raises(ValueError):window(dt.date(2026,10,16),now)

def test_weekend_future_delayed_date():
 with pytest.raises(ValueError):window(dt.date(2026,10,17),dt.datetime(2026,10,17,19,tzinfo=KST))
 with pytest.raises(ValueError):window(dt.date(2026,10,19),dt.datetime(2026,10,16,19,tzinfo=KST))
 assert target_date(dt.datetime(2026,10,19,19,tzinfo=KST),dt.datetime(2026,10,16,18,40,tzinfo=KST))==dt.date(2026,10,16)
 assert set(CRONS)=={'30 9 * * 1-5','0 12 * * 1-5'}

def empty():return {'records':{},'excluded':{},'failures':{'000001':'old failure'}}
def test_success_journal_resume_hash_date_and_truncation(tmp_path):
 p=tmp_path/'successes.jsonl';j=Journal(p,'20261016');j.append('000001',{'flow':{'value':1}})
 with p.open('ab') as f:f.write(b'{"truncated":')
 b=empty();assert j.restore(b)==1 and not b['failures']
 assert j.restore(b)==0
 with pytest.raises(ValueError):Journal(p,'20261015').restore(empty())
 p.write_text(p.read_text().replace('"value":1','"value":2'))
 with pytest.raises(ValueError,match='hash/date'):j.restore(empty())

def test_operation_times_and_no_false_manual_schedule_success():
 from scripts.record_market_run import make_record
 env={'DAY':'20261016','EVENT':'schedule','SCHEDULE':'0 12 * * 1-5'}
 r=make_record(env,{},{});assert r['planned_start_at']=='2026-10-16T21:00:00+09:00' and not r['new_trading_day_published']
 env['SCHEDULE']='30 9 * * 1-5';assert '18:30' in make_record(env,{},{})['planned_start_at']
 env['EVENT']='workflow_dispatch';assert make_record(env,{},{})['planned_start_at'] is None

def test_only_active_daily_schedule_is_new_slots():
 text=Path('.github/workflows/daily-dashboard.yml').read_text()
 assert "cron: '30 9 * * 1-5'" in text and "cron: '0 12 * * 1-5'" in text
 assert "cron: '30 11 * * 1-5'" not in text and "cron: '30 14 * * 1-5'" not in text
 assert 'kis-validation-auth' in text and 'cancel-in-progress: false' in text

from test_daily import bundle
from screener.daily_quote import chart_quote
from collect_daily_snapshot import fetch_stock

def enrich(bundle):
 b=copy.deepcopy(bundle);r=b['records']['000001'];r['adjusted_prices'][0]['acml_tr_pbmn']=r['flow']['acml_tr_pbmn']
 r['quote']['acml_tr_pbmn']=r['flow']['acml_tr_pbmn']
 return b,r

class API:
 def __init__(self,r,missing=False):self.r=r;self.calls=[];self.missing=missing
 def prices(self,*args):
  self.calls.append('chart');q=copy.deepcopy(self.r['quote'])
  if self.missing:q.pop('hts_avls')
  return {'output1':q,'output2':self.r['adjusted_prices']}
 def request(self,path,*args):
  self.calls.append(path)
  return {'output2':[self.r['flow']]} if path.endswith('investor-trade-by-stock-daily') else {'output':self.r['quote']}

def test_two_calls_and_quote_fallback_use_all_original_checks(bundle,monkeypatch):
 import collect_daily_snapshot as c
 b,r=enrich(bundle)
 monkeypatch.setattr(c,'utcnow',lambda:'2026-10-15T18:31:00+09:00')
 for missing,n in [(False,2),(True,3)]:
  api=API(r,missing)
  _,record,excluded,error=fetch_stock(b['universe'][0],api,b['trade_date'],'20260816',b,set(),dt.datetime(2099,1,1,tzinfo=KST),float('inf'))
  assert not error and record and not excluded
  assert len(api.calls)==n
  assert all(record['quote'][k]==v for k,v in r['quote'].items())
  assert bool(record['quote_fallback_reasons'])==missing

@pytest.mark.parametrize('field',['stck_clpr','acml_vol','acml_tr_pbmn'])
def test_dated_conflict_cannot_be_fixed_by_undated_quote(bundle,field):
 b,r=enrich(bundle);api=API(r)
 r['adjusted_prices'][0][field]='999'
 _,record,_,error=fetch_stock(b['universe'][0],api,b['trade_date'],'20260816',b,set(),dt.datetime(2099,1,1,tzinfo=KST),float('inf'))
 assert error and record is None and len(api.calls)==2

@pytest.mark.parametrize('field,value',[('hts_avls','1'),('lstn_stcn','1'),('stck_shrn_iscd','999999'),('acml_tr_pbmn','9')])
def test_bad_fallback_remains_blocked(bundle,monkeypatch,field,value):
 import collect_daily_snapshot as c
 b,r=enrich(bundle);r['quote'][field]=value
 monkeypatch.setattr(c,'utcnow',lambda:'2026-10-15T18:31:00+09:00')
 _,record,_,error=fetch_stock(b['universe'][0],API(r),b['trade_date'],'20260816',b,set(),dt.datetime(2099,1,1,tzinfo=KST),float('inf'))
 assert error and record is None

def test_expired_budget_makes_no_stock_requests(bundle):
 b,r=enrich(bundle);api=API(r)
 _,record,_,error=fetch_stock(b['universe'][0],api,b['trade_date'],'20260816',b,set(),dt.datetime(2099,1,1,tzinfo=KST),0)
 assert error and record is None and api.calls==[]

def test_normal_complete_checkpoint_makes_no_auth(tmp_path,monkeypatch):
 import collect_daily_snapshot as c,gzip
 day=dt.datetime.now(KST).date()
 monkeypatch.setattr(c,'window',lambda day,now:(now,now+dt.timedelta(hours=4)))
 b={'trade_date':day.strftime('%Y%m%d'),'exchange':'KRX','status':'complete','records':{'000001':{'ok':True}},'excluded':{},'failures':{}}
 (tmp_path/'bundle.json.gz').write_bytes(gzip.compress(json.dumps(b).encode()))
 monkeypatch.setattr(c,'KIS',lambda:pytest.fail('No authentication for complete checkpoint'))
 c.collect(b['trade_date'],tmp_path)

def test_partial_retries_only_failed_stock_and_preserves_success(bundle,tmp_path,monkeypatch):
 import collect_daily_snapshot as c,gzip
 b,r=enrich(bundle);b['status']='partial';b['holiday']={'opnd_yn':'Y'};b['kind_industries']={};b['records']={};b['universe'].append({**b['universe'][0],'symbol':'000002'})
 (tmp_path/'bundle.json.gz').write_bytes(gzip.compress(json.dumps(b).encode()))
 monkeypatch.setattr(c,'window',lambda day,now:(now,dt.datetime(2099,1,1,tzinfo=KST)))
 monkeypatch.setattr(c,'utcnow',lambda:'2026-10-15T18:31:00+09:00')
 calls=[];bad=[True]
 class Whole(API):
  def __init__(self):self.metrics=[];self.key='never-a-response-key';self.secret='never-a-response-secret';self.token='never-a-response-token'
  def prices(self,code,*args):
   calls.append((code,'chart'));q=copy.deepcopy(r['quote']);q['stck_shrn_iscd']=code
   return {'output1':q,'output2':r['adjusted_prices']}
  def request(self,path,params,tr):
   code=params.get('FID_INPUT_ISCD');calls.append((code,'flow'))
   f=copy.deepcopy(r['flow'])
   if code=='000002' and bad[0]:f['frgn_ntby_qty']='999'
   return {'output2':[f]}
 monkeypatch.setattr(c,'KIS',Whole)
 with pytest.raises(RuntimeError,match='Partial'):c.collect('20261015',tmp_path)
 saved=json.loads(gzip.decompress((tmp_path/'bundle.json.gz').read_bytes()))
 assert set(saved['records'])=={'000001'} and set(saved['failures'])=={'000002'}
 first=copy.deepcopy(saved['records']['000001']);calls.clear();bad[0]=False
 c.collect('20261015',tmp_path)
 assert {code for code,_ in calls}=={'000002'} and len(calls)==2
 saved=json.loads(gzip.decompress((tmp_path/'bundle.json.gz').read_bytes()))
 assert saved['status']=='complete' and saved['records']['000001']==first and not saved['failures']
 calls.clear();c.collect('20261015',tmp_path);assert calls==[]

def test_closed_day_no_stock_calls(bundle,tmp_path,monkeypatch):
 import collect_daily_snapshot as c,gzip
 b=copy.deepcopy(bundle);b.update(status='partial',records={},holiday={'opnd_yn':'N'})
 (tmp_path/'bundle.json.gz').write_bytes(gzip.compress(json.dumps(b).encode()))
 monkeypatch.setattr(c,'window',lambda day,now:(now,dt.datetime(2099,1,1,tzinfo=KST)))
 class Closed:
  key='never-a-response-key';secret='never-a-response-secret';token='never-a-response-token';metrics=[]
  def request(self,*a):pytest.fail('No market requests on confirmed closed day')
 monkeypatch.setattr(c,'KIS',Closed);c.collect('20261015',tmp_path)
 assert json.loads(gzip.decompress((tmp_path/'bundle.json.gz').read_bytes()))['status']=='closed'

def test_industry_fallback_keeps_original_quote_priority(bundle,monkeypatch):
 import collect_daily_snapshot as c
 b,r=enrich(bundle);b['universe'][0]['sector']=None;b['kind_industries']={'000001':'different fallback'}
 r['quote']['bstp_kor_isnm']='original quote industry'
 monkeypatch.setattr(c,'utcnow',lambda:'2026-10-15T18:31:00+09:00')
 api=API(r)
 _,record,_,error=fetch_stock(b['universe'][0],api,b['trade_date'],'20260816',b,set(),dt.datetime(2099,1,1,tzinfo=KST),float('inf'))
 assert not error and len(api.calls)==3 and record['quote']['bstp_kor_isnm']=='original quote industry'

def test_unready_benchmark_is_not_a_durable_success():
 from collect_daily_snapshot import benchmark_ready
 assert not benchmark_ready({'output2':[{'stck_bsop_date':'20261015','bstp_nmix_prpr':'100'}]},'20261016')
 assert benchmark_ready({'output2':[{'stck_bsop_date':'20261016','bstp_nmix_prpr':'100'}]},'20261016')
 assert not benchmark_ready({'output2':[{'stck_bsop_date':'20261016','bstp_nmix_prpr':'nan'}]},'20261016')

def test_physical_metrics_distinguish_auth_retry_and_cached_success():
 from daily_metrics import summarize
 metrics=[{'kind':'authentication','attempt':1},
          {'symbol':'000001','endpoint':'flow','attempt':1,'http':200},
          {'symbol':'000001','endpoint':'price','attempt':1,'http':429},
          {'symbol':'000001','endpoint':'price','attempt':2,'http':200}]
 d=summarize(metrics,{'000001':{},'000002':{}})
 assert d['physical_requests']==4 and d['authentication']==1 and d['retries']==1
 assert d['more_than_two_request_symbols']=={'000001':3}
 assert d['successful_with_two_physical_requests']==0 and d['successful_without_request_this_attempt']==1
 assert d['rate_limit_responses']==1

def test_checkpoint_git_publish_keeps_running_checkout_and_resumes(tmp_path):
 import subprocess,gzip
 from persist_daily_checkpoint import persist
 remote=tmp_path/'remote.git';repo=tmp_path/'checkout'
 def run(*args,cwd=None):return subprocess.check_output(['git',*args],cwd=cwd,stderr=subprocess.DEVNULL).decode().strip()
 run('init','--bare',str(remote));run('clone',str(remote),str(repo))
 run('config','user.name','fixture',cwd=repo);run('config','user.email','fixture@example.invalid',cwd=repo)
 (repo/'code.txt').write_text('unchanged source');run('add','code.txt',cwd=repo);run('commit','-m','fixture',cwd=repo)
 head=run('rev-parse','HEAD',cwd=repo)
 (repo/'daily-results').mkdir();p=repo/'daily-results/bundle.json.gz';p.write_bytes(gzip.compress(b'{"records":{}}'))
 (repo/'unrelated.txt').write_text('preserve')
 first=persist(repo,'20261016');assert first['status']=='saved'
 assert run('rev-parse','HEAD',cwd=repo)==head and (repo/'code.txt').read_text()=='unchanged source'
 assert persist(repo,'20261016')['status']=='unchanged'
 p.write_bytes(gzip.compress(b'{"records":{"000001":{}}}'))
 (repo/'daily-results/successes.jsonl').write_text('journal fixture\n')
 second=persist(repo,'20261016');assert second['status']=='saved' and second['commit']!=first['commit']
 assert run('rev-parse',second['commit']+'^',cwd=repo)==first['commit']
 assert run('rev-parse','HEAD',cwd=repo)==head and (repo/'unrelated.txt').read_text()=='preserve'
 assert set(run('ls-tree','--name-only',second['commit']+':daily-results',cwd=repo).splitlines())=={'bundle.json.gz','successes.jsonl.gz','successes.manifest.json'}
