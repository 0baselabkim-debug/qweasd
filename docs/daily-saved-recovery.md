# Automatic publication recovery from preserved daily results

The regular collection schedules, market API budgets and briefing calculations
are unchanged. No new secrets or permissions are required. Both workflows use
`contents: write`, `actions: read` and the existing `kis-validation-auth` group.

## Regular execution

1. Fetch current main; bind the trading date to the originating run creation time,
   including reruns after midnight. Skip an already published date or a date older
   than the current publication.
2. Restore that date's checkpoint, validating compressed journal integrity. When
   the branch is absent, search completed failed runs of the exact daily workflow
   on this repository's main branch for a complete, eligible artifact. Never treat
   a Git/API access failure as an absent source. Partial checkpoints retain the
   existing collector behavior; partial/corrupt artifact recovery is refused.
3. Collect only missing observations using the existing collector. A complete
   restored bundle returns before market authentication or market API calls.
4. Seal a source receipt with the actual target date, run/attempt, original byte
   counts, SHA-256 digests and outcome counts. Keep bundle, original journal and
   diagnostics unchanged. Persist the receipt with the gzip/split checkpoint and
   upload a separate `daily-source-DAY-RUN-ATTEMPT` artifact before publication.
5. Checkpoint persistence and artifact upload fail independently. At least one
   durable copy plus a valid receipt is mandatory. If both fail, publication stops.
   A failed checkpoint push alone no longer blocks a fully preserved result.
6. In an isolated checkout of current main, verify the preserved bytes, run the
   existing prepare/regression/stage commands, and publish all existing daily and
   briefing files together. Recheck main and the ledger immediately before push.
   If main changed, rebuild and revalidate once against the new main. Use only
   ordinary fast-forward pushes. If a push reply is lost, inspect the actual remote
   commit before deciding whether another attempt is safe.

The source checkout and archived originals are retained. Candidate manifests,
validation results and briefing outputs are copied out of temporary worktrees
before cleanup. The attempt recorder refreshes main before reading the ledger.

## Immediate bounded recovery

`daily-saved-recovery.yml` listens only for completion of the regular daily
workflow. It has no schedule, dispatch or market credentials, and cannot trigger
itself. A first failed main run is eligible only when its verified outcome artifact
classifies the error as `fetch_main`, `main_changed` or `push_not_applied`.
Validation, source corruption, access denial and uncertain push outcomes are not
retryable. Old runs without source receipts/outcome artifacts require inspection.

Recovery verifies repository, source workflow path, main branch, event, attempt,
artifact run ID, API ZIP size/digest, receipt file digests, target date, complete
counts and Journal row integrity/conflicts. Complete bundle and journal must agree.
It prefers a complete checkpoint with the matching source receipt; if absent it
uses the verified artifact. If both exist, the original bundle and journal must
match. Conflicting or corrupt sources stop recovery.

A non-force, create-once `recovery/daily-SOURCE_RUN` branch claims each source before
restoration/publication. A second claimant, a workflow rerun, or an already claimed
failed recovery does not publish again. A failed claim push is followed by a remote
lookup; no unconfirmed claim is used. Claim branches intentionally remain as durable
attempt records and should not be automatically deleted. Concurrency also serializes
this work with existing collection, but is not the deduplication mechanism.

The recovery day comes from the source report/receipt, never the current clock.
Recovery rechecks the latest manifest/ledger and uses the same bounded publication
transaction. Already published/newer dates are skipped. No collection is dispatched,
no active run is cancelled, and no validation is relaxed. A separate recovery can
wait for an already-running job in the existing concurrency group.

## Failures requiring a person

- Both durable copies unavailable, expired artifacts, missing API artifact digest,
  absent/invalid receipt or classified outcome, or pagination exceeding the bound.
- Partial/corrupt/wrong-date/conflicting data, Journal errors, data regression or
  briefing staging failure.
- Authentication/permission errors or an uncertain push result whose remote state
  cannot be read. No automatic credential or permission changes are attempted.
- More main churn than the two local publication attempts, or any failure after the
  one claimed automatic recovery. Inspect the outcome and remote ledger before
  deciding on further manual work; do not blindly rerun or remove a claim.

Source artifacts retain the existing seven-day recovery window; recovery reports
are retained for thirty days. Upload failures and runner termination can prevent
artifact preservation, so preservation is checked rather than assumed. Historical
unpublished dates are not silently advanced or overwritten by a later schedule.

## Verification

Tests inject checkpoint/artifact failures, hash/date/completeness errors, unsafe ZIP
entries, conflicting sources, two competing claims, main changes, duplicate/newer
publication, a lost push reply, access denial and repeated recovery. Local Git bare
repositories stand in for the remote. No live market requests or intentional
production failures are needed. These tests do not establish that a real Actions
workflow_run recovery has completed; report the actual CI and runtime evidence
separately.
