# 독립 투자 분석 V2 통합 계층

이 계층은 기존 `decision_evidence`의 검증된 출력만 읽는다. 원문·수집기·공유 KIS 잠금·운영 게시본·예약·대시보드를 수정하지 않는다. 기존 source/metrics/technical/price acceptance와 보존 데이터 검증을 재사용한다. 원본을 재수집하거나 결측을 채우는 기능은 없다.

## 입력과 실행

입력은 기존 `build_decision_evidence.py`의 독립 세대 디렉터리와 **따로 보관한 manifest SHA256**이다. 기존 JSON/DuckDB/Parquet·파일 집합·해시 검증을 먼저 통과해야 한다. 서로 다른 거래일·세대·시장 자료를 합치지 않는다. 출력은 저장소와 모든 입력 경로 바깥의 새 세대에만 저장한다.

```bash
python scripts/run_investment_v2.py \
  --evidence /preserved/evidence-generation \
  --expected-manifest-sha256 <외부에_보관한_원문_manifest_SHA256> \
  --out /independent/investment-generations
```

기본은 외부 호출 0이다. `--context`는 검증할 로컬 문맥 계약, `--portfolio`는 명시적 사용자 계좌 원장이다. 없으면 NOT_RUN/UNAVAILABLE를 출력한다. decision_at은 기본 실제 시각이며 미래·데이터 생성/knowledge_cutoff 이전 시각을 거부한다. 데이터 거래일을 현재 날짜로 바꾸지 않는다.

## 모듈과 자료 흐름

| 단계 | 구현 | 계약 |
|---|---|---|
| 원천 검증·정량 분석 | 기존 `decision_evidence`, `decision_support` | 기존 값 유지. 3거래일 수량·기관 비중복 수량 변화·현재 검증 OHLC 필드만 추가 |
| 입력 계약 | `investment_v2/contracts.py` | 외부 manifest anchor, 세대·날짜·시장, 가격 보류 |
| 재무·매크로·사건 | `context.py` | 로컬 원문 SHA256·JSON pointer 값 일치·공개/관측 시각·단위·회계기준 |
| 점수·가설·전략 | `assessment.py` | 부분 점수와 7항목 가설 검증 분리, 지지 테스트·원래 방어선·손익비 |
| 계좌 | `portfolio.py` | 사용자 확인 이벤트, 해시 체인·원자적 추가, 과거 시점 재생 |
| GPT | `engine.py`, `strategy-v1.md` | 명시적 버전·데이터 fact·JSON schema·근거/등급 검증 |
| 출력 | `pipeline.py`, `briefing.py` | 전체 종목 JSON/DuckDB/Parquet 및 최대 20개 후보, GPT가 고르는 최대 4개 |
| 판단 기록·사후평가 | `records.py`, 기존 `judgments.py` | 원래 판단 불변, 5·10·20거래일 관찰 성과와 복기 축 |

각 출력은 원천 dataset_id/manifest SHA/asof, 파생 dataset_id, 결정 시각, 실행 코드·전략 지침 해시에 연결된다. 공급자 최종성 UNVERIFIED와 PENDING_KRX, historical_backtest_eligible=false를 임의 승격하지 않는다. KIS 내부 PASS는 공식 확정 가격 승인이 아니다. 영문 혼합 6자리 코드는 검증된 기존 마스터의 정식 식별자로 받아들이며 모집단을 줄이지 않는다.

## 정량·전략 정책 v1

기존 외국인 1/3/5/20일 금액, 1/5/20일 수량과 기술 계산을 유지하고 3일 수량을 추가한다. 기관은 수량만 비교하며 미확인 금액 단위를 외국인 배율로 채우지 않는다. 기관 최근 5일/직전 5일 수량과 변화도 별도로 계산한다.

백점 평가 배점은 외국인 30, 가격 30, 기관·거래량 15, 기업·산업 15, 위험 10이다. 모든 세부 항목은 출력 `scorecard.categories.criteria`에 조건·배점·충족 여부를 제공한다. 정책 문서는 법칙이나 검증된 투자 수익률이 아니다.

