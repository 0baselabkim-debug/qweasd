"""Independent arithmetic from received OHLCV; no synthetic production observations."""
import ast,hashlib,json,math,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.technical_batch import read,write,CACHE,PRIORITY
from screener.technical import indicators,buy_guide,levels,rules
from screener.store import load

def close(a,b):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-8)

def verify(b):
    bars=b['bars'];f=indicators(bars);r=f.iloc[-1];checks={}
    mid=lambda rows:(max(x['high'] for x in rows)+min(x['low'] for x in rows))/2
    expected={**{f'ma{n}':sum(x['close'] for x in bars[-n:])/n for n in [5,20,60,120]},'tenkan':mid(bars[-9:]),'kijun':mid(bars[-26:]),'cloud_a':(mid(bars[-35:-26])+mid(bars[-52:-26]))/2,'cloud_b':mid(bars[-78:-26]),'future_span_a':(mid(bars[-9:])+mid(bars[-26:]))/2,'future_span_b':mid(bars[-52:]),'recent_high':max(x['high'] for x in bars[-120:]),'recent_low':min(x['low'] for x in bars[-120:]),'prior_turnover20':sum(x['turnover'] for x in bars[-21:-1])/20}
    tr=[]
    for i,x in enumerate(bars):tr.append(max(x['high']-x['low'],abs(x['high']-bars[i-1]['close']),abs(x['low']-bars[i-1]['close'])) if i else x['high']-x['low'])
    atr=sum(tr[:14])/14
    for value in tr[14:]:atr=(atr*13+value)/14
    expected['atr']=atr
    checks.update({k:close(v,r[k]) for k,v in expected.items()})
    checks['chikou_is_plot_only']=math.isnan(r.chikou_plot) and close(f.chikou_plot.iloc[-27],bars[-1]['close'])
    # Independent pivot detection and grouped repeated reactions from original prices.
    cfg=rules()['levels'];points=[]
    for i in range(max(2,len(bars)-120),len(bars)-2):
        window=bars[i-2:i+3]
        hi=max(x['high'] for x in window);lo=min(x['low'] for x in window)
        if bars[i]['high']==hi and any(x['high']<hi for x in window):points.append((hi,i))
        if bars[i]['low']==lo and any(x['low']>lo for x in window):points.append((lo,i))
    groups=[]
    for price,i in sorted(points):
        match=None
        for g in groups:
            if abs(price-sum(x[0] for x in g)/len(g))<=cfg['cluster_atr']*atr:match=g;break
        if match is None:groups.append([(price,i)])
        else:match.append((price,i))
    refs=[]
    for g in groups:
        touches=[]
        for p,i in sorted(g,key=lambda x:x[1]):
            if not touches or i-touches[-1][1]>=cfg['touch_separation']:touches.append((p,i))
        if len(touches)>=cfg['min_touches']:
            center=sum(p for p,i in touches)/len(touches)
            refs.append((center,center-cfg['band_atr']*atr,center+cfg['band_atr']*atr,bars[max(i for p,i in touches)+2]['trade_date']))
    lv=levels(f);checks['pivot_levels']=len(refs)==len(lv['all']) and all(close(a[0],v['price']) and close(a[1],v['lower']) and close(a[2],v['upper']) and a[3]==v['confirmed_on'] for a,v in zip(refs,lv['all']))
    supports=sorted([x for x in refs if x[2]<bars[-1]['close']],reverse=True);resistance=sorted(x for x in refs if x[1]>bars[-1]['close'])
    stop=supports[0][1]-rules()['buy']['stop_atr']*atr if supports else None;target=resistance[0][1] if resistance else None;entry=bars[-1]['close']
    rr=(target-entry)/(entry-stop) if stop is not None and target is not None and 0<stop<entry<target else None
    checks['observed_reward_risk']=(rr is None and b.get('observed_reward_risk') is None) or (rr is not None and close(rr,b['observed_reward_risk']))
    # Prefix calculations must be invariant when later OHLCV is replaced.
    cutoff=len(bars)-5;prefix=indicators(bars[:cutoff]);altered=[dict(x) for x in bars]
    for x in altered[cutoff:]:
        for key in ['open','high','low','close']:x[key]*=2
    changed=indicators(altered)
    checks['no_future_reference']=all(close(prefix.iloc[-1][k],changed.iloc[cutoff-1][k]) for k in expected) and levels(prefix)==levels(changed.iloc[:cutoff])
    assert all(checks.values()),(b['symbol'],checks)
    return {'checks':checks,'indicators':expected,'levels':lv,'observed_reward_risk':rr}

