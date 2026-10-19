# 일일 수급 브리핑 자동 생성·게시 검증

## 범위와 원본 보존

최신 main `7d828ab02edc0ed62e908ce5866474fe33093704`에서 시작했다. `briefing-20261016` 전체를 병합하지 않고 exporter와 공개 시험 JSON만 재사용했다. 시험 브랜치 이후의 공시·가격 복구 변경을 보존했다.

앱 UI, 수급 계산 함수, KIS·KRX·OpenDART 수집 코드 및 기존 DB·재무·테마 자료는 수정하지 않았다. 정규 cron은 UTC 09:30 / 12:00 평일(한국 18:30 / 21:00), 공유 직렬화는 그대로다. 이 작업에서 시장 API 호출은 0회다.

## 구현

- `scripts/export_daily_briefing.py`: 대시보드 계산 재사용, 원본 게시 날짜·해시·검증 상태 확인, UTC 시각·단위·최신성 계약, 실패 시 파일 보존 및 동일 결과 재생성 방지.
- `scripts/verify_daily_briefing.py`: 별도 SQL/원본 합산으로 전체 후보에서 TOP 3 및 섹터 내 TOP 5 순위, 1/5/20일 금액, 직전 5일 대비 변화, 확산율, 수급 강도를 대조.
- `scripts/stage_daily_publication.py`와 `daily-dashboard.yml`: 정상 후보 JSON 생성 후 DB·manifest·validation·ledger·JSON을 한 Git 커밋으로 게시. 이전 정상 세대는 hash별 history에 보존.
- `publish_liquidity.py`: 검증된 가격 복구로 DB hash가 바뀌면 JSON도 같은 커밋에서 갱신.
- `daily-briefing.yml`: 기존 검증 스냅샷만 사용한 수동 초기 게시/복구, 공개 URL 바이트 대조. 새 예약 일정이나 API 호출은 추가하지 않음.
- `tests/test_daily_briefing.py`, 실제 시험 JSON fixture 및 `dashboard-check.yml`: 회귀·변조·결측·실패 격리·중복 게시 방지 검증.
- `docs/daily-briefing.md`: JSON 소비·운영 계약.

## 실제 2026-10-16 데이터 대조

원본 DB SHA256: `d6d2a13ca137d6911ee0f1de83ae955496482141cc30c1d57aafd9e5f75bb11e`.

2,430종목 모집단에서 3개 섹터와 15개 종목을 선택했다. 시험 JSON의 모든 `top_sectors` 값과 정확히 일치한다. 원본 1/5/20일 금액 54건, 개선액·확산율·강도 등 추가 33건, 섹터 순위 3개와 종목 순위 15개를 독립 확인했다.

| 순위 | 시장 | 업종 | 최근 5일 순매수(원) |
|---|---|---|---:|
| 1 | KOSPI | IT 서비스 | 153,769,000,000 |
| 2 | KOSPI | 유통 | 97,170,000,000 |
| 3 | KOSPI | 건설 | 76,959,000,000 |

## 소비자 주의 및 미검증 범위

공개 URL: https://raw.githubusercontent.com/0baselabkim-debug/qweasd/main/snapshot/daily_briefing.json

`asof`는 수급 거래일이다. 10월 19일에 10월 16일 JSON을 읽더라도 당일 자료로 사용하면 안 된다. 21:30 소비자는 한국 날짜와 asof 및 ready_for_briefing/passed 상태를 함께 확인해야 한다. `published_at`은 커밋 준비 시각이며 실제 게시 확인은 공개 URL 접근 결과로 구분한다. 공급자 최종 확정 여부는 기존 UNVERIFIED를 유지한다.

기존 ChatGPT 예약 작업의 내부 프롬프트·실행은 이 저장소 작업으로 검증하지 않았다. 미래 18:30/21:00 schedule 실행은 아직 검증되지 않았으며 수동 Actions 성공과 구분한다. 검증 실패/지연이면 이전 JSON을 유지하고 새 날짜 자료를 생성하지 않는다.