- 외국인: 5/20일 순매수, 양수 일수, 수급 변화, 검증 거래대금 대비 강도 각 5점.
- 가격: SMA20 상회·상승, SMA5>SMA20, 5일 상승, 시장 초과수익, 지지 가격 테스트 각 5점.
- 기관·거래량: 기관 5일 순매수·압력 완화, 거래량 기준 충족 각 5점.
- 기업·산업: 같은 기준 매출 성장, 영업이익률 양수, 검증된 산업 전망 각 5점.
- 위험: 현재 진입 기준 손익비 2 이상, 사용자 계좌 위험/비중 여유 각 5점.

결측 항목은 0점이나 중립으로 바꾸지 않는다. `partial_score/assessed_maximum`과 `total_score=null`을 구분하며 백점 환산하지 않는다. 점수와 별도로 가설/수급/가격·타점/기업·매크로/반대 가설/위험/보유 대비 일곱 검토를 출력한다. 팔십점은 자동 매수 조건이 아니다.

예상 지지는 SMA20/확인 스윙 등 관측 수준이다. `support_test_confirmed`는 실제 당일 저가 테스트와 종가 회복 관측이며 미래 지지 보증이 아니다. N자 구조도 확인된 피벗의 순서/가격에 따른 후보다. 돌파·갭·ATR·이동평균·상대강도는 기존 승인 일봉만 사용한다. SMA60/120 부족은 계산 불가로 유지한다.

전략은 눌림, 추세 확인, 돌파, 추세 강화 추가매수, 수급 유지 반전 추가매수다. 각각 적용/부적합/필요 자료를 출력한다. 반전 추가매수는 기존 보유, 수급 유지, 실제 가격 회복·지지 테스트, 확인된 일시적 악재 성격이 필요하다. 외국인 매수만으로 통과하지 않는다.

진입 구간은 현재 승인 종가와 ATR의 1/4 이내 구간을 기술한 후보다. 손익비는 **현재 종가 진입 기준**으로 별도 계산한다. 상승 관찰가는 실제 관측 저항 후보만 쓰며 부족하면 null이다. 이익을 만들어 내기 위해 2R 목표가를 역산하지 않는다. 방어 후보는 확인된 저점 피벗 그 자체이며 임의 하방 오프셋을 두지 않는다. 방어 거리 1ATR 미만·ATR 결측은 적용을 보류한다. 기존 보유의 명시적 방어선이 있으면 새 피벗으로 교체하지 않는다.

보유 유지·부분 익절·축소·최종 방어·논리 무효화도 각 적용/부적합 조건을 가진다. 관찰가 도달만으로 익절하지 않는다. 부분 익절은 평가이익과 추세 훼손 증거가 필요하다. 이미 발동한 방어 조건을 HOLD 판단으로 소급 취소하지 않는다. 실제 주문은 없다.

## 기업·매크로·이벤트 로컬 계약

`investment-context-v1` JSON은 asof, generated_at, sources, items를 가진다. source에는 원문 상대 경로, SHA256, upstream validation_status, published_at/observed_at이 필요하다. item에는 id/kind/metric/value/unit/source_id/pointer와 동일 시점 정보가 필요하다. pointer는 원문 JSON에 접근하는 키 목록이며 값이 정확히 일치해야 한다. 경로 이탈·변조·중복 ID를 거부한다. 같은 지표의 다중 버전은 조용히 선택하지 않고 보류한다.

financial은 stock_code, period_start/end, CFS/OFS, currency를 추가한다. 같은 전년 기간 성장률, 같은 기간/통화/기준의 영업이익률·부채비율만 계산한다. 신규 수주·수주잔고·실적발표·공시·계약 변경/지연/취소·뉴스·정책/규제/산업 사건을 보존하는 event 유형과 macro 지표 입력을 지원한다. S&P500/Nasdaq/Dow, 미국 금리, 달러·환율, 유가·변동성 지수 등은 별도 metric/unit으로 입력한다. 없는 항목은 만들지 않는다.

