# 전체 시장 수급 JSON v3 구현·검증

## 범위와 호환성

최신 main `0ba1768cbb2c44fa830dae9328174fe982ccf41c`에서 시작해 구현 커밋 `32abcd8131df6235d5870cf5eeb10ae7612f85fb`을 반영했다. 작업 중 기존 가격 복구 예약 실행 39360325355가 정상 종료해 새 복구 자료를 게시했으며, 이를 덮어쓰지 않고 보존한 최신 main으로 생성한다.

기존 v2 `snapshot/daily_briefing.json`의 schema_version=2, 상위 3개 업종 및 각 5종목 의미는 유지한다. 기존 UI, screener 계산 함수, DuckDB 원본 테이블, 수집/API/인증 및 평일 18:30/21:00 일정은 변경하지 않았다. 구현 커밋의 보호 경로 변경 없음은 preservation.json에 기록했다. 이 기능은 추가 증권 API 호출을 하지 않는다.

## 변경 파일

- `briefing_schema.py`: v3 필드·단위·결정적 ASCII sector_id·dataset_id·해시 계약.
- `briefing_raw.py`: SQL 포함/제외 모집단 및 원본 관측값의 독립 집계.
- `export_market_briefing.py`: 기존 계산 프레임을 재사용해 전체 인덱스와 종목 파일 생성. 같은 입력은 동일 바이트 유지. 후보 검증 후 교체하고 설치 실패 시 이전 디렉터리 복원.
- `verify_market_briefing.py`: 전체 업종·종목 식별자, 금액·변화·강도·확산율·양수 일수, 결측 상태, 세대·파일 해시·파일 수 검증.
- `export_daily_briefing.py`: 선택적인 v3 출력과 시간 계측 추가. v2의 필드·선정 의미는 그대로.
- `stage_daily_publication.py`, `publish_liquidity.py`, daily-dashboard.yml, daily-briefing.yml: 정상 일일 후보·가격 복구·수동 게시 모두 v2/v3를 DB 및 manifest와 같은 커밋에 반영.
- `publish_snapshot.py`: 초기 구축용 옛 수동 경로가 현재 ledger 기반 게시본을 우회해서 덮어쓰는 것을 차단. 정상 수집 경로는 변경하지 않음.
- `check_public_market_briefing.py`: 인증 없는 Raw 응답 바이트·해시·세대 검증. 기본 표본 3개 업종, --all은 모든 업종 파일.
- `tests/test_market_briefing.py`, dashboard-check.yml: 신규 회귀·실패·보존 테스트 및 CI 연결.
- README, docs/daily-briefing.md, docs/daily-publication.md: 소비 절차·운영·복원 방법.

## 데이터 검증 방식

대시보드 universe/stock_activity/sector_indicators 프레임을 v2와 v3에 함께 사용한다. 독립 검증은 이 집계 함수들을 호출하지 않고 SQL로 기준일 모집단을 다시 확인하고 원본 일별 정수 금액을 합산한다. 5일/직전 5일은 겹치지 않는다. 섹터 합계는 날짜마다 모든 구성 종목이 있어야 유효하다. 일부 종목이나 날짜가 비면 해당 지표는 null이다.

전체 데이터셋에 공식 종합 점수나 투자 순위는 없다. 음수도 포함하고, 일부 기간 결측 종목도 유지한다. 미분류가 있으면 별도 그룹 및 official_sector=false로 보존한다. 현재 구성으로 과거 구간을 계산하는 한계와 provider_finality=UNVERIFIED는 유지한다.

coverage.population_total은 instruments와 당일 exclusions의 종목 합집합이다. registered_instruments, included, excluded, not_observed를 구분하며 다른 모집단 수를 전체 평가 가능 종목 수처럼 표시하지 않는다. 제외 목록에만 있는 종목의 시장/업종은 추측하지 않는다.

## 소비 절차와 복원

인덱스: https://raw.githubusercontent.com/0baselabkim-debug/qweasd/main/snapshot/briefing/index.json

