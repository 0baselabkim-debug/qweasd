# 2026-11-02 20:30 KST 정기 수집 사전 점검

## A. 전체 운영 준비 상태 — PARTIAL

**현재 확인한 운영 구성에는 11월 2일 자동 수집을 가로막는 차단 사유가 없다. 기존 예약을 그대로 진행할 수 있는 구성이다.** 기본 브랜치의 실제 workflow 활성화, 기존 KIS Secrets 존재, 공유 잠금 미점유, 대상 슬롯 미점유, 검토된 양 저장소 코드 일치를 확인했다. 이 작업에서 예약을 변경하거나 실행권을 선점하지 않았다.

다만 공식 KRX 웹사이트 접근 차단으로 **11월 2일의 공식 거래일 자료를 확보하지 못했다**. 인증과 시장 API 호출도 지시대로 하지 않았다. 따라서 공식 거래일·실계정 연결·첫 정규 수집 성공까지 검증 완료한 READY로 표시하지 않는다. 기존 운영은 실행 당시 KIS 공급자 휴장일 응답으로 거래 여부를 판단한다.

| 영역 | 판정 | 근거 |
|---|---|---|
| P0 예약·실행 경로 | READY_CONFIGURATION | main/active/UTC cron 일치, Nov2 슬롯 미점유 |
| P1 공유 계정 구성 | READY_CONFIGURATION, LIVE_AUTH_NOT_RUN | 기존 Secrets 두 개 존재 확인, 실제 lease owner null, 코드 해시 12/22 일치 |
| P2 수집·보존 | PASS_OFFLINE; Nov2 실제 결과 NOT_RUN | 운영 main 관련 77개 테스트, 이전 실패 원본 검증 |
| P3 공식 거래일·최종성 | PARTIAL | KRX 공식 웹은 접근 차단, 런타임 KIS 달력 사용, 최종성 UNVERIFIED 유지 |
| P4 독립 V2 입력 | READY_FOR_POST_COLLECTION_VALIDATION | 기존 실데이터 2,346행 재복원·3포맷 일치, Nov2 실제 입력은 아직 없음 |

