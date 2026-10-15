"""Separate, fail-closed technical rules. Never alters the screening snapshot."""
import json, math
from decimal import Decimal
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]

def rules():return json.loads((ROOT/'technical_rules.json').read_text())

def validate(bundle,asof=None,require_final=True):
    cfg=rules();issues=[]
    if not bundle:return ['검증된 OHLCV 미확보']
    if bundle.get('exchange')!='KRX' or bundle.get('adjustment')!='adjusted':issues.append('시장 범위·수정주가 기준 불일치')
    if require_final and (bundle.get('finality')!='VERIFIED' or not bundle.get('finality_evidence')):issues.append('장마감 최종 확정 근거 미확인')
    if bundle.get('price_conflicts'):issues.append('기존 원본과 종가 불일치 · 원본 보존')
    if asof and bundle.get('asof')!=str(asof):issues.append('시세·수급·조회 기준일 불일치')
    try:
        f=pd.DataFrame(bundle['bars'])
        if len(f)<cfg['min_bars']:issues.append(f"OHLCV {len(f)}/{cfg['min_bars']}거래일")
        dates=f.trade_date.tolist()
        if dates!=sorted(set(dates)) or dates[-1]!=bundle['asof']:issues.append('OHLCV 날짜 중복·순서·기준일 오류')
        if not bundle.get('calendar') or dates!=bundle['calendar'][-len(dates):]:issues.append('거래일 누락 또는 공식 거래일 검증 미완료')
        values=f[['open','high','low','close','volume','turnover']].astype(float)
        if not np.isfinite(values.to_numpy()).all():issues.append('OHLCV 결측·비정상 수치')
        if (values[['open','high','low','close']]<=0).any().any() or (values[['volume','turnover']]<0).any().any():issues.append('OHLCV 범위 오류')
        if ((values.high<values[['open','close','low']].max(axis=1))|(values.low>values[['open','close','high']].min(axis=1))).any():issues.append('고가·저가 범위 오류')
        if float(f.volume.iloc[-1])<=0:issues.append('최근 거래 없음·거래정지 확인 필요')
        flows=bundle.get('flows',[])
        if len(flows)<20 or [r['trade_date'] for r in flows[-20:]]!=dates[-20:] or len({r['trade_date'] for r in flows})!=len(flows):issues.append('동일 거래일 외국인 수급 20일 미확보')
        if any(r.get('net_amount') is None or not math.isfinite(float(r['net_amount'])) for r in flows):issues.append('외국인 수급 결측')
    except (KeyError,ValueError,TypeError,IndexError,AttributeError):issues.append('일봉·수급 스키마 오류')
    return list(dict.fromkeys(issues))

def indicators(bars,cfg=None):
    cfg=cfg or rules();f=pd.DataFrame(bars).copy()
    for c in ['open','high','low','close','volume','turnover']:f[c]=pd.to_numeric(f[c],errors='coerce')
    for n in cfg['moving_averages']:f[f'ma{n}']=f.close.rolling(n,min_periods=n).mean()
    i=cfg['ichimoku']
    midpoint=lambda n:(f.high.rolling(n,min_periods=n).max()+f.low.rolling(n,min_periods=n).min())/2
    f['tenkan']=midpoint(i['conversion']);f['kijun']=midpoint(i['base'])
    f['future_span_a']=(f.tenkan+f.kijun)/2;f['future_span_b']=midpoint(i['span_b'])
    f['cloud_a']=f.future_span_a.shift(i['displacement']);f['cloud_b']=f.future_span_b.shift(i['displacement'])
    # Plot-only backward displacement. Never read this future-leaking column in a signal.
    f['chikou_plot']=f.close.shift(-i['displacement'])
    tr=pd.concat([f.high-f.low,(f.high-f.close.shift()).abs(),(f.low-f.close.shift()).abs()],axis=1).max(axis=1)
    n=cfg['atr_period'];atr=pd.Series(np.nan,index=f.index,dtype=float)
    if len(f)>=n:
        atr.iloc[n-1]=tr.iloc[:n].mean()
        for j in range(n,len(f)):atr.iloc[j]=(atr.iloc[j-1]*(n-1)+tr.iloc[j])/n
    f['atr']=atr;f['prior_turnover20']=f.turnover.shift(1).rolling(20,min_periods=20).mean()
    f['recent_high']=f.high.rolling(120,min_periods=120).max();f['recent_low']=f.low.rolling(120,min_periods=120).min()
    return f

