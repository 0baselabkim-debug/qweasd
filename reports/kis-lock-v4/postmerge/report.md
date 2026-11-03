# 중앙 잠금 V4 운영 전환 결과 — PARTIAL

2026-11-03 KST 검증. 실제 KIS/OAuth/KRX/OpenAI/Telegram 호출은 모두0이다. 기존 두 참여자 보안 패치의 독립 반영은 완료했으며 신규 통합 저장소의 세 번째 참여자 활성화는 차단 상태다.

## A. 종합 판정

- MERGE_COMPLETE: 기존 장후/장중2개 시스템 완료, 신규 통합1개 미완료(2/3).
- OPERATIONAL_LOCK_VERIFIED: 기존2개 운영 main에 대해 GitHub-only 원장 접근·격리 ref 취득/소유권/반납 및 소스 해시 검증 PASS. 세 번째 참여자는 BLOCKED.
- READY_FOR_LIMITED_LIVE_TEST: 신규 통합 시스템은 BLOCKED. 자격 주입, 실제 원장 권한 검증, schema2 등록/승인 정책 및 실제 병합 소스 일치 확인이 남았다. 실제 KIS2회는 별도 실행 승인 대상이다.
- BLOCKED_BY_OFFICIAL_DATA: 공식 가격0, PENDING_KRX2,348, 실전 후보0을 유지한다. 중앙 잠금 문제와 별개다.

## B. 신규 저장소 자격