사전 검증 [실제 Actions Run 41254701865](https://github.com/0baselabkim-debug/qweasd/actions/runs/41254701865)은 **무호출 구성 검사**다. 이 실행의 SUCCESS를 전체시장 수집 성공으로 취급하지 않는다.

## B. 정기 수집 예약 상태

- 저장소 기본 브랜치: **main**. 현재 운영 HEAD: `f36ab970428564e7569ae37d61f2cfa3f644e119`.
- 실제 등록 workflow ID **400006658**, 경로 `.github/workflows/kis-market-2030.yml`, 상태 **active**.
- cron **`30 11 * * 1-5`**, 별도 timezone 설정이 없으므로 UTC. 다음 명목 슬롯은 **2026-11-02 11:30 UTC = 20:30 Asia/Seoul**이다.
- 기존 허용 창은 **20:30~23:20 KST, 170분**이다. 새 정책으로 늘리지 않았다. 스케줄 Run 생성 시각의 UTC 날짜로 슬롯과 대상 거래일을 고정한다. 허용 창 안에서는 UTC/KST의 거래일이 일치하며, 자정 이후 대기로 날짜를 재계산하지 않는다.
- 실제 요청 직전에도 고정 asof/창/세대/예산/공유 계정 상태를 재검사한다. 창이 닫히면 중단하며 전일 자료를 다음 날 자료로 바꾸지 않는다. 기존 다음 날 09:00 제한보다 23:20 마감이 더 엄격하다.
- **실제 수집 step은 `schedule` AND `freeze.outputs.collect == true`일 때만 실행한다.** `workflow_dispatch`와 운영 코드 push는 VALIDATION_ONLY다. Run workflow 버튼을 누르는 것으로 실제 전체시장 수집이 시작되는 구조가 아니다.
- 동일 저장소 `kis-validation-auth`, `cancel-in-progress:false`, `queue:max`로 직렬화한다. 다른 저장소까지 이 concurrency로 잠긴다고 가정하지 않는다.
- `kis-regular-state`의 `ledger/2026.json`에서 거래일/2030별 원자 CAS 실행권을 확보한다. 잠금 대기 후 기존 슬롯과 실제 active Run을 다시 조회한다. 완료/진행/실패 슬롯이 이미 있으면 같은 슬롯을 새로 수집하지 않는다. 실행 attempt 2도 거절한다.

현재 원장에는 `2026-10-29/2030=BLOCKED`, `2026-10-30/2030=FAILED`만 있고, **`2026-11-02/2030`은 없다**. 이전 실패 키가 다음 거래일을 전역 차단하는 로직은 없다. `latest_complete`는 현재 null이므로 정기 수집의 과거 성공 handoff가 있다고 가정하지 않았다.

10월 30일의 별도 수동 Run **41052557610**은 `.github/workflows/kis-manual-now-20261030.yml`의 push 실행이었다. 정기 cron 성공/실패의 증거로 바꾸어 부르지 않는다. 정기 workflow의 마지막 실제 schedule Run은 **40870637994**(10/29, 당시 shared rollout 미승인으로 BLOCKED)다. 현재 main의 10/30 push SUCCESS **41027170596**은 배포 검사이고, 실제 수집 성공이 아니다.

과거 `manual-start/2026-10-30-schedule-disabled.json`에는 사용자의 당시 중단 요청으로 disabled_manually였다는 기록이 남아 있다. 현재 API 상태는 **active**, workflow updated_at은 **2026-10-30 23:30:45 KST**다. 이 과거 기록을 지우지 않았고 현재 활성화를 이번 작업에서 새로 수행했다고 보고하지 않는다.

[GitHub 공식 문서](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)를 다시 확인했다. schedule은 기본 브랜치 최신 커밋에서 실행되며 지연·누락될 수 있다. **20:30 정각 시작을 보장하지 않는다.** 현재 코드가 그대로 유지되면 f36ab970이 실행 기준이다. 이후 main 변경이 있으면 실행 당시 HEAD와 source-bound 가드를 다시 확인해야 한다.

main에서 정기 cron이 있는 파일은 기존 정규 수집과 `full-market-experiment.yml`뿐이다. 후자는 API 상태 **disabled_manually**다. 예전 evening/watchdog 등은 일부 등록 상태가 active로 남아 있어도 기본 브랜치에 파일이 없으므로 현재 cron 경로가 아니다. 기존 감시·복구·실험을 재활성화하지 않았다. [전체 workflow 목록 대조](main-workflow-audit.json).

## C. KIS 공유 계정 상태

[GitHub 무호출 검사](native-preflight-final.json)에서 `KIS_APP_KEY=true`, `KIS_APP_SECRET=true` **존재 여부만** 확인했다. workflow expression이 boolean을 만들며 실제 Secret 값은 검사 프로세스에 주입하지 않는다. 프로세스에 실제 KIS/KRX/OpenAI 자격이 들어오면 거절하는 검사도 추가했다. 새 토큰 발급·Secret 변경은 없다.

현재 작업 자격으로 Secrets 목록·Actions 기본 권한 조회는 HTTP403이었다. 이를 Secret 부재로 단정하지 않고 기존 권한 내의 boolean 검사로 확인했다. 공유 조정을 위한 qweasd의 운영 workflow는 기존 `contents:write`, `actions:read`를 사용한다. 신규 검사 workflow는 **contents:read/actions:read**뿐이며 권한을 확대하지 않았다.

- 중앙 저장소/브랜치: `qweasd / kis-shared-account-state`.
- 확인된 revision: **7110690ce6139d416a9039ed6d56fb11ff4251b5**, sequence **9**, **owner:null**.
- 이전 auth/handover cooldown은 이미 경과했다. Nov2 전에 새 경쟁자가 나타나면 실제 실행 시 다시 판정한다.
- 기존 source-bound proof와 qweasd main **12개**, monitor main **22개**의 SHA256이 일치한다. monitor main은 **8fe527d87271d28431f14b36a233596ab3b73b18** 그대로다.
- 현재 공유 검토는 **2027-01-27T00:00:00Z** 만료이며 대상 창에 유효하다. 사용자 신고 coordinator token 만료일 2027-01-28과 구분한다. monitor 토큰의 현재 유효성/현재 쓰기 권한을 새로 실험한 것은 아니다.
- 기존 Git CAS 비강제 fast-forward와 write/readback 확인으로 소유권을 얻는다. 경쟁 패배·저장소 읽기/쓰기 불가·불확실 응답은 인증 전에 차단한다.
- OAuth와 시장 요청을 **세션 전체에서 배타 보호**한다. 정규 수집은 준비 과정에서 OAuth 1회, 같은 프로세스의 메모리 토큰만 수집 API로 전달한다. 토큰을 파일/JSON/로그/저장소에 저장하지 않는다.
- 토큰 실제 만료 필드와 expires_in을 보수적으로 해석해 고정 실행 창을 덮지 못하면 차단한다. generic 복구 코드가 재인증을 시도하더라도 공유 세션의 1회 OAuth 가드가 두 번째 발급을 허용하지 않는다.
- 다른 소유자가 있으면 강제 취소·자동 탈취하지 않고 BLOCKED한다. 만료만으로 owner를 지우지 않는다. stale 복구는 실제 소유 Run/job 종료·모든 worker 중단·격리 시간·다른 발급자 상태를 확인한 명시적 `Lease.recover` 경로뿐이다.
- 네트워크 결과가 불명확하거나 regular 수집이 FAILED면 lease가 남을 수 있다. 이는 안전 정책이다. 다음 실행 전에 명시적 terminal-owner 검토가 필요하며 이번 점검에서는 lease를 변경하지 않았다. 현재는 남은 owner가 없다.

monitor의 정기 `intraday-monitor-plan.yml`은 **10~15시 KST 시간별 오프라인 watchlist 계획 생성**이며 KIS HTTP를 호출하지 않는다. 실제 조회는 수동 mode/승인 게이트와 기존 공유 wrapper를 사용한다. 이번 작업에서 monitor 파일·예약·기능은 수정하거나 실행하지 않았다. 시간대만 다르다는 이유로 안전하다고 결론 내리지 않았다.

검증 범위는 기존 승인된 양 저장소 Actions 경로다. qweasd에는 오래된 직접 KIS 수동 시험 workflow도 남아 있다. 같은 저장소 concurrency와 active-run 검사가 정규 수집과의 겹침을 막지만, 이를 모든 잠재적인 외부/레거시 호출자까지 중앙 잠금에 등록됐다는 증거로 확대하지 않는다. 현재 활성 KIS 호출자는 발견되지 않았다. 정규 실행 창에 별도 수동 시험이 생기면 안전 차단될 수 있고 기존 실행을 임의 취소하지 않는다.

KIS 제공처의 정확한 quota 적용 단위와 토큰 재발급 무효화 조건은 미확인이다. 계정·앱키·토큰·IP 중 하나라고 추정하지 않는다. 기존 배타 정책과 1.05초 간격/한도를 유지한다.

## D. 데이터 수집 준비 상태

1. 준비 4회: OAuth 1 + KIS 휴장일 1 + KOSPI/KOSDAQ 공식 master 2. 응답·관측 시각·원문 해시를 보존한다.
2. `opnd_yn=N`이면 master/종목 수집 전에 SKIPPED_CLOSED. 날짜 불일치·응답 누락·충돌은 휴장으로 추정하지 않고 실패/차단한다. 주말은 0회 생략한다.
3. 당일 새 master로 적격 모집단을 산출한다. 기존 종목 필터·제외 사유·중복 검사·시장 범위를 유지하고 **2,348/2,343을 고정하지 않는다**.
4. 적격 N개에 수급 1+가격 1, 지수 2개를 조회한다. 같은 프로세스 토큰 재사용 시 기본 예상은 **2N+6회**이며, 준비·인증·실패·복구를 포함한 전체 상한은 **5,600회**다. 따라서 준비를 포함해 N이 2,797개를 넘으면 종목 요청 전에 거절한다. 예산을 늘리지 않는다.
5. 요청 간격 **1.05초**, 기존 timeout·백오프·작업당 최대 6회/최대 두 recovery pass·회차 전체 예산을 유지한다. 인증 오류·원문 유출·영구 데이터 충돌은 무제한 재시도하지 않는다. 같은 회차 성공 응답을 재조회하지 않으며 자동 전체 재수집/추가 시간대 예약은 없다.
6. 거래일·종목/시장·원주가 OHLCV·거래대금·외국인 금액/수량·기관 수량과 수급 응답/가격 응답의 종가·거래량·거래대금 일치를 확인한다. 외국인 금액은 기존 백만원→KRW 변환, 수량은 주 단위다. 기관 금액 원문은 보존하지만 단위 미검증 상태로 두고 KRW 확정값을 만들지 않는다.
7. 시장은 기존 **J=KRX 종목, U=지수**, 가격은 원주가 옵션을 유지한다. NX/UN로 바꾸거나 NXT 포함 수치라고 해석하지 않는다. 10/28의 20:30·23:30 시장 응답이 같았다는 이전 실험은 그날의 관측 증거다. Nov2 또는 모든 거래일에 20:30부터 공급자 데이터가 공식 확정된다는 근거로 일반화하지 않는다. 내부 PASS도 UNVERIFIED/PENDING_KRX로 유지한다.
8. 원문 content hash, metadata hash, master/calendar 원문 replay, 요청 원장, population, DuckDB/JSON 행·값 및 sealed manifest를 검증한다. 누락/충돌을 0이나 과거 값으로 채우지 않는다.

운영 수집기는 **DuckDB·JSON**을 생성·대조한다. Parquet는 기존 운영 출력에 없으며, **독립 V2 evidence/investment 단계의 JSON·DuckDB·Parquet**에서 대조한다. 운영 수집기의 3포맷 검증이 원래부터 있었다고 표시하지 않는다.

10/30 원본 ZIP **590847b13fa83a0634290a2792114b71b5efc9306697a51d5d8a8c31e2f81b74**를 다시 해시 확인하고 manifest **9,401개 파일**을 검사했다. 2,343 목표/2,342 내부 통과/002690 `flow_price_conflict`, 전체 HTTP4,693, 소요 **7,144.58초(약119분)**였다. 기록된 request start 최소 간격은 **1.054622초**, 1.05 미만 0건이다. 이는 원장 시각이며 독립 packet dispatch 측정은 아니다. 이전 복구 one-off의 간격 오류를 운영 regular에도 발생했다고 일반화하지 않았다. **이번에 10/30을 재수집·복구하지 않았다.**

원본·부분 결과는 `data/kis-regular/<generation>/preparation|collection` 및 상위 report/summary/manifest에 남고 `seal`의 credential scan 통과 시 업로드한다. 성공·부분 실패 모두 가능한 원문·HTTP·오류·DB를 보존한다. credential scan 실패 시 유출 방지를 위해 업로드를 차단한다. 강제 종료·업로드 실패 상황에서 저장을 보장하지 않으며 미확인 상태로 남겨야 한다.

정규 Artifact 이름은 `kis-regular-20261102-2030-run-<실제RunID>`다. 보존 요청은 **90일**이고 실제 expires_at은 업로드 후 API로 확인한다. 이전 failed Run의 Artifact **12983267630**은 실제 존재/미만료, 만료 **2027-01-28T12:03:54Z**다. 실패를 정상 handoff로 연결하지 않는다.

성공 finalize는 local manifest/원문/DB를 다시 검증한 뒤 실제 Artifact API의 이름·digest·Run ID·미만료를 확인하여 원장을 COMPLETE_PROVISIONAL로 바꾼다. 운영 finalize 자체가 ZIP을 다시 다운로드하는 것은 아니므로 **소비자는 ZIP SHA256와 내부 원문을 독립 검증해야 한다**. failed 슬롯은 terminal로 남으며 artifact는 해당 Run에서 찾는다. 마지막 정상 게시본을 실패본으로 교체하지 않는다.

## E. V2 분석 연동 준비

- Draft **PR #2**, 시작 HEAD **b81b99cc3c3ae8acda3eb3fe9fc506ac90f87f0d**, 시작 시 open/draft/unmerged/mergeable=true/clean이었다. main 병합은 하지 않았다.
- 검증 시 main-only 0 / feature-only 42커밋(뒤이은 보고서 전용 커밋 제외)으로 현재 main은 기능 브랜치의 조상이다. 이번 변경은 유한 읽기 전용 검사·회귀 테스트·결과 보고서다. 기존 V2 정량/판단/가설/브리핑/기록 코드는 수정하지 않았다.
- 고정 Oct29 기본 adapter를 Nov2 자료로 가장하지 않는다. 새 regular ZIP은 외부 **Run ID/Artifact ID/ZIP SHA/manifest SHA/asof/report 경로/universe 경로**를 명시하는 generic collector proof 경로로 검증할 수 있다. 실제 master·calendar·원장·DB를 replay하며 다른 날짜/모집단을 섞지 않는다.
- 기존 generic 경로는 안전하게 **RETROSPECTIVE_REANALYSIS / historical_backtest_eligible=false**를 유지한다. 신규 자료를 독립 검증하는 경로로 사용할 수 있지만, 이를 당시 실제 GPT 판단 기록으로 자동 승격하지 않는다. 실시간 판단 모드·자동 handoff/게시를 운영에 배포한 것은 아니다.
- Nov2 KRX 자료가 없으면 같은 날짜의 수급 내부 검증은 제한을 표시해 사용하고, 공식 가격이 필요한 지표는 보류한다. Oct29에서 승인된 2,336종목 가격은 Nov2 가격으로 대체되지 않는다.
- GPT 입력은 dataset/date/hash/단위/사실 ID와 검증 상태를 확인한다. 실제 GPT 호출은 기본 NOT_RUN이며 명시적 허용 없이 실행하지 않는다. 근거 없는 숫자·출처를 응답 검증에서 차단한다.
- 기존 briefing 생성 인터페이스와 사후 재분석 표시를 유지한다. 운영 브리핑·대시보드 자동 게시, 주문 기능은 추가하지 않았다.

기존 실제 Oct29 **2,346행** 분석 아카이브를 새 독립 경로에 실제 복원하여 evidence/investment 양쪽 JSON·DuckDB·Parquet와 manifest를 다시 검증했다. [실데이터 재검증](v2-preserved-realdata-check.json). 이를 아직 존재하지 않는 Nov2 데이터 검증 성공이라고 표시하지 않는다.

## F. 테스트 결과

**최종 전체 회귀 694 PASS(기존 675 전부 유지 + 신규 19), 실패/오류/건너뜀 0**이다. 실제 CI `41254671960`, 검증 코드 `60f300f85`의 업로드된 ZIP digest와 JUnit을 다시 검증했다. [TEST_RESULTS.json](TEST_RESULTS.json)에 보존한다.

- 기존 **675개 전체 회귀를 로컬에서 다시 실행: 675 PASS, 실패/오류/건너뜀 0**, 이전 JUnit case ID 전부 유지.
- **운영 main의 실제 코드**에서 정기 수집·공유 잠금 테스트: **77 PASS**, 실패0. 이는 전체 회귀의 부분집합이므로 중복 합산하지 않는다.
- 신규 Nov2 사전 검사 테스트: **19 PASS**, 실패0. UTC/KST, 기존 failed slot 격리, 대상 슬롯의 진행/완료/실패 중복, Secret 미존재, 잠금 owner, default/main/workflow 변경, 실제 자격 유입 차단, 재실행/만료, GET-only, canonical GitHub endpoint와 유한 workflow를 검증했다.
- 기존 휴장/확인 불가·달력 충돌, queue/pending, 시간 초과, 예산·모집단 변화, 원본/DB/manifest 변조, 부분 실패·artifact 후 handoff·lease race/만료/복구 테스트도 유지했다.
- 실제 GitHub 구성 검사 최종 PASS. 초기 오류/실패도 [native-runs.json](native-runs.json)에 남겼다.
- **신규 KIS 시장·OAuth·KRX 시장·OpenAI 호출 모두 0회.** 실제 연결 검증 성공이라고 주장하지 않는다.

## G. 발견된 문제와 처리

| 분류 | 문제·원인 | 처리·영향 |
|---|---|---|
| A | 과거 `AUTOMATIC_2030_DISABLED_BY_USER` 기록과 현재 상태의 혼동 | 현재 API active 재확인. 과거 기록 삭제·예약 변경 없음 |
| A | green push/수동 workflow가 실제 수집 성공처럼 보일 수 있음 | VALIDATION_ONLY와 실제 schedule collection을 코드·원장·history로 분리. 기존 첫 정규 성공은 미검증 |
| A | Secrets 메타데이터 HTTP403 | 권한 확대 없이 GitHub expression boolean으로 두 기존 Secret 존재 확인. 유효성/실인증은 NOT_RUN |
| B | 새 읽기 전용 검사에서 `/repos/.../qweasd/`가 HTTP404 | canonical `/repos/.../qweasd`로 수정. 실제404/200 비교·회귀2건·GitHub 재실행 PASS. 기존 운영 GitHub client는 원래 정상 경로이므로 운영 버그로 오인하지 않음 |
| A | 이전 수동 수집은 002690 수급·가격 충돌 | 보존 원문·manifest 재검증만 수행. Nov2 재현 여부는 미확인. 종목 제외/값 변경/가드 완화 없음 |
| 미확인 | 공식 KRX 미래 거래일 정보 접근 불가 | 공식 웹 Access Denied(IP 차단 페이지)/다른 경로403·404. 차단 우회 없음. 블로그/주중 계산을 공식 근거로 사용하지 않음 |
| 기존 안전 동작 | 실패/강제 종료 시 stale lease가 남을 수 있음 | 현재 owner null, 이번에 lease 변경 없음. 향후 owner가 생기면 종료 증거 없이 자동 삭제하지 않음 |
| 기존 표시 한계 | duplicate/window BLOCKED가 collect를 건너뛰면 Job이 녹색일 수 있음 | Job SUCCESS만으로 성공 판정 금지. 원장/원문/Artifact 조건으로 판정. 현재 Nov2 슬롯은 비어 있음 |
| 기존 범위 | 운영 Parquet 없음, V2 자동 게시/현재 판단 연결 미배포 | 계층별 실제 검증 범위를 표시. 기능 완료/실제 게시를 혼동하지 않음 |

현재 운영을 진행하기 위해 필수 main/cron/Secrets/잠금 정책 변경이 필요하다는 증거는 없다. 이 작업에서 그런 변경은 하지 않았다. green-without-data의 workflow exit code 변경, 자동 stale recovery, 새로운 공식 달력 연결, 실제 판단 자동 연계 등을 운영에 적용하려면 별도 변경 검토·승인이 필요하며 이번에 우회 구현하지 않았다.

## H. GitHub 결과

작업 브랜치는 **feat/decision-evidence-v2**, 기존 [Draft PR #2](https://github.com/0baselabkim-debug/qweasd/pull/2)다. main·monitor·운영 상태 브랜치는 읽기 전용으로 조사했다. 변경 파일·테스트 commit·최종 확인 링크는 [GITHUB.json](GITHUB.json), [CHANGES.json](CHANGES.json)에 기록한다. 마지막 보고서 commit은 최종 응답/브랜치 HEAD에서 확인할 수 있다.

주요 파일:

- `scripts/preflight_regular_20261102.py`: GitHub GET-only, 자격 존재 boolean, source-bound hash/슬롯/lease 확인. provider credential 유입 금지.
- `tests/test_regular_preflight.py`: 신규 19개 오프라인 검사.
- `.github/workflows/regular-preflight-20261102.yml`: 기능 브랜치의 제어 파일 push만 받는 유한 무호출 검사. cron/dispatch/contents-write 없음.
- `.github/readiness/regular-20261102.json`: request ID·대상일·운영 main·만료 창. 신규 정기 예약이 아니다.
- 기존 `investment-v2-offline.yml`: 위 파일의 회귀 검사 경로만 추가. 운영 regular workflow는 그대로다.
- 본 결과 보고서·읽기 전용 메타데이터/검증 영수증.

실제 구성 검사: [41254701865](https://github.com/0baselabkim-debug/qweasd/actions/runs/41254701865). Artifact **13062772261**, ZIP SHA256 **ac03ada70de05043e4742d0f00f49721f33a17a4a269c150010601991eb3d900**를 다운로드해 확인했다. 만료 **2027-01-29T23:58:31Z**. 이는 검사 영수증이며 수집 데이터 아티팩트가 아니다.

실제 최종 회귀: [41254671960](https://github.com/0baselabkim-debug/qweasd/actions/runs/41254671960). 실제 SUCCESS 및 694개 JUnit PASS와 기존 675개 case ID 보존을 확인했다. 이후 변경은 제어 파일/보고서뿐이고 검증된 scripts/tests/workflow 코드는 동일하다.

## I. 11월 2일 정기 실행 조건

1. **추가 버튼 조작 없이 현재 active cron이 기본 main에서 schedule 이벤트를 생성하면 기존 실제 수집 경로로 진입할 수 있다.** 이번 검사나 수동 Run workflow가 그 수집을 대신하지 않는다.
2. 예약은 **11/2 20:30 KST**, UTC 변환은 정확하다. 이벤트 누락/queue 지연은 GitHub 정책상 가능하다. 충분한 수집 시간이 남지 않거나 23:20 창이 닫히면 안전 중단한다. 이전 실제 119분에 준비·설치·queue 시간이 더해지므로 완료 시각을 확정하지 않는다.
3. 실제 실행 시 **Nov2 슬롯 미점유·현재 Run/코드 일치·공유 lease 획득·유효 검토/자격·토큰 수명·KIS 공급자 달력 `opnd_yn=Y`·동적 모집단/예산·시간 창**이 필요하다. 현재 검사는 미래의 경쟁자·자격 유효성·응답 품질을 보장하지 않는다.
4. `workflow_dispatch/push`, duplicate, 공식이 아닌 공급자 휴장 판정, 주말은 의도적으로 수집하지 않을 수 있다. 날짜 확인 불가·권한/잠금/시간/검증 실패는 BLOCKED/FAILED 근거를 남긴다. **현재 Nov2를 VALIDATION_ONLY로 고정하는 설정이나 기존 실패 때문에 무조건 SKIPPED하는 가드는 없다.**
5. 실패하면 가능한 원문·해시·HTTP 원장·실패 종목·부분 DB/JSON·report/manifest를 별도 아티팩트에 보존한다. 업로드 실패/강제 종료는 미확인으로 남기고 이전 정상 세대로 대체하지 않는다. 동일 failed slot을 자동 초기화하거나 전체 재실행하지 않는다.
6. 성공 판정은 **당일 실제 적격 N 전부 내부 통과, 누락/중복/충돌 없음, 지수2개, DB/JSON/원문/manifest 일치, 저장된 실제 Artifact identity/digest 확인, 원장 COMPLETE_PROVISIONAL**이다. KRX 공식 일치/공급자 확정/대시보드 반영까지 완료했다는 뜻은 아니다.
7. 이후 V2 검증은 **Nov2의 정상 handoff → 정확한 Artifact ID/ZIP digest 확보 → ZIP 실제 해시/원문 manifest/날짜/모집단/원장 replay → 새 독립 분석 세대 → JSON/DB/Parquet 검증** 후 시작한다. KRX 미확보 가격은 PENDING_KRX, 공식 확정이 필요한 지표는 보류한다. Nov2 자료가 없으면 분석을 시작하지 않고 Oct29/Oct23으로 메우지 않는다.

**기존 예약 구성으로 진행 가능한 상태를 확인했다. 공식 거래일·실제 공급자 연결·첫 정규 데이터 결과는 아직 검증되지 않았으므로 전체 사전 점검은 PARTIAL이며, 현재 수집이 BLOCKED 상태라고 판정한 것은 아니다.**
