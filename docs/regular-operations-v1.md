# Regular20:30 collection: operational repair V1

The weekday20:30 KST registration remains `30 11 * * 1-5` on main. There is no
new cron, watchdog schedule,23:30 run or revival of cancelled experiments.
GitHub may delay or omit scheduled events; this patch cannot guarantee an exact
start. Slot receipts expose event-creation lag and a read-only exact-date health
check. An independent scheduler/alarm would need a separate operational decision.

## Operator modes

The same existing workflow exposes `operation=validate|start|resume`.
Default validate and all ordinary code pushes are explicitly NO MARKET HTTP.
Start requires `USER_APPROVED_REGULAR_START`, the current KST date, a unique
request ID and the existing20:30~23:20 window. It shares the scheduled slot CAS;
a queued duplicate, existing completed/failed slot or other account owner cannot
be silently reset. Ordinary start does not accept a window override.

Resume requires `USER_APPROVED_REGULAR_RESUME`, exact asof/source Run and a new
request ID. Optional timezone-aware not_before/expires_at are an explicitly
approved <=170minute window between that date's20:30 and next09:00. The target
date never rolls at midnight. Only preserved main executions with the new V1
source/lease attestations, complete calendar/master and known-completed OAuth
are eligible. Unsupported old formats, incomplete auth/preparation, dangling
HTTP, changed critical source, permanent source errors or ambiguous artifacts
are BLOCKED before provider requests; manual incident review is then necessary.
This is not a claim of generic recovery from any hard kill or unknown OAuth.

Source ZIP digest, exact member set, hashes, calendar/master/population, raw
records and partial DB/JSON are verified before copying the execution state.
The original source is immutable. The global budget includes every prior
reservation, auth, failure and retry; changing directory or request ID cannot
reset it. A per-source-Run CAS claim is consumed once. Subsequent recovery must
use the latest failed attempt's archive, not an earlier cheaper checkpoint.
The source modules are byte-bound to the supported synchronous V1 contract.

The worker independently proves source Run/attempt and all hosted Jobs/steps
stopped, rechecks metadata, observes the full190minute lease+60second grace,
then uses the existing Lease.recover CAS. TTL alone never grants ownership;
another participant is never unlocked. No KIS credentials are present in source
restoration or recovery-wait steps. Unknown OAuth remains blocked. Source budget,
cooldown and request scopes remain intact. No automatic full-market retry exists.

## HTTP and account lifecycle

Only exception class, phase and numeric errno are added to network diagnostics;
exception text, URLs, headers, bodies and secrets are not diagnostic output.
Unknown transport stops before the inner retry loop can send anything else.
Known HTTP5xx recovery remains bounded by the existing policies. Minimum1.05s,
read-only endpoints, timeouts, stock filters and5600 combined HTTP remain intact.

Tokens are discarded before normal owner-checked release. Known closed response
failures or pre-request failures can take that normal release path. Any in-flight
HTTP, dispatch ticket, unknown OAuth, uncertain owner or failed ledger read keeps
fail-closed isolation. No common six-file lease/transport protocol is changed.

## Data and delivery

Completed provisional data still requires every eligible row, raw qualification,
DB/JSON equality, manifest and confirmed stored artifact. Failed source slots
and their history are never reset. A successful explicit resume writes a separate
exact-date `recovered/YYYY-MM-DD.json` receipt; handoff can read it without
pretending the original failed run succeeded. Prices remain PENDING_KRX and
provider finality UNVERIFIED. No dashboard, operating DB or snapshot is published.
Artifacts retain90days; no external backup is created here.

The production preflight uses only synthetic, offline unit tests. Real preserved
source fixtures are tested in CI separately and are not a daily production
artifact-download dependency. No KIS/OAuth request is part of deployment checks.

## Approval and rollout

Runtime file hashes and three-participant Native proof are refreshed together.
Only this operational patch is proposed for main; V2 analysis and KRX PRs stay
separate. Inspect and validate on actual main after merge, confirm unchanged
cron/credential bindings/lease policy and actual Source hash agreement. Report
registration/validation separately from the next real scheduled collection.

## 실행권 확보 순서

날짜와 실행창은 먼저 고정하되, 일별 CAS 실행권은 공유 계정 잠금 획득 후 실제 공급자 요청 전에 확보합니다. 의존성 설치 실패나 다른 참여자의 잠금 점유는 당일 슬롯을 소모하지 않습니다. 이런 실패는 별도 실행 진단으로 남기며 자동 재수집하지 않습니다. 잠금 획득 후 원장과 실행 목록을 다시 확인하며, 경쟁에서 패배한 실행은 HTTP 없이 자기 잠금만 정상 반납합니다. 이미 실패한 수집 슬롯은 초기화하지 않습니다.
