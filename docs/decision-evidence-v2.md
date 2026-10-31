# 투자 가설 검증용 데이터 확장 V1 (출력 decision-evidence-v2.1)

이 모듈은 보존된 자료를 읽어 GPT/Dot 검토용 객관적 근거를 생성한다. 주문·최종 추천·목표주가·상승 확률·매집/흡수/반등 확정을 생성하지 않는다. 운영 수집기, Actions, 인증/공유 잠금, 원문, 운영 DB/JSON, 대시보드를 수정하지 않는다. 기존 decision-support 후처리와도 별도로 수동 실행한다.

## 입력과 재사용

현재 어댑터 입력은 동일 세대의 `snapshot/{manifest.json,dashboard.duckdb,daily_briefing.json,decision-support/…}`이다. `macro.json`은 있으면 읽는다. 새로운 시장 요청이나 외부 API 클라이언트는 없다.

- `scripts.build_decision_support.verify`: 기존 세대·원문 SHA256·DB/JSON·외국인 합계·기관 수량 검증을 먼저 실행한다. 실패를 무시하지 않는다.
- `screener.decision_support.evidence`: 정수 OHLCV 검증, 공식 KRX 보존 원문과 6개 필드 정확 대조, canonical JSON·SHA256·타임스탬프 처리.
- `screener.decision_support.history.pages`: 종목에 연결된 동일 빈티지 지수 페이지에서 검증된 장기 거래일을 복원한다. 운영 달력과 겹치는 날짜 및 지수 값이 정확히 일치해야 한다. 평일 목록으로 거래일을 만들어 채우지 않는다.
- `screener.decision_support.indicators`: 외국인 1/3/5/20일, 직전 비중복 5일, 기관 수량, SMA·Ichimoku·Wilder ATR14·거래량·수익률·시장 초과수익·기존 종가 스윙을 재사용한다.
- 기존 기업 실적·공시 문맥, 매크로 자료는 출처와 함께 읽는다. 수집 방식은 바꾸지 않는다.

새 정기 수집기의 `COMPLETE_PROVISIONAL`/`PENDING_KRX` 아티팩트는 게시본과 다른 스키마다. 후속 작업에서 지정된10월29일 원문을 공식 Actions로 확보하고 별도 `collector-artifact` 어댑터를 구현했다. [연결 문서](decision-evidence-collector.md)와 [실제 결과](../reports/decision-evidence-v2-connected/RESULT.md)를 참조한다. 게시본 입력에 디렉터리만 바꿔 넣거나 KIS 내부 PASS를 KRX 대조 PASS로 승격하지 않는다. 서로 다른 날짜·모집단의 관측을 합치지 않는다.

## 실행·소비자 인터페이스

DuckDB가 설치된 기존 개발 가상환경에서 실행한다. 출력은 저장소 및 입력 트리 바깥에 둔다.

```bash
python scripts/build_decision_evidence.py \
  --snapshot snapshot --out /workspace/decision-evidence-output \
  --knowledge-cutoff 2026-10-30T08:00:00+00:00 --limit 20
```

기본 cutoff는 실제 실행 시각이다. 미래 cutoff, 관측 시각/분석 생성 시각이 cutoff보다 뒤인 입력, 세대/해시 불일치, 중복 날짜를 거부한다. cutoff는 과거 자료를 새로 승인하거나 과거 공개 시각을 증명하지 않는다.

출력 루트 아래 `decision-evidence-v2-<asof>-<identity SHA256>/`를 새로 만든다. identity는 원본 manifest·index·전체 입력 트리 해시, 코드 해시, cutoff, 후보 수에 묶인다. 같은 identity는 기존 출력 검증 후 재사용하고 덮어쓰지 않는다. 입력 트리는 실행 전후 전체 파일 해시를 비교한다. 생성 중 실패는 독립 출력에 국한된다.

| 파일 | 내용 |
| --- | --- |
| `market.json.gz` | 전체 입력 종목. 비후보도 보존. 버전/거래일/생성 시각/원천 세대/검증 상태/단위/전체 객관적 지표 |
| `briefing.json` | 최대 20개 후보. 기존 게시 브리핑 순서 우선, 이후 입력 순서. 새 점수·순매수 금액 절대 순위 없음 |
| `quality.json` | 계산 가능 종목 수, 필드·사유별 결측, 원인별 보류 종목 수와 관측 행 수, 연속 이력 분포 |
| `price-conflicts.csv.gz` | 최신 KIS/KRX 원문 6필드의 실제 차이·출처 ID. 원인 미확정 표시 |
| `evidence.duckdb` | 종목 키/거래일/세대와 JSON payload 테이블 `evidence` |
| `records.parquet` | 위 테이블의 ZSTD Parquet |
| `manifest.json` | 파일별 SHA256, 원천/코드 identity, 입력 불변 검증 |

