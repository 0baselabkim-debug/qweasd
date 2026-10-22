# 읽기 전용 의사결정 지원 데이터셋 구현

## 범위 및 결과

기존 정규 수집의 응답을 더 보존하고, 운영 DB와 별개인 분석 DB·JSON을 생성했다. 매매 UI·주문·개인 포트폴리오는 추가하지 않았다. 18:30/21:00 예약, 기본 2회 조회와 예외 조회, 원천 검증 및 v2/v3를 유지했다. **기능 구현 및 기존 실제 자료 검증은 완료했으나, 기술지표의 실제 계산 가능 범위는 원천 검증 부족으로 보류**한다.

실제 재현 결과는 [REAL_DATA_CHECKS.json](REAL_DATA_CHECKS.json), 산식·게시 계약은 [문서](../../docs/decision-support.md), 추가 이력·장중 조사 결과는 [조사 보고](INTRADAY_AND_HISTORY.md)에 보존한다. 공식 예제의 정확한 커밋/해시는 [provenance](official/provenance.json)에 있다.

## 2026-10-21 정상 게시 자료에서 확인한 범위

| 항목 | 실제 결과 |
|---|---:|
| 분석 대상 / 당일 원본 OHLCV 보존 | 2,430 / 2,430종목 |
| 기관 당일 순매수 수량 | 2,430종목 |
| 기관 5·20일 수량 | 각각 0종목 — 기존 날짜별 보존 자료의 연속성/요청 근거 부족 |
| 기관 원화 순매수 금액 | 0종목 — 해당 API 기관 필드의 공식 배수 근거 대기 |
| 외국인 20일 순매수 | 2,419종목 |
| 공시일 검증 연간 / 기간 재무 연결 | 2,408 / 2,408종목 |
| 당일 공식 KRX 6필드 대조 통과 | 0종목 — 당일 공식 KRX 근거 없음 |
| SMA·ATR·일목·시장 상대강도 실제 산출 | 0종목 — 가격 검증 보류, 임의 값 없음 |
| 추가 시장 API 호출 | 0회 |

기관 순매수 수량과 매수−매도 수량을 검증했고, 금액 원문은 별도 단위로 보존했다. 단위 미확정을 수집 실패와 혼동하지 않는다. 향후 정규 응답의 전체 수급 행을 저장하므로 자료가 갖춰지면 해당 일수의 수량을 자동 계산한다. 기관 금액은 공식 단위 근거를 코드·문서에 명시한 검증 변경 없이는 활성화되지 않는다.

## 변경

- `scripts/collect_daily_snapshot.py`: 이미 받은 전체 수급 행과 공개 요청·관측 시각을 추가 저장. 추가 HTTP 요청 없음.
- `screener/decision_support/`: 공식 6필드 대조·기업행사 보류·결측 처리·기술지표 계산·객관적 수급/가격 증거.
- `scripts/build_decision_support.py`: 기존 원본을 재사용해 독립 DB와 업종 분할 JSON 생성. 원본 해시·세대·수급 독립 합계 대조. 실패 격리.
- `.github/workflows/decision-support.yml`: 기존 성공 게시 이후 실행되는 보조 워크플로. 신규 cron·시장 API 키 없음.
- `.github/workflows/dashboard-check.yml`: 신규 모듈/테스트의 기존 CI 경로 추가.
- `tests/test_decision_support.py`, 문서·실제 검증 보고 및 `snapshot/decision-support/`.

DB는 `institution_flows_daily`, `stock_ohlcv_daily`, `indicator_snapshots`, `source_files` 네 개 테이블이다. 기존 `dashboard.duckdb` 스키마 및 내용은 이 변경으로 수정하지 않았다. 실행 중 기존 운영 복구/공시 작업이 main에 게시한 변경은 별도로 보존·통합한다.

## 게시와 지연

보조 검증 실패는 기존 정상 수급 게시를 되돌리거나 중단하지 않는다. 이전 정상 분석본은 보존하되, 소비자는 정상 manifest와 **기준일 및 DB 해시**를 확인해야 한다. 새 정상본 게시와 분석본 생성 사이에는 구세대 분석본을 사용하면 안 된다. 파일 묶음은 단일 Git 커밋으로 게시한다. 동시 운영 게시와 충돌하면 force-push 없이 실패하여 원격 정상본을 보존한다.

기존 10월 21일 수집은 약 93분 4초였다. 이는 과거 실행 실측이며 이번 코드로 새 거래일 전체 수집을 재실행한 값이 아니다. 분석 생성 소요시간은 REAL_DATA_CHECKS의 실측값을 따른다. 요청 간격·호출 수 변화는 없지만, 전체 수급 행 보존에 따른 향후 체크포인트 직렬화·압축 증분은 아직 새 정규 실행에서 측정하지 않았다. 별도 보조 빌드 시간은 기존 정상 게시 완료를 막지 않는다.

## 남은 사항

- 기관 금액의 공식 단위 배수 확인. 다른 API 또는 외국인 필드의 단위를 전용하지 않음.
- 최신일 및 충분한 과거 공식 가격 대조, 기업행사/조정 기준 확인. SMA120 최소120개 유효 종가, 현재 구름 B 최소78개 유효 OHLC 필요.
- 동일 업종 역사 지수, 검증된 차입금 세부·수주/계약·자금조달·주주환원 상세 자료 부족. 사실을 추정하지 않음.
- TTM/PER/ROE는 비교 가능한 기간/귀속 기준 확인 후 연결. 기존 재무 평가를 변경하지 않음.
- 새 거래일의 예약 수집→분석 자동 게시 성공은 실제 미래 실행 후에만 판단. 이번에는 수동 보조 실행 및 기존 실제 자료로 검증.