발생일 occurred_on, 공개 시각 published_at, 관측 시각 observed_at을 분리한다. 미래 실제 사건은 거부하고 이미 발표된 일정은 scheduled_event/earnings_schedule로 구분한다. 미검증·시각 불명·cutoff 이후 자료는 facts에서 제외한다. 이 검증은 로컬 원문의 무결성과 값·시각 및 upstream 승인 상태 확인이지 외부 제공처 진위의 독립 인증이 아니다.

기존 세대의 재무·매크로는 `preserved_context`로 함께 연결하지만 시점/공식 검증이 부족하면 점수나 GPT fact로 승격하지 않는다. 10/23 게시본의 재무·수급을 10/29 수집본에 임의 혼합하지 않는다. 이번 외부 DART/뉴스/매크로 실연동은 NOT_RUN이다.

## 계좌 원장

`scripts/manage_investment_portfolio.py --ledger <독립경로> --events <사용자확인_JSON>`으로 입력한다. 각 이벤트에 id, kind, at, user_confirmed가 필요하다. 계좌 값은 코드/로그/Git에 저장하지 않는다.

- OPENING_BALANCE: cash_krw, positions[{stock_code, stock_name, market, quantity, average_cost_krw}]. 과거 체결을 생성하지 않는다. 첫 이벤트만 허용한다. 이전 실현손익은 미확인이다.
- BUY/SELL: 실제 수량·가격·수수료·세금·종목·시장을 명시한다. 존재하지 않는 보유 매도나 음수 현금을 거부한다. 수수료를 추정하지 않는다.
- THESIS/DEFENSE: 이유와 evidence_ids를 보존한다. 방어선 하향 및 발동 뒤 같은 투자 주기의 대체를 거부한다.
- DEFENSE_TRIGGER: 기존 규칙과 검증된 관측 근거를 요구한다. 주문 체결은 뜻하지 않는다.

해시 체인·파일 잠금·원자적 hard-link 게시로 같은 ID 중복 적재/동시 추가를 보호한다. 새 실제 매수로 시작하는 투자 주기는 이전 종료 주기의 방어·판단·체결 기록을 별도로 보존한다. 과거 cutoff 조회에는 이후 이벤트를 반영하지 않는다. 마지막 파일 삭제까지 탐지하려면 반환된 head_hash를 따로 보관하고 expected_head로 검증한다. OS 관리자가 삭제할 수 없는 WORM 저장소는 아니다.

현금 포함 평가자산을 분모로 비중을 계산한다. 최대 네 종목/최대 40%, 기본 20~30%·확신 30~35%를 구분한다. 단일 계획 1%/전체 3%는 참고 위험 한도다. 현재 가격이나 기존 방어선이 없으면 자산/위험을 0으로 채우지 않는다. Decimal 계산은 계좌 계산이며 원천 가격을 보정하지 않는다. 반복 분할 체결의 소수 평균원가는 Decimal 연산 정밀도를 따른다.

## GPT 경로와 안전 한계

`strategy-v1.md`는 이번 요청을 명시적으로 버전 관리한다. 저장소에 없는 과거 통합 프롬프트 전체를 복원했다고 주장하지 않는다. API가 대화·메모리를 상속한다고 가정하지 않는다.

기본은 NOT_RUN이다. 실제 연동은 별도 승인·모델 선택·안전한 OPENAI_API_KEY 주입 후 `--allow-live-openai --model <승인모델>`로만 가능하다. 키를 CLI 인수나 파일로 받지 않는다. 키 누락 시 NOT_RUN이며 토큰을 출력하지 않는다. 이 작업에서는 실제 키 접근/호출을 수행하지 않았다.

단일 Responses API 배치에 최대 20개 후보와 최대 4개 실제 보유의 증거를 함께 넣는다. GPT가 각각의 가설·반대 가설·위험·무효화·보유 비교를 해석하고 최종 최대 4개를 순서대로 명시한다. 점수 순으로 자동 대체하지 않는다. 근거가 부족하면 빈 선택을 허용한다.