소비자는 먼저 manifest를 검증하고 `stock_code + asof + dataset_id`로 연결한다. 반환된 manifest SHA256을 별도로 보관하면 manifest 자체 교체도 탐지한다.

```bash
python scripts/build_decision_evidence.py --verify-only \
  --out /workspace/decision-evidence-output/<dataset_id> \
  --expected-manifest-sha256 <별도 보관한 manifest SHA256>
```

검증은 파일 집합/해시, JSON 세대 identity, DuckDB 구조화 열과 payload, 전체 JSON/Parquet 행·값, 중복 종목을 검사한다. 자체 manifest만으로 공격자가 모든 파일과 manifest를 함께 바꾸는 것을 증명할 수는 없으므로 외부 보관 해시가 필요하다. 이번 작업은 외부 백업·게시·운영 자동 연결을 등록하지 않는다.

## 기간·단위·가격 승인

`D[-n:]`은 검증된 달력의 최근 n개 거래일이다. 외국인 n일 수급은 그 n개 날짜 합계이고, n일 수익률은 n+1개 연속 종가에서 계산한다. 직전 5일 수급은 `D[-10:-5]`, 직전 5일 수익률은 `C[-6]/C[-11]-1`이다. 최신 검증 가격이 과거 날짜이면 오늘 가격으로 가져오지 않는다.

- 외국인 금액 KRW, 외국인/기관 수량 주. 소수점 표현 `100.000`은 정확한 정수 100으로 정규화하지만 비정수/NaN/무한대/불리언 값은 거부한다.
- 기관 금액은 기존 공급자 단위가 공식 검증되지 않아 전부 unavailable. 외국인 배율을 기관에 차용하지 않는다.
- 가격은 기존 `operational_crosscheck_pass`, 사유 없음, 엄격한 OHLC/양수 거래량·거래대금, 관측 시점에 완료된 KRX 일봉만 사용한다. 같은 날은 KST 15:30 이후 관측 여부를 추가 확인한다. 장 종료 조건은 공급자 최종 확정을 뜻하지 않는다.
- 가격/이력 충돌·조정 미검증·무거래·결측 날짜를 넘어서 연결하거나 보간하지 않는다. ATR은 기존 구현의 연속 승인 접미 이력만 사용한다.
- KIS 요청의 시장 구분과 KRX 대조를 상속한다. NXT 포함 여부를 충돌 값만 보고 추론하거나 시장 범위를 바꾸지 않는다. `provider_finality=UNVERIFIED`와 `historical_backtest_eligible=false`를 유지한다.
- 기존 지표의 부동소수점 출력 표현은 유지한다. 신규 비율에는 약분된 `exact_ratio`를 제공하고, 상대강도 개선 여부는 Fraction의 정확 비교로 결정한다. 허용오차/임의 반올림은 없다.
- `null + unavailable + reason`은 미확인/계산 불가다. 중립·거짓·0으로 대체하지 않는다.

## 신규 지표 공식

### 수급 지속성·집중도

- 양수 일수: 완전한 5/20일 구간에서 `count(Fd > 0)`.
- 연속 순매수: 최신 날짜부터 `Fd > 0` 연속 일수. 앞의 비양수 날을 확인하지 못하면 정확한 길이는 null, 관측된 최소 길이와 `streak_is_exact=false`를 별도 제공한다.
- 일별 분포: 완전한 최근 5일 날짜·금액 배열.
- 최대 순매수일 비중: `max(양수 Fd) / sum(양수 Fd)`. 최근 5일 순수급이 양수일 때만 계산한다. 총 순액을 분모로 쓰지 않는다.
- 양수일 평균 순매수액: `sum(양수 Fd) / 양수일 수`.
- 양수일 평균 강도: 양수일 각각의 `Fd / 거래대금d` 평균. 각 날짜의 승인 거래대금이 필요하다.
- 5/20일 누적 강도: `sum(Fd)/sum(거래대금d)`. 순수급이 양수이고 완전한 가격 구간일 때만 계산한다.
- 분모 1 KRW/주 미만 또는 0은 계산하지 않는다. 원천 최소 단위 이상인 값을 자의적으로 '작다'며 제거하지 않는다.
- 중단기 방향: 5/20일 순수급의 양수/0/음수 부호.

현재 최근 5일 합계 N, 직전 5일 합계 P에 따른 전환:

| 상태 | 정의 |
| --- | --- |
| new_net_buying | N > 0, P <= 0 |
| buying_strengthening / continuing / slowing | N,P > 0이고 N > / = / < P |
| new_net_selling | N < 0, P >= 0 |
| selling_expanding / continuing / easing | N,P < 0이고 N < / = / > P |
| zero_net_flow | N = 0, P 알려짐 |
| unavailable | N 또는 P 결측 |

