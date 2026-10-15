# 개선 적용 결과 — 2026-10-16

- 코드 반영: `151ba4a`. 기존 DB·원본 일봉·매수 규칙 변경 없음.
- GitHub 전체 회귀 검사 159항목 및 Streamlit HTTP 검사 성공: [실행 기록](https://github.com/0baselabkim-debug/qweasd/actions/runs/38819765390).
- 새 검증 워크플로 수동 실행·결과 게시 성공: [실행 기록](https://github.com/0baselabkim-debug/qweasd/actions/runs/38819774408).
- 실제 10/15 KRX 응답은 두 시장 모두 HTTP 200·0행. 정확한 상태는 자료 미제공(`REFERENCE_NOT_AVAILABLE`).
- 2,430종목 검증 대기, KIS 인증·추가 조회 0회. 워크플로 성공과 매수 판정 성공은 구분한다.
- 실제 체크포인트에서 같은 날짜 반복 수집 후보 22개 → 0개. 짧은 이력 22종목은 새 거래일에 다시 증분 수집한다.
- 당일 매수 가이드는 아직 모두 판정 보류. 원본 가격과 후속 가격 불일치 387종목도 미해결로 유지한다.
- 검증 전용 예약은 등록됐으나 아직 실행되지 않은 미래 예약의 성공을 주장하지 않는다.

## Technical details

The 2026-10-15 reference endpoints returned HTTP 200 with zero rows. This is now
`REFERENCE_NOT_AVAILABLE`, distinct from authentication, network, schema and date
errors. It is not evidence of an official final publication time.

Changes:

- Date-scoped official reference caches; empty/error responses never replace valid
  data. `technical/reference-status.json` records request dates, response dates,
  counts, HTTP status and timestamps without keys or response bodies.
- No KIS token or flow request when a symbol lacks a usable same-date official
  reference. Known price conflicts also stop before a redundant flow call.
- Exact previously received raw flow responses can be reused with provenance;
  absent or inconsistent proof requires a real KIS query. No synthesized flows.
- A dedicated verification workflow checks availability at KST 08:17 Mon-Sat,
  and 11:17/14:17/17:17/20:17 weekdays. These are check times, not claimed KRX
  release times. GitHub may delay runs. It uses the existing shared concurrency
  group and queues rather than cancelling pending work.
- Unaccepted dates are archived under `technical/pending/<date>/` before a new
  day's bundle replaces the current cache. Verification considers these dates too.
- Verified new-listing short histories are deferred for the same date, and become
  collection candidates again on the next date. Genuine API failures still retry.
- Excluded symbols no longer keep the public acquisition queue artificially open.

Unchanged: buy/sell numeric rules, finality and daily acceptance criteria, existing
prices/flows, DuckDB files, macro/financial/theme features, UI and the 20:30/23:30
daily collection schedule. An operational WAITING_REFERENCE outcome is not a
successful signal validation. No forced buy/watch verdict is introduced.

Evidence: `live-reference-check.json` contains the real KRX response checks on a
copy of published data. `validation.json` records regression tests and source
preservation. GitHub execution evidence is recorded after the new workflow runs.