JSON schema와 검증기는 데이터셋/날짜/종목·모든 인용 fact의 값과 단위·출처 ID를 확인한다. 가설 본문에 임의 숫자/URL을 허용하지 않고, 수치는 구조화된 claims에서만 인용한다. 자연어는 가설로 명시하며 그 해석의 정확성까지 보증하는 것은 아니다. 불완전 점수/가격/계좌/가설 검증, 오래된 거래일로 A/B를 승인하지 않는다.

인증/429/5xx/출력 검증 실패에서 자동 재시도하지 않는다. 실패 요청도 호출 예산에 포함한다. 기본 최대 4회, 상한 20회이며 정상 배치는 한 번이다. 배치 출력 토큰 상한은 12,000이고 실제 토큰·가격/모델 호환성은 실연동 전 확인해야 한다. 입력에 소스 지시가 들어 있어도 명령으로 따르지 않도록 명시한다. 리디렉션으로 키를 다른 호스트에 보내지 않는다. journal에는 요청/응답/프롬프트 해시와 상태만 남긴다. 실연동·금융 판단 정확성은 오프라인 테스트로 검증됐다고 주장하지 않는다.

## 출력·브리핑·사후평가

독립 세대는 market.json.gz, investment.duckdb, records.parquet, briefing.json/html, quality.json, model-journal.json, manifest.json과 실제 검증된 판단이 있을 때 decisions/를 저장한다. 전체 종목은 비후보까지 보존한다. DB 구조화 열·전체 JSON·Parquet 값과 해시를 검증한다. 기존 정상 게시본을 교체하지 않는다. 실패 출력은 독립 임시 세대의 FAILURE.json과 진단 상태로 보존한다.

브리핑은 데이터/국내외 시장/보유 대응/후보/보유 대비 비교/계좌 판단/최종 가격표 순서다. HTML 고정 col 폭·가로 스크롤·종목명 sticky·숫자 nowrap·문자열 escape를 적용한다. 가격표 뒤 설명은 없다. 새 Streamlit 또는 운영 페이지를 배포하지 않는다.

판단은 기존 immutable judgments.record를 재사용한다. mock은 SIMULATED_RECORD이며 실제 GPT 판단/실적과 구분하고 과거 자격을 승격하지 않는다. 사용자 보유/체결과 모델 의견도 별도로 보존한다.

사후평가 명령은 `scripts/evaluate_investment_v2.py`다. 원래 record SHA와 별도 미래 입력 SHA를 모두 요구한다. 미래 입력은 `investment-outcome-input-v1`, stock_code/asof, record_sha256, evidence_dataset_id/evidence_sha256, cutoff, calendar/prices/market/foreign_flows, 선택적 user_trades를 포함한다. 이미 검증된 일봉·시장·관측 시각·동일 가격 기준이 전제다. 미래 bundle의 해시만으로 제공처 진위를 증명하지 않는다.

선택적 context_file/context_sha256은 평가 cutoff 기준으로 검증한 별도 사후 이벤트 계약이다. 원래 입력으로 소급하지 않는다. 실제 5/10/20거래일이 지나고 시점 재현 자격이 있는 경우에만 성과·시장 초과수익·낙폭·수급·원래 방어선 침범 및 선택/타점/추가매수/익절·손절/논리/사후 사건의 복기 축을 출력한다. 미검증·미경과·정지/상폐/조정·누락을 건너뛰지 않는다. 매매 적합성의 인과 검증이나 최적 매매였다는 주장은 하지 않는다. 실제 입력이 없으면 NOT_RUN/보류다.

## 운영과 CI

새 `investment-v2-offline.yml`은 이 기능 브랜치의 관련 코드 push에서만 오프라인 테스트한다. cron·시장 자격·OpenAI 자격·운영 쓰기 권한이 없으며 KIS concurrency를 사용하지 않는다. 테스트 아티팩트는 90일이고 영구 백업이 아니다. 기존 20:30 정기 수집과 Word 예약은 변경하지 않는다.

모든 검증 결과는 `reports/investment-v2-integration/`에 보존한다. 운영 main 병합·대시보드/브리핑 게시·원천 복구·KRX 신규 대조·장기 이력 수집은 후속 별도 작업이다.
