"""As-observed stage comparison for a FIXED current 701-stock research cohort.
Never use newly collected pilot prices or 19-Oct bars to assign a past event.
"""
from pathlib import Path
import collections,gzip,hashlib,json,tarfile,sys
import pandas as pd,duckdb
from stages import stages_at

ROOT=Path(__file__).resolve().parent
OLD=Path('/workspace/research-price-recovery-20261019')
REPO=Path('/workspace/qweasd')
sys.path.insert(0,str(OLD))
from recover import merge,normalize,valid,FIELDS,KRX

def main():
    cohort=pd.read_csv(OLD/'results/screening.csv',dtype={'symbol':str}).query('research_price_eligible').set_index('symbol')
    assert len(cohort)==701
    calendar=pd.read_csv(OLD/'results/research-calendar.csv').trade_date.tolist()
    days=['2026-10-15','2026-10-16','2026-10-19']
    cutoffs={d:pd.Timestamp(d+'T23:59:59+09:00') for d in days}
    cp={};best={d:{} for d in days};flow_versions={d:collections.defaultdict(dict) for d in days}
    refs={d:{} for d in days};versions_read=0
    inv={x['path']:x for x in json.loads((REPO/'audit-archive/inventory.json').read_text())['files']}
    def members(suffix):
        with tarfile.open(REPO/'audit-archive'/('former-observations-'+suffix+'.tar.xz')) as t:
            for m in t:
                if not m.isfile():continue
                raw=t.extractfile(m).read();assert hashlib.sha256(raw).hexdigest()==inv[m.name]['sha256']
                x=json.loads(gzip.decompress(raw) if m.name.endswith('.gz') else raw)
                yield m.name,x
    for d in days:
        bundle=json.loads(gzip.decompress((OLD/'inputs'/('checkpoint-'+d.replace('-','')+'.json.gz')).read_bytes()))
        cp[d]=bundle
        for code,r in bundle['records'].items():
            if code not in cohort.index:continue
            observed=pd.Timestamp(r['observed_at'])
            if observed>cutoffs[d]:continue
            b=normalize(r['adjusted_prices'])
            best[d][code]=(len(b),r['observed_at'],b,'checkpoint:'+d)
            for future in days:
                if observed<=cutoffs[future] and d<=future:
                    flow_versions[future][code][d]=(observed,float(r['flow']['frgn_ntby_tr_pbmn'])*1_000_000,'checkpoint:'+d)
    def add_flows(code,x,path):
        try:observed=pd.Timestamp(x['observed_at'])
        except (KeyError,ValueError):return
        for day in days:
            if observed>cutoffs[day]:continue
            for row in x.get('flows',[]):
                date=row['trade_date']
                if date>day:continue
                prior=flow_versions[day][code].get(date)
                if prior is None or observed>prior[0]:
                    flow_versions[day][code][date]=(observed,row['net_amount'],path)
    for suffix in ['market','history','candidates','observations','pending']:
        for path,x in members(suffix):
            if not isinstance(x,dict) or x.get('symbol') not in cohort.index or not x.get('bars'):continue
            assert x.get('exchange')=='KRX' and x.get('units',{}).get('foreign_net')=='KRW'
            versions_read+=1;code=x['symbol'];add_flows(code,x,path)
            if x.get('exchange')!='KRX' or x.get('adjustment')!='adjusted':continue
            observed=pd.Timestamp(x['observed_at']);bars={r['trade_date']:r for r in x['bars']}
            assert len(bars)==len(x['bars'])
            for d in days:
                if observed>cutoffs[d] or x.get('adjustment_asof','9999')>d or max(bars)>d:continue
                r=cp[d]['records'].get(code)
                if not r or pd.Timestamp(r['observed_at'])>cutoffs[d]:continue
                joined,_,_=merge(bars,normalize(r['adjusted_prices']))
                if joined is None:continue
                prior=best[d].get(code)
                if prior is None or (len(joined),x['observed_at'])>(prior[0],prior[1]):
                    best[d][code]=(len(joined),x['observed_at'],joined,path)
    for suffix in ['reference','reference-observations']:
        for path,x in members(suffix):
            observed=pd.Timestamp(x['observed_at'])
            for r in x.get('rows',[]):
                code=r.get('ISU_CD');date=str(pd.Timestamp(r.get('BAS_DD')).date())
                if code not in cohort.index:continue
                try:values=tuple(float(r[k].replace(',','')) for k in KRX)
                except (KeyError,ValueError,AttributeError):continue
                for d in days:
                    if observed>cutoffs[d] or date>d:continue
                    old=refs[d].get((code,date))
                    if old is None or observed>old[0]:refs[d][code,date]=(observed,values,path)
    # On 19-Oct reproduce exactly the already-reviewed 701 series, not a newly preferred vintage.
    latest=pd.read_parquet(OLD/'results/research-prices.parquet')
    latest=latest[latest.symbol.isin(cohort.index)]
    for code,g in latest.groupby('symbol'):
        b={r['trade_date']:r for r in g.to_dict('records')}
        row=cohort.loc[code]
        best['2026-10-19'][code]=(len(b),row.source_observed_at,b,row.source)
    events=[];coverage=[];flow_checks=0;panel=[];seen=set()
    for day in days:
        dates=[d for d in calendar if d<=day];window=dates[-40:]
        for code,row in cohort.iterrows():
            reasons=[];source=best[day].get(code);b=source[2] if source else {}
            if any(d not in b for d in window):reasons.append('40일 관측 창 부족')
            elif any(not valid(b[d]) or b[d]['volume']<=0 or b[d]['turnover']<=0 for d in window):reasons.append('가격·무거래 검토')
            for d in window:
                ref=refs[day].get((code,d))
                if ref and d in b and tuple(b[d][k] for k in FIELDS)!=ref[1]:reasons.append('당시 보존 KRX 대조 불일치');break
            if day!='2026-10-19' and source and source[1] and pd.Timestamp(source[1])>cutoffs[day]:reasons.append('미래 관측')
            r=cp[day]['records'].get(code,{})
            actions=[a for a in r.get('adjusted_prices',[]) if str(pd.Timestamp(a['stck_bsop_date']).date()) in window and
                     (a.get('prtt_rate','0.00') not in ['','0','0.0','0.00'] or a.get('revl_issu_reas','').strip())]
            if actions:reasons.append('기업행사 조정 표시')
            fv=flow_versions[day][code]
            def net(ds):return sum(fv[d][1] for d in ds) if all(d in fv for d in ds) else None
            n5=net(dates[-5:]);p5=net(dates[-10:-5]);n20=net(dates[-20:])
            improve=n5 is not None and p5 is not None and n5>0 and n5>p5
            if n5 is None or p5 is None:reasons.append('당시 10일 수급 미확보')
            if day=='2026-10-19':
                assert n5==row.net_5d and p5==row.previous_5d,(code,n5,p5,row.net_5d,row.previous_5d)
                assert not reasons,(code,reasons)
                flow_checks+=2
            coverage.append({'date':day,'symbol':code,'name':row['name'],'evaluable':not reasons,
                             'reasons':'; '.join(reasons),'flow_improving':improve,'source':source[3] if source else None})
            if reasons:continue
            # Save each point-in-time price/flow panel for reproducibility.
            panel.append({'date':day,'symbol':code,'source':source[3],'base_observed_at':source[1],
                          'checkpoint_observed_at':r.get('observed_at'),'dates':window,
                          'closes':[b[d]['close'] for d in window],'net5':n5,'previous5':p5,'net20':n20})
            found=stages_at([b[d]['close'] for d in window],39)
            for stage,e in found.items():
                identity=(stage,code,window[e['reference_low_index']],window[e.get('higher_low_index',e['reference_low_index'])])
                if identity in seen:continue
                seen.add(identity)
                event={'date':day,'stage':stage,'symbol':code,'name':row['name'],'market':row.market,'sector_current_cohort':row.sector,
                       'confirmation_close':e['confirmation_close'],'reference_low':e['reference_low'],
                       'reference_low_date':window[e['reference_low_index']],'rise_pct':e['rise_pct'],'within_10':e['within_10'],
                       'flow_improving':improve,'net5':n5,'previous5':p5,'change5':n5-p5,'net20':n20,
                       'source':source[3],'base_observed_at':source[1],'checkpoint_observed_at':r.get('observed_at')}
                events.append(event)
    f=pd.DataFrame(events);cover=pd.DataFrame(coverage);out=ROOT/'results'
    f.to_csv(out/'stage-events.csv',index=False,encoding='utf-8-sig')
    f[f.within_10].to_csv(out/'stage-within10.csv',index=False,encoding='utf-8-sig')
    cover.to_csv(out/'stage-coverage.csv',index=False,encoding='utf-8-sig')
    (out/'as-observed-panels.json.gz').write_bytes(gzip.compress(json.dumps(panel,ensure_ascii=False).encode(),mtime=0))
    summary=[]
    for label,group in [('2026-10-19',f[f.date=='2026-10-19']),('관측가능3일',f)]:
        for stage in ['조기','중간','확정']:
            g=group[group.stage==stage];eligible=g[g.flow_improving]
            summary.append({'scope':label,'stage':stage,'price_events':len(g),'price_events_within10':int(g.within_10.sum()),
                            'flow_improving_events':len(eligible),'flow_improving_within10':int(eligible.within_10.sum()),
                            'distinct_stocks_within10_with_flow':int(eligible.loc[eligible.within_10,'symbol'].nunique())})
    pd.DataFrame(summary).to_csv(out/'stage-summary.csv',index=False,encoding='utf-8-sig')
    result={'fixed_current_cohort':701,'dates':days,'coverage':cover.groupby('date').evaluable.sum().to_dict(),
            'asof19_flow_value_checks':flow_checks,'archival_versions_read':versions_read,
            'price_events':len(f),'summary':summary,'new_market_api_calls':0,
            'scope_limit':'fixed current 701-stock cohort; no retrospective results before contemporaneous preserved observations; no forward returns'}
    (out/'stage-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
