# Read-only decision-support dataset (schema 1)

Entry point: `snapshot/decision-support/index.json`. This is an independent auxiliary publication, not a change to dashboard v2/v3, trading rules, or orders. No personal position or entry price is stored.

## Publication and consumption

`decision-support.yml` runs **after successful main-branch daily, liquidity, or corporate workflows**, or by an explicit main-branch manual dispatch. It has no cron, market credentials, or market API requests. It checks out the latest main and reads only existing complete checkpoint branches and saved official KRX references. Normal publication can finish even if this auxiliary workflow fails. A single Git commit publishes the auxiliary DB, shards, index and validation; concurrent main pushes are rejected rather than overwritten.

Before consumption, require `index.asof == snapshot/manifest.json.asof` and `index.source_snapshot_sha256 == manifest.sha256`. Pin one Git commit when downloading multiple files, then verify the index's shard/DB hashes and each shard's `dataset_id`. An old, internally valid sidecar must **not** be presented as today's data while the next build is pending/failed. `status.json` records the latest attempt separately. The helper `python scripts/build_decision_support.py --verify-only` enforces generation, source hashes, normalized DB/shard equality and independent foreign/institution quantity sums.

No-op generations are not republished. Failure preserves the prior valid index/data and records a failure status; subsequent successful parent completions/manual dispatch can retry without recollecting market data. A push collision leaves the previous remote auxiliary generation intact. Git commit history preserves previous derived generations; content-addressed original responses are never overwritten/deleted. Repository growth from retaining response vintages needs monitoring; no paid storage is introduced.

## Sources, tables and validity

The **existing two stock requests are unchanged**. `collect_daily_snapshot.py` additionally retains all already-returned flow rows, exact public request parameters, observation timestamps and response codes. Existing selected-day flow, chart rows, cap/price validation, fallback request, retries and checkpoint behavior remain intact. Historical successful records are reused, not recollected to populate new fields.

The independent gzip-compressed `analysis.duckdb.gz` contains:

- `institution_flows_daily`: symbol/date/market/exchange, quantity, raw amount/provider units, normalized amount (currently null), original row hash, checkpoint hash, observation time, subgroup fields, reasons. Conflicting vintages are retained but not aggregated as valid.
- `stock_ohlcv_daily`: one full response vintage per symbol, six OHLCV/turnover fields, basis, observation time, source/row hashes, official source hashes, adjustment flags and validation reasons.
- `indicator_snapshots`: matching dataset ID, symbol and complete JSON payload.
- `source_files`: content-addressed original checkpoint/reference inventory and provenance.

`source-manifest.json` indexes immutable gzip evidence. Shards are official market/industry groups split into at most 60 symbols. Repeated series use explicit `columns` + `rows` arrays; prices are KRW, volume/quantity shares, foreign amount KRW. Raw institution amounts are **not KRW-labelled** until endpoint-specific official multiplier evidence is available.

The official KIS source examples explicitly list institution amount/quantity and purchase/sale fields, but the obtainable examples do not document the amount multiplier. The documentation service returned HTTP 503 during this task. Foreign units must not be silently applied to institution fields. Quantity identity is checked exactly; raw monetary buy-minus-sell is checked within one **raw provider unit** for rounded components. Unknown amount multiplier means all normalized institution amounts/amount-derived indicators stay null even when the raw field is zero. Raw values remain available for later audited normalization.

Price acceptance requires exact date/symbol/market, official calendar and listing date, nonduplicate dates, positive traded OHLCV, logical OHLC order, same-date official KRX six-field agreement, no conflicting official versions, consistent adjustment flags, and consistency with the normal snapshot's close/volume/turnover. The selected day also matches the original flow price fields. No individual close from another provider is inserted into a bar. Unverified corporate-action adjustments remain held. `operational_crosscheck_pass` is **not** a provider-finality guarantee; `provider_finality` remains `UNVERIFIED`. Raw held bars are retained with reasons, and are never inputs to technical indicators. The dataset is current descriptive research, not a point-in-time backtest (`historical_backtest_eligible=false`).

## Calculations

Missing calendar dates remain missing, not zero. Foreign 1/3/5/20-day sums, yesterday difference, positive-day count and 5-day difference use exact official trading days; the previous 5 days do not overlap. Institution quantities provide 1/5/20-day sums where complete; institution KRW sums and persistence remain held pending unit proof.

SMA 5/20/60/120 uses consecutive accepted closes. Slope is today's SMA minus the previous session's SMA in KRW, not an angle or fitted regression. Alignment requires all four averages. Deviation is `(close/SMA-1)*100`.

Ichimoku midpoint uses high/low 9 and 26 sessions. Today's projected A/B are distinct from the cloud displayed today: current A/B use calculations **26 sessions earlier**. Current B needs 78 verified sessions. Chikou stores today's close and its plot date 26 sessions earlier; no future prices enter calculation.

Wilder ATR14 starts with the mean of 14 true ranges (15 consecutive bars required) then recurses `(previous_ATR*13+TR)/14`. Its seed start is explicit. A different history seed can yield a different ATR; this is not hidden. No gap is skipped. High/low 5/20/60 and prior-window breakout/support values are separate. Turnover activity is recent 5-day mean / preceding nonoverlapping 20-day mean. Returns require 6/21 prices for 5/20-day returns. Excess return is the same-market, exact-date stock return minus KOSPI/KOSDAQ return in percentage points. Official historical industry-index relative return is unavailable.

Flow state is a descriptive 5-day sign/change classification: new positive inflow after a nonpositive previous window, strengthening, exactly unchanged positive flow, slowing positive flow, negative flow, or zero. Independent 20-day persistence/net amount and validated price trend remain visible; possible trend damage combines negative 20-day flow and negative SMA20 deviation/slope. This is not a composite score. Price/flow divergence reports signs only; “flat” means exactly zero return, not an invented tolerance. Breakout/support/ATR evidence is provided, but `order_signal=null`. No buy, sell, stop-loss deadline or personal holding advice is generated.

Annual records and separately verified CFS/OFS annual/YTD/standalone-quarter reports preserve their period/basis/receipt. Only disclosures on or before `asof` are joined. Borrowing detail, contract/funding/shareholder-return facts are not inferred from periodic reports. TTM/PER/ROE are held until comparable periods and ownership bases are verified.

## Reproduction

```sh
python scripts/build_decision_support.py --fetch-checkpoints --install
python scripts/build_decision_support.py --verify-only
python reports/decision-support/check_real_data.py
python -m pytest -q
```

The first command reads GitHub checkpoint branches, not market APIs. An unsuccessful build must not be described as completed analytics. Tests use synthetic data only in test memory/temporary directories; exported observations exclusively come from stored real sources.
