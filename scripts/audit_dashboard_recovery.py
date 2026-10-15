"""Read-only real-data recovery audit; only the evidence report is written."""
import collections,datetime as dt,gzip,hashlib,json,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.store import load
from screener.technical import validate,buy_guide,sell_guide
from screener.technical_quality import daily_accepted
from scripts.verify_technical_expansion import verify
out=Path('reports/functional-recovery');out.mkdir(exist_ok=True)
data=load();asof=str(data['flows'].trade_date.max().date());flow=data['flows'].set_index(['symbol','trade_date']).net_amount
report={'asof':asof,'base_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'new_market_api_calls':0,'cached_symbols':0,'accepted_daily':0,'data_gate_passed':0,'guide_counts':collections.Counter(),'hold_reasons':collections.Counter(),'independent_symbols':{},'raw_preservation':{}}
for path in sorted(Path('technical/market').glob('*.json.gz')):
 b=json.loads(gzip.decompress(path.read_bytes()));report['cached_symbols']+=1
 assert b['symbol']==path.name[:6]
 assert all(int(flow.loc[(b['symbol'],r['trade_date'])])==r['net_amount'] for r in b['flows'])
 result=buy_guide(b,asof);report['guide_counts'][result['status']]+=1
 report['accepted_daily']+=daily_accepted(b);report['data_gate_passed']+=not validate(b,asof)
 if result['status']=='판정 보류':report['hold_reasons'].update(result['reasons'])
 if b['symbol'] in ['005930','000660','035420','003280','003160','005380','034020']:
  independent=verify(b) if b.get('indicator_ready') else None
  sell=sell_guide(b,{'average_price':b['bars'][-1]['close'],'buy_date':b['bars'][min(170,len(b['bars'])-1)]['trade_date']})
  report['independent_symbols'][b['symbol']]={'bars':len(b['bars']),'arithmetic':independent['checks'] if independent else None,'buy_status':result['status'],'buy_reasons':result['reasons'],'sell_gate_example_only':sell,'not_a_real_position':True}
report['all_cached_flows_match_snapshot']=True
current_codes=set(data['flows'].loc[data['flows'].trade_date==data['flows'].trade_date.max(),'symbol'])
current={'target_symbols':len(current_codes),'accepted_daily':0,'data_gate_passed':0,'guide_counts':collections.Counter(),'older_cached_symbols_excluded':[]}
for path in sorted(Path('technical/market').glob('*.json.gz')):
 b=json.loads(gzip.decompress(path.read_bytes()))
 if b['symbol'] not in current_codes:
  current['older_cached_symbols_excluded'].append(b['symbol']);continue
 current['accepted_daily']+=daily_accepted(b)
 current['data_gate_passed']+=not validate(b,asof)
 current['guide_counts'][buy_guide(b,asof)['status']]+=1
report['current_asof']=current
for name in ['snapshot/dashboard.duckdb','snapshot/corporate.duckdb','snapshot/macro.json','theme_mapping.json','config.toml','technical_rules.json']:
 current=Path(name).read_bytes();baseline=subprocess.check_output(['git','show','HEAD:'+name]);assert current==baseline
 report['raw_preservation'][name]=hashlib.sha256(current).hexdigest()
report['technical_payload_changes']=subprocess.check_output(['git','diff','--name-only','HEAD','--','technical'],text=True).splitlines();assert not report['technical_payload_changes']
(out/'data-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ['independent_symbols','hold_reasons','raw_preservation']},ensure_ascii=False))
