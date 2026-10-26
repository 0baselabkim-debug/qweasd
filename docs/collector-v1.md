# 독립 데이터 수집기 v1

새 개발 브랜치 `feat/market-data-collector-v1`의 수동 표본 수집 기반이다. 기존 앱·snapshot·수집 예약을 변경하지 않는다. 신규 `collector_v1/` 이외의 수집 엔진은 실행하지 않는다. 주문, 추천, 신호, 백테스트 및 자동 예약은 없다. main 병합·배포·전체시장 실수집은 수행하지 않았다.

## 구조와 보증 범위

- `providers.py`: 저장소에서 확인한 공식 경로만 사용. KIS 인증 토큰은 같은 프로세스에서 재사용하며 디스크에 저장하지 않는다. J/KRX, 원주가 옵션 `1`을 사용한다. 기본 종목당 수급·원가격 두 요청이며, 추가 시세 호출은 하지 않는다. 수정주가가 필요하면 별도 검증 설계가 필요하다.
- `core.py`: HTTP 직전에 영속 예산 차감, 응답 원문 gzip 보존, SHA256, 실패 이력, 단일 작성자 잠금, 체크포인트. 인증 응답·인증 URL·요청 헤더는 보존하지 않는다. 외부 리다이렉트는 따르지 않는다. TLS 검증은 유지한다.
- `validation.py`: 수집/검증 분리, 날짜·종목·시장·거래일, OHLC 논리, KIS↔KRX 6개 필드 정확 일치 및 수급 행의 가격·거래량·거래대금 대조. 기관 수량은 기존 `institution()`으로 매수−매도 항등식 검증. 기관 금액은 공식 단위 미확인으로 null, 원문 값만 보존한다. 외국인 금액은 기존 검증 배율인 백만원→원이다.
- `__main__.py`: 초기화·선택·수집·상태·중단·재개·재시도·검증·로컬 게시 명령. 표본은 최대 3종목이다.
- `audit.py`: 기존 DB/원천 파일 범위의 읽기 전용 조사. 과거 자료를 신규 자료로 등록하지 않는다.
- `.github/workflows/collector-v1.yml`: `workflow_dispatch`만 허용. 기존 인증 직렬화 그룹을 공유하고 Git 쓰기 권한은 없다. 실패 시에도 아티팩트를 업로드한다. 실제 Actions 실행/복원은 이 작업에서 검증하지 않았다.

저장 구조 (`data/collector-v1`은 기존 .gitignore의 data/로 제외):

```
plan.json                     선택 날짜/대상/공급자/요청 식별
state.json                    요청별 수집 결과 및 원천 참조
requests.json                 인증 포함 HTTP 시도 및 실패 기록
raw/<SHA256>.gz               응답 원문, 변경 금지
observations/<SHA256>.json     원천/요청/관측 시각/실행 ID
errors.json, task-history/     실패 및 명시적 재수집 이력
last-run.json, quality.json    수집 수량과 검증 보류를 별도로 기록
validated.json                검증 후보; 게시본과 구분
staging/                      DB/파일 저장 실패 시 증거 유지
generations/<세대해시>/       data.duckdb, analysis.json, manifest.json
published.json                단일 원자 교체 포인터
```

DuckDB `observations(kind, identity PRIMARY KEY, payload JSON)`은 정밀한 정수와 공시별 사실을 보존한다. JSON에는 asof, collected_at, request_identity, source, source_run, source_hash, validation_status, provider_finality, publication_status를 기록한다. JSON 내부 상태는 candidate, 게시 manifest의 상태는 published다. 최종성은 검증 통과와 별개로 UNVERIFIED다. 서로 다른 날짜는 결합하지 않는다. 원가격·수정가격을 결합하지 않으므로 재개 시 과거 수정가격 세대를 이어붙이지 않는다. 수급·가격 관측 시각 차이가 15분을 넘으면 재수집 검토가 필요하다.

게시 전에 모든 요청 수집 및 검증 완료를 요구한다. 실패/보류가 하나라도 있으면 이전 published.json을 유지한다. 동일 세대 재게시는 멱등적이다. 다른 원문/정정자료는 새 세대가 된다. 기존 세대와 실패 staging은 삭제하지 않는다. 게시본 DB·JSON 해시도 재검사한다. 로컬 디스크의 세대 게시이며 GitHub/main 게시가 아니다. 서버에서 아티팩트를 내려받아 `publish`를 실행하는 단계는 수동이다.

