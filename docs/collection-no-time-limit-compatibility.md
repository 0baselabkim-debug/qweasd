# Integrated regular collection lease compatibility

The integrated full-market producer may explicitly use owner-release-only leases
(`expires_at: null`, `lease_policy: OWNER_RELEASE_OR_VERIFIED_TERMINATION`,
`purpose: REGULAR_FULL_MARKET_2030_V1`). This participant reads and respects that
same Git CAS owner and never takes over it based on age. The original owner ID,
request scopes, cooldown and source-bound checks are preserved. The decoder is
additive for legacy finite leases; this repository's short/legacy sessions do not
acquire open full-market leases. Recovery of open ownership requires independently
verified stopped owner AND closed requests/budget/quarantine proof. No production
ledger writes, market HTTP, automatic collection or schedule activation are added.
The user's stop instruction remains in force.