1. 한국 기준 대상 거래일과 asof, v3 스키마 및 ready_for_briefing/passed 상태를 확인한다.
2. dataset_id/source_snapshot_sha256을 고정하고 전체 업종 지표에서 분석할 3개 업종을 선택한다. 공식 업종은 official_sector=true만 사용한다.
3. 선택한 path를 인덱스 URL 디렉터리에 연결해 종목 파일을 읽는다.
4. 원시 응답 바이트 sha256/bytes, 동일 asof/dataset_id/source_snapshot_sha256, 종목 수를 확인한다. 일치하지 않으면 재시도하며 세대를 섞지 않는다.
5. 전체 stocks에서 관점에 따라 5개를 선택한다. 금액 KRW, intensity ratio, breadth %를 구분하고 null을 0으로 해석하지 않는다.

정확한 내용 해시 직렬화 및 필드 정의는 docs/daily-briefing.md에 있다. v3 이용을 중단할 때 기존 v2 URL로 돌아갈 수 있다. 코드 revert 시 v3 구현 커밋만 되돌리고 기존 DB·공시·수급 이력을 과거 파일로 덮어쓰지 않는다. 갱신되지 않는 v3는 계속 소비하면 안 된다.

## 한계

운영 검증 passed는 공급자 최종 확정 보증이 아니다. 10월 19일 자료를 10월 20일 당일 수급으로 사용하면 안 된다. 미래 정규 예약 실행과 GPT Dot의 실제 후보 선정은 이 작업의 수동 게시 검증과 다르다.

공개 Streamlit 직접 접속은 현재 Codex 네트워크에서 CONNECT 403으로 차단됐다. 이를 공개 앱 장애라고 단정하지 않는다. 기존 UI 코드는 보존됐고 Streamlit AppTest를 포함한 회귀 및 CI HTTP 시작 검증으로 확인한다. 공개 브라우저 클릭 검증을 완료했다고 표현하지 않는다.

## 실제 게시 결과

