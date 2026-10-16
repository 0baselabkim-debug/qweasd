"""Price/flow schema and evidence checks; no trading indicators or decisions."""
import math
import numpy as np
import pandas as pd
from screener.liquidity_quality import daily_accepted,blocking_conflicts

def validate(bundle,asof=None,require_final=True,minimum_bars=25):
    issues=[]
    if not bundle:return ['검증된 OHLCV 미확보']
    if bundle.get('exchange')!='KRX' or bundle.get('adjustment')!='adjusted':issues.append('시장 범위·수정주가 기준 불일치')
    if require_final and (bundle.get('finality')!='VERIFIED' or not bundle.get('finality_evidence')) and not daily_accepted(bundle):issues.append('공식 확정 또는 일별 교차검증 근거 미확보')
    if blocking_conflicts(bundle):issues.append('시세 정합성 미확인 · 기존 원본 보존')
    if asof and bundle.get('asof')!=str(asof):issues.append('시세·수급·조회 기준일 불일치')
    try:
        f=pd.DataFrame(bundle['bars'])
        if len(f)<minimum_bars:issues.append(f"OHLCV {len(f)}/{minimum_bars}거래일")
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