클라우드 개발 환경의 오래된 설치·시작 지침은 실제 사용한 Python venv 방식으로 초안을 저장했다. 이 초안은 환경 설정에서 검토·저장 후 Publish해야 새 환경에 적용되며, 이번 GitHub 기능 배포와는 별개다.

## 실제 게시·재실행

- 구현 커밋: [`fc22417e54489627c647723157dba9958324b8fd`](https://github.com/0baselabkim-debug/qweasd/commit/fc22417e54489627c647723157dba9958324b8fd).
- 실제 JSON 게시 커밋: [`4470d757edfd2d02c9927d51312a67e212a1318e`](https://github.com/0baselabkim-debug/qweasd/commit/4470d757edfd2d02c9927d51312a67e212a1318e).
- 최초 수동 게시: [Actions 39181712575](https://github.com/0baselabkim-debug/qweasd/actions/runs/39181712575), success. 실제 테스트→생성→push→인증 없는 공개 URL 확인까지 성공.
- 같은 스냅샷 재실행: [Actions 39181853550](https://github.com/0baselabkim-debug/qweasd/actions/runs/39181853550), success. main HEAD 및 JSON 커밋 수(1개)가 유지되어 중복 게시 없음.
- 공개 JSON HTTP 200, 검증 파일과 byte 일치. JSON SHA256 `79e1f712899c2da62ff5f4e712c2d8abe53d69f8dbb3a9da166a26fb47cf33e9`. 로컬에서 다시 원본 54+33 대조 및 시험 JSON과 top_sectors 전체 일치를 확인했다.
- 공개 JSON에 포함된 거래일은 2026-10-16이며 10월 19일 자료가 아니다. 오늘 저녁 예약 성공은 아직 주장할 수 없다.
- 로컬 Streamlit 직접 loopback health HTTP 200 / ok. 최초 기본 urllib 경로에서는 health 확인 시간이 초과되어 localhost 직접 연결로 재검증했다. 외부 API 프록시 설정은 변경하지 않았다. 실제 브라우저 조작 검증과는 구분한다.
- Actions 로그 ZIP 다운로드는 클라우드 네트워크에서 Forbidden이므로 로그 본문을 증거로 사용하지 않았다. GitHub 실행·단계 결과 API, 직접 공개 URL 대조, 로컬 회귀 출력으로 검증했다. `publish-run.json`, `idempotence-run.json`, `public-check.json`, `preservation.json`에 근거를 저장했다.

## 최종 테스트 결과

- 최종 공개 JSON을 포함한 로컬 전체 회귀: **197 passed in 196.40s**. 첫 로컬 전체 회귀 196개에 새 정상 게시 경로 테스트 1개를 추가한 최종 수치다.
- 신규 브리핑 테스트: **18 passed in 42.23s** (독립 대조, 변조 차단, 실패 시 보존, 결측, 중복 게시, 정상 후보 게시).
- 구현 커밋의 [CI 39181706285](https://github.com/0baselabkim-debug/qweasd/actions/runs/39181706285): **success**. 전체 테스트 및 Streamlit HTTP 시작 확인 단계 모두 성공. API에서 확인한 단계 결과는 `ci-run.json`.
- 테스트 자료는 임시 경로에만 만들었으며 공개 DB는 원본과 동일하다. 변경된 보호 대상 경로가 없음을 `preservation.json`에서 확인했다.

## 남은 조치

기능 구현·실제 수동 게시·중복 방지·공개 URL 검증은 완료했다. 향후 정규 schedule 자동 게시 및 실제 21:30 ChatGPT 브리핑 실행은 시간이 지난 뒤 실행 기록으로 확인해야 한다. 현재 제공된 도구에는 ChatGPT 예약 작업 조회/수정 기능이 없어 내부 프롬프트는 확인하지 못했다. 기존 작업이 이 공개 URL과 asof/상태 검사를 이미 사용한다면 별도 설정은 필요 없다. 그렇지 않다면 예약 작업에 문서의 소비자 확인 조건을 반영해야 한다. 공급자 최종 확정 미보장(UNVERIFIED)은 이번 작업에서 해제하지 않았다.
