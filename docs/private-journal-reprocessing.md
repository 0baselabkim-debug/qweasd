# Private journal evidence reprocessing

This change follows main `ac25d83087dfcf4284716b198356501968024741`.

## Behavior

- `collected_asof` records structural collection. `completed_asof` now means the worker validated the exact payload, has corporate-action verification, and has at least 160 pre-purchase bars. A structurally collected but unverified bundle stays eligible.
- The bounded queue orders by oldest attempt so blocked symbols do not permanently starve other symbols. Missing position signals reopen processing even when market evidence was already completed. The existing revision signature still rejects a result computed against an edited/deleted position.
- Same-date sufficient private histories are reused without chart/calendar requests. A previously attempted same-date insufficient history is also retained for an unchanged earliest purchase, avoiding repeated impossible backfills. An earlier purchase can require a longer fetch; the 5,500-bar cap remains explicit.
- Saved KIS response rows can be replayed from a matching public bundle or the private bundle. Verification computes the hash of the entire private bar history and its flows. It never copies a public acceptance verdict. KRX references must match the date/symbol; missing references preserve a hold. KIS authentication is lazy.
- Corporate-action observations remain evidence for review, not a complete entitlement ledger. No new code sets `corporate_actions_verified=True`; sell guidance remains held while that proof is absent.
- Public latest-cache writes retain exact date bundles under `technical/history/YYYY-MM-DD/`. Acceptance updates use the same write path. Existing `technical/pending` dates remain readable. Date-selected UI lookups accept only that exact date; missing history is unavailable, never reconstructed from today's price. Already lost dates cannot be recovered by this change.
- No private holdings, bundles, or identifiers are written to GitHub artifacts/logs. Existing guide thresholds and UI layout are unchanged.

## Deployment

For an existing configured Supabase project, its owner must run `docs/sql/private_journal_reprocessing.sql` in that project's SQL Editor. Do not rerun the original bootstrap on an existing project (its policies/triggers are not idempotent). The migration is transactional and retains bundles, purchases, audit, risk state, and policies; it reopens old structural completion markers once. For a new installation, the updated `docs/sql/private_journal.sql` includes the change.

Use the existing configuration only: app Supabase URL/anon key, Actions Supabase URL/service-role key, KIS credentials, and the existing KRX API key when available. The workflow now binds the KRX key to the private step. Missing provider credentials/evidence must remain pending; no new account or access grant is required by this code.

After SQL application, run the existing technical workflow in private mode. With an authorized test user, verify login, owner-only reads/writes, durable save/reload, editing a purchase after same-date processing, and worker signal revision rejection. Confirm provider-backed results independently. Local tests below do **not** establish hosted Auth, RLS, permanent storage, or live provider success.

## Local verification

`python -m pytest -q` runs the offline regression suite, including `tests/test_private_reprocessing.py` (exact long-history hash, saved-response replay without API requests, missing-reference hold, position reevaluation, exact-date preservation).

`tests/private_reprocessing.sql` is a local PostgreSQL integration fixture, never a production migration. On disposable PostgreSQL 17, bootstrap mock `auth.users`, `auth.uid()` and the three Supabase role names, apply the schema and migration, then run this fixture with `psql -v ON_ERROR_STOP=1`. Also test upgrading the unmodified base-commit schema. It asserts separate collection/verification, completed-job suppression, edit reopening, stale-signature rejection, and retention on upstream failure. Mock Auth does not validate Supabase authentication or RLS.

Validation performed on 2026-10-16: full offline suite **169 passed** (170.98 seconds); after final archive-path isolation and insufficient-history regression additions, focused journal/technical suite **60 passed** (3.43 seconds). PostgreSQL 17 clean install and upgrade from `ac25d83` both passed the SQL fixture; reapplying the migration with an existing cache row also succeeded. No production SQL, hosted login/RLS/storage, or live private provider run was performed.