### 가격 반응·구조·상대강도

- 1/3/5/20일 순수급과 같은 기간 수익률. 5일 탄력 변화 = 최근 5일 수익률 − 직전 비중복 5일 수익률(pp).
- 최근 5/10/20/60일 고가 최대·저가 최소, 비중복 최근/직전 5일 극값 변화.
- 당일 범위 종가 위치 = `(종가−저가)/(고가−저가)`; 고가=저가이면 null.
- 저점 갱신 후 종가 회복: 오늘 저가 < 직전 20일 저가 <= 오늘 종가. '지지 확인' 결론은 아님.
- 전일 종가가 그 이전 20일 고가를 돌파했을 때 오늘 종가가 해당 가격 초과 유지/이하 재이탈했는지. 완전한 22봉 필요.
- 갭: 오늘 시가 > 전일 고가면 up, < 전일 저가면 down, 그 외 overlap.
- 당일 거래량/거래대금 ÷ 최근 5/20일 평균(오늘 포함). 하락일 거래량 확대/축소는 오늘 종가<전일 종가이면서 오늘 거래량> / < 전일 거래량이다. 기존 상승/하락일 평균 거래량도 제공한다.
- 대량 거래: 직전 5일 평균 거래량의 2배 이상. 실제 다음 거래일이 관측된 사건만 출력한다. 최근 26봉이 완전해야 사건 없음 `[]`을 승인한다.
- 고가/저가 스윙: 40개 완전한 봉에서 양옆 2개 봉보다 엄격히 높은 고가/낮은 저가. 발생 2거래일 뒤 확인일을 기록한다. 미확정 최신 2봉 제외. 기존 종가 스윙과 별개 이름으로 제공한다.
- 최근 2개 확인 스윙 고가/저가의 차이, 현재 종가의 마지막 스윙 대비 거리(%)를 제공한다. 지지·저항·손절가 확정은 unavailable.
- 시장 초과수익 = 같은 시장·같은 날짜의 종목 수익률 − KOSPI/KOSDAQ 수익률(pp). 공식 업종지수가 없어 업종 초과수익은 unavailable. 구성 종목 평균을 공식 지수로 대체하지 않는다.

### 관측 태그

모든 필요 입력이 있을 때만 true/false이다. P0 가격 승인을 우회하지 않는다.

| 태그 | 정의 |
| --- | --- |
| foreign_flow_strengthening | N>0, N−P>0 |
| foreign_flow_weakening | N−P<0 |
| foreign_buying_price_falling / rising | N>0, 5일 수익률<0 / >0 |
| foreign_selling_price_rising | N<0, 5일 수익률>0 |
| institutional_selling_pressure | 기관 최근 5일 순매수 수량<0 |
| relative_strength_improving | 최근 5일 시장 초과수익 > 직전 5일 시장 초과수익 |
| foreign_strengthening_and_relative_strength_improving | 위 수급 강화와 상대강도 개선 동시 충족 |
| flow_and_price_momentum_strengthening | N>0, N−P>0, 최근 5일 수익률>0이며 직전 5일보다 큼 |
| price_breakout_observed / breakdown | 오늘 종가가 직전 20일 고가 초과 / 저가 미만 |
| rising_on_market_down_day | 시장 1일 수익률<0, 종목 수익률>0 |
| outperforming_on_market_down_day | 시장 1일 수익률<0, 종목 수익률>시장 수익률 |
| underperforming_on_market_up_day | 시장 1일 수익률>0, 종목 수익률<시장 수익률 |
| recent_event_detected | 현재 null; 검증된 시각별 이벤트 피드 미연결 |

수급·가격 동반 상승, 수급 강화·가격 하락, 수급 약화·가격 상승, 수급·가격 동반 하락, 신규 순매수 전환, 직전 5일<=0에서 최근 5일>0 가격 회복 유형도 배열로 출력한다. 수급 선행·반전 관찰은 추가 검토할 가설 이름이다. 반전 확정/추천을 뜻하지 않는다.

브리핑 후보는 외국인 5/20일, 5일 검증 수익률, 시장 초과수익이 있어야 한다. 양수 수급·상승, 강화 양수 수급·하락, 수급 부호 전환의 세 유형을 포함한다. 충분하지 않으면 20개보다 적게 반환한다. 전체시장 출력에서는 미선정 종목을 제거하지 않는다.

## P2 오프라인 준비 범위

`events.event_evidence`는 이벤트 종류/날짜, 시각, 원문 ID·URL·SHA256, 종목 관계, 확인 사실, 검증 상태, 미확인 사항을 검증한다. 직접 관련은 관련 종목 코드의 명시가 필요하다. 업종 동종기업 사건은 직접 사건으로 바꾸지 않는다. 시각 미확인/컷오프 이후/UNVERIFIED 자료의 확인 사실을 승인하지 않는다. 제목 감성·주가 영향은 생성하지 않는다. caller가 제공하는 메타데이터 계약이며 외부 원문의 진위 검증기나 실시간 공시 수집기가 아니다.

