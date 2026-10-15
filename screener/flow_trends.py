"""Price-independent KRX flow trends. Membership is explicitly fixed at the analysis date."""
import numpy as np
import pandas as pd
from .scoring import config,percentile


def calendar_days(data,asof,markets):
    c=data['calendar'];end=pd.Timestamp(asof)
    return pd.DatetimeIndex(sorted(c.loc[(c.trade_date<=end)&c.market.isin(markets),'trade_date'].unique()))[-20:]


def change_state(recent,previous):
    """Signed, non-overlapping five-day sums; zero is observed neutral, not missing."""
    if pd.isna(recent) or pd.isna(previous):return '자료 부족','판정 보류'
    direction='강화' if recent>previous else '약화' if recent<previous else '유지'
    if recent>0:
        state=('순매수 전환' if previous<0 else '순매수 시작' if previous==0 else
               '순매수 증가' if recent>previous else '순매수 감소' if recent<previous else '순매수 유지')
    elif recent<0:
        state=('순매도 전환' if previous>0 else '순매도 시작' if previous==0 else
               '순매도 축소' if recent>previous else '순매도 확대' if recent<previous else '순매도 유지')
    else:state='순매수 종료' if previous>0 else '순매도 해소' if previous<0 else '중립 유지'
    return direction,state


def screen_flows(frame,mode,days):
    """Views are descriptive filters; they never modify composite scores."""
    if mode=='자금 유입 상위':mask=frame[f'net_{days}d']>0
    elif mode=='매수세 강화':mask=frame.change_5d>0
    elif mode=='자금 이탈':mask=frame[f'net_{days}d']<0
    elif mode=='꾸준한 순매수':mask=frame.buying_pattern=='꾸준한 순매수'
    else:return frame.copy()
    return frame.loc[mask].copy()


def summarize(values,cap=np.nan):
    """A missing observation invalidates its window; real zero remains a valid observation."""
    def total(n,offset=0):
        v=values.iloc[-(n+offset):len(values)-offset if offset else None]
        return float(v.sum()) if len(v)==n and v.notna().all() else np.nan
    a,b,c=total(5),total(5,5),total(20)
    valid_cap=pd.notna(cap) and np.isfinite(cap) and cap>0
    full=len(values)==20 and values.notna().all()
    direction,state=change_state(a,b)
    pattern=('꾸준한 순매수' if (values>0).sum()>=15 and a>0 and b>0 else '기준 미충족') if full else '자료 부족'
    return {'change_direction':direction,'change_state':state,'buying_pattern':pattern,'change_intensity_5d':(a-b)/cap if valid_cap else np.nan,'net_1d':total(1),'intensity_1d':total(1)/cap if valid_cap else np.nan,'net_5d':a,'previous_5d':b,'change_5d':a-b,'net_20d':c,
            'positive_days_20d':int((values>0).sum()) if full else np.nan,
            'observed_days_20d':int(values.notna().sum()),
            'intensity_5d':a/cap if valid_cap else np.nan,'intensity_20d':c/cap if valid_cap else np.nan,
            'flow_trend':(a/5-c/20)/cap if valid_cap else np.nan}


def stock_trends(data,stocks,asof):
    if stocks.symbol.duplicated().any():raise ValueError('Duplicate stock universe')
    f=data['flows'];f=f[(f.exchange=='KRX')&(f.trade_date<=pd.Timestamp(asof))&f.symbol.isin(stocks.symbol)]
    if f.duplicated(['symbol','trade_date']).any():raise ValueError('Duplicate flow observations')
    days=calendar_days(data,asof,stocks.market.unique())
    matrix=f.pivot(index='trade_date',columns='symbol',values='net_amount').reindex(index=days,columns=stocks.symbol).astype(float)
    rows=[]
    for r in stocks.itertuples():
        own_days=calendar_days(data,asof,[r.market])
        rows.append({'symbol':r.symbol,**summarize(matrix[r.symbol].reindex(own_days),r.market_cap)})
    metrics=pd.DataFrame(rows).set_index('symbol').drop(columns=['intensity_1d'])
    for n in [1,5,20]:metrics[f'net_{n}d_rank']=metrics[f'net_{n}d'].rank(ascending=False,method='min')
    return metrics,matrix


def group_trends(data,stocks,matrix,asof,theme_members=None):
    """No historical membership claim: freeze the selected day's stock set for every observation."""
    if theme_members is None:
        mapped=stocks[['symbol','market','sector','market_cap']].copy()
    else:
        m=theme_members[['symbol','theme']].drop_duplicates()
        mapped=stocks[['symbol','market_cap']].merge(m,on='symbol',validate='one_to_many').rename(columns={'theme':'sector'})
        mapped['market']='테마'
    rows=[];series={}
    for (market,sector),g in mapped.groupby(['market','sector']):
        codes=g.symbol.drop_duplicates().tolist();markets=stocks.loc[stocks.symbol.isin(codes),'market'].unique()
        days=calendar_days(data,asof,markets);observations=matrix.reindex(index=days,columns=codes)
        daily=observations.sum(axis=1,min_count=len(codes))
        cap=float(g.market_cap.sum()) if g.market_cap.notna().all() and (g.market_cap>0).all() else np.nan
        series[market,sector]=pd.DataFrame({'trade_date':days,'net_amount':daily.to_numpy(),
            'cumulative_amount':daily.cumsum(skipna=False).to_numpy(),'observed_members':observations.notna().sum(axis=1).to_numpy(),'expected_members':len(codes)})
        rows.append({'market':market,'sector':sector,'members':len(codes),'market_cap':cap,**summarize(daily,cap)})
    columns=['market','sector','members','market_cap',*summarize(pd.Series(dtype=float))]
    result=pd.DataFrame(rows,columns=columns)
    for n in [1,5,20]:result[f'net_{n}d_rank']=result[f'net_{n}d'].rank(ascending=False,method='min')
    cfg=config();minimum=cfg['scoring']['minimum_peers']
    def rank(metric):
        v=result[metric].mask(result.sector=='업종 미분류')
        return v.groupby(result.market).transform(lambda s:percentile(s,minimum))
    # Same 40/30 weights and formulas, separate from point-in-time sector total and stock scores.
    result['flow_intensity_points']=rank('intensity_20d')*cfg['sector_weights']['foreign_intensity']
    result['flow_persistence_points']=(rank('positive_days_20d')+rank('flow_trend'))/2*cfg['sector_weights']['foreign_persistence']
    result['flow_points']=result[['flow_intensity_points','flow_persistence_points']].sum(axis=1,min_count=2)
    return result,series


def stock_series(matrix,symbol):
    daily=matrix[symbol]
    return pd.DataFrame({'trade_date':matrix.index,'net_amount':daily.to_numpy(),'cumulative_amount':daily.cumsum(skipna=False).to_numpy(),
                         'observed_members':daily.notna().astype(int).to_numpy(),'expected_members':1})
