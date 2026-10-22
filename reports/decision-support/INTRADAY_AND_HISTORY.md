# History and intraday feasibility (no new market calls)

Official examples and exact source commit/hashes: [official/provenance.json](official/provenance.json). These are provider-published **code/specification evidence**, not live API success. The public API documentation metadata service returned HTTP 503; no login, proxy or permission restriction was bypassed.

## Existing history

The tested normal snapshot (2026-10-21) contains prices from 2026-09-03 through 2026-10-21, at most 31 rows per symbol. Full saved KIS chart responses retain additional OHLCV observations, but history length is not six-field/adjustment validation. Saved operational official KRX reference dates end at 2026-10-20, so the current day has no eligible official comparison. No accepted current-day bars means no current technical result, even when raw observations exist. The exporter reports both available rows and consecutive accepted rows.

Official chart example: FHKST03010100, option 0 adjusted / 1 original, J KRX / NX NXT / UN integrated; maximum 100 rows per request. Reconstructing 120+ sessions cannot be assumed to fit one response. For an approved small candidate list of N symbols, a provisional upper plan is **4 chart requests per symbol** (two pages/ranges per adjustment basis) plus at most one authentication if needed. Actual counts depend on preserved reusable data, holidays, response coverage and retries. At the unchanged 1.05-second interval, 4N requests alone take at least 4.2N seconds, excluding network, official reference and action checks. For 10 symbols: at most 40 basic price requests, ~42 seconds spacing alone; this is an estimate, not an executed test. Do not expand all 2,430 symbols by default.

Official KRX history uses two market-wide responses per missing date (KOSPI/KOSDAQ), not two requests per symbol. Authentication/transport failures and retries consume their own budgets. Crosscheck saved references first; any collection requires a separate explicit bounded plan. Corporate-action proof must establish a consistent adjusted basis; original KRX closes alone cannot validate adjusted series across a split or other action. No extra history collection was performed in this implementation.

## Intraday options (not enabled)

| Data | Official existing route | Evidence / limitation | Additional request estimate |
|---|---|---|---|
| Current quote, intraday OHLCV | FHKST01010100 / inquire-price | Already-used fallback; current observation, not final daily price | 1 per selected symbol and observation |
| Intraday minute prices | FHKST03010200 / inquire-time-itemchartprice | Official example: current day only, up to 30 rows; past sessions unavailable through this route | If rows are one-minute bars, up to 13 full 30-row pages for a 390-minute session, plus overlap/boundary validation |
| 5-minute bars | Aggregate validated minute bars locally | Only complete bins, exact session/market, no zero filling; opening/closing auctions and off-session bars must be separated. No claim of a native five-minute parameter | No extra call beyond minute data; estimates depend on actual response granularity, which has NOT been live-tested |
| Foreign/institution provisional trends | FHPTJ04400000 / foreign-institution-total | Official example lists estimated snapshots, not continuous final flows. Foreign around 09:30/11:20/13:20/14:30; institution around 10:00/11:20/13:20/14:30. Unit/scope must be verified independently | Per queried market/condition/page at each selected observation; pagination unknown without a pilot, so no full-market call estimate asserted |

For 10 selected symbols, a single full-day minute download could require ~130 requests / at least 136.5 seconds spacing, before network/auth/retries/validation. This conditional estimate is not evidence of live success or a proposed new schedule. Requesting whole-session history every poll would waste calls; any future approved implementation should retain timestamped incremental raw observations. Without multiple comparable provisional snapshots, do not calculate flow acceleration.

No new subscription or paid provider was introduced. Existing provider/account eligibility and limits still apply. Intraday automation, additional market calls and new schedules remain **disabled**.

## Cross-check against the existing research archive

The separately maintained `research/full-market-transition-20261019` branch at `d31ffa137b653a65602444145ee7ca0717c2a4a5` was inspected read-only. Its `krx-40d-study/LATEST_MANUAL_RESULT.json` explicitly records **prepare only, 0/80 market requests, zero complete official 40-day series**, and 70 missing market/date combinations. Its saved official dates are likewise October 14/15/16/19/20. Thus no additional current-day official price was available to reuse from that archive. This task did not consume that research authorization, start a delayed/night job, change its research branch or weaken acceptance to populate indicators.
