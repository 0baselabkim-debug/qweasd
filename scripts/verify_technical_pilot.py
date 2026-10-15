"""Independent market-data arithmetic audit, no personal journal access."""
import gzip,hashlib,json,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from screener.technical import buy_guide,indicators
from screener.store import load
raw=json.loads(gzip.decompress(Path('technical/pilot.json.gz').read_bytes()));data=load();report={'run_id':raw['run_id'],'asof':raw['asof'],'requests':raw['requests'],'elapsed_seconds':raw['elapsed_seconds'],'rate_limit_errors':raw['rate_limit_errors'],'universe_symbols':int(data['flows'].loc[data['flows'].trade_date==data['flows'].trade_date.max(),'symbol'].nunique()),'symbols':{}}
for code,b in raw['bundles'].items():
    bars=b['bars'];f=indicators(bars);checks={}
    for n in [5,20,60,120]:checks['ma'+str(n)]=abs(sum(r['close'] for r in bars[-n:])/n-f.iloc[-1]['ma'+str(n)])<1e-8
    mid=lambda seq:(max(r['high'] for r in seq)+min(r['low'] for r in seq))/2
    checks.update(tenkan=mid(bars[-9:])==f.tenkan.iloc[-1],kijun=mid(bars[-26:])==f.kijun.iloc[-1],current_span_b=mid(bars[-78:-26])==f.cloud_b.iloc[-1],future_span_b=mid(bars[-52:])==f.future_span_b.iloc[-1])
    tr=[max(r['high']-r['low'],abs(r['high']-bars[i-1]['close']),abs(r['low']-bars[i-1]['close'])) if i else r['high']-r['low'] for i,r in enumerate(bars)]
    atr=sum(tr[:14])/14
    for value in tr[14:]:atr=(atr*13+value)/14
    checks['atr14']=abs(atr-f.atr.iloc[-1])<1e-8
    actual=data['flows'][data['flows'].symbol==code].set_index(data['flows'][data['flows'].symbol==code].trade_date.dt.strftime('%Y-%m-%d')).net_amount.to_dict()
    checks['saved_flow_20d_matches']=all(actual.get(r['trade_date'])==r['net_amount'] for r in b['flows'])
    checks={k:bool(v) for k,v in checks.items()}
    assert all(checks.values()),(code,checks)
    report['symbols'][code]={'start':bars[0]['trade_date'],'end':bars[-1]['trade_date'],'bars':len(bars),'missing_or_duplicate_dates':len(b['calendar'])-len({r['trade_date'] for r in bars}),'ohlcv_structurally_valid':b['structure_valid'],'buy_status':buy_guide(b)['status'],'signal_blockers':b['validation'],'independent_checks':checks,'indicators':b['computed']}
paths=['snapshot/dashboard.duckdb','snapshot/corporate.duckdb','snapshot/macro.json','theme_mapping.json','config.toml','.github/workflows/daily-dashboard.yml','.github/workflows/corporate-refresh.yml','.github/workflows/macro-refresh.yml']
report['preserved']={}
for p in paths:
    if not Path(p).exists():continue
    before=subprocess.check_output(['git','show','d93b23c:'+p]);after=Path(p).read_bytes();report['preserved'][p]=hashlib.sha256(before).hexdigest()==hashlib.sha256(after).hexdigest()
assert all(report['preserved'].values())
report['technical_coverage_percent']=len(report['symbols'])/report['universe_symbols']*100;report['signal_ready_symbols']=0
out=Path('reports/trading-guidelines');out.mkdir(parents=True,exist_ok=True);(out/'actual-data.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:report[k] for k in ['technical_coverage_percent','signal_ready_symbols','preserved']},ensure_ascii=False))
