"""Synthetic boundary fixtures stay in tests; independent real-data checks use public pilot."""
import copy,gzip,json,math
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from screener import technical as t

@pytest.fixture
def bundle():
    # Business-day fixture is synthetic, not a production trading calendar.
    dates=pd.bdate_range('2024-01-01',periods=360).strftime('%Y-%m-%d').tolist()
    bars=[]
    for i,d in enumerate(dates):
        close=100+i*.08+5*math.sin(i/5)
        bars.append(dict(trade_date=d,open=close-.2,high=close+1,low=close-1,close=close,volume=1000,turnover=close*1000))
    return dict(symbol='999999',asof=dates[-1],exchange='KRX',adjustment='adjusted',bars=bars,calendar=dates,flows=[{'trade_date':d,'net_amount':1 if i%2 else -1} for i,d in enumerate(dates[-20:])],finality='VERIFIED',finality_evidence='Synthetic unit test fixture only',price_conflicts=[])

def test_sma_ichimoku_wilder_independent(bundle):
    f=t.indicators(bundle['bars']);bars=bundle['bars'];last=f.iloc[-1]
    for n in [5,20,60,120]:assert last[f'ma{n}']==pytest.approx(sum(b['close'] for b in bars[-n:])/n)
    mid=lambda subset:(max(b['high'] for b in subset)+min(b['low'] for b in subset))/2
    assert last.tenkan==mid(bars[-9:]);assert last.kijun==mid(bars[-26:])
    assert last.future_span_b==mid(bars[-52:]);assert last.cloud_b==mid(bars[-78:-26])
    assert last.cloud_a==(mid(bars[-35:-26])+mid(bars[-52:-26]))/2
    tr=[]
    for i,b in enumerate(bars):tr.append(max(b['high']-b['low'],abs(b['high']-bars[i-1]['close']),abs(b['low']-bars[i-1]['close'])) if i else b['high']-b['low'])
    atr=sum(tr[:14])/14
    for value in tr[14:]:atr=(atr*13+value)/14
    assert last.atr==pytest.approx(atr);assert pd.isna(last.chikou_plot)
    assert f.chikou_plot.iloc[0]==bars[26]['close']
    assert last.prior_turnover20==pytest.approx(sum(b['turnover'] for b in bars[-21:-1])/20)

@pytest.mark.parametrize('mutation',[lambda b:b.update(finality='UNVERIFIED'),lambda b:b.update(price_conflicts=['x']),lambda b:b.update(exchange='UN'),lambda b:b['bars'].pop(12),lambda b:b['flows'][-1].update(net_amount=None),lambda b:b['flows'][-1].update(trade_date='2020-01-01'),lambda b:b['bars'][-1].update(high=1)])
def test_invalid_data_never_hold_or_buy(bundle,mutation):
    mutation(bundle);assert t.buy_guide(bundle)['status']=='판정 보류';assert t.sell_guide(bundle,{'average_price':100,'buy_date':bundle['bars'][200]['trade_date']})['status']=='판정 보류'

def test_no_future_pivots_and_touch_confirmation(bundle):
    f=t.indicators(bundle['bars']);before=t.levels(f.iloc[:250]);changed=f.copy();changed.loc[250:,'high']=99999
    assert t.levels(changed.iloc[:250])==before
    for level in before['all']:assert level['touches']>=2 and level['confirmed_on']<=f.trade_date.iloc[249]
    flat=f.copy();flat['high']=100;flat['low']=100;flat['close']=100;flat['atr']=1
    assert t.levels(flat)['all']==[]

def test_reward_risk_geometry():
    assert t.reward_risk(100,90,120)==2
    for entry,stop,target in [(100,100,120),(100,101,120),(100,90,99),(100,0,120)]:assert t.reward_risk(entry,stop,target) is None

def test_buy_conditions_three_types_and_nonoverlap(bundle,monkeypatch):
    f=t.indicators(bundle['bars']);f.loc[f.index[-7:],'ma20']=100;f.loc[f.index[-2:],'ma20']=102
    for col,val in [('ma60',90),('tenkan',101),('kijun',102),('cloud_a',95),('cloud_b',96),('atr',2),('prior_turnover20',100)]:f[col]=val
    f.loc[f.index[-2],['close','low','high']]=[104,102,105];f.loc[f.index[-1],['close','turnover']]=[110,150]
    monkeypatch.setattr(t,'indicators',lambda *args:f)
    def fake_levels(frame,cfg):
        return {'support':[{'lower':106}], 'resistance':[{'lower':120,'upper':108 if len(frame)<len(f) else 121}], 'all':[]}
    monkeypatch.setattr(t,'levels',fake_levels)
    for i,r in enumerate(bundle['flows']):r['net_amount']=2 if i>=15 else 1
    result=t.buy_guide(bundle);assert result['status']=='매수';assert result['types']==['추세전환형','눌림목형','돌파형']
    for r in bundle['flows'][-5:]:r['net_amount']=-1
    result=t.buy_guide(bundle);assert result['status']=='관망';assert not result['checks']['추세전환형']['5일 수급 개선']

def test_sell_priority_cost_unknown_loss_profit_and_trailing(bundle,monkeypatch):
    f=t.indicators(bundle['bars']);f['atr']=2;f['ma20']=160;f['kijun']=160;f.loc[f.index[-1],'close']=130
    monkeypatch.setattr(t,'indicators',lambda *args:f)
    monkeypatch.setattr(t,'levels',lambda *args:{'support':[{'lower':140}], 'resistance':[{'lower':180}], 'all':[]})
    position={'average_price':150,'buy_date':bundle['bars'][200]['trade_date']}
    r=t.sell_guide(bundle,position);assert r['status']=='판정 보류' and '거래비용' in r['reasons'][-1]
    cfg=t.rules();cfg['sell']['costs']={'buy_rate':0,'sell_rate':0,'tax_rate':0}
    r=t.sell_guide(bundle,position,cfg=cfg);assert r['status']=='손절' and r['reasons']==['핵심 지지대 이탈']
    old=r['trailing_stop']+20;r=t.sell_guide(bundle,position,{'trailing_stop':old,'activated':True},cfg);assert r['trailing_stop']>=old
    # Positive-price liquidation is profit classification, even if structural break triggered it.
    monkeypatch.setattr(t,'levels',lambda *args:{'support':[{'lower':90}], 'resistance':[{'lower':180}], 'all':[]})
    position['average_price']=100
    r=t.sell_guide(bundle,position,{'trailing_stop':140,'activated':True},cfg);assert r['status']=='익절'
    f.loc[f.index[-1],'close']=100;r=t.sell_guide(bundle,position,{'trailing_stop':140,'activated':True},cfg);assert r['status']=='판정 보류' and '순손익 0' in r['reasons'][-1]

def test_actual_pilot_metrics_independently_and_original_preserved():
    path=Path('technical/pilot.json.gz')
    if not path.exists():pytest.skip('Actual pilot not available in this checkout')
    payload=json.loads(gzip.decompress(path.read_bytes()));assert len(payload['bundles'])==3
    for b in payload['bundles'].values():
        assert len(b['bars'])>=160 and b['structure_valid']
        f=t.indicators(b['bars'])
        for n in [5,20,60,120]:assert f.iloc[-1][f'ma{n}']==pytest.approx(sum(r['close'] for r in b['bars'][-n:])/n)
        for k,v in b['computed'].items():assert f.iloc[-1][k]==pytest.approx(v)
        assert t.buy_guide(b)['status']=='판정 보류'
