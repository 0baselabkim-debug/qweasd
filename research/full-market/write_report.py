"""Generate final research documents from computed outputs, never invented results."""
from pathlib import Path
import json,pandas as pd
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'results'
def md(frame,columns=None,limit=None):
 if columns is not None:frame=frame[columns]
 if limit is not None:frame=frame.head(limit)
 def val(v):
  if pd.isna(v):return '—'
  if isinstance(v,float):return f'{v:,.4f}'.rstrip('0').rstrip('.')
  return str(v).replace('|','/')
 return '| '+' | '.join(frame.columns)+' |\n| '+' | '.join(['---']*len(frame.columns))+' |\n'+'\n'.join('| '+' | '.join(val(v) for v in row)+' |' for row in frame.itertuples(index=False,name=None))
def main():
 s=json.loads((OUT/'summary.json').read_text());v=json.loads((OUT/'verification.json').read_text());plan=json.loads((ROOT/'plan.json').read_text())['summary']
 f=pd.read_csv(OUT/'all-stocks.csv',dtype={'symbol':str});ev=pd.read_csv(OUT/'stage-events.csv',dtype={'symbol':str});core=f[f.final_group=='핵심 연구 후보'].copy();verified=pd.read_csv(OUT/'verified-candidates.csv',dtype={'symbol':str})
 ledger=json.loads((ROOT/'collection/ledger.json').read_text());state=json.loads((ROOT/'collection/state.json').read_text());complete=sum(set(e)=={'adjusted','raw'} for e in state['records'].values());responses=sum(map(len,state['records'].values()))
 calpath=ROOT/'collection/calendar.json';cal=json.loads(calpath.read_text()) if calpath.exists() else {'status':'not_collected','sessions':[]}
 krx_path=ROOT/'collection/official-reference-probe.json';krx=json.loads(krx_path.read_text()) if krx_path.exists() else {'status':'not_collected'}
 action_path=ROOT/'collection/sangji-official-probe.json';action=json.loads(action_path.read_text()) if action_path.exists() else {'status':'not_collected'}
 calendar=pd.read_csv(ROOT/'inputs/calendar.csv').trade_date.tolist();expected=[d for d in calendar if '2026-06-01'<=d<='2026-10-19'];calendar_matches=cal.get('status')=='passed' and cal['sessions']==expected
 threshold=pd.read_csv(OUT/'threshold-comparison.csv');cross=pd.read_csv(OUT/'cross-tabulation.csv');runs=pd.DataFrame(v['batches'])
 new=ev[ev.event_new_asof];counts=new.groupby(['asof_data_trusted','stage']).agg(events=('symbol','size'),stocks=('symbol','nunique')).reset_index()
 table=core[['symbol','name','market','sector','close','L2_date','L2_price','H3_price','L3_price','rise_pct','net_5d_krw','change_5d_krw','net_20d_krw','intensity_5d_pct','current_stage_confirmation_date','breakout_within10_impossible']].copy()
 for col in ['net_5d_krw','change_5d_krw','net_20d_krw']:table[col.replace('_krw','_억원')]=table.pop(col)/100_000_000
 group_counts=f.groupby(['trusted_candidate_data','final_group']).size().reset_index(name='종목 수')
 reasons=pd.DataFrame(sorted(s['reason_counts'].items(),key=lambda x:-x[1]),columns=['상세 사유(중복 가능)','종목 수'])
 data_counts=pd.DataFrame(s['data_states'].items(),columns=['고유 데이터 상태','종목 수'])
 current=pd.DataFrame(s['current_price_states'].items(),columns=['현재 가격 상태','종목 수'])
 stage_sector=pd.read_csv(OUT/'candidate-sectors.csv')
 previous_nine=pd.read_csv(OUT/'previous-nine-followup.csv',dtype={'symbol':str})
 boundary=pd.read_csv(OUT/'middle-boundary-cases.csv',dtype={'symbol':str})
 middle10=f[f.trusted_candidate_data & f.flow_improving & (f.price_structure=='중간 전환') & f.within10.fillna(False)]
 base=pd.read_csv(ROOT/'inputs/baseline-screening.csv',dtype={'symbol':str}).set_index('symbol');b=set(base[base.research_price_eligible].index);c60=set(base[base.consecutive_60].index);p=set(json.loads((ROOT/'inputs/pilot/run.json').read_text())['records']);short=set(base[base.prelisting_40>0].index)
 reuse_breakdown={'기존 기준선':len(b),'별도 기존60일':len(c60-b),'추가 시범 재사용':len(p-b-c60),'신규상장 짧은 이력':len(short-b-c60-p),'신규 요청':plan['targets']}
 report=f'''# 한국 증시 전체 종목 가격·수급 기반 1차 전수 선별

기준 거래일 **2026-10-19**, 모집단 **2,429종목**. 연구 브랜치만 사용했고 운영에 적용하지 않았다.

**질문에 대한 답:** 확보한 증거에 중대한 미해결 문제가 없고, 외국인 수급이 개선되며,
현재 중간 전환 구조·현재 종가 H3 이하·L2 대비 0~10% 조건을 함께 충족한 종목은 **{len(core)}개**다.
기업명: **{', '.join(core['name']) if len(core) else '없음'}**.
이는 수익률 우위·매수 추천이 아니라 자료 정합성 검사를 통과한 연구 후보다.
가격 충돌이 남은 잠정 후보는 이 숫자에 포함하지 않았다.

원래 중간 구조 조건이 유지되고 수급 개선+10% 이내인 집단 전체는 **{len(middle10)}개**다.
그중 현재 종가가 이미 H3를 넘은 경우를 '돌파 전'이라고 부르지 않도록 위 핵심 집단과
분리했다. 원래 단계별 사건 계산은 변경하지 않았다.

## 1. 조사·수집 범위

- 고정 모집단: {s['population']}개. 40일 구조 자체를 계산할 수 있는 수: {s['structural_price_evaluable']}개.
- 가격·필수 수급 및 확보한 증거 검사를 통과한 수: **{s['trusted_data_stocks']}개**.
- 기존 701종목의 가격·원래 단계 판정·5일/직전5일 수급 기준선 **701개 모두 보존**.
- 보존 자료 재사용 후 추가 요청 대상 {plan['targets']}개. 그중 수정·원주가 쌍 확보 {complete}개,
  저장 응답 {responses}개. 응답 확보와 가격 신뢰성 검증 통과는 다르다.
- 기존 10종목 시범 응답 전부 재사용. 해당 종목 재호출 0회.
- 모집단의 중복 없는 요청 배분: `{json.dumps(reuse_breakdown,ensure_ascii=False)}`.
  10개 시범 중 일부는 앞선 기존 자료 집단과 겹치므로 합산하지 않는다.
- 현재 연속 40일 확보 {s['continuous40']}개, 연속 60일 확보 {s['continuous60']}개.
- 검토 보관소: market/history/candidates/observations/pending/reference/provider/ledger 원본.
  모든 읽은 멤버를 기존 inventory 해시와 대조했고, 이전 31개 게시 스냅샷 조사 결과를 재사용했다.
- 10/20 체크포인트로 연결을 다시 검사했지만, 추가로 일관된 60일 이력을 복구한 종목은
  {plan['additional_existing_recovered']}개였다. 불일치하는 가격을 골라 끼우지 않았다.
- 실제 추가 물리 요청 **{v['physical_requests']}회** (인증·재시도·달력 검증 포함),
  재시도 {v['retry_requests']}회, 호출 제한 응답 {v['rate_limit_responses']}건.
- 배치 수집 시간 합계 **{v['batch_collection_seconds']:.3f}초**. 이는 API 수집 단계 실측 합계이며
  GitHub 대기·checkout·파일 보존을 포함한 전체 벽시계 시간은 실행 기록 별첨을 참조한다.
- 신규 외국인 수급 조회 **0회**. 과거 5·20일 수익률 계산 **미실시**.

### 실제 실행 기록

{md(runs)}

요청은 배치당 300회, 전체 3,800회 이하. 공유 KIS 직렬화·1.05초 간격을 유지했다.
기존 정규 수집을 취소하거나 새로운 예약을 생성하지 않았다.

## 2. 데이터 신뢰성

고유 대표 상태의 합계는 2,429개이며, 여러 문제를 가진 기업의 상세 사유는 따로 보존한다.

{md(data_counts)}

수집된 행 수만으로 정상으로 인정하지 않았다. 40일 연속성, 상장일, 무거래, OHLCV,
J=KRX·수정/원주가 옵션, 요청 종료일, 중복/미래 날짜, 이전 관측 및 보존 KRX 원시세를 대조했다.

- 기준 거래일과 API 수신 시각은 별도 필드다. 신규 관측은 10/19 당시 알고 있던 자료가 아니다.
- 사후 재조회 가격으로 **10/19까지의 구조를 재구성**한 분석이며, 당시 실시간 선정의 재현이 아니다.
- 각 사건은 그 확인 날짜까지의 가격 접두 구간만 사용한다. 이후 거래일 가격으로 스윙을 확정하지 않는다.
- 과거 기업행사 원장이 완전하지 않다. 수정/원주가 차이 또는 조정 표시가 있으면 공식 근거 없이
  분할·병합·권리락으로 단정하지 않고 검토 보류한다.
- 두 시장 공식 지수 거래일 검증 상태: **{cal.get('status')}**. 보존 연구 달력과 6/1~10/19
  거래일 집합 일치: **{calendar_matches}**, 확인 거래일 수 {len(cal.get('sessions',[]))}.
- 공급자 최종 확정 상태는 `UNVERIFIED`로 유지했다. “정상”은 확보된 근거의 연구 정합성 통과이며
  거래소의 완전한 최종가격/기업행사 인증을 뜻하지 않는다.

{md(reasons)}

상세: [데이터 신뢰성 보고서](RELIABILITY.md), [전체 충돌 관측](observation-conflicts.csv),
[조정 차이](adjustment-differences.csv), [연속성 및 보류 사유](coverage.csv).

## 3. 기존 701종목과의 차이

기존 가격과 원래 `stages_at` 계산은 바꾸지 않았다. 701종목 모두 원래 계산과 일치하며
5일·직전5일 수급도 일치한다. 그러나 뒤에 보존된 10/20 체크포인트와 비교하면
**{s['baseline_new_conflicts']}개**에서 새 가격 충돌 증거가 있다. 원래 신호를 지우지 않으며, 미해결 충돌이 남은 종목은
이번 자료검증 통과 후보에서 분리한다.

원래 10/19 신규 사건 기준 조기96·중간9·확정0(수급 개선+10% 이내)과 아래 **현재 유지 상태**
후보 수는 같은 통계가 아니다. 며칠 전에 발생해 유지 중인 상태가 포함되며 새 품질 보류도 반영된다.
이벤트 기준 비교 결과: `{json.dumps(v['baseline_new_event_differences'],ensure_ascii=False)}`.

기존 중간 단계 9종목의 현재 비교:

{md(previous_nine,['symbol','name','price_structure','rise_pct','price_data_status','final_group','reasons'])}

원본 가격을 바꾼 결과가 아니라, 원래 계산 위에 새 검증 증거를 별도로 반영한 차이다.

## 4. 신규 사건과 현재 상태

기존 40일·전후2일 스윙 규칙은 수정하지 않았다. 동률 제외, 연속 동일 극값 정리,
높아진 저점 확인 및 `직전 종가 ≤ H3 < 당일 종가`를 그대로 사용했다.

### 10/19 신규 확인 사건

{md(counts)}

같은 날 중간·확정이 함께 생길 수 있다. 사건 수를 더해 전체 종목 수나 단계 전이율로 해석하지 않는다.

### 기준일 현재 상태 (고유 상태)

{md(current)}

현재 상태는 최근 40일의 가장 최근 유효 L2에 연결한다. L2 재이탈은 상태를 무효화하고,
L3 이탈은 중간/확정을, H3 이하 복귀는 현재 확정 상태를 해제한다.
이는 화면상 현재 구조를 구분하는 사전 명시 규칙이며 기존 사건 조건을 바꾸지 않는다.
현재 구조만 보이고 당시 사건일의 40일 창이 없는 경우 확인일은 비워 두고
`current_structure_only`로 구분한다. 이전 사건 날짜를 만들어 넣지 않는다.

### 중간 이력은 있으나 현재 H3를 넘은 경계 사례

{md(boundary,['symbol','name','close','H3_price','L3_confirmed_date','rise_pct','current_stage_confirmation_date'])}

L3 확인 전의 H3 초과 또는 이후 새 극값 때문에 원래 확정 사건 조건을 충족하지 않을 수 있다.
중간 사건을 삭제하거나 확정 사건을 소급 생성하지 않는다. 기존 중간 구조 통계에 남기되
현재 종가가 H3 이하인 핵심·확장 후보와는 분리한다. 이 구분이 과거 모든 고점 돌파 시도가 없었다는 뜻은 아니다.

## 5. 수급·상승률 제한 비교

수급 개선 충족은 **{s['flow_improving']}개**다. 이는 전체 모집단의 수급 조건 수이며 가격 검증과 별개다.
최근 5일 합계가 양수이고 겹치지 않는 직전 5일보다 증가해야 한다. `change_5d`는 전일 대비가 아니다.
가격 검증 통과/잠정 결과를 분리한 누적 상한 비교:

{md(threshold)}

10% 이내는 15%·20% 이내에도 포함되므로 합산하지 않는다. 중복되지 않는 구간은
[cross-tabulation.csv](cross-tabulation.csv)에 별도 제공한다. 10%로 먼저 잘라낸 뒤 분석하지 않았다.
H3 자체가 L2의 110% 이상이면 해당 고정 H3의 **엄격한 종가 초과** 돌파를 10% 이내에서 만들 수 없다.
`h3_above_l2_10`과 `breakout_within10_impossible`을 구분해 저장했다.

{md(group_counts)}

## 6. 핵심 연구 후보의 실제 근거

금액 표시만 억원으로 환산했으며 원본 CSV의 수급 단위는 KRW다.

{md(table)}

전체 H1/L1/H2/L2/H3/L3 가격·발생일·확인일과 수급 원화 금액은 [후보별 상세 근거](CANDIDATES.md)에 있다.

전체 검증 통과 후보(핵심·확장·확정)의 업종별 분포:

{md(stage_sector)}

[검증 통과 후보 CSV](verified-candidates.csv)에는 선정 근거·다음 확인 조건이 있다.
[핵심](core-candidates.csv)·[확장](expanded-candidates.csv)·[확정](confirmed-candidates.csv) 파일도 분리했다.
[잠정 결과 CSV](provisional-only.csv)는 미해결 충돌이 있는 별도 집단이다.
가격 구조가 확인되어도 미래 수익·기업가치 개선을 의미하지 않는다.

## 7. 독립 검증 및 운영 보존

- 모집단 중복 0건, 2,429개 모두 고유 최종 분류 존재.
- 독립 DuckDB SQL 수급 비교 {v['independent_SQL_flow_checks']}개 통과.
- 기준일 유효 공식 업종·시장 구분 {v['dated_market_sector_checks']}종목 대조 통과.
- 당일 원시 KIS 수급 금액의 백만원→KRW 대조 {v['raw_asof_flow_unit_checks']}개 통과.
- 기존 701종목의 가격·원래 사건 규칙·수급 회귀 검증 통과.
- 실제 수집 파일 해시 {v['raw_response_hash_checks']}개, 호출 장부·인증·재시도 상한 검증 통과.
- 운영 DB·main·JSON·UI·정규 cron은 이번 연구에서 수정하지 않았다.
- 유일한 운영 파일 경로 차이는 **연구 브랜치에만 있는 수동 진단 workflow**다.
  main의 동일 파일은 그대로이며 운영 예약을 생성/수정하지 않았다.
- 전체 자동 테스트 결과는 [tests.txt](tests.txt), 기계 판독 검증은 [verification.json](verification.json).
- 원시 종가로 독립 재검산한 스윙 극값 {v['independent_pivot_checks']}개 통과.
- 과거 사건의 `asof_data_trusted`는 기준일 품질이고 과거 사건일의 완전 검증을 뜻하지 않는다.
  `event_date_quality_verified`를 별도 기록하며 이번에는 과거 사건일의 품질·수급을 이용한 성과 평가를 하지 않았다.

### 요청한 10개 자체 검증

{md(pd.DataFrame(v['completion_checks'].items(),columns=['검사','통과']))}

이 표의 통과는 미해결 가격을 해결했다고 뜻하지 않는다. 미해결 종목을 검증 완료 후보에서
제외하고 사유를 보존했는지까지 검사한 결과다.

## 8. 미확인 사항과 다음 단계

8개 시범 기업의 10/19 공식 KRX 직접 대조 자료와 상지건설의 정확한 기업행사 근거는
별도의 공식 대조/공시 점검 기록으로 관리한다. 최신 값이나 어느 공급자를 임의로 정답으로 선택하지 않았다.
이번 KRX 대조 결과: `{json.dumps([{'market':r.get('market'),'http':r.get('http'),'status':r.get('status')} for r in krx.get('records',[])],ensure_ascii=False)}`.
상지건설 공식 공시 확인 결과: `{action.get('status')}`, 공급자 상태 `{action.get('provider_status')}`,
목록 완전성 `{action.get('list_complete')}`. 공시 원문을 받았다는 사실만으로 가격 조정 근거가 확인된 것은 아니다.
이번 전수 재조회에서 발견한 추가 충돌도 [신뢰성 보고서](RELIABILITY.md)에 분리했다.

성과 검증을 하려면 동결 후보와 당시 관측 가능한 가격 버전, 다음 거래일 실제 거래 가능 원시가,
이후 5·20거래일 가격·거래정지/상장폐지·기업행사·거래비용·동일 시장 기준지수가 필요하다.
아직 그 성과를 계산하지 않았다. 따라서 10% 제한의 수익률 우월성이나 전략의 투자 우위를
이번 결과로 주장할 수 없다. 후속 대량 수집·운영 적용은 하지 않았다.

전체 연구 자료 ZIP: [full-market-20261019.zip](../full-market-20261019.zip)

연구 브랜치: https://github.com/0baselabkim-debug/qweasd/tree/research/full-market-transition-20261019
'''
 (OUT/'REPORT.md').write_text(report)
 details=['# 검증 통과 연구 후보의 가격·수급 근거','기준일 2026-10-19. 미래 성과·투자 추천을 의미하지 않는다.']
 for row in verified.itertuples():
  details.append(f'\n## {row.name} ({row.symbol}) — {row.final_group}\n\n{row.market} / {row.sector}, 현재 상태 {row.price_structure}. 기준일 종가 {row.close:,.0f}원, L2 대비 {row.rise_pct:.4f}%.')
  pivot_rows=[{'극값':name,'가격(원)':getattr(row,name+'_price'),'발생일':getattr(row,name+'_date'),'확인일':getattr(row,name+'_confirmed_date')} for name in ['H1','L1','H2','L2','H3','L3']]
  details.append(md(pd.DataFrame(pivot_rows)))
  flow_rows=[{'수급 항목':label,'값':getattr(row,field)} for label,field in [('1일 순매수(KRW)','net_1d_krw'),('최근5일(KRW)','net_5d_krw'),('직전5일(KRW)','previous_5d_krw'),('5일 개선액(KRW)','change_5d_krw'),('20일 순매수(KRW)','net_20d_krw'),('20일 순매수 일수','positive_days_20'),('기준일 시가총액(KRW)','market_cap_krw'),('5일 수급 강도(%)','intensity_5d_pct')]]
  details.append(md(pd.DataFrame(flow_rows)))
  details.append(f'고정 H3의 L2 대비 상승률: {(row.H3_price/row.L2_price-1)*100:.4f}%. 이 H3를 엄격히 넘는 돌파가 10% 이내에서 가능한지: {'불가능' if row.breakout_within10_impossible else '가격 수준상 가능(실현 보장 아님)'}.\n\n현재 단계 확인일: {row.current_stage_confirmation_date}. 기준일 신규 사건: {row.new_events if pd.notna(row.new_events) else "없음"}.\n\n다음 관찰 항목: 같은 L2·L3 재이탈 여부, 같은 H3의 종가 돌파, 동일 L2 대비 상승률 구간 및 가격 충돌 재발 여부. 새로운 기준 저점으로 바꾸어 10% 조건을 맞추지 않는다.\n\n선택 자료 수신 시각: {row.observed_at}. 공급자 최종 확정: UNVERIFIED.\n\n원본 식별자: `{row.source_identifiers}`')
 (OUT/'CANDIDATES.md').write_text('\n\n'.join(details)+'\n')
 eight=pd.read_csv(ROOT/'inputs/eight-price-comparison.csv',dtype={'symbol':str});dif=pd.read_csv(OUT/'observation-conflicts.csv',dtype={'symbol':str});adjpath=OUT/'adjustment-differences.csv'
 reliability=f'''# 데이터 신뢰성 보고서

고유 상태 집계와 중복 가능한 사유를 구분한다.

{md(data_counts)}

{md(reasons)}

## 관측 충돌

6필드 대조 관측 차이 행 {len(dif)}개, 종목 {dif.symbol.nunique()}개.
서로 다른 관측 시각·요청 옵션·해시 파일을 그대로 보존한다.
분석창 안의 차이가 남으면 최신 값으로 덮어쓰거나 정상으로 바꾸지 않는다.
종가 변경 여부와 고가·저가·거래량·거래대금 변경 여부는 `fields`에 분리되어 있다.
종가가 같아도 OHLCV 전체 정합성 미해결이므로 보수적으로 잠정 집단에 둔다.
단일 필드 차이를 보고 NXT·기업행사·가격 정정 중 하나로 원인을 확정하지 않는다.

## 기존 8종목

{md(eight)}

기존 시범 원문은 재사용했으며 같은 10종목을 다시 조회하지 않았다.
이 8종목의 10/19 재조회 수정주가와 원주가는 서로 같았다. 따라서 그날 기존 관측과의
차이를 수정 옵션 하나로 설명할 근거는 없다. 상지건설의 과거 조정 차이는 별도 문제다.
공식 명세의 `J=KRX`, `0=수정주가`, `1=원주가`는 `docs/official_itemchart.py`에 보존했다.
보존 KRX 원시세는 10/14·10/15 대조에 활용했으나 10/19 대조를 대신하지 않는다.
이번 공식 대조 시도 기록: `{json.dumps([{'market':r.get('market'),'http':r.get('http'),'status':r.get('status')} for r in krx.get('records',[])],ensure_ascii=False)}`.
접근이 거부되면 우회·연속 재시도하지 않았다. HTTP 거부의 발생 주체를 단정하지 않는다.

## 상지건설

상지건설의 6/1~9/21 수정·원주가 차이, 9/22 조정 표시는 보존했지만
정확한 기업행사 결정·효력일·기준가격·조정계수 공식 근거는 미확인이다.
원래 상세 기록은 `inputs/sangji-status.json`에 있다. 분할·병합·권리락이라고 확정하지 않는다.
추가 종목도 조정 표시/수정·원가격 차이를 동일 원칙으로 분류했다.
추가 공식 공시 목록/원문 확인 상태: `{action.get('status')}`.
선택한 원문: `{json.dumps(action.get('selected_originals',[]),ensure_ascii=False)}`.
원본·식별자·요청 기간은 `collection/sangji-official-probe.json`에 보존한다.

## 거래일·결측·기업 상태

- 공식 KIS 지수 거래일 검증 상태: {cal.get('status')}; 보존 연구 달력과 일치: {calendar_matches}.
- 상장 전 행·신규상장 짧은 이력·실제 무거래·빈 날짜를 구분했다.
- 결측을 0으로 채우지 않고 각 창의 날짜가 모두 존재할 때만 합산·분류했다.
- 전체 이벤트의 가격 접두 구간만 사용했지만, 사후 수신 자료의 당시 관측 가능성은
  별개다. `point_in_time_observation_available=false`는 실시간 재현으로 사용할 수 없다.
- 기업행사 원장 완전성·공급자 최종 확정은 보장하지 않는다.

## 자료 위치

전체: `all-stocks.csv`; 사유: `coverage.csv`; 충돌: `observation-conflicts.csv`;
조정: `adjustment-differences.csv`; 잠정: `provisional-only.csv`.
'''
 (OUT/'RELIABILITY.md').write_text(reliability)
 print('Wrote reports; verified core candidates:',len(core))
if __name__=='__main__':main()
