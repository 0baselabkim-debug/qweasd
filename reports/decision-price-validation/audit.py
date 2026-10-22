"""Read-only reproducible real-evidence audit; never calls a market API."""
import collections
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import pandas as pd
from screener.decision_support.evidence import read,sha,bar,KRX_FIELDS,KIS_FIELDS,date,encoded


def audit(directory=ROOT/'snapshot/decision-support'):
    ix=read(directory/'index.json')
    stocks={r['symbol']:r for s in ix['sectors'] for r in read(directory/s['file'])['stocks']}
    source=read(directory/'source-manifest.json')
    current=[r for r in source['checkpoints'].values() if r['asof']==ix['asof']]
    if len(current)!=1:raise ValueError('ambiguous_current_checkpoint')
    checkpoint=read(directory/current[0]['file'])
    official={};conflicts=collections.Counter();initial=collections.Counter();details=[]
    for v in source['references'].values():
        obj=read(directory/v['file'])
        if obj['asof']!=ix['asof']:continue
        for raw in obj['rows']:
            try:official[raw['ISU_CD']]=bar(raw,KRX_FIELDS)
            except ValueError:pass
    for code,r in checkpoint['records'].items():
        raw=[x for x in r['adjusted_prices'] if date(x['stck_bsop_date'])==ix['asof']]
        if len(raw)!=1 or code not in official:initial['missing_or_invalid_reference']+=1;continue
        kis=bar(raw[0],KIS_FIELDS);krx=official[code]
        fields=[k for k in kis if kis[k]!=krx[k]]
        initial['match' if not fields else 'conflict']+=1
        conflicts.update(fields)
        if fields:details.append({'symbol':code,'fields':fields,'initial':kis,'official':krx})
    results={};independent=[]
    for code in ('178320','000720','064400'):
        r=stocks[code];s=r['price_series'];frame=pd.DataFrame(s['rows'],columns=s['columns']).set_index('trade_date')
        valid=frame['validation_status'].eq('operational_crosscheck_pass')
        # Missing/rejected days remain NaN and interrupt rolling windows.
        values=frame[['open','high','low','close','volume','turnover']].where(valid)
        checks={}
        def check(key,value):
            expected=None if pd.isna(value) else float(value);actual=r['technical'][key]['value']
            good=(expected is None and actual is None) or (expected is not None and actual is not None and abs(expected-actual)<max(1e-8,abs(expected)*1e-10))
            checks[key]={'expected':expected,'actual':actual,'passed':good}
        for n in (5,20,60,120):
            roll=values.close.rolling(n).mean();check(f'sma_{n}',roll.iloc[-1])
            check(f'sma_{n}_slope_1d',roll.diff().iloc[-1])
        def midpoint(n):return (values.high.rolling(n).max()+values.low.rolling(n).min())/2
        check('ichimoku_current_span_b',midpoint(52).shift(26).iloc[-1])
        check('ichimoku_current_span_a',((midpoint(9)+midpoint(26))/2).shift(26).iloc[-1])
        # EWM seeded at the first 14 true ranges of the valid suffix.
        bad=[i for i,v in enumerate(valid) if not v];suffix=values.iloc[(bad[-1]+1 if bad else 0):]
        atr=None
        if len(suffix)>=15:
            previous=suffix.close.shift()
            tr=pd.concat([suffix.high-suffix.low,(suffix.high-previous).abs(),(suffix.low-previous).abs()],axis=1).max(axis=1).iloc[1:]
            seeded=pd.concat([pd.Series([tr.iloc[:14].mean()]),tr.iloc[14:].reset_index(drop=True)],ignore_index=True)
            atr=seeded.ewm(alpha=1/14,adjust=False).mean().iloc[-1]
        check('atr_14',atr)
        if not all(v['passed'] for v in checks.values()):raise ValueError('independent_indicator_mismatch:'+code)
        results[code]={'name':r['name'],'range':[frame.index[0],frame.index[-1]],'validity':r['validity'],
            'hold_reasons':dict(collections.Counter(reason for reasons in frame.reasons for reason in reasons)),
            'technical':r['technical'],'independent_checks':checks,
            'auxiliary_vintage':s.get('qualified_auxiliary_history')}
    return {'asof':ix['asof'],'dataset_id':ix['dataset_id'],'source_snapshot_sha256':ix['source_snapshot_sha256'],
            'coverage':ix['coverage'],'initial_vs_official':dict(initial),'conflicting_field_counts':dict(conflicts),
            'price_hold_reasons_asof':ix.get('price_hold_reasons_asof'), 'samples':results,
            'original_conflicts':details,'all_checks_passed':True}


if __name__=='__main__':
    result=audit(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'snapshot/decision-support')
    target=Path(__file__).with_name('REAL_DATA_AUDIT.json');target.write_bytes(encoded(result))
    print(json.dumps({k:result[k] for k in ('asof','coverage','initial_vs_official','conflicting_field_counts','all_checks_passed')},ensure_ascii=False))
