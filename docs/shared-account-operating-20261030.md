# Shared KIS account operation — 2026-10-30

The existing KIS account, app key, app secret and 20:30 KST weekday cron remain unchanged. No new KIS credentials, external service or repeated recovery schedule were created.

Both repositories use the same qweasd/kis-shared-account-state Git lease. Ordinary non-fast-forward rejection is the atomic ownership primitive. Workflow concurrency groups remain repository-local. Native two-repository tests prove denial in both directions and clean handover using an isolated branch, with zero OAuth or KIS market requests.

The monitor uses the registered KIS_COORDINATOR_TOKEN through step-local GH_TOKEN for the central Git lease. Its own GITHUB_TOKEN remains available for its private repository claims and metadata. Existing OPERATIONS_READ_TOKEN takes precedence; when absent, read access to qweasd uses the coordinator credential. No credential values are stored in evidence, source or logs.

Every approved monitor bootstrap/flow/remaining entrypoint keeps its existing instrument list, request scope and one-authentication/no-retry caps while participating in the shared account lease before claims and HTTP. Existing formal-data approval and UNKNOWN/UNVERIFIED labels remain independent of account coordination. Old preserved reports remain verifiable under their historical metadata schema.

The full-market collector keeps the dynamic daily master, original filters, two stock requests per eligible instrument, both indices, 1.05-second minimum spacing, existing timeouts and recovery ceilings. Its single 5,600-request physical budget includes preparation, authentication and failed calls. It never resets a date slot or repeats a full failed generation automatically. Runtime writer.lock files are excluded from the uploaded manifest population.

An active owner blocks another repository before authentication. There is no automatic expiration takeover, cancellation, force push, or owner deletion. The synchronous monitor distinguishes a closed response followed by a controlled data-validation failure from an unknown transport outcome. Closed controlled failures release the account lease; unknown outcomes and killed processes retain ownership for review. Existing raw responses and failure ledgers remain preserved.

Authentication is exclusive for the entire collection, not just token issuance. Tokens stay in the current process, no refresh is attempted, and the next owner observes the shared auth handover quarantine. The exact provider quota scope and token-reissue invalidation behavior remain unverified. Those unknowns are not relabelled as provider guarantees. The policy keeps conservative request spacing and prevents overlapping token users.

The provider's naive expiry timezone remains unverified. The shared implementation uses the earliest possible civil-offset bound and expires_in only to shorten that bound. A cached or short-lived token cannot be extended by a fresh response duration. Before market collection, the regular guard requires the bound to cover its frozen execution window. Rejection stops further requests; no new OAuth retry is invented.

Activation is bound to the immutable native evidence JSON and reviewed transport hashes. A true verified flag alone is insufficient. Modified transport/entrypoint code, missing evidence, expired review or failed central-state access blocks requests. The enrollment covers the approved Actions paths in these two repositories. Changes to those paths require a new review and proof; unrelated offline analysis is not serialized by the account lease.

The GitHub inspection of the other private repository may remain 404 under qweasd's own token. Mutual exclusion uses the tested shared lease and enrolled transport hashes, not that auxiliary inventory query. There is no assumption that 404 means the other repository has no active runs. Expired or unknown owners still require explicit verification and recovery, never automatic deletion.

The latest native proof is reports/shared-account/rollout-proof-20261030.json. The actual earlier KIS full-market evidence is Run 40882669831. Together these support account coordination readiness; they do not claim the first regular scheduled market execution has succeeded. UNVERIFIED and PENDING_KRX remain unchanged. No KRX API request was added.

The user's declared GitHub token expiry is 2027-01-28. This review expires earlier, at 2027-01-27T00:00:00Z. Rotation and a new capability check must happen before expiry. Existing Secrets were not changed by this rollout.

The next configured slot is 2026-10-30 20:30 KST. GitHub schedule may be delayed or omitted; the existing 170-minute slot window, fixed target date, provider-calendar evidence, duplicate ledger and fail-closed validation remain active. A full capture takes roughly two hours in the latest measurement; completion before the unchanged 22:30 Word briefing is not guaranteed.

Raw responses, request ledgers, DB/JSON, manifest and verification outputs remain independent generation artifacts with 90-day retention, not a permanent external backup or dashboard publication. Operational snapshot, historical raw generations, operating DB/JSON, dashboard and Word schedules are unchanged.