## 검증된 재사용 / 수정 / 신규 / 보류

| 구분 | 기능과 근거 |
|---|---|
| 재사용 가능 | `market_reference.classify_rows`의 KRX 날짜/코드/스키마 검사, `decision_support.evidence.institution`의 기관 수량 항등식, `corporate.filing_record/financial_record`의 접수번호/통화/기간/YTD 분리, `macro.record/valid_indicator`의 관측일/지표 검증. 신규+관련 기존 회귀 테스트 통과. 현재 API 접근 가능성을 뜻하지 않는다. |
| 수정 후 재사용 | 기존 KIS 경로/파라미터/1.05초 간격과 인증 구조. 신규 transport로 원문 보존, 호출 전 영속 차감, 리다이렉트 차단, 오류 분류를 추가. 기존 `collect_daily_snapshot`은 snapshot 의존·사전 필터링·전체 게시 결합 때문에 직접 실행하지 않음. |
| 신규 구현 | 독립 저장소, 전체 master와 분석대상 분리, 검증 결과/수집 결과 구분, 요청별 재개, immutable 세대 및 DB/JSON 포인터 전환, 수동 Actions 아티팩트 복원. |
| 도입 보류 | 무제한 과거 수급, 과거 전체 모집단 복원, 원인 미확인 가격 충돌의 승인, 기업행동을 넘는 수정가격 연결, 기관 금액 배율, 시장 전체 수급 금액, 비12월 결산/100개 초과 공시 페이지, TTM·ROE·5/20/60일 전략 검증, 전체시장 운영 및 예약 시각. |

기존 `daily_publish_transaction.py`는 main push와 임시 worktree를 포함하므로 신규 실행 경로에서 재사용하지 않는다. 기존 `decision_support/history.py`의 이중 가격기준 및 공식 기준 대조 원칙은 유지하되 장기 지표 엔진은 호출하지 않는다. DART는 기존 corpCode.xml/list.json/fnlttSinglAcntAll.json만 이용하며, 정정공시와 원공시를 별개 접수번호로 남긴다. 자산은 같은 응답의 BS 표준 자산 계정만 읽고 모호하면 null이다. 누락 계정은 추정하지 않는다.

## 확인한 과거 자료 범위 (2026-10-26 조사)

| 기존 DB 자료 | 최초~최종 | 행 / 서로 다른 날짜 |
|---|---|---|
| 외국인 수급 | 2026-09-11~2026-10-23 | 65,573 / 27 |
| 가격 | 2026-09-03~2026-10-23 | 80,896 / 33 |
| 지수 | 2026-08-18~2026-10-23 | 90 / 45 |
| 재무 공개일 | 2025-06-12~2026-10-07 | 2,415 / 122 |
| 종목 구성 valid_from | 2026-10-14~2026-10-23 | 19,436 / 8 |

KRX 참조 파일은 10/14, 10/15, 10/16, 10/19, 10/20 두 시장별 각 1개를 확인했다. 보존된 daily-replay와 10/14 수급 이력 파일에는 각각 2,430종목이 있다. 위 숫자는 파일/DB 보존 범위이며 모든 종목의 연속성·가격 정합성·과거 모집단·당시 공개 가능성을 보장하지 않는다. 추가 API 없이 원문을 재검증할 후보 범위다. 현재 정기 예약 정의는 없다. 예약을 기대하는 기존 테스트 5건은 이 상태와 불일치하며 예약을 복구해 통과시키지 않았다.

재현: `.venv/bin/python -m collector_v1.audit`. 원천 파일을 신규 저장 영역으로 임의 이관하지 않는다.

## 수동 사용

작업 디렉터리는 `/workspace/qweasd`, Python은 `.venv/bin/python`이다. 의존성은 기존 requirements.txt와 pytest==9.1.1을 사용한다. 아래 날짜/코드는 실행 예시이며 수집 사실을 뜻하지 않는다.

