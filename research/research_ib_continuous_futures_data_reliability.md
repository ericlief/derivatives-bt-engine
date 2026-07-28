# IB API continuous-futures data: reliability for historical prices, returns, and covariance

Investigation triggered by trying to cross-check this project's DB-driven `ZC` (corn)
continuous series against a live IB pull (`ib.cont_future('ZC', exchange='CBOT')`,
`ib_tools`/`ib_insync`), to validate the TSMOM signal/regime logic against an independent
data source. The cross-check itself failed — not because either source is "wrong," but
because IB exposes at least three, and possibly four, mutually inconsistent notions of
"the ZC continuous price series," none of which reconstructs genuine historical
front-month price levels the way this project's own DB does. Everything below was
derived interactively this session against real pulled data (not assumed) — dates,
prices, and volumes are quoted directly from what was actually returned.

---

## 1. What was being compared

- **This project's DB** (`FuturesDataLoader` / `_CONTINUOUS_FRONT_MONTH_SQL`,
  `src/derivatives_bt_engine/domain/futures_dataloader.py`): raw, unadjusted, real
  front-month splice — for each date, whichever not-yet-expired contract had the highest
  volume that day, sticky/monotonic guarded. Real dated-contract OHLC, no adjustment,
  genuine roll-day price jumps preserved by design.
- **IB, three different surfaces**, all nominally "ZC":
  1. `ib.reqHistoricalData(contract, ...)` against a qualified `ContFuture` (via
     `ib_tools`'s `get_historical_bars`/raw `ib_insync`).
  2. IB's own embedded **Advanced Chart** "back series" (TWS/IB desktop charting).
  3. Independent **TradingView.com**'s own `ZC1!` continuous contract symbol (not an IB
     surface at all, checked as a fourth, fully independent reference point).

## 2. `reqHistoricalData` on a `ContFuture`: two hard findings

**2.1 Cannot pin a historical end date.** `endDateTime` set to any explicit past
timestamp (e.g. `'20250501 00:00:00'`) raises **error 10339**: "Setting end date/time
for continuous future security type is not allowed." Only `endDateTime=''` (meaning "as
of now") is accepted for a `ContFuture`; historical duration is controlled solely via
`durationStr` counting backward from *today*. This alone makes `ContFuture` historical
bars unsuitable for "what did this look like as of some past date" queries — every pull
is anchored to whatever "now" is when the request runs.