최신 [무호출 검사41555024044](https://github.com/0baselabkim-debug/investment-integrated-dev/actions/runs/41555024044), HEAD8e5efb7efb239b4879d8e6e80906d18928cdf3d4에서 KIS_APP_KEY=false, KIS_APP_SECRET=false, central_GitHub_token=false다. KIS_COORDINATOR_TOKEN은 central_GitHub_token 항목으로 확인했다. 기존 Run41547226090도 실패 job만 공식 재실행한 attempt2에서 같은 결과였고, 두 ZIP의 GitHub digest를 검증했다.

현재 연결은 Repository Secrets 목록 및 등록 공개키 API에403(Resource not accessible by integration)이다. 따라서 Secret의 절대적인 존재 유무를 확정할 수 없지만 Actions 실행에 세 자격이 미제공됐다는 사실은 확인했다. 소유자는 개인 User(Organization 아님), Environment0개, Workflow environment 미지정이다. 이름3개는 정확하며 자체 저장소 push이므로 fork/Dependabot/PR Secret 제한이나 reusable workflow 전달 누락으로 설명하지 않는다. Workflow contents/read와 actions/read를 늘리는 방식으로 해결하지 않았다.

**필요한 최소 사용자 조치:** [신규 저장소 Repository Secrets](https://github.com/0baselabkim-debug/investment-integrated-dev/settings/secrets/actions)에 보관 중인 기존 KIS_APP_KEY, KIS_APP_SECRET, KIS_COORDINATOR_TOKEN을 등록한다. Environment Secrets를 선택하지 않는다. 토큰은 기존 중앙 qweasd 저장소 Contents read/write 및 Actions read 권한이 필요하다. 기존 암호화 Secret 원문은 GitHub에서도 조회·복사할 수 없으며 채팅으로 요청하지 않는다. 새 KIS 계정·앱키 발급은 불필요하다. 연결 도구로 등록하려면 신규 저장소 Secrets read/write 권한이 추가로 필요하지만 이번 작업에서 권한 확대/토큰 발급은 하지 않았다.

신규 PR#1은 Draft·미병합, main0939b9e5bba7c90fda2096f159a0766eb676bf58이다. 계정 등록과 실조회 승인은 분리했고 schema2 enabled=false, ENABLE_LIVE_KIS_VALIDATION=false다. 격리 GitHub-only 수동 시험 준비만 했으며 미주입 검사 실패 후 probe는 실행되지 않았다.

## C. 비정상 Run40811582221

실제 저장소는 qweasd가 아니라 kis-intraday-monitor다(qweasd GET404, monitor GET200). 직접 조회 queued/conclusion=null, 생성·수정2026-10-29T03:20:26Z, Job0, Check Run0, Check Suite started_at=null, billable{}다. 상태별 전체 활성 목록에는 없지만 이것만으로 종료로 간주하지 않았다.

같은 concurrency 그룹의 실제 main offline Run41551747569와 병합 후41553770787이 완료됐다. 운영 lease 소유자도 비어 있다. 옛 ec4aa5c 소스의 일반 실조회 정책은 disabled이며 잠금이 없던 bootstrap/flow 경로는 이미 사용된 immutable claim tag를 OAuth 전에 검사한다. 현재 인증된 GitHub GET 응답을 정확한 path/status/body별로 보존해 옛 claim_once 함수를 실행했고 두 경로 모두 OAuth 이전 차단을 재현했다. 이는 실제 KIS 호출 시험이 아니다.

따라서 현재 executor/잠금 점유나 보안 패치 배포 차단에서는 제외했다. queued 표시의 GitHub 내부 원인은 확인 불가이며 상태를 terminal/종료로 바꾸어 보고하지 않는다. V3의 일반/강제 취소409 이후 같은 취소를 반복하거나 Run 삭제·운영 잠금 강제 해제를 하지 않았다.

## D. 실제 운영 반영

| 저장소 | PR | 최종 main | 실제 병합 UTC |
|---|---|---|---|
| qweasd | [#4](https://github.com/0baselabkim-debug/qweasd/pull/4) | 4b256c4f3e383aeadc040e9a0a67d0ca09e29ac7 | 2026-11-03T00:46:57Z |
| kis-intraday-monitor | [#23](https://github.com/0baselabkim-debug/kis-intraday-monitor/pull/23), [#24](https://github.com/0baselabkim-debug/kis-intraday-monitor/pull/24) | 21f08a94646b16c144e79d20083ba1a0caafce12 | #23 00:33:59Z / #24 00:36:38Z |
| investment-integrated-dev | [#1](https://github.com/0baselabkim-debug/investment-integrated-dev/pull/1) Draft | 0939b9e5bba7c90fda2096f159a0766eb676bf58 | 미병합 |

V4에서 명시적으로 허용한 기존 두 참여자의 독립 보안 반영 경로를 사용했다. 장중 패치를 먼저 병합하고 실제 main SHA를 qweasd 접근 검토에 연결한 뒤 qweasd를 병합했다. old/old, old/new, new/old, new/new 네 조합의 실제 로컬 Git CAS 양방향 배제를 검증했다. 중간 배포 호환성 없이 부분 병합하지 않았다. qweasd KRX PR#3은 미병합이다.

활성 승인 정책과 운영 lease는 schema1 기존2참여자를 유지한다. schema2 제안 및 세 번째 참여자는 비활성이다. 기존 승인 플래그를 새로 true로 꾸미지 않고, 실제 source-bound Native proof로 재바인딩했다. 기존 승인 파일 범위를 줄이지 않았으며 qweasd22개/monitor28개 runtime source SHA256과 보고서 digest가 실제 main에 일치한다. 공통6개 core 파일은 두 main과 신규 개발 HEAD에서 동일하다. 승인 proof SHA256: 7de21d923cf6ad8593ed3f1b2e91f5d719c69cdf90c321c0286a53e02fc0161b.

두 실제 Actions [qweasd41555324167](https://github.com/0baselabkim-debug/qweasd/actions/runs/41555324167), [monitor41554114948](https://github.com/0baselabkim-debug/kis-intraday-monitor/actions/runs/41554114948)은 자격 존재·운영 원장 읽기 및 동일 중앙 저장소의 격리 시험 ref에서 취득/check/반납2전이를 각각 확인했다. 실제 GitHub 권한 검증은 순차 취득/반납이며, 3자 동시성 증거는 별도 프로세스 로컬 Git CAS 시험이다. 운영 lease를 시험용으로 점유하지 않았다.

qweasd 기본 github.token으로 private monitor Actions 목록을 읽는 부가 관측은 UNREADABLE로 남는다. 이를 숨기거나 권한을 확대하지 않았다. 계정 배제는 각 실제 HTTP 이전 중앙 Git CAS 소유권 검사로 수행하며, 원장 오류/불명 결과/복구 정보 부족 시 fail-closed다. 자동 강제 해제는 없다.

## E. 구현·테스트·아티팩트

기존 구형 클라이언트/보조 진입점/Session/물리 전송 보호 패치를 운영 반영했다. 이번 안정화 수정은 source proof의 기존 request_id 부재 때문에 rollout receipt가 생성되지 않던 보고 오류 수정, proof_kind/digest 보존과 회귀 테스트1개 추가, 기존 승인 소스 목록 전체 보존, 실제 병합 소스 해시 재바인딩, 무호출 수동 CI, 격리 시험 control이다. 관련 파일은 collector_v1/regular/runner.py, tests/test_regular_collector.py, kis_shared/native_proof.py, 활성/제안 승인 JSON, kis-regular-access.json, .github/lock-v3/readiness.json, Native/회귀 workflow 및 reports/kis-lock-v4다.

| 최종 실행 범위 | 통과 | 실패 | 건너뜀 | 증거 |
|---|---:|---:|---:|---|
| qweasd 실제 main 전체 회귀 |524|0|0|[41555331792](https://github.com/0baselabkim-debug/qweasd/actions/runs/41555331792)|
| monitor 실제 main 전체 회귀 |583 +219 하위 검사|0|0|[41554118636](https://github.com/0baselabkim-debug/kis-intraday-monitor/actions/runs/41554118636)|
| 신규 개발 HEAD 전체 회귀 |413|0|0|[41555031222](https://github.com/0baselabkim-debug/investment-integrated-dev/actions/runs/41555031222)|
| 실제 두 main + 신규 개발 소스 Native proof |33|0|0|native-postmerge-actual-sources.json; 실제 별도 CLI·GitBackend·클라이언트, 최종 HTTPS만 모의|
| main Native CI |33|0|0|[q41555328137](https://github.com/0baselabkim-debug/qweasd/actions/runs/41555328137), [monitor41554118636](https://github.com/0baselabkim-debug/kis-intraday-monitor/actions/runs/41554118636); 고정 client bundle와 해당 core 사용|

위0실패는 최종 회귀 기준이다. 신규 저장소 readiness는 자격 부족으로 FAIL/BLOCKED이며 실계정 연동 성공이 아니다. monitor 최초 probe는 V3 stop control false 때문에 BLOCKED였고, 격리 ref만 허용하는 PR#24 후 실제 main에서 PASS했다. 잘못된 로컬 소스 경로로 시작한 초기 Native 실행은 증거에서 제외하고 정확한 main 경로로 재실행33PASS했다.

qweasd 배포 push 검증 Run41555281842는 같은 concurrency 그룹의 retired 시험 workflow들과 동시에 생성된 후 Job0 상태로 cancelled였다. 실수집 실패/성공으로 간주하지 않았다. 이후 [41555515515](https://github.com/0baselabkim-debug/qweasd/actions/runs/41555515515)를 명시적 workflow_dispatch 검증 모드로 실행해 성공했다. 이는 코드상 시장 수집이 불가능한 모드다. 실제 보고서는 DEPLOYMENT_VALIDATED_NO_MARKET_HTTP, READY_SHARED_COORDINATION_FIRST_REGULAR_RUN_NOT_VERIFIED, slot_claimed=false, market_http=0, cron30 11 * * 1-5 및 source proof digest다. artifact13195210527 ZIP/digest와 내부 manifest3파일을 검증했다. 원문 기반 source proof의 request_id는 임의 생성하지 않고 null로 보존했다.

[대시보드 기동 회귀41555281814](https://github.com/0baselabkim-debug/qweasd/actions/runs/41555281814)도 PASS다. 실제 앱 배포/운영 게시를 한 것은 아니다. 신규 개발 [실데이터 재현41555031295](https://github.com/0baselabkim-debug/investment-integrated-dev/actions/runs/41555031295)은 기존2,348종목 V2 차이0, 공식 가격0/PENDING2,348/후보0을 유지했다. 새 시장 조회가 아니다.

다운로드한 최종 CI ZIP마다 GitHub digest/SHA256/ZIP CRC/JUnit을 대조했다. monitor JUnit802는583테스트+219하위검사다. 상세 artifact id/digest/만료일은 artifact-verification.json이다. 잠금/회귀 증거는14일, 정규 배포 검증 아티팩트는90일 보존 후 만료될 수 있다.

## F. 운영 영향

- 실제 KIS/OAuth/KRX/OpenAI/Telegram 호출0. 주문0. Secrets 생성·변경·삭제0. 권한 확대0.
- qweasd와 monitor main은 승인된 보안 패치/필수 시험 설정만 변경했다. 신규 main과 독립 KRX PR#3은 불변이다.
- 모든 cron 정의 불변. 기존20:30 cron 활성 유지. 중단된 full-market-experiment 상태disabled_manually 유지. 새 반복 예약/기존 실험 재무장0.
- 운영 snapshot/data/research/DB/Parquet/앱 소스 변경0을 Git blob diff로 확인했다.
- 운영 lease 원본·revision5042ac0499c09aca639eb53ecc76845bb03b276d/sequence12/owner=null 불변. 정규 ledger/2026.json 및 기존 세대 불변.
- 변경된 원장성 정보는 승인된 코드 소스 정책, 격리 probe ref(최종sequence22/owner=null), kis-regular-state/deployments/41555515515.json 무호출 배포 영수증이다. 전체 상태 브랜치가 불변이라고 주장하지 않는다.
- 다음 기존20:30 KST 슬롯은2026-11-03이다. GitHub schedule 지연·누락 가능성 및 실제 첫 정규 수집 성공은 별도 검증 대상이다.

## G. 남은 차단과 다음 단계

1. 신규 저장소 Repository Secrets3개 미주입: 현재 연결의 Secrets API403으로 직접 해결 불가. 위 최소 등록 후 무호출 Actions 검사와 실제 동일 격리 ref 취득/반납을 다시 수행한다.
2. 실제 세 번째 참여자 권한 확인, PR#1 실제 병합 코드 조합·source hash 확인 및 schema2 활성 승인 정책 적용: 자격 문제 해결 전에는 수행하지 않는다. 기존 두 참여자 정책을 조용히3자 승인으로 바꾸지 않는다.
3. 제한적 실조회는 별도 승인 후000720, 읽기 전용, OAuth 포함 최대2물리요청, 정규 수집 중 금지, 중앙 Session 필수다. 통합 신규 프로세스는 외부 토큰 캐시를 읽지 않으므로 POST /oauth2/tokenP 1회와 GET /uapi/domestic-stock/v1/quotations/inquire-price(FID_COND_MRKT_DIV_CODE=J, FID_INPUT_ISCD=000720, tr_id=FHKST01010100) 1회를 예산으로 잡는다. 제공자가 같은 토큰을 반환하더라도 OAuth 물리 요청1회로 계산하며 신규 발급으로 단정하지 않는다. 인증을 반복 요청하지 않는다. 응답에 거래 기준일이 없다면 UNAVAILABLE로 남기고 과거 기준일로 추정하지 않는다. 실제 전송 전후 소유권/기한, response 구조·종목·제공 날짜 상태, 원문 SHA256 및 격리 저장/반납을 확인하며 주문·redirect·자동 재시도는 금지한다. 미승인 상태에서는 실조회0을 유지한다.
4. KRX 공식 가격 문제는 독립 후속 과제다. 공급자 범위/날짜/6필드 공식 원본 대조 없이 가격·기술지표·실전 후보 승인을 해제하지 않는다.