```bash
# 같은 한국 날짜의 공식 마스터부터 확보한다.
.venv/bin/python -m collector_v1 init --date 2026-10-26 --mode market
.venv/bin/python -m collector_v1 collect --max-calls 2
.venv/bin/python -m collector_v1 select --symbols 005930,000660
# 다음 한국 날짜에 수급/원가격/거래일/지수/KRX 참조를 확보한다.
.venv/bin/python -m collector_v1 collect --max-calls 20
.venv/bin/python -m collector_v1 status
.venv/bin/python -m collector_v1 validate
.venv/bin/python -m collector_v1 publish
```

동일 날짜 마스터가 없는 과거일은 `historical_universe_unverified`로 보류한다. 당일 시세를 확정 일봉으로 승인하지 않는다. 기존 보수적인 익일 관측 원칙 때문에 시장 데이터 요청은 KST 익일부터 허용한다. **최초 당일 마스터 확보 → 익일 시장 자료 확보**의 두 단계가 필요하며, 한 번의 과거 날짜 실행으로 모집단을 조작하지 않는다. 휴장일은 exchange_closed로 보류하며 게시하지 않는다. 휴장일을 임의 영업일로 대체하지 않는다.

다른 날짜·다른 모드는 `--root data/collector-v1/<날짜>-<모드>`를 모든 명령에서 공통 사용한다. 같은 root의 plan은 덮어쓸 수 없다. 여러 root에서 KIS를 병렬 실행하지 않는다.

- 중단: `... stop`으로 다음 HTTP 전 중단한다. 진행 중 HTTP는 최대 30초 timeout 뒤 체크포인트된다. Ctrl+C 이후에도 이미 완료한 응답별 state는 남는다.
- 재개: `... resume`, 이후 `... collect`. 같은 요청 정상 원문은 해시 확인 후 재사용한다. 전송 실패/429/5xx는 60초부터 지수 backoff를 적용하며 다음 수동 실행에서만 재시도한다. CLI는 최초 수집 오류 시 후속 요청을 중단한다. 자동 즉시 재시도는 없다.
- 인증/권한/스키마 오류는 blocked다. 설정을 수정한 뒤 `status`의 state.json 요청 키로 `... retry --task-key <키>`를 실행한다. 재수집이 필요한 정상 응답은 `--refresh`를 명시해야 하며 기존 응답은 보존한다. 검증 보류를 재수집으로 자동 덮어쓰지 않는다.
- 가격 충돌: 원문과 quality.json을 확인하고 해당 종목 요청만 명시적 refresh한다. 반올림 허용오차를 확대하지 않는다. 같은 날짜 최신값을 자동 선택하지 않는다.
- 로그의 `attempted`는 크래시로 결과를 모르는 호출도 포함한다. 실제 전송 수의 보수적인 상한이며 성공 수가 아니다. 실패 호출도 예산을 소비한다.

기업: 별도 root에서 `init --date ... --mode corporate` → collect(corpCode) → select(최대 3종목) → collect(공시 목록) → select(동일 종목; 접수번호별 financial 작업 생성) → collect → validate → publish. 모든 지원 기간/접수번호를 독립 보존하며, 미래 공개 공시는 사용하지 않는다. 비12월 결산이나 페이지네이션이 필요한 목록은 보류한다.

매크로: 별도 root에서 `init --date ... --mode macro` → collect → validate → publish. 기존 catalog의 ECOS 2개와 FRED 8개를 사용하며 FRED 공식 CSV는 키가 필요 없다. 관측일과 실제 수집 시각을 보존한다. 지연·결측 관측을 선택 날짜 값으로 꾸미지 않는다. FRED CSV 자체의 단위 변경을 실시간 메타데이터로 재확인하는 기능은 아직 없고 기존 catalog 기준이다.

## 접근 요건과 예산

필요한 환경 변수 **이름과 존재 여부만** `... credentials`로 확인한다. 키 값은 채팅·소스·결과에 넣지 않는다. GitHub Actions의 기존 동명 secrets를 사용하며 클라우드 로컬에도 별도 안전한 주입이 필요하다. 이 작업에서는 모든 필수 인증 변수가 부재했다.