def main():
    data=load();original=data['flows'];report={'summary':read(Path('technical/summary.json'),{}),'symbols':{},'preserved':{}}
    names=data['instruments'].set_index('symbol').name.to_dict()
    for code in PRIORITY:
        b=read(CACHE/(code+'.json.gz'))
        if not b:report['symbols'][code]={'name':names.get(code),'status':'not_collected'};continue
        result=verify(b) if b.get('indicator_ready') else {'checks':{},'reason':b['structure_errors']}
        stored=original[original.symbol==code].set_index('trade_date').net_amount
        result['flow_matches_original']=all(int(stored.loc[x['trade_date']])==x['net_amount'] for x in b['flows'])
        assert result['flow_matches_original']
        result.update(name=names.get(code),bars=len(b['bars']),first=b['bars'][0]['trade_date'],last=b['bars'][-1]['trade_date'],buy=buy_guide(b),price_comparison=b['price_comparison'],finality=b['finality'],corporate_actions=b['corporate_actions'])
        report['symbols'][code]=result
    paths=['snapshot/dashboard.duckdb','snapshot/corporate.duckdb','snapshot/macro.json','theme_mapping.json','config.toml','technical_rules.json','.github/workflows/daily-dashboard.yml','.github/workflows/corporate-refresh.yml','.github/workflows/macro-refresh.yml']
    for path in paths:
        if Path(path).exists():report['preserved'][path]=hashlib.sha256(subprocess.check_output(['git','show','d75da76:'+path])).hexdigest()==hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert all(report['preserved'].values())
    original_engine=ast.parse(subprocess.check_output(['git','show','141dcb7:screener/technical.py']).decode())
    current_engine=ast.parse(Path('screener/technical.py').read_text())
    functions=lambda tree:{n.name:ast.dump(n,include_attributes=False) for n in tree.body if isinstance(n,ast.FunctionDef) and n.name!='validate'}
    report['preserved']['technical_calculation_and_signal_functions']=functions(original_engine)==functions(current_engine)
    assert report['preserved']['technical_calculation_and_signal_functions']
    reference=read(Path('reports/technical-expansion/local-krx-crosscheck.json'),{})
    rows={r['ISU_CD']:r for m in reference.get('markets',{}).values() for r in m.get('samples',[])}
    for code in PRIORITY:
        b=read(CACHE/(code+'.json.gz'));r=rows.get(code)
        if b and r and r['BAS_DD']==b['asof'].replace('-',''):
            mapping={'open':'TDD_OPNPRC','high':'TDD_HGPRC','low':'TDD_LWPRC','close':'TDD_CLSPRC','volume':'ACC_TRDVOL','turnover':'ACC_TRDVAL'}
            checked=all(b['bars'][-1][k]==float(r[field]) for k,field in mapping.items())
            report['symbols'][code]['independent_krx_ohlcv_match']=checked
            assert checked
    report['cache_bytes']=sum(p.stat().st_size for p in CACHE.glob('*.gz'))
    write(Path('reports/technical-expansion/independent-verification.json'),report)
    print(json.dumps({'symbols_verified':len(report['symbols']),'preserved':report['preserved'],'cache_bytes':report['cache_bytes']},ensure_ascii=False))
if __name__=='__main__':main()