최신 데이터 진입점: https://raw.githubusercontent.com/0baselabkim-debug/qweasd/main/snapshot/decision-support/index.json

기존 대시보드: https://0baselabkim-debug-qweasd-app-nuf4kg.streamlit.app/

## 실제 배포 및 최종 검증 (2026-10-22)

- 구현 커밋: [`b26431bb`](https://github.com/0baselabkim-debug/qweasd/commit/b26431bb97e859598958f31fe3398abc998b3bda).
- **GitHub Actions가 직접 생성·게시한 데이터 커밋**: [`c2e3eaef`](https://github.com/0baselabkim-debug/qweasd/commit/c2e3eaef4d05b8d4be49680349f0149d310e7493).
- 실제 생성·게시 [실행 39731694568](https://github.com/0baselabkim-debug/qweasd/actions/runs/39731694568): 성공. 실행 06:04:32~06:05:55 UTC(한국시간 15:04:32~15:05:55), 전체 **83초**, 생성·독립검증 **33.355초**, 시장 API **0회**.
- [동일 입력 재실행 39732000928](https://github.com/0baselabkim-debug/qweasd/actions/runs/39732000928): 성공. main은 `c2e3eaef`를 유지하여 중복 게시하지 않음.
- 최종 게시 코드·자료 커밋 `c2e3eaef`의 [CI 39731997930](https://github.com/0baselabkim-debug/qweasd/actions/runs/39731997930): 회귀 및 Streamlit HTTP health 성공.
- 최종 로컬 전체 회귀 **269개 통과 / 실패 0개**, 272.65초. [원문](published-regression.txt). 초기 268개 회귀 후 교체 실패 복구 테스트 1개를 추가했다. CI 콘솔 로그 직접 다운로드는 403으로 막혔지만, 공식 run/job/step 상태와 실제 게시 파일은 별도로 확인했다. [실행 기록](EXECUTION_RESULTS.json).
- 2,430종목 외국인 1·3·5·20일·직전5일·개선액 **14,580건 독립 SQL 대조**, 별도 DB와 JSON 동등성 및 기관 수량 집계 검증. KOSPI·KOSDAQ 10종목 원본 표본 대조 통과. [재현 기록](REAL_DATA_CHECKS.json).
- 인증 없는 **main 공개 JSON HTTP 200**, 기준일/정상 manifest 해시 일치, KOSPI·KOSDAQ 표본 shard 해시와 압축 DB 해시 확인. [공개 URL 검사](PUBLIC_JSON_CHECK.json).
- 기존 운영 DB·manifest·검증·ledger·v2/v3·기업 상태·app·정규 workflow 9개 파일은 통합한 최신 운영 main `4ce53478`과 동일. [최종 보존 검사](PROTECTED_FINAL.json). 작업 도중 기존 운영 복구·공시가 게시한 변경도 보존했다.
- 기존 공개 브라우저 검사에 실제 배포 해시를 실패 전 기록하도록 진단 순서만 보완한 커밋 `fae680aa`. UI·허용 기준을 바꾸지 않았다. 이후 보고서/검증 기록 보충은 분석 코드·원본 데이터 변경이 아니다.

최종 데이터: **2026-10-21**, 정상 원천 DB SHA-256 `827a7cbaf02fc938e914f9955729e6877fda4b931c7e3e325876031161042b8b`.

### 공개 앱 별도 차단 사항

[공개 웹 재검사 39732428691](https://github.com/0baselabkim-debug/qweasd/actions/runs/39732428691)는 **실패**했다. 앱 접속은 HTTP 200이지만 실제 실행 커밋은 `5545fce140411702b77ad19419b1bba03fc25a62`(2026-10-21 20:37:30 KST)이며, 데이터 해시는 `c88b68bbeab6512caca5c9f0ea01fea0535b216e6a0bf4055741b9899ae96948`이다. 최신 정상 게시본도 기준일은 10월 21일이지만 이후 가격 복구 버전이므로 **기준일만 같다고 최신 반영 성공이라고 할 수 없다**. [실제 배포 비교](PUBLIC_APP_CHECK.json).

버전 검사에서 멈췄으므로 이번 공개 앱의 전체 조작·CSV 검증을 완료했다고 보고하지 않는다. 로컬/CI 앱 검사와 공개 앱 반영은 구분한다. 클라우드 내부 동기화 지연/정지의 원인은 아직 확정하지 않았다. 로컬 추가 브라우저 확인도 환경의 터널 연결 오류로 실패했으며 우회하지 않았다.

JSON 자동 생성·공개 접근에는 사용자 조치가 없다. **웹 최신 버전 반영을 위한 최소 조치**는 Streamlit Community Cloud(`https://share.streamlit.io/`) 앱 목록 → 해당 앱의 `⋮` → **Reboot app**이다. 관리 권한이 없어 직접 재부팅하지 않았다. 재부팅 후에도 다르면 연결 브랜치가 `main`인지와 앱 로그를 확인해야 한다. 코드 수정이나 인증키 재발급은 필요하지 않다.

### 완료 수준

**구현·Actions 실제 생성·게시·공개 JSON 검증 완료 / 지표 데이터 검증은 부분 완료 / 공개 웹 최신 반영은 미완료.** 오늘 18:30의 미래 정기 실행 성공을 주장하지 않는다. 기존 성공 게시 후 분석 작업이 실행되도록 연결했으며, 기관 금액 공식 단위와 충분한 공식 가격·기업행사 근거가 없는 지표는 앞으로도 보류한다.
