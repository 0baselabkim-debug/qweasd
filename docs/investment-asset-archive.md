# Investment assets — stage 1, offline originals and point-in-time management

This module archives already preserved data. It never runs a collector, requests market data, edits existing Actions or schedules, writes the operating DB/JSON, or changes the dashboard. Signal detection, forward returns, backtests, a UI, deployment and automatic scheduling are out of scope.

## Repository and data placement

Code stays in this repository on `feat/investment-asset-archive-v1`. Existing DuckDB/Pandas dependencies and the collector's **offline** ZIP/hash/task/DB verifier are reusable. A separate repository would add credentials and coordination without solving artifact access or storage retention. No new repository is required for stage 1.

Large data must live **outside the source Git checkout**. No archive data is staged or pushed to Git. The real local archive is `/workspace/investment-assets-v1`; local backups and restored copies use sibling directories. `--root` is mandatory: there is no hidden fallback to a temporary or checkout-local data directory.

```text
investment-assets-v1/
  FORMAT.json
  objects/sha256/<prefix>/<sha256>       exact original bytes, content deduplicated
  generations/<generation_id>/
    manifest.json + manifest.sha256     lineage, source status, inventory and version
    files.parquet                      generation → original logical path/hash
    population.parquet                 fixed source population/profile
    observations.parquet               typed dates/times/quality + source payload JSON
    summary.json                       first ingestion report; never overwritten
  commits/<generation_id>.json          durable visibility/commit receipt
  catalog.duckdb                       rebuildable metadata + Parquet views
  writer.lock                          archive-only single-writer lock
```

DuckDB is a derived catalog, not the sole copy of originals. Parquet generations and source objects are immutable through the application API. The catalog can be rebuilt in a fresh process or location. Exact duplicate bytes share one object even when multiple logical files/generations reference them. A generation's identity includes source channel, pinned commit/branch, file hashes, population version and adapter code hashes. Reprocessing the same inputs/version preserves the original ingestion and commit timestamps.

Files are created exclusively, fsynced and set read-only. Generation publication is atomic and data is queryable only after an independent commit receipt is sealed. A crash before that receipt leaves a prepared, uncommitted generation; replay verifies it and completes publication without re-fetching/replacing originals. A failed catalog rebuild does not invalidate an already committed generation; retry/rebuild is safe. There is no delete, update, force-replace or automatic garbage-collection command.

These are **application-level** protections. The filesystem owner/administrator can override permissions or delete files outside this API. Cryptographic verification detects missing/tampered content but is not hardware WORM or an external disaster-recovery guarantee. Unreferenced blobs left by an interrupted preparation are retained, not silently deleted.

## Existing sources and explicit separation

| Channel | Actual source | Scope and policy |
|---|---|---|
| `operating_publication` | Pinned Git commit's `snapshot/dashboard.duckdb`, manifest, daily validation, publication ledger and available briefing JSON | Restore a published snapshot; original and adjusted prices are separate archived versions. Source publication validation is not independent per-row API/finality approval. |
| `operating_analysis` | Pinned `snapshot/decision-support/` DB, shards, inventory and saved evidence | All original files retained; row-specific price acceptance remains separate from flows. `historical_backtest_eligible=false` is retained on every derived observation. |
| `kis_full_trial` | Feature-branch Run `40326538555`; ZIP artifact `12658373693` plus Git receipt | 2026-10-26, 3,950 master rows and 2,349 eligible stocks when the original ZIP passes verification. Receipt-only registration stores **zero** raw/normalized stock observations. |

The operating 2026-10-23 snapshot and the feature-branch 2026-10-26 trial are not interchangeable. Channel, source branch/commit, collection run, population profile/hash and generation are recorded independently. Query requires exactly one channel and selects one generation; it never silently unions populations or joins trial rows into published data.

A pinned local Git object is read with `git show`; the commit must belong to the declared local or `origin/` branch history. No checkout, merge, fetch, branch mutation or push is performed by the module. Source DB bytes are copied to a private temporary file and opened with `read_only=True`. Temporary copies are removed after reading; operating source files are never opened for writing.

For operating snapshots, exact DB/manifest/validation/ledger hashes, v3 shard hashes, duplicate keys, missing required flow/price identities and future trade dates are checked. Original tables remain preserved in the exact DB object even when only flows/prices/indices are normalized to Parquet. Corporate and other independent datasets are not falsely claimed as fully reconstructed from the normalized tables.

For the analysis sidecar, source and shard hashes, parent snapshot, validation generation, compressed/uncompressed DB hashes and the explicit backtest policy are checked. Raw institution monetary units remain unverified; no new normalization is invented.

For the full KIS ZIP, the adapter pins the receipt's run/artifact/digest, safely extracts to a temporary directory, verifies the complete manifest and population, reuses existing raw/task/quantity/price/DB validation, and checks the 4,701-call ledger. No executor or HTTP method is called. A matching raw ZIP is not currently available in this workspace: `gh api .../actions/artifacts/12658373693` returned `Forbidden`. This is an access limitation, not evidence that the artifact expired or that the raw data failed validation. No alternate authentication route or new Actions run is used to bypass it.

## Time and validation semantics

- `trade_date` / `asof`: the source trading date; midnight never changes a preserved date.
- `observed_at`: only an explicit, source-bound observation time. Unknown remains null/`UNKNOWN`.
- `published_at`: source-recorded publication time, when available. Unknown remains null.
- `available_at`: conservative recorded source-version time; the maximum of publication/generation and relevant Git file-version timestamps. Per-row explicit observations can only move their availability later.
- `ingested_at`: first archive ingestion start.
- `committed_at`: archive data publication after original/Parquet preparation and verification; this controls visibility to archive users.