| 기능 | 환경 변수 | 필요한 HTTPS 호스트 |
|---|---|---|
| KIS | KIS_APP_KEY, KIS_APP_SECRET | openapi.koreainvestment.com (9443) |
| 종목 master | 없음 | new.real.download.dws.co.kr |
| KRX | KRX_API_KEY | data-dbg.krx.co.kr |
| OpenDART | DART_API_KEY | opendart.fss.or.kr |
| ECOS | ECOS_API_KEY | ecos.bok.or.kr |
| FRED 공식 CSV | 없음 | fred.stlouisfed.org |

현재 프록시는 FRED 터널 연결에 403을 반환했다. 네트워크 설정에서 필요한 호스트 허용 후 재검증해야 한다. TLS 검증이나 접근 제한을 우회하지 않았다.

2단계 검토에서 CLI 누적 예산과 오류 시 중단을 강화했다. 라이브러리 Budget의 범용 기본 누적값은 300이지만 CLI는 명시적으로 20을 적용한다. 다른 root로 나누어 이번 시험의 총 20회 제한을 우회해서는 안 된다.

기본 예산: 프로세스 20회/240초, CLI의 root 누적 최대 20회(재실행/재인증 포함), 요청 시작 간격 최소 1.05초, 순차 요청, 30초 전송 timeout. 최초 마스터 수집은 2회. 3종목 시장 표본은 인증 1 + 휴장일 1 + 지수 2 + KRX 2 + 종목 6 = 12회, 마스터 포함 총 14회(오류/재인증 제외). DART·ECOS·FRED도 같은 물리 예산을 사용한다. 기존 보수적인 auxiliary 정책의 상한을 늘리지 않았다.

기존 instruments의 2,438종목은 **신규 모집단이 아니다**. 이 숫자를 비용 예시로만 쓰면 최소 `2N+8=4,884회`, 시작 간격만 약 85.5분에 재시도·네트워크 시간이 추가된다. 이는 현재 표본/누적 예산을 초과한다. 전체시장 수집 코드는 표본 제한으로 실행할 수 없다. 실연결 및 공식 모집단 확인, 자원·호출 예산 승인 후 별도 변경이 필요하다. 장기 소급 가능 범위와 자동 실행 시각은 확정하지 않았다.

## 검증과 현재 결과

```bash
.venv/bin/python -m pytest -q tests/test_collector_v1.py tests/test_corporate.py tests/test_market_reference.py tests/test_market_budget.py tests/test_decision_prices.py
```

최종 관련 테스트는 **100개 통과(2.17초)**이며, 그중 신규 수집기 테스트는 27개다. 기존 전체 테스트에서 알려진 예약 기대값 불일치 5개는 이번 변경 대상에서 제외했다.

신규 테스트는 전부 `SYNTHETIC_TEST_ONLY` 모의 자료다. 정상 세대 저장, 일부/전체 실패, 중복, 날짜/종목/시장 불일치, 가격/거래량 충돌, 원천 해시 손상, rate limit, 인증 포함 호출 예산, cooldown, 중단·재개, lock, 기관 수량 항등식, 공시 미래정보/기간/접수번호, DB 실패, 게시 포인터 실패 및 복구, 멱등성, 게시본 해시 손상 차단을 검증한다.

현재 KIS/KRX/DART/ECOS 실제 API 호출은 **0회**, 표본 종목 수집 성공 **0**, 실데이터 검증 통과 **0**, 전체시장 수집 미실행이다. FRED는 수집기 1회와 원인 확인용 전송 1회(총 2회) 모두 프록시 거부로 공급자에 도달하지 못했다. 수집기 호출 원장에는 첫 1회가 기록돼 있고, 별도 진단 1회를 여기에 명시한다. 모의 테스트 통과는 실연결 완료를 뜻하지 않는다. 코드 구조와 오프라인 검증 단계이며 운영 구축 완료가 아니다.

정기 실행을 활성화하려면 인증/허용 호스트 설정 → 소수 표본 익일 검증 → 공급자 확정 시각/소요시간/실패율 측정 → 전체 예산 승인 → 사용자 일정 승인 순서가 필요하다. 기존 예약 복구와 main 배포는 수행하지 않는다.
