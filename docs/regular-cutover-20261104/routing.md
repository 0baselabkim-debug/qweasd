# Shared 20:30 producer selection, November 4

The original UTC cron remains `30 11 * * 1-5`. Missing
`kis-regular-state:routing/2030.json` selects qweasd. Code deployment alone does
not change that selection. A reviewed GO control may select the integrated main;
qweasd then records NOT_SELECTED and sends no provider request. The same CAS
`ledger/2026.json` date/2030 slot remains authoritative across both repositories.
Account ownership is separately enforced by the unchanged schema2 Session.

A GO requires both actual-main source proofs and ten PASS checks before19:30 KST.
Invalid, inaccessible, changed or expired controls fail closed. It never changes
target-day calculation, holiday checking, filters,1.05s spacing,5600 combined
physical requests,170minute window, or190minute account lease.

Only a stopped, archived source with zero possible physical requests, matching
manifest/source-contract/account evidence can produce PRESTART_RELEASED. Previous
failed history is retained in previous_attempts when the successor claims. Any
actual/unknown request forbids a fresh collection; partial results and budget
must instead be restored under the separately reviewed resume contract.

The repository_dispatch readiness event performs only deployment validation.
The distinct handover event requires the central exact source proof, current date,
manual-start time window and route recheck. It requires no new token scope: GitHub
repository_dispatch uses the existing coordinator Contents:write permission.
API acceptance is not proof of receiver success. Normal code push validates only.

The integrated collector uses a pinned namespaced engine; it neither replaces
this code nor the operating database/dashboard. Derivative analysis failure never
replaces the collection receipt. All provisional prices remain PENDING_KRX.
The existing artifacts expire after90days; no durable external backup is implied.