- 구현: [32abcd8](https://github.com/0baselabkim-debug/qweasd/commit/32abcd8131df6235d5870cf5eeb10ae7612f85fb)
- 실제 데이터 게시: [aa866c2](https://github.com/0baselabkim-debug/qweasd/commit/aa866c288b74ce4130b350e125ea25ed1c597592)
- [게시 Actions 39360596737](https://github.com/0baselabkim-debug/qweasd/actions/runs/39360596737): success. 실제 생성·Git 게시·v2 및 v3 표본 공개 GET/해시 검증 성공.
- [코드 CI 39360561516](https://github.com/0baselabkim-debug/qweasd/actions/runs/39360561516): success. 전체 테스트와 Streamlit HTTP 시작 검사 단계 성공.
- 로컬 전체 회귀 **223 passed in 242.28s**, 신규 **26 passed in 42.63s**. 과거 정상 10월 16일 2,430종목/50업종 생성과 기존 v2 수치 일치도 검증했다. 테스트용 변경 DB는 임시 디렉터리에만 생성했다.
- 게시 데이터 기준일 **2026-10-19**, source SHA256 `cce160ee1dab74a88e9bbd3ba7e5c1e7878a5eef632027d7e2ae58232f532bd0`.
- **공식 업종 50개 / 포함 종목 2,429개 / 업종 파일 50개 + 인덱스 1개**. 총 **2,190,829 bytes**(약 2.19MB), 인덱스 105,963 bytes, 가장 큰 업종 파일 245,861 bytes.
- 공개 URL에서 인덱스와 **50개 업종 파일 전체**를 인증 헤더 없이 수신하고 로컬 검증본과 바이트·SHA·세대·종목 수를 대조했다. 전체 조회 실측 **9.49초**. 전체 원본 지표 재대조 **20,232건 통과**.
- 기존 v2 JSON과 DB는 게시 직전 main `cd7c2a9`와 바이트 단위로 동일하다. 작업 중 DB hash 변경은 별도 기존 예약 복구가 게시한 metadata 변경이며 이번 JSON 게시 커밋은 DB를 수정하지 않았다.

### 포함·제외·결측

| 항목 | 수 |
|---|---:|
| instruments 등록 종목 | 2,433 |
| instruments + 당일 exclusions 합집합 | 2,544 |
| 대시보드 포함 종목 | 2,429 |
| 제외 종목(고유 코드) | 115 |
| 별도 당일 미관측 | 0 |
| 포함 종목 중 5일 결측 | 4 |
| 포함 종목 중 20일 결측 | 7 |
| 5일 집계 불완전 업종 | 3 |
| 20일 집계 불완전 업종 | 5 |
| 미분류 포함 종목 | 0 |

제외 원인은 거래정지 110개, 공식 상장폐지 5개다. 추가로 거래량 0 이하 사유가 3개 코드에 겹치므로 **110+5+3을 제외 총계로 합산하지 않는다**. 원본 사유 배열과 고유 제외 총계를 별도로 제공한다. 순매도 업종 13개, 5일 순매도 종목 778개, 당일 실제 0원 종목 131개를 보존했다. 데이터가 부족한 4/7개 종목도 삭제하지 않았으며 해당 지표만 null이다.

### 성능(실측)

| 단계 | GitHub Actions 실측 |
|---|---:|
| 기존 v2 계산·검증 | 11.194초 |
| 업종 인덱스 작성 | 0.052초 |
| 전체 종목 파일 작성 | 0.246초 |
| 독립 SQL/전체 파일 검증 | 0.238초 |
| v3 추가 전체 비용(파일 설치 등 포함) | 0.563초 |
| v2+v3 전체 생성 | 11.760초 |
| Git 게시 단계(git add/commit/push 포함) | 3초 |
| runner 공개 확인 단계(v2+업종 표본 3개) | 1초 |

표의 생성 수치는 generation.json, 게시/공개 단계는 GitHub Jobs API의 초 단위 시작·종료 기록에서 얻었다. 위 시간은 단일 실행 실측이며 향후 고정 소요시간을 보장하지 않는다. 최초 Codex 로컬 시험은 기존 8.366초 + v3 0.416초였다. 전체 업종 51파일 외부 조회 9.49초는 별도 Codex 실행이며 runner의 표본 조회 시간과 혼동하지 않는다.

### 실제 중복 게시 방지 확인

[동일 자료 재실행 39360936276](https://github.com/0baselabkim-debug/qweasd/actions/runs/39360936276)도 success다. 재실행 후 원격 main HEAD는 여전히 `aa866c2`이며 v3 인덱스의 게시 커밋은 1개였다. 생성 시간 계측 파일이 달라져도 데이터가 동일하면 새 커밋을 만들지 않는다. 근거: idempotence-run.json, dataset-commits.json.

### 남은 확인

- 신규 v3가 포함된 향후 정규 schedule 및 가격 복구 schedule 게시의 실제 실행 기록은 이후 확인해야 한다. 이번 신규 v3 실운영 검증은 workflow_dispatch이며, 기존 복구 schedule 성공과 혼동하지 않는다.
- 공개 Streamlit 브라우저 직접 조작은 네트워크 CONNECT 403으로 미확인. 로컬/CI의 기존 기능 테스트와 HTTP 서버 시작은 성공했다.
- GPT Dot이 이 새 인덱스를 실제로 조회·선정하는 실행은 이 저장소에서 변경하거나 검증하지 않았다. **소비자 읽기 주소를 새 인덱스로 설정하고 docs/daily-briefing.md의 asof·상태·세대·해시 확인 절차를 적용**해야 한다. 추가 API 키나 유료 서비스는 필요 없다.
- 기존 v2의 TOP 3 부족 차단 등 검증 정책도 그대로다. 해당 검증이 실패하면 이전 정상 게시본을 유지하며 v3만 먼저 부분 게시하지 않는다.