def levels(f,cfg=None):
    cfg=cfg or rules();c=cfg['levels'];atr=float(f.atr.iloc[-1]);close=float(f.close.iloc[-1])
    if not math.isfinite(atr) or atr<=0:return {'support':[],'resistance':[],'all':[]}
    left,right=c['pivot_left'],c['pivot_right'];start=max(left,len(f)-c['lookback']);pivots=[]
    for j in range(start,len(f)-right):
        window=f.iloc[j-left:j+right+1]
        for column,extreme in [('high',window.high.max()),('low',window.low.min())]:
            value=float(f.iloc[j][column])
            # Flat every-day highs/lows do not constitute independent reactions.
            if value==extreme and ((window[column]<value).any() if column=='high' else (window[column]>value).any()):pivots.append((value,j))
    clusters=[]
    for value,index in sorted(pivots):
        group=next((g for g in clusters if abs(value-np.mean([x[0] for x in g]))<=c['cluster_atr']*atr),None)
        if group is None:clusters.append([(value,index)])
        else:group.append((value,index))
    confirmed=[]
    for group in clusters:
        touches=[]
        for value,index in sorted(group,key=lambda x:x[1]):
            if not touches or index-touches[-1][1]>=c['touch_separation']:touches.append((value,index))
        if len(touches)<c['min_touches']:continue
        price=float(np.mean([x[0] for x in touches]));band=c['band_atr']*atr
        confluence=[k for k in ['ma20','ma60','kijun','cloud_a','cloud_b'] if pd.notna(f.iloc[-1][k]) and abs(f.iloc[-1][k]-price)<=band]
        confirmed.append({'price':price,'lower':price-band,'upper':price+band,'touches':len(touches),'confirmed_on':str(f.trade_date.iloc[max(x[1] for x in touches)+right]),'confluence':confluence})
    return {'support':sorted([x for x in confirmed if x['upper']<close],key=lambda x:-x['price'])[:2], 'resistance':sorted([x for x in confirmed if x['lower']>close],key=lambda x:x['price'])[:2], 'all':confirmed}

def reward_risk(entry,stop,target):
    return (target-entry)/(entry-stop) if all(math.isfinite(float(v)) for v in [entry,stop,target]) and 0<stop<entry<target else None

def buy_guide(bundle,asof=None,cfg=None):
    cfg=cfg or rules();errors=validate(bundle,asof)
    out={'status':'판정 보류','types':[],'reasons':errors,'asof':bundle.get('asof') if bundle else None,'rule_version':cfg['version']}
    if errors:return out
    f=indicators(bundle['bars'],cfg);r,p=f.iloc[-1],f.iloc[-2];lev=levels(f,cfg);prior=levels(f.iloc[:-1],cfg);b=cfg['buy']
    flows=[float(x['net_amount']) for x in bundle['flows'][-20:]];net5=sum(flows[-5:]);change=net5-sum(flows[-10:-5])
    stop=lev['support'][0]['lower']-b['stop_atr']*r.atr if lev['support'] else None
    target=lev['resistance'][0]['lower'] if lev['resistance'] else None
    rr=reward_risk(r.close,stop,target) if stop is not None and target is not None else None
    rr_ok=rr>=b['min_reward_risk'] if rr is not None else None
    two_above=all(bool((f.close.iloc[-2:]>f[k].iloc[-2:]).all()) for k in ['ma20','tenkan','kijun'])
    near=any(abs(p.low-p[k])<=b['near_atr']*p.atr for k in ['ma20','kijun'])
    breakout=bool(prior['resistance'] and r.close>prior['resistance'][0]['upper']+b['breakout_atr']*r.atr)
    checks={
      '추세전환형':{'3개 선 위 2일':two_above,'5일 수급 양수':net5>0,'5일 수급 개선':change>0,'손익비 2 이상':rr_ok},
      '눌림목형':{'20일선 > 60일선':r.ma20>r.ma60,'20일선 상승':r.ma20>f.ma20.iloc[-1-b['slope_days']],'현재 구름대 상단 위':r.close>max(r.cloud_a,r.cloud_b),'전일 지지 부근 조정·전일 고가 돌파':near and r.close>p.high,'5일 수급 양수':net5>0,'손익비 2 이상':rr_ok},
      '돌파형':{'확인 저항 돌파':breakout if prior['resistance'] else None,'거래대금 1.5배':r.turnover>=b['turnover_multiple']*r.prior_turnover20,'당일 수급 양수':flows[-1]>0,'5일 수급 양수':net5>0,'손익비 2 이상':rr_ok}}
    checks={k:{a:None if v is None else bool(v) for a,v in d.items()} for k,d in checks.items()}
    matches=[k for k,v in checks.items() if all(x is True for x in v.values())]
    unknown=any(all(x is not False for x in v.values()) and any(x is None for x in v.values()) for v in checks.values())
    out.update(status='매수' if matches else '판정 보류' if unknown else '관망',types=matches,checks=checks,levels=lev,entry=float(r.close),stop=stop,target=target,reward_risk=rr,indicators={k:None if pd.isna(r[k]) else float(r[k]) for k in ['ma5','ma20','ma60','ma120','tenkan','kijun','cloud_a','cloud_b','future_span_a','future_span_b','atr','recent_high','recent_low','prior_turnover20']})
    if unknown:out['reasons']=['지지·저항 또는 정상 손익비 미확인']
    return out