**2.2 The returned series is back-adjusted, anchored to today's front contract.**
Confirmed by diffing the IB pull against the DB date-by-date for all of 2025 (`ZC`,
contract currently qualified to `ZCZ6`/Dec'26 as of this session, 2026-07-27):

| DB roll date | contract change | IB−DB offset before → after |
|---|---|---|
| 2025-02-20 | H25 (Mar) → K25 (May) | ~114 → ~101 |
| 2025-04-16 | K25 (May) → N25 (Jul) | ~96 → ~88 |
| 2025-06-23 | N25 (Jul) → Z25 (Dec) | ~76 → ~62 |
| 2025-11-20 | Z25 (Dec) → H26 (Mar) | ~60 → ~48 |

The offset (IB close − DB close) is large early, steps down at each of the DB's own real
roll transitions, and converges toward **zero** as dates approach the present (offset was
≈ −0.25 by 2026-06-17, right at the point the series reaches the currently-qualified
Z6/Dec'26 contract itself). That is the signature of cumulative back-adjustment anchored
to whatever contract is front *today* — not a wrong symbol, not a data error. (Initial
suspicion that this was actually wheat data, based on the price level alone, was
disproven once the qualified contract was inspected directly:
`ContFuture(conId=602619735, symbol='ZC', localSymbol='ZCZ6', tradingClass='ZC', ...)` —
genuinely corn.)

## 3. IB's own Advanced Chart disagrees with its own API pull

Checked a single concrete date, 2025-02-20, against every source at once. Raw
per-contract data from the DB's underlying `daily` table that day:

| contract | open | high | low | close | volume |
|---|---|---|---|---|---|
| H25 (Mar, exp. 2025-03-14) | 497.75 | 503.25 | 497.5 | 497.5 | 62,320 |
| K25 (May, exp. 2025-05-14) | 512.0 | 518.0 | 511.75 | **511.75** | **73,448** |

K25 wins the DB's volume ranking that day (73,448 > 62,320) — a genuine, narrow, real
crossover, not a thin/noise-driven pick (contrast with the still-open BRE/6L
sticky-anchor bug documented in
`research/research_futures_roll_logic_and_active_months.md` §1.2, where the "winning"
contract's own volume was noise-level).

Four readings for the same nominal date/symbol:

| source | value | construction |
|---|---|---|
| This project's DB | 511.75 | raw, volume-selected front month (K25), real print |
| TradingView.com `ZC1!`, raw | ~498 | raw, unadjusted, hadn't rolled off H25 yet (matches H25's real 497.5–503.25 day range almost exactly) |
| IB `reqHistoricalData`/`ContFuture` | 612.75 | back-adjusted, anchored to today's Dec'26 front |
| IB Advanced Chart "back series" | ~474 | back-adjusted, but a **different** construction than #3 — pulls the number *down* where the API pull pulls it *up* |

TradingView's raw feed is explained cleanly: it simply hadn't rolled from H25 to K25 yet
on that exact date (a ~1-day roll-timing lag versus the DB's same-day volume crossover —
plausibly a different roll trigger, e.g. open interest rather than same-day volume, not a
construction disagreement). IB's own Advanced Chart back series, however, does **not**
reconcile with IB's own `reqHistoricalData` back-adjusted value for the same date/symbol
— the two go in opposite directions from the DB's raw print (612.75 above it, 474 below
it), meaning they use different anchors and/or different roll chains internally. This was
not further reverse-engineered (no documentation found, no live isolation of the exact
chart setting attempted beyond confirming the roll-rule/adjustment toggle exists in TWS's
chart config) — flagged as observed, not fully explained at the mechanism level.

## 4. Conclusions

**Is IB usable as a source of continuous-contract *price levels*? No.** Every IB-derived
continuous view checked (API back-adjusted, chart back-adjusted) diverges substantially
and inconsistently *from the DB and from each other* — up to ~115 points on a ~500-point
instrument (23%) in the API case, observed at multiple dates across the whole of 2025,
not an isolated event. None of them is traceable to an actual dated-contract print the
way the DB's own construction is. Do not use `ContFuture`/`reqHistoricalData` (or the
Advanced Chart's back-adjusted view) as ground truth for historical ZC — or, by
inference, any instrument — price levels in this project. The DB's own
`FuturesDataLoader` remains the only source confirmed to reconstruct genuine per-date
front-month prices.

**Is IB usable for *returns*, and therefore covariance/realized-vol estimation? Probably
not, without further work — not confirmed either way this session.** The theoretical
case for "yes" is real: a back-adjustment that's locally constant between rolls cancels
out in a day-over-day price difference, so returns *away from roll dates* should track
the DB's own returns closely regardless of which back-adjustment convention is in play.
But two things specifically undermine trusting this without independent verification:

1. **The back-adjustment magnitude itself changes over time** (it isn't a single fixed
   constant — it steps at every roll, per §2.2's table) — meaning naive returns computed
   *across* a roll date from IB's series will contain a spurious jump equal to that
   roll's adjustment step, not the real price move. Any realized-vol/covariance estimate
   that doesn't explicitly detect and exclude roll-day returns from an IB-sourced series
   would be contaminated at every roll, for every instrument in the covariance matrix —
   and instruments have their own independent, unsynchronized roll calendars (per
   `research_futures_roll_logic_and_active_months.md` §2), so this isn't a single
   shared date to special-case, it's a different set of dates per symbol.
2. **IB's own two continuous surfaces disagree with each other** (§3) — different
   back-adjustment conventions, not just different absolute levels. That means even
   "which days are roll days, and how big is the jump" isn't a stable, single answer
   for a given IB-sourced pull; it depends on exactly which IB surface/roll-rule
   produced the series, which wasn't independently confirmed for the `reqHistoricalData`
   path (no IB documentation was consulted this session; findings are purely empirical,
   from one symbol, one 18-month window).

**Recommendation:** continue using this project's own DB (`FuturesDataLoader`) as the
sole source for any price-level, return, or covariance work. If IB data is ever needed as
a supplementary/live source, it would need to be reconstructed from **individual dated
contracts** (`Future`, not `ContFuture`, with an explicit `lastTradeDateOrContractMonth`)
pulled and spliced using the *same* roll methodology this project's DB already uses
(volume crossover, per `_CONTINUOUS_FRONT_MONTH_SQL`) — not IB's own continuous-contract
convenience object, whose adjustment behavior is opaque, internally inconsistent between
IB's own surfaces, and not something this session found a way to control or verify via
the API.

## Caveats

- Single symbol (`ZC`), single ~18-month window (2025 plus early 2026), single account/
  data-permission context. Not verified across other clusters (rates, equity, FX, metals)
  or other IB accounts/market-data subscriptions, which could plausibly affect chart/API
  behavior.
- The Advanced Chart's exact roll-rule/adjustment settings were not read directly off a
  screenshot or config dialog — inferred only from the resulting numbers. A future
  session with direct access to the chart's settings panel could confirm this more
  precisely.
- No IB API documentation was fetched or consulted this session (no network access to do
  so); the back-adjustment characterization is entirely empirical, derived from diffing
  real pulled data against the DB.
- `reqHistoricalData`'s 10339 restriction and the back-adjustment behavior were both
  confirmed directly against a live TWS/Gateway session during this conversation, not
  assumed from prior knowledge.
