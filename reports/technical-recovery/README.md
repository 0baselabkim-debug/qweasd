# KRX reference availability recovery

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