def sell_guide(bundle,position,state=None,cfg=None):
    cfg=cfg or rules();errors=validate(bundle);state=state or {};out={'status':'판정 보류','reasons':errors,'asof':bundle.get('asof') if bundle else None,'costs_applied':False}
    if errors:return out
    f=indicators(bundle['bars'],cfg);r=f.iloc[-1];c=cfg['sell'];entry=float(position['average_price']);date=str(position['buy_date'])
    if state.get('asof','')>bundle['asof']:
        out['reasons']=['저장된 위험 기준보다 오래된 시세'];return out
    if not bundle.get('corporate_actions_verified'):
        out['reasons']=['매수 이후 기업행사 검증 미완료'];return out
    if any(a['trade_date']>=date for a in bundle.get('corporate_actions',[])):
        out['reasons']=['매수 이후 기업행사 · 수량·평균단가 조정 확인 필요'];return out
    history=f[f.trade_date<date]
    if len(history)<cfg['min_bars']:out['reasons']=['매수 직전 160일 기준 지지·저항 미확보'];return out
    anchor=levels(history,cfg)
    if not anchor['support']:out['reasons']=['매수 시점 핵심 지지대 미확인'];return out
    floor=anchor['support'][0]['lower'];initial_stop=floor-c['support_break_atr']*float(history.atr.iloc[-1])
    if initial_stop>=entry:out['reasons']=['평균매수가 대비 초기 위험 구간 불일치'];return out
    held=f[f.trade_date>=date]
    if held.empty:out['reasons']=['매수 이후 확정 일봉 없음'];return out
    candidates=held.close.cummax()-c['trailing_atr']*held.atr
    trailing=max(float(candidates.max()),float(state.get('trailing_stop',0)))
    # Only activate the trailing exit after a two-risk-unit favorable close.
    activated=float(held.close.max())>=entry+cfg['buy']['min_reward_risk']*(entry-initial_stop) or bool(state.get('activated'))
    flows=[float(x['net_amount']) for x in bundle['flows'][-10:]]
    adverse=sum(flows[-5:])<0 and sum(flows[-5:])<sum(flows[:5])
    trend=bool((f.close.iloc[-2:]<f.ma20.iloc[-2:]).all() and (f.close.iloc[-2:]<f.kijun.iloc[-2:]).all() and adverse)
    # Support is anchored before entry and never reclassified using the breached current close.
    core=r.close<floor-c['support_break_atr']*r.atr
    target=anchor['resistance'][0]['lower'] if anchor['resistance'] else None
    reached=target is not None and (held.high.iloc[-c['resistance_failure_days']: ]>=target-c['resistance_near_atr']*r.atr).any()
    failed=reached and r.close<target and r.close<f.close.iloc[-2]
    profit_condition=bool(failed and (r.close-entry)/(entry-initial_stop)>=cfg['buy']['min_reward_risk'])
    reason='핵심 지지대 이탈' if core else '추세 훼손·수급 악화' if trend else '추적 손절' if activated and r.close<trailing else '저항 도달 후 상승 실패·손익비 2 이상' if profit_condition else None
    out.update(trailing_stop=trailing,activated=activated,initial_stop=initial_stop,target=target,close=float(r.close),gross_return=float(r.close/entry-1))
    if not reason:out.update(status='보유',reasons=[]);return out
    costs=c.get('costs')
    if costs is None:out['reasons']=[reason,'거래비용 미설정 · 순손익 분류 보류'];return out
    net=Decimal(str(r.close))*(1-Decimal(str(costs['sell_rate']))-Decimal(str(costs['tax_rate'])))-Decimal(str(position['average_price']))*(1+Decimal(str(costs['buy_rate'])))
    if net==0:out['reasons']=[reason,'거래비용 반영 순손익 0 · 분류 보류'];return out
    out.update(status='익절' if net>0 else '손절',reasons=[reason],costs_applied=True,net_per_share=float(net))
    return out