컨센서스 서프라이즈는 실제 실적과 컨센서스 모두 검증된 출처가 있고, 컨센서스 공개 시각이 발표보다 엄격히 앞서며 기간/CFS·OFS/통화/항목이 같고 분모>0일 때만 `(실제−예상)/예상*100`이다. 현재 실제 피드는 없다.

기존 annual/verified_period_reports의 YTD·분기 원천, 전년 값, 단위·CFS/OFS·영수증을 그대로 보존한다. 새로운 분기 증감·이벤트 사실을 빈 데이터로 생성하지 않는다. 정확한 공개 시각을 증명하지 못하므로 재무 문맥의 point_in_time_usable=false다. 신규 DART/IR/컨센서스 연결은 미완료다. 기존 DART·매크로 구현/자격은 변경하지 않았다. 실제 호출량·요금·라이선스/재배포 가능 여부는 선택한 원천별 확인과 별도 실행 승인이 필요하다. 이번 추가 API 호출/비용은 0이다.

`judgments.record(evidence, judgment, decision_at, invalidation_conditions, output)`는 명시적으로 입력된 GPT 판단만 원문 근거 전체와 해시로 독립 저장한다. 생성 시각/컷오프보다 과거 판단 시각이나 미래 시각을 거부하고, 동일 판단은 중복 저장하지 않는다. 기존 historical_backtest_eligible=false를 true로 바꾸지 않는다. 이번 실제 데이터로 판단 기록을 생성하지 않았다.

`judgments.evaluate(payload, calendar, prices, market, foreign_flows, cutoff)`는 시점 재현 자격 true, 명시적 판단, 검증 달력 출처, KST 판단일의 당시 이용 가능 가격이 필요하다. 5/10/20거래일이 지나고 각 날짜의 종목·시장·출처·관측 시각·완성 봉·동일 가격 기준을 확인한 뒤 수익률, 시장 초과수익, 종가 기준 최대 낙폭, 진입가 대비 장중 최악/최선 변동, 이후 누적 외국인 순수급을 계산한다. 거래정지·상장폐지·조정 미검증·누락을 건너뛰거나 대체하지 않는다. 평가 가격 관측은 해당 거래일 정규장 종료 이후, 평가 cutoff 이전이어야 한다. 지수·수급도 날짜/시장·종목 정합성을 별도로 검사한다.

평가 계약: calendar는 `{dates, source_id, validation_status:'verified', observed_at}`. 가격 행은 `{trade_date,stock_code,market,source_id,observed_at,basis,is_closed,validation_status:'operational_crosscheck_pass',OHLCV}`, 지수/수급 행도 해당 거래일·식별자·출처·관측 시각과 검증 상태를 가진다. 제공자가 이미 검증한 원천이 전제되며 이 함수가 원격 증빙을 가져오지 않는다. 자유문장 무효화 조건의 성립은 자동 판정하지 않고 명시적 검토 필요로 남긴다. 실제 과거 자료의 시점 재현 자격 false 때문에 이번 평가 실적은 없다. 합성 테스트 성공을 투자 성과로 보고하지 않는다.

## 실제 검증 범위와 후속 작업

실제 실행 결과·원천 세대·지표 이용률·테스트·용량은 `reports/decision-evidence-v2/RESULT.md`와 `REAL_DATA_AUDIT.json`에 기록한다. 실제로 읽은 10월 23일 게시본과 원문을 확보하지 못한 10월 29일 수집 세대를 구분한다. 10월 23일 데이터를 10월 30일 시장 상태나 10월 23일 당시 이용 가능 자료로 표시하지 않는다.

가격 이용률 개선에는 충돌 날짜별 KIS 원주가/수정주가 및 공식 KRX 6필드 원문, 조정 이벤트와 연결 증빙, 60/120/Ichimoku에 필요한 연속 달력이 필요하다. 자료가 없으면 추가 API를 호출하거나 승인 기준을 바꾸지 않는다. 현 코드의 P0는 진단과 명확한 보류이며, 검증 가능 가격 종목 수 증가는 달성하지 않았다.

운영 자동화·브리핑/대시보드 게시·공식 업종 지수·외부 이벤트/컨센서스·실제 GPT 판단과 장기 사후 성과 검증은 후속 작업이다. 지정된 새 수집 스키마 연결의 후속 실적은 위 연결 문서에서 별도로 관리한다. 기능 브랜치의 테스트·출력 검증을 운영 반영으로 해석하지 않는다.