Recovery may update a DB hash while retaining its first publication time in the ledger. Therefore the ledger timestamp alone is unsafe. For example, the current 2026-10-23 DB version became available on 2026-10-26; it is not exposed to a 2026-10-23 cutoff. Original first-publication commits can be imported independently for that earlier state.

Git committer/publication timestamps are recorded provenance, not proof of exactly when every external investor/CDN client received a file. Operating DB rows lack source-bound per-row flow/price timestamps: exact snapshot restoration is supported, while those rows explicitly report `observation_time_reproducible=false`. A snapshot's date is not substituted for missing observation time.

Point-in-time query has **two cutoffs**:

1. `--at`: source knowledge cutoff.
2. `--knowledge-at`: archive knowledge cutoff, defaulting to `--at`.

The default query cannot change merely because an older source is imported later: its archive commit must already exist at the knowledge cutoff. To inspect an authentic old published copy imported today, explicitly set the later `--knowledge-at`; output is labelled `EXPLICIT_HISTORICAL_RECONSTRUCTION`. This is not an assertion that this new archive or a strategy existed at the historical time.

Unknown source availability excludes a generation from cutoff queries. `--reproducible-only` excludes unknown observation times and explicit `historical_backtest_eligible=false`. Basic queries can inspect those preserved rows with their restrictions clearly attached. Archive integrity, internal validation, official price validation and provider finality remain distinct. `UNVERIFIED` is never promoted to `FINAL`; price matching cannot certify foreign-flow finality. No historical observation is modified when a later validation/version is archived.

## Commands

Use the existing Python 3.12 environment and `requirements.txt`. No additional dependency or service is required. Commands are explicit local actions; there are no registered timers or Actions hooks.

```bash
# Published operating snapshot at a pinned source commit
.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 ingest-publication --repo /workspace/qweasd --commit 197aa235 --branch main

# Independently archive the analysis sidecar and its existing restrictions
.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 ingest-analysis --repo /workspace/qweasd --commit 197aa235 --branch main

# Preserve the feature-branch receipt without pretending to have its raw ZIP
.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 register-kis-receipt --repo /workspace/qweasd --commit 3d838753 --branch feat/market-data-collector-v1

# Later, after obtaining the existing original ZIP through authorized GitHub access
.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 ingest-kis-artifact --repo /workspace/qweasd --commit 3d838753 --branch feat/market-data-collector-v1 --zip /path/to/original-artifact.zip

.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 verify
.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 rebuild-catalog

# Historical reconstruction: explicitly choose archive knowledge after ingestion.
.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 query --channel operating_publication --at 2026-10-15T08:00:00+09:00 --knowledge-at 2026-10-27T23:59:59+09:00 --asof 2026-10-14 --kind foreign_flow --limit 5
```

`restore-generation --generation <ID> --destination <new-directory>` restores exact original logical paths, e.g. `<new-directory>/snapshot/dashboard.duckdb`. Existing destinations, source checkouts and the archive itself are rejected. It does not install a snapshot into the operating app.

## Backup, restore and persistence

```bash
.venv/bin/python -m investment_assets --root /workspace/investment-assets-v1 backup --destination /workspace/investment-assets-backups/stage1-20261027.tar.gz

# Use the SHA256 emitted by backup; the target must not exist.
.venv/bin/python -m investment_assets --root /workspace/investment-assets-restored-v1 restore-backup --archive /workspace/investment-assets-backups/stage1-20261027.tar.gz --sha256 <recorded-64-character-digest>
```

Backup includes format, immutable objects, Parquet/manifests/reports and commit receipts. It excludes locks and derived DuckDB. Restore checks the complete archive digest, rejects unsafe paths/symlinks/duplicate members/size overruns, verifies every generation/object and rebuilds catalog paths for the new location. The default expanded-size cap is 2GB; future larger backups require an explicit reviewed capacity change.

Repeat commands/new processes preserve the explicit root. A redeployment preserving that mounted directory also preserves the data; a new empty machine does not. This workspace and a sibling local backup are **not independent failure domains**. No external persistent volume, object-store account, retention lock or automatic off-machine backup is configured by this stage. Durable redeployment needs that root mounted persistently or the verified backup copied to independent storage and restored. Credentials, retention costs and actual off-machine durability remain unverified.

For growth management, use content deduplication and daily/generation Parquet partitions, retain originals and all statuses, and monitor object/Parquet/backup bytes separately. Do not keep committing full daily DB copies into Git. Do not delete old sources or compact away generations in this stage. The archive's local capacity is not a promise of indefinite free storage.

## Tests and reproducibility

`python -m pytest -q tests/test_investment_assets.py` covers idempotency, old-query stability after revisions/backfill, channel/population separation, unknown timestamps, finality/backtest restrictions, tampering/loss, duplicate detection, source-path protection, partial commit recovery, independent cache failure, exact restore and backup relocation. Real Git tests restore 2026-10-14 and inspect current 2026-10-23 operating/analysis data. The ZIP adapter's positive path uses **synthetic** responses because the real ZIP is blocked; it is not reported as a real 2,349-stock restoration.

Real local ingestion and all-file source-preservation checks are recorded in `reports/investment-assets/stage1.json` once verification completes. Normalized observation counts include multiple dates, price bases and generations; they are not unique stock counts.
