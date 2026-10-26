# 2차 지정 종목 실연결 시험 준비 결과

**판정: READY — 코드 및 오프라인 검증 완료. 실제 2차 실행은 미승인·미실행.**

이 작업은 기능 브랜치 `feat/market-data-collector-v1`에서 구현·테스트·push만 수행한다. 실제 시험 제어 파일 `.github/trials/collector-v1.json`은 기존 master 단계 그대로 유지한다. 일반 코드/문서 push는 워크플로의 paths 필터에 해당하지 않아 실수집을 시작하지 않는다.

## 복원할 원본 (변경 불가 핀)

| 항목 | 요구값 |
|---|---|
| Run | 40289601356 |
| Artifact | collector-v1-trial-state / ID 12636170227 |
| ZIP SHA256 | 5cbc6ef7db90e4a1f1aaaf95b5e8d78a90bec648a201a488a68e119b42b22a1a |
| 기준일 / 이전 단계 / 상태 | 2026-10-26 / master / PASS |
| 누적 물리 HTTP | 4회, 전부 HTTP 200 |
| 보존 파일 무결성 목록 | 11개 |
| 공식 마스터 | 3,950행 / 적격 2,349 / 코드 중복 없음 |
| 재사용 원본 응답 | 달력 1 + 시장 마스터 2 = 3개 |

위 실제 수치는 사용자가 독립 검증하여 제공한 사실이다. 이번 준비 작업에서는 실제 아티팩트를 다시 내려받거나 시장 API에 연결하지 않았다. 모의 아티팩트가 같은 행 수·적격 수·파일 수·상태 구조를 갖도록 만들어 검증 경로를 테스트했으며, 실제 원본 해시 상수는 변경하지 않았다.

Actions 사전 점검에서 최신 이전 실행이 정확히 지정 Run인지 확인한다. Artifact ID·이름·만료·API digest·workflow_run ID를 확인한 다음 공식 GitHub API로 ZIP을 다운로드한다. **원본 ZIP bytes SHA256을 검증한 후에만** 경로 탈출/심볼릭 링크/중복 ZIP 엔트리/크기 검사를 거쳐 임시 복원한다. 이후 다음 항목을 검사해야 고정된 기존 root `data/collector-v1-trial`로 원자 이동한다.

- 파일별 manifest 해시 및 정확한 파일 목록, 원문 gzip SHA256, 관측 메타데이터의 해시 연결
- receipt의 run/date/stage/status/trial ID/누적 4회 및 마스터 수량
- 원장 길이·순서·요청 종류·HTTP 200과 수집 상태 키 3개 일치
- 달력의 해당 거래일과 개장 여부, 원본 2개를 재파싱한 전체 마스터와 masters.json 완전 일치
- 지정 세 종목 코드·시장·공식 이름과 적격성

수집 단계 진입 직전에도 저장된 ZIP 해시와 원본 ZIP 안 manifest를 현재 root와 대조하고 같은 검사를 반복한다. 복원이 실패하면 제공처 HTTP는 0회인 채 중단한다. 다른 root를 지정하는 CLI 옵션은 없다. 기존 root를 삭제하거나 덮어써서 4회부터 다시 시작하지 않는다. 임시 복원 디렉터리는 검증만 하며 제공처 요청을 발행하지 않는다.

## 수집 및 검증

대상과 날짜는 코드에 고정한다: **000720/KOSPI, 064400/KOSPI, 300080/KOSDAQ, 2026-10-26**. KST 2026-10-27 이전에는 사전 점검 및 매 HTTP 전 예산 검사에서 실행을 차단한다. 이후 실행하더라도 공급자가 10월 26일 행을 주지 않으면 보류하며 다른 날짜로 대체하지 않는다.

기존 `Providers`의 공식 경로/검증된 파라미터만 사용한다. 순서는 지수 2 → KRX 2 → 지정 종목별 수급 1·원가격 1이다. KIS 인증은 같은 프로세스에서 1번, 토큰은 메모리에서 재사용한다. 과거에 성공한 달력·마스터는 파일에서 읽고 재조회하지 않는다.

- 수급: 외국인 금액(기존 검증 배율 백만원→원)·수량, 기관 수량 및 매수−매도 항등식. 기관 금액은 원문만 보존하고 정규화 금액은 null로 유지한다. 기관 수량이 검증되지 않으면 보류한다.
- 원주가 OHLCV: 기존 옵션 `FID_ORG_ADJ_PRC=1`, J/KRX. 날짜·종목 echo·OHLC·mod_yn 검사를 유지하고 수정주가와 결합하지 않는다.
- KRX: 같은 시장·날짜·코드의 공식 open/high/low/close/volume/turnover 6개를 정확히 비교한다. 기존 시장 시총 등 검증도 유지한다.
- 가격 충돌: `validation-failure.json`에 필드별 KIS/KRX 값·차이·출처 해시를 남긴다. API 응답은 received, 검증은 HOLD로 구분하며 그 즉시 다음 요청을 중단한다. 원문을 고치거나 오차를 확대하지 않는다.
- 지수: KOSPI 0001, KOSDAQ 1001의 지정 거래일만 사용한다.
- 종합 검증 통과 후에만 trial root 아래 DuckDB와 JSON을 만든다. DB 재조회 값과 JSON 전체 행/식별자/값을 비교한 뒤 게시 포인터를 전환한다. 동일 세대를 두 번 저장해도 행·세대가 중복되지 않는지 확인한다. 검증 실패 시 정상 게시 포인터를 만들지 않는다.

