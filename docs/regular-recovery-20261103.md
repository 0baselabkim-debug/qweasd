# Explicit operator recovery and continuation of Nov3

User authorized recovery followed by actual collection. This branch adds one
finite recovery run, with no main merge, new recurring schedule, credential
change, validation relaxation or expiry takeover. The normal20:30 path is intact.

Source Run41633101454, artifact13230751197, ZIP SHA256
26c5fb9f4e925374725b99747cca2ab28b250fef6748c18ab254d1e3e4e7de40
contains a failed2348-stock generation:280 internally qualified,563 successful
observations,564 collection HTTP+4 preparation HTTP=568 physical attempts. The
only transport failure was a read-only price GET for009290, not an unknown OAuth.
Complete source auth/calendar/master receipts and raw hashes are verified.

The original190minute lease is not shortened. Recovery waits until
2026-11-04 00:21:12.590782 KST (expiry+60seconds), then rechecks exact owner,
revision, Run attempt1, Job125907375199, hosted runner, every completed step and
termination grace using the real GitHub API. Existing Lease.recover performs
CAS. Unknown GET cost, request scopes and cooldown remain; no old failure is
relabeled received. The original FAILED slot is never reset or overwritten.

A separately approved170minute continuation window ends03:11:12.590782 KST,
with asof permanently2026-11-03. No prior-day new start at/after09:00 is allowed.
The recovery gets a new run/request identity and a new normal190minute session.
Source ZIP/extracted prefix remain immutable. The execution copy retains all
original HTTP reservations, raw hashes, observations, failure records, attempts
and timestamps. An explicit review permits retrying only exact prior failures
caused by shared_prior_HTTP_outcome_unresolved after verified old-owner recovery.
Other permanent errors remain blocked. A new unknown transport stops this run.

Existing collector parsing/qualification/DuckDB/JSON algorithms are reused.
The missing4135 market tasks and one new OAuth imply total4704 physical attempts
if successful. Combined total can never exceed5600, including the previous568.
No master, calendar, indices or successful stock response is needlessly queried.
The restored master3961/eligible2348 and population hash are fixed to the source.
Prices remain PENDING_KRX and provider_finality UNVERIFIED. This is a continuation
with mixed actual observation times, not a new independent time-of-day sample.

Only a control-file-only arming commit after successful offline CI can execute.
The approved source hashes include the recovery code and both new workflows.
KIS credentials appear only after the wait/recovery step. No changes are made to
main, the scheduled workflow, old shared protocol, existing source dataset,
secrets, dashboard or production publication. Recovery state is appended under
kis-regular-state/recoveries/2026-11-03/<request-id>.json.

Scanned raw/partial/complete output gets a90day artifact. Finalization downloads
that actual stored ZIP, verifies digest/CRC/file manifest, and records
COMPLETE_PROVISIONAL only with2348 verified rows, DB/JSON equality and budget
compliance. An unsuccessful generation cannot replace a successful publication.

Actual GitHub read-only preflight found the completed Run metadata updated_at
12:29:53Z and its attempt1 view updated_at12:29:54Z. These are distinct metadata
update clocks, not different executions. Execution/run/attempt/path/check-suite/
workflow/start identities and completed failure status must match exactly; each
view's own updated_at must remain stable on a fresh second read and cannot
precede the terminal Job or be in the future. This real owner proof passed
without changing the original190minute expiry or60second quarantine.
