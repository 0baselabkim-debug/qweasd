"""Transparent hypotheses, not forecasts. Missing components never gain redistributed weight."""
from pathlib import Path
import tomllib
import numpy as np
import pandas as pd

CONFIG=Path(__file__).resolve().parents[1]/'config.toml'
def config():
    with CONFIG.open('rb') as f:c=tomllib.load(f)
    for group in ['sector_weights','stock_weights']:
        if sum(c[group].values())!=100 or min(c[group].values())<0:raise ValueError('Weights must total 100')
    if c['scoring']['minimum_peers']<2:raise ValueError('At least two peers required')
    return c

def percentile(s,minimum=3,higher=True):
    s=pd.to_numeric(s,errors='coerce').replace([np.inf,-np.inf],np.nan)
    valid=s.dropna();out=pd.Series(np.nan,index=s.index,dtype=float)
    if len(valid)<minimum:return out
    out.loc[valid.index]=(valid.rank(method='average',ascending=higher)-1)/(len(valid)-1)
    return out

def _dateframe(frame,column,asof):
    return frame.loc[pd.to_datetime(frame[column])<=asof].copy()

def _membership(m,day):
    return m[(m.valid_from<=day)&(m.valid_to.isna()|(m.valid_to>=day))]

def _exact(series,dates):
    if series.index.duplicated().any():return None
    s=series.reindex(dates)
    return None if s.isna().any() or len(s)!=len(dates) else s

def _financial(row):
    def finite(k):return pd.notna(row.get(k,np.nan))
    out={k:np.nan for k in ['revenue_growth','op_growth','margin','roe','debt_ratio','ocf_ratio','per','pbr']}
    out.update(turnaround=False,financial_hold='재무 미확보',financial_period=None,disclosed_on=None)
    out['financial_period']=row.get('period_end');out['disclosed_on']=row.get('disclosed_on')
    rev,op,ni,equity=row.get('revenue'),row.get('operating_profit'),row.get('net_income'),row.get('equity')
    prev_rev,prev_op,prev_eq=row.get('previous_revenue'),row.get('previous_operating_profit'),row.get('previous_equity')
    if finite('revenue') and finite('previous_revenue') and prev_rev>0:out['revenue_growth']=rev/prev_rev-1
    if finite('previous_operating_profit') and prev_op>0 and finite('operating_profit'):out['op_growth']=op/prev_op-1
    elif finite('previous_operating_profit') and prev_op<=0 and finite('operating_profit') and op>0:out['turnaround']=True
    if finite('revenue') and rev>0 and finite('operating_profit'):out['margin']=op/rev
    if finite('equity') and equity>0:
        if finite('previous_equity') and prev_eq>0 and finite('net_income'):out['roe']=ni/((prev_eq+equity)/2)
        if finite('liabilities'):out['debt_ratio']=row['liabilities']/equity
    if finite('ocf') and finite('revenue') and rev>0:out['ocf_ratio']=row['ocf']/rev
    # CFS valuation uses owners-of-parent earnings/equity when the enriched schema exists.
    # Legacy test fixtures retain their explicitly supplied total-company definition.
    val_ni=row.get('parent_net_income') if row.get('scope')=='CFS' and 'parent_net_income' in row else ni
    val_eq=row.get('parent_equity') if row.get('scope')=='CFS' and 'parent_equity' in row else equity
    if finite('market_cap') and row['market_cap']>0:
        if pd.notna(val_eq) and val_eq>0:out['pbr']=row['market_cap']/val_eq
        if pd.notna(val_ni) and val_ni>0:out['per']=row['market_cap']/val_ni
    if row.get('special',False):reason='업종 특수기업·별도 평가 필요'
    elif not finite('equity') or equity<=0:reason='자본총계 0 이하 또는 누락'
    elif not finite('net_income') or ni<=0 or not finite('operating_profit') or op<=0:reason='적자 또는 손익 누락'
    elif row.get('scope')=='CFS' and pd.notna(val_eq) and val_eq<=0:reason='지배주주 귀속 자본 0 이하'
    elif row.get('scope')=='CFS' and pd.notna(val_ni) and val_ni<=0:reason='지배주주 귀속 순손익 적자'
    elif not finite('ocf'):reason='영업현금흐름 누락'
    elif not finite('revenue'):reason='매출 계정 미확보'
    else:reason=''
    out['financial_hold']=reason
    return out