내부 검증 성공과 공급자 최종성을 구분하며 **provider_finality=UNVERIFIED**를 유지한다. 운영 snapshot/DB나 main에 저장·게시하는 경로는 없다. 시험 아티팩트에는 기존 원본/원장, 2차 원문·상태·실패 보고서·receipt·독립 시험 DB/JSON이 함께 보존된다.

## 호출 예산

| 항목 | 추가 호출 |
|---|---:|
| KIS 인증 | 1 |
| KOSPI·KOSDAQ 지수 | 2 |
| KRX 두 시장 | 2 |
| 3종목 수급·원가격 | 6 |
| **2차 정상 경로** | **11** |
| **1차 4회 포함 누적** | **15 / 최대 20** |

예상 정상 경로는 상한보다 5회 적다. 요청 시작 전에 원장에 차감하며, 인증·실패·결과 불명 요청도 포함한다. 실제 복원 원장 4회를 포함한 누적 20회와 이번 실행 최대 16회를 이중 제한한다. 간격 1.05초, 240초 이후 신규 시작 금지, HTTP timeout 30초를 유지한다. 즉시/자동 재시도 및 조건부 추가 시세 호출은 구현하지 않는다. 첫 오류에서 중단하므로 이번 계획 자체는 11회보다 더 많이 요청하지 않는다. GitHub 제어·ZIP 다운로드·패키지 설치 통신은 제공처 HTTP 예산과 구분한다.

실패 후 원래의 4회 아티팩트로 재시도해 누적 수를 초기화할 수 없다. 최신 이전 실행을 강제하므로 최초 2차 시도 이후 같은 핀으로 새 시험을 시작하면 차단된다. 후속 복구가 필요하면 그때의 최신 실패 원장을 포함하는 별도 검토/승인이 필요하다. Actions Re-run 역시 기존 가드로 금지한다.

## 오프라인 검증

관련 **144개 테스트 통과 / 10.21초**, 이번 추가 **27개** 포함.

```
.venv/bin/python -m pytest -q tests/test_collector_market_trial.py tests/test_collector_trial.py tests/test_collector_v1.py tests/test_corporate.py tests/test_market_reference.py tests/test_market_budget.py tests/test_decision_prices.py
```

모의 원본 복원과 4+11=15 호출 경로, 원장·receipt·원문·manifest 변조 차단, ZIP 핀/탈출 차단, 날짜 가드, 마스터 재수집 차단, 다른 종목 요청 차단, 최초 인증 실패 1회 후 정지, 기준일 부재 시 보류, 6필드 충돌 원문/차이값 보존, 누적 20회 상한, DB/JSON 재조회·멱등성·검증 실패 시 포인터 미게시를 검증했다.

실제 2차 제공처 HTTP는 **0회**다. 실제 pinned artifact를 Actions에서 복원하는 통합 실행, 현재 Secrets의 유효성, 지정 종목의 실제 가격·수급·KRX 일치는 아직 시험하지 않았다. 기존 전체 suite에서 알려진 예약 기대값 불일치 5건은 이번 관련 회귀 명령에 포함하지 않았고 예약을 복구하지 않았다.

## 별도 실행 승인 후 필요한 절차

1. KST 2026-10-27 이후인지 확인한다.
2. 사용자에게서 **실제 2차 실행에 대한 명시적 승인**을 받는다.
3. 그 후에만 제어 파일을 단독 커밋으로 변경한다. stage=market, asof=2026-10-26, previous_run=40289601356, trial_id=master-20261026, 새 request_id, max_total_http=20이 필요하다.
4. 지정 기능 브랜치에 해당 제어 커밋을 push한다. Actions가 최신 이전 run 및 pinned ZIP을 확인하고 복원하며, 실패하면 수집하지 않는다.
5. 실행 결과·호출 원장·원문/가격 차이·DB/JSON 결과를 새 아티팩트에서 확인한다.

이 문서는 절차 설명이다. 이번 작업에서 제어 파일을 변경하거나 2차를 예약·실행하지 않는다.

## 변경 파일

- `collector_v1/market_trial.py`: 고정 아티팩트 복원, 3종목 수집/검증, 원장/DB/JSON 검사
- `collector_v1/trial.py`: 단계별 제어·메타데이터 검증 및 중단 receipt
- `collector_v1/core.py`: 게시 포인터 변경 전 DB/JSON 검증 callback
- `.github/workflows/collector-v1-trial.yml`: master/market 복원·실행 분리 및 관련 테스트
- `tests/test_collector_market_trial.py`: 이번 신규 테스트
- `docs/collector-v1-actions-trial.md`, 본 문서: 단계 구분과 준비 결과
