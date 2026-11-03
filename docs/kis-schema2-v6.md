# V6 account-coordination rollout

All three core clients are deployed after integrated PR#1 merge67a0cea.
The lease wire format remains1; source-bound approval policy becomes2.
Existing actors retain their existing OAuth/market authority and rate controls.
The new actor is approved for account coordination, with operator_live_validation_approved,
ENABLE_LIVE_KIS_VALIDATION and actual_market_calls_authorized all false.
The legacy live_activation_approved field denotes schema2 activation compatibility,
not a new provider HTTP grant; the independent HTTP gate remains mandatory.

The three approval JSONs and their old immutable Native reports are backed up.
Six live runtime core files are unchanged. The prior approved source-map coverage
is retained and extended to the reviewed GitHub-only proof adapter/workflow;
the new limited workflow is source-bound and default off with a separate disabled
2-request control. No schedule, provider transport, algorithm or data change.

Each policy commit contains its exact source hashes and identical actual-source
Native33 evidence. A normal merge changes the commit identity, not reviewed file
bytes. Compare file SHA256 again on main, and test actual GitHub credentials
against the same isolated kis-shared-probe-v3 ref. Production lease is read-only.
Perform bounded holder/contender tests with actual repository run IDs. A missed
holder window is a failure, never converted to PASS or an implicit acquisition.

Mixed schema1/schema2 approval is safe: unchanged three-actor wire validation and
exclusive compare-and-append, no expiry takeover, and no new-actor HTTP permission.
If a stage fails stop later merges. Restoring the backed-up schema1 JSONs via
ordinary reviewed commits remains source-valid because the former runtime files
and former proof reports are retained. Remove/disable the new active approval
and leave both proposed/live control disabled. Never reset the ledger or force-push.

Current core/native proofs use synthetic final HTTPS. Actual credential binding
and provider behavior are not inferred from boolean presence or metadata policy
review. The metadata policy review uses declared-presence surrogates without
loading actual KIS values; the GitHub Lease uses real Actions identity and GitHub
credentials. KIS/OAuth/KRX/OpenAI/Telegram remain0 during this rollout.
