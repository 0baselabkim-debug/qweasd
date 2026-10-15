"""Score-free screening. Legacy scoring and data remain intact and are not executed."""
import numpy as np
import pandas as pd
from .scoring import _financial, _membership
from .flow_trends import stock_trends, summarize, calendar_days


def universe(data,asof):
    day=pd.Timestamp(asof)
    f=data['flows'];f=f[(f.exchange=='KRX')&(f.trade_date==day)]
    m=data['memberships'].copy()
    for c in ['valid_from','valid_to']:m[c]=pd.to_datetime(m[c])
    m=_membership(m,day)
    x=f.merge(data['instruments'],on='symbol',validate='one_to_one').merge(m[['symbol','sector']],on='symbol',how='left',validate='one_to_one')
    excluded=set(data['exclusions'].loc[data['exclusions'].trade_date==day,'symbol'])
    x=x[~x.symbol.isin(excluded)].copy()
    p=data['prices'];p=p[(p.exchange=='KRX')&(p.trade_date==day)].drop(columns='exchange').rename(columns={'source':'price_source'})
    x=x.merge(p,on=['symbol','trade_date'],how='left',validate='one_to_one')
    x=x[x.volume.isna()|(x.volume>0)].copy();x['sector']=x.sector.fillna('업종 미분류')
    fin=data['financials'];fin=fin[(fin.disclosed_on<=day)&(fin.period_end<=day)&fin.disclosure_verified&(fin.currency=='KRW')]
    fin=fin.sort_values(['period_end','disclosed_on','receipt']).drop_duplicates('symbol',keep='last')
    x=x.merge(fin.drop(columns='source'),on='symbol',how='left',validate='one_to_one')
    # Only raw financial ratios, no legacy hold rules or weighted/percentile scores.
    facts=pd.DataFrame([_financial(r) for r in x.to_dict('records')],index=x.index).drop(columns='financial_hold')
    for c in facts:x[c]=facts[c]
    x['intensity_1d']=x.net_amount/x.market_cap.where(x.market_cap>0)
    metrics,matrix=stock_trends(data,x,day)
    x=x.join(metrics,on='symbol',validate='one_to_one')
    x['eligibility']='판정 보류'
    return x,matrix


def turnover_matrix(data,codes,markets,asof):
    cal=data['calendar'];days=pd.DatetimeIndex(sorted(cal.loc[(cal.trade_date<=pd.Timestamp(asof))&cal.market.isin(markets),'trade_date'].unique()))[-25:]
    p=data['prices'];p=p[(p.exchange=='KRX')&p.symbol.isin(codes)&p.trade_date.isin(days)].copy()
    if p.duplicated(['trade_date','symbol']).any():raise ValueError('Duplicate turnover observations')
    # Do not use prices under a known unresolved market/source reconciliation conflict.
    for date,rows in data.get('metadata',{}).get('adjusted_price_conflicts',{}).items():
        affected={r['symbol'] for r in rows}
        p.loc[(p.trade_date==pd.Timestamp(date))&p.symbol.isin(affected),'turnover']=np.nan
    p.loc[(p.turnover<0)|~np.isfinite(p.turnover),'turnover']=np.nan
    return p.pivot(index='trade_date',columns='symbol',values='turnover').reindex(index=days,columns=codes)


def activity(values):
    recent=values.iloc[-5:];prior=values.iloc[-25:-5]
    mean=float(recent.mean()) if len(recent)==5 and recent.notna().all() else np.nan
    ratio=mean/prior.mean() if len(values)==25 and len(prior)==20 and prior.notna().all() and prior.mean()>0 else np.nan
    return mean,ratio


def stock_activity(data,stocks,asof):
    x=stocks.copy();x['average_turnover']=np.nan;x['activity_ratio']=np.nan
    for market,g in x.groupby('market'):
        matrix=turnover_matrix(data,g.symbol.tolist(),[market],asof)
        for i,r in g.iterrows():x.loc[i,['average_turnover','activity_ratio']]=activity(matrix[r.symbol])
    return x


def sector_indicators(data,stocks,matrix,asof,themes=None):
    if themes is None:mapped=stocks[['symbol','market','sector','market_cap']]
    else:
        mapped=stocks[['symbol','market_cap']].merge(themes[['symbol','theme']].drop_duplicates(),on='symbol',validate='one_to_many').rename(columns={'theme':'sector'})
        mapped['market']='테마'
    rows=[];series={};stock_index=stocks.set_index('symbol')
    for (market,sector),g in mapped.groupby(['market','sector']):
        codes=g.symbol.unique().tolist();own=stock_index.loc[codes]
        days=calendar_days(data,asof,own.market.unique())
        values=matrix.reindex(index=days,columns=codes)
        daily=values.sum(axis=1,min_count=len(codes))
        cap=g.market_cap.sum() if g.market_cap.notna().all() and (g.market_cap>0).all() else np.nan
        record={'market':market,'sector':sector,'members':len(codes),'market_cap':cap,**summarize(daily,cap)}
        for window in [1,5,20]:
            valid=own[f'net_{window}d'].notna();positive=own[f'net_{window}d']>0
            record[f'evaluable_{window}d']=int(valid.sum());record[f'positive_{window}d']=int(positive.sum())
            record[f'breadth_{window}d']=positive.sum()/valid.sum()*100 if valid.any() else np.nan
        turns=turnover_matrix(data,codes,own.market.unique(),asof)
        record['average_turnover'],record['activity_ratio']=activity(turns.sum(axis=1,min_count=len(codes)))
        rows.append(record)
        series[market,sector]=pd.DataFrame({'trade_date':days,'net_amount':daily.to_numpy(),'observed_members':values.notna().sum(axis=1).to_numpy(),'expected_members':len(codes)})
    return pd.DataFrame(rows),series