def evaluate(data,asof=None,cfg=None):
    cfg=cfg or config();p=cfg['scoring'];flow_days=p['flow_days'];recent=p['recent_days'];prior=p['activity_prior_days']
    asof=pd.Timestamp(asof or data['flows'].trade_date.max()).normalize()
    f=_dateframe(data['flows'],'trade_date',asof);f=f[f.exchange=='KRX']
    price=_dateframe(data['prices'],'trade_date',asof);price=price[price.exchange=='KRX']
    membership_frame=data['memberships'].copy()
    for col in ['valid_from','valid_to']:membership_frame[col]=pd.to_datetime(membership_frame[col])
    membership_cache={}
    def membership(day):
        if day not in membership_cache:membership_cache[day]=_membership(membership_frame,day)
        return membership_cache[day]
    today=f[f.trade_date==asof].copy();mapping=membership(asof)
    if mapping.symbol.duplicated().any():raise ValueError('Ambiguous point-in-time sector mapping')
    x=today.merge(data['instruments'],on='symbol',validate='one_to_one').merge(mapping[['symbol','sector']],on='symbol',how='left',validate='one_to_one')
    exclusions=data['exclusions'];excluded=set(exclusions.loc[exclusions.trade_date==asof,'symbol'])
    x=x[~x.symbol.isin(excluded)].copy()
    px=price[price.trade_date==asof].drop(columns=['exchange']).rename(columns={'source':'price_source'})
    x=x.merge(px,on=['symbol','trade_date'],how='left',validate='one_to_one')
    x=x[(x.volume.isna())|(x.volume>0)].copy()
    x['sector']=x.sector.fillna('업종 미분류')
    fin=_dateframe(data['financials'],'disclosed_on',asof)
    fin=fin[(fin.disclosure_verified==True)&(fin.currency=='KRW')&(fin.period_end<=asof)]
    fin=fin.sort_values(['period_end','disclosed_on','receipt']).drop_duplicates('symbol',keep='last')
    x=x.merge(fin.drop(columns=['source']),on='symbol',how='left',validate='one_to_one')
    features=pd.DataFrame([_financial(row) for row in x.to_dict('records')],index=x.index)
    for col in features:x[col]=features[col]
    x['intensity_1d']=x.net_amount/x.market_cap.where(x.market_cap>0)
    for col in ['flow_intensity','flow_persistence','turnover_level','activity_ratio']:x[col]=np.nan
    x['flow_observations']=0
    calendars={m:pd.DatetimeIndex(sorted(data['calendar'].loc[(data['calendar'].market==m)&(data['calendar'].trade_date<=asof),'trade_date'].unique())) for m in x.market.unique()}
    # Index once: a full-market 20-day snapshot must not scan every row per stock.
    flow_series=dict(iter(f.set_index('trade_date').groupby('symbol').net_amount))
    turnover_series=dict(iter(price.set_index('trade_date').groupby('symbol').turnover))
    empty_series=pd.Series(dtype=float,index=pd.DatetimeIndex([]))
    for i,row in x.iterrows():
        days=calendars[row.market]
        ff=flow_series.get(row.symbol,empty_series)
        pp=turnover_series.get(row.symbol,empty_series)
        if ff.index.duplicated().any() or pp.index.duplicated().any():raise ValueError('Duplicate stock/date observations')
        x.at[i,'flow_observations']=ff.reindex(days[-flow_days:]).notna().sum()
        if len(days)<min(flow_days,recent):continue
        if len(days)>=flow_days:
            values=_exact(ff,days[-flow_days:])
            if values is not None:
                x.at[i,'flow_persistence']=(values>0).mean()
                if pd.notna(row.market_cap) and row.market_cap>0:x.at[i,'flow_intensity']=values.sum()/row.market_cap
        if len(days)>=recent:
            values=_exact(pp,days[-recent:])
            if values is not None:x.at[i,'turnover_level']=values.mean()
        if len(days)>=recent+prior:
            values=_exact(pp,days[-(recent+prior):])
            if values is not None and values.iloc[:prior].mean()>0:x.at[i,'activity_ratio']=values.iloc[-recent:].mean()/values.iloc[:prior].mean()
    sectors=[]
    for (market,sector),group in x.groupby(['market','sector']):
        row={'market':market,'sector':sector,'members':len(group),'net_amount':group.net_amount.sum(),'intensity_1d':np.nan,'foreign_intensity':np.nan,'positive_days':np.nan,'flow_trend':np.nan,'relative_strength':np.nan,'activity':np.nan}
        if group.market_cap.notna().all() and (group.market_cap>0).all():row['intensity_1d']=group.net_amount.sum()/group.market_cap.sum()
        days=calendars[market];series=[]
        for day in days[-max(flow_days+1,prior+recent):]:
            members=membership(day);symbols=set(members.loc[(members.market==market)&(members.sector==sector),'symbol'])
            dayflow=f[(f.trade_date==day)&f.symbol.isin(symbols)]
            dayprice=price[(price.trade_date==day)&price.symbol.isin(symbols)]
            net=dayflow.net_amount.sum(min_count=len(symbols)) if symbols and set(dayflow.symbol)==symbols else np.nan
            turnover=dayprice.turnover.sum(min_count=len(symbols)) if symbols and set(dayprice.symbol)==symbols else np.nan
            series.append({'day':day,'net':net,'turnover':turnover})
        hist=pd.DataFrame(series)
        if len(hist)>=flow_days and hist.net.iloc[-flow_days:].notna().all() and group.market_cap.notna().all() and (group.market_cap>0).all():
            values=hist.net.iloc[-flow_days:]
            row['foreign_intensity']=values.sum()/group.market_cap.sum();row['positive_days']=(values>0).mean()
            row['flow_trend']=(values.iloc[-recent:].mean()-values.mean())/group.market_cap.sum()
        if len(hist)>=prior+recent:
            values=hist.turnover.iloc[-(prior+recent):]
            if values.notna().all() and values.iloc[:prior].mean()>0:row['activity']=values.iloc[-recent:].mean()/values.iloc[:prior].mean()
        if len(days)>=flow_days+1:
            window=days[-(flow_days+1):];returns=[]
            for prev,day in zip(window[:-1],window[1:]):
                members=membership(prev)
                symbols=set(members.loc[(members.market==market)&(members.sector==sector),'symbol'])
                before=price[(price.trade_date==prev)&price.symbol.isin(symbols)].set_index('symbol')
                after=price[(price.trade_date==day)&price.symbol.isin(symbols)].set_index('symbol')
                if not symbols or set(before.index)!=symbols or set(after.index)!=symbols:break
                conflicts=data.get('metadata',{}).get('adjusted_price_conflicts',{})
                affected={r['symbol'] for d in [str(prev.date()),str(day.date())] for r in conflicts.get(d,[])}
                if symbols & affected:break # source revision remains unresolved; never silently combine vintages
                cols=before[['adjusted_close','market_cap']]
                if cols.isna().any().any() or (cols<=0).any().any() or after.adjusted_close.isna().any() or (after.adjusted_close<=0).any():break
                weights=before.market_cap/before.market_cap.sum()
                returns.append(float(((after.adjusted_close/before.adjusted_close-1)*weights).sum()))
            bm=data['benchmarks'];bm=bm[bm.market==market].set_index('trade_date').close
            benchmark=_exact(bm,window)
            if len(returns)==flow_days and benchmark is not None and (benchmark>0).all():row['relative_strength']=np.prod(1+np.array(returns))-1-(benchmark.iloc[-1]/benchmark.iloc[0]-1)
        sectors.append(row)
    sectors=pd.DataFrame(sectors)
    def rank(frame,metric,groups,higher=True):
        values=frame[metric].mask(frame.sector=='업종 미분류')
        return values.groupby([frame[k] for k in groups],group_keys=False).transform(lambda s:percentile(s,p['minimum_peers'],higher))
    if len(sectors):
        sectors['score_foreign_intensity']=rank(sectors,'foreign_intensity',['market'])*cfg['sector_weights']['foreign_intensity']
        sectors['score_foreign_persistence']=(rank(sectors,'positive_days',['market'])+rank(sectors,'flow_trend',['market']))/2*cfg['sector_weights']['foreign_persistence']
        sectors['score_relative_strength']=rank(sectors,'relative_strength',['market'])*cfg['sector_weights']['relative_strength']
        sectors['score_activity']=rank(sectors,'activity',['market'])*cfg['sector_weights']['activity']
    x['op_growth_metric']=x.op_growth.where(~x.turnaround,p['turnaround_value'])
    groups=['market','sector'];financial_groups=['market','sector','scope','period_end'];w=cfg['stock_weights']
    x['score_foreign_flow']=(rank(x,'flow_intensity',groups)+rank(x,'flow_persistence',groups))/2*w['foreign_flow']
    x['score_growth']=(rank(x,'revenue_growth',financial_groups)+rank(x,'op_growth_metric',financial_groups))/2*w['growth']
    x['score_profitability_health']=(rank(x,'margin',financial_groups)+rank(x,'roe',financial_groups)+rank(x,'debt_ratio',financial_groups,False)+rank(x,'ocf_ratio',financial_groups))/4*w['profitability_health']
    x['score_valuation']=(rank(x,'per',financial_groups,False)+rank(x,'pbr',financial_groups,False))/2*w['valuation']
    x['score_activity']=(rank(x,'turnover_level',groups)+rank(x,'activity_ratio',groups))/2*w['activity']
    x.loc[x.financial_hold!='',['score_profitability_health','score_valuation']]=np.nan
    for frame,weights in [(x,w),(sectors,cfg['sector_weights'])]:
        cols=['score_'+k for k in weights]
        if frame.empty:continue
        frame.loc[frame.sector=='업종 미분류',cols]=np.nan
        frame['available_weight']=sum(frame[col].notna()*weights[col[6:]] for col in cols)
        frame['earned_score']=frame[cols].sum(axis=1,min_count=1)
        frame['total_score']=frame[cols].sum(axis=1,min_count=len(cols))
        if frame is x:frame.loc[frame.financial_hold!='','total_score']=np.nan
        frame['evaluation_rank']=frame.groupby(['market','sector'] if frame is x else ['market']).total_score.rank(ascending=False,method='min')
    return x,sectors,{'asof':asof,'days':{k:len(v) for k,v in calendars.items()},'config':cfg}
