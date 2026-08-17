# Futures roll mechanics and "active month" liquidity structure

Research/documentation task, following directly from this session's continuous-front-month
data-layer fix. Three parts: (1) what was fixed and what's still open in the
continuous-series construction, (2) an empirical check of which calendar months are
actually liquid across this project's whole instrument universe (not just the metals
where the pattern was first found), and (3) a from-source walkthrough of all three
places this codebase decides "roll now" — the naked single-symbol backtester, the TSMOM
multi-symbol backtester, and the live IB rebalancer — since a design proposal at the end
would be premature without first being precise about how each of the three actually
behaves today. All duckdb queries below were re-run directly against
`/home/dev/fin/db/globex_mdp_3.0.duckdb` (table `daily`) while writing this document, not
copied from an earlier session — the exact query text is inline. No production code was
modified for this document.

---

## 1. The continuous front-month fix, and what's still open

### 1.1 What was fixed (recap, for context — already documented in-line)

`_CONTINUOUS_FRONT_MONTH_SQL` (`src/derivatives_bt_engine/domain/futures_dataloader.py:77-99`)
is the single query every backtest path (naked, TSMOM, and this session's diagnostic
scripts) draws its "front-month" price series from. It used to rank each date's
not-yet-expired candidate contracts by `expiration ASC` — soonest calendar expiration
wins, with no memory of which contract was front yesterday. Two confirmed failure modes
(full history in the module's own docstring, `futures_dataloader.py:44-64`): a
near-expiry flip-flop (ZN, March 2023, a one-day data gap in the expiring contract
caused a jump-then-revert) and off-month contamination (GC, Dec'25→Jan'26, a token
20-lot trade in a thin non-primary month out-ranked an already-5,137-lot-a-day real
active month purely on calendar proximity).

The fix (same file, `futures_dataloader.py:85-97`) ranks by `volume DESC` instead of
`expiration ASC`, wrapped in a sticky/monotonic guard: a running `max(expiration)` over
the naive daily pick, re-selecting each date's bar against that sticky value rather than
the naive one-off pick. `assert_monotonic_expiration()` (`futures_dataloader.py:102-123`)
raises if a regression ever reintroduces a decreasing-expiration row; it's wired into
`FuturesDataLoader.daily` itself (both the cache-hit and fresh-query paths,
`futures_dataloader.py:147,157`) and, as defense-in-depth, into `Backtester.__init__`
(`backtester.py:53`) and `tsmom_backtester.load_portfolio_data`
(`tsmom_backtester.py:173`).

### 1.2 What's still open: the BRE/6L sticky-anchor hijack (NOT fixed)

Validating the fix against BRE (`6L`, Brazilian Real) surfaced a third, distinct failure
mode the volume-ranking + stickiness fix does not close, and re-deriving it directly
against the production query while writing this document (rather than relying on the
2010-07-01/2010-2016-window summary from earlier in the session) showed it is
**substantially larger than originally characterized** — not a brief, isolated
one-off event, but a mechanism that drops years of otherwise-perfectly-good data from
the current production continuous series.

**The exact mechanism, confirmed by decomposing `_CONTINUOUS_FRONT_MONTH_SQL`'s own CTEs
stage by stage against 6L's full history:** `bars` (the base filter,
`instrument_class='F' AND security_type='FUT' AND ts_event < expiration`) has 9,784 rows
across 4,446 distinct dates. `naive_ranked`/`naive_front`'s `row_number() OVER (PARTITION
BY ts_event ...)` step preserves every one of those 4,446 dates exactly once — the
per-date partitioning itself never drops anything. **The final join is what drops rows**:
`bars b JOIN naive_front f ON b.ts_event = f.ts_event AND b.expiration =
f.sticky_expiration` requires an *exact* match to the running-max `sticky_expiration`,
not "whatever traded well that day." Re-running the full query end to end: only 3,262 of
those 4,446 dates survive — **1,184 dates (26.6% of 6L's entire history) are silently
dropped**, and the dropped dates are *not* dates lacking real data — on every one of
them, `bars` has a perfectly ordinary, fully-populated row (valid `volume`,
`instrument_class='F'`, `security_type='FUT'`, valid `expiration`) for that day's
genuinely-trading near-month contract, exactly the same row shape as a healthy ES or CL
day. The row is excluded purely because its `expiration` doesn't equal whatever far-off
value the sticky pointer has been knocked onto — the join has no fallback to "closest
available" or "whatever's actually liquid right now," only an exact match or nothing.

**Root cause, more precisely than "a single 2-lot trade":** the single longest
contiguous run of dropped dates is **1,534 days** (2011-05-24 through 2015-08-05 — over
four years), and decomposing `sticky_expiration` day by day shows it is fully explained
by exactly two step-changes:

| `sticky_expiration` locked to | active from | active to | days |
|---|---|---|---|
| 2012-05-31 | 2011-05-24 | 2011-07-12 | 34 |
| 2015-11-30 | 2011-07-13 | 2015-08-05 | 999 |

On 2011-07-13, some single day's naive per-date volume-winner happened to be a contract
expiring **2015-11-30 — over four years later** — and the sticky running-max rule
locked onto it. It then stayed locked for 999 consecutive days (nearly three years)
purely because no other date's naive winner tops an expiration that far out until real
calendar time actually reaches late 2015; meanwhile the real, actively-quoted near-month
6L contract kept trading completely normally throughout (confirmed directly: e.g.
2011-06-09 through 2011-06-15, instrument 26632, expiration 2011-08-31, ~20-30 lots/day —
an entirely ordinary BRE trading week, just never matching the hijacked sticky value).
This is not really "one malicious/anomalous trade" so much as a structural consequence
of 6L's overall thinness: with total daily volume across *all* 6L contracts routinely in
the 10s-to-low-100s of lots, "rank by volume DESC" stops reliably identifying the true
liquid front month and starts behaving close to a coin flip on any given slow day — any
contract with a nonzero print can win. The sticky/monotonic guard then locks onto
whatever won for as long as it takes real trading calendar-time to catch up to that
contract's own expiration, which for a multi-year-out accidental winner is measured in
years, not days.

A `volume >= threshold` floor was tested as a mitigation and helps but does not fully
close the gap: BRE's usable days over a roughly 2010-2016 window went from 295 to
~421-462 out of 1,454 days where *some* 6L contract had a bar that day. A second
variant — naive fallback before the first day a contract clears the threshold — was also
tried and discarded: it reintroduced monotonicity violations (the exact flip-flop class
of bug the sticky rule exists to prevent). **This is an open item, not resolved** — no
change was made to `_CONTINUOUS_FRONT_MONTH_SQL` for this specific failure mode. Anyone
picking this up next should treat "a stickiness rule that advances on volume-rank winner
alone, with no floor, in a market thin enough that daily 'highest volume' is itself
mostly noise" as the actual shape of the bug — a simple volume floor raises the bar for
what can win, but doesn't change the fact that once *any* contract clears that floor on a
single day far from its own expiration, the exact-match join still has no way back
until real time catches up to it. The two known mitigations tested so far trade one
failure mode for a fraction of the other; neither is a free fix.

This finding is orthogonal to, and a direct explanation of, §2's cross-asset liquidity
survey below: 6L (BRE) turned out to be the *only* symbol in the 14-instrument sweep
where every one of the 12 CME month codes wins the naive daily volume race at least once
(§2.2) — now confirmed as the same underlying mechanism as the 1,534-day gap above, not
merely "consistent with" it: 6L is thin enough that essentially any listed month can win
a given day's noise-dominated ranking, and each such win is a candidate for a multi-year
sticky-anchor hijack once it happens to land on a far-dated contract.

---

## 2. Empirical off-month liquidity survey across the instrument universe

The background for this document (GC/SI's off-month pattern, confirmed in the fix's own
validation) explicitly warns against assuming other clusters — grains, rates, equity
index, FX — behave the same way as metals, or the same way as CL (which has essentially
no off-months). All results below were queried directly, not assumed.

### 2.1 Method

For each asset, over 2015-01-01 through 2026-01-01 (`ts_event < expiration`, restricted
to `instrument_class = 'F' AND security_type = 'FUT'`, matching
`_CONTINUOUS_FRONT_MONTH_SQL`'s own filters), rank every date's candidate contracts by
`volume DESC` (the naive daily pick, no stickiness — deliberately, since the question
here is "which months are ever genuinely liquid," not "what the sticky series would
show"), and count how many distinct days each expiration month-letter wins:

```sql
WITH bars AS (
    SELECT instrument_id, ts_event, volume, expiration
    FROM daily
    WHERE asset = ? AND instrument_class = 'F' AND security_type = 'FUT'
      AND expiration IS NOT NULL AND ts_event < expiration
      AND ts_event >= DATE '2015-01-01' AND ts_event <= DATE '2026-01-01'
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY ts_event ORDER BY volume DESC, expiration ASC) AS rn,
           strftime(expiration, '%m') AS exp_month
    FROM bars
)
SELECT exp_month, count(*) AS n_days FROM ranked WHERE rn = 1 GROUP BY exp_month ORDER BY n_days DESC
```

Symbols were the traded/DB tickers, mapped from `instruments.py`'s `INSTRUMENTS`
`cluster` field (`instruments.py:88-187`): grains (`ZC`, `ZL`, `ZS`, `ZW`), rates (`ZN`,
`ZT`), equity (`ES`, `NQ`), FX (`6J`, `6L`, `6M` — the Globex tickers `JPY`/`BRE`
actually resolve to via `db_symbol`, `instruments.py:179-186`), plus metals (`GC`, `SI`)
re-confirmed for consistency and `CL` (energy) as the "no off-months" baseline already
established. `NKD`/`MNK` (intl equity) were skipped — confirmed no `NKD` history in the
local db during earlier work on this project's data layer.

### 2.2 Results

| Cluster | Symbol | # distinct months winning | Winning months (CME letters) |
|---|---|---|---|
| Grain | ZC (corn) | **5** | Z(1185), H(665), N(480), K(435), U(3) |
| Grain | ZL (soybean oil) | **5** | Z(1170), N(505), H(451), K(434), F(208) |
| Grain | ZS (soybeans) | **5** | X(984), N(473), H(448), F(432), K(431) |
| Grain | ZW (wheat) | **5** | Z(732), H(674), N(501), U(434), K(427) |
| Rates | ZN (10Y) | **4** | U(869), Z(855), M(853), H(846) |
| Rates | ZT (2Y) | **4** | U(872), Z(853), M(851), H(847) |
| Equity | ES | **4** | U(863), M(861), Z(859), H(841) |
| Equity | NQ | **4** | U(864), M(862), Z(858), H(840) |
| FX | 6J (JPY) | **4** | U(871), M(861), Z(858), H(834) |
| FX | 6M (MXN) | **4** | U(873), M(859), Z(856), H(836) |
| FX | 6L (BRE) | **12** (all) | Q(292)…J(250), no clear leader — see §2.3 |
| Metal | GC | **5** | Z(1145), Q(585), G(577), M(564), J(549) |
| Metal | SI | **5** | Z(856), H(842), U(583), N(576), K(563) |
| Energy | CL | **12** (all) | M(294)…H(271), broadly flat |

Month-letter code: F=Jan, G=Feb, H=Mar, J=Apr, K=May, M=Jun, N=Jul, Q=Aug, U=Sep,
V=Oct, X=Nov, Z=Dec.

### 2.3 What this means, by cluster

**Rates (ZN, ZT), equity (ES, NQ), and the two "normal" FX contracts (6J, 6M) are
genuinely quarterly** — exactly 4 winning months each, and in every one of those six
symbols the 4 are `{H, M, U, Z}` (Mar/Jun/Sep/Dec), the identical cycle
`FuturesSignalGenerator._get_quarterly_roll_dates` already assumes (§4.2, §4.3.2). For
this half of the universe, the fixed-quarterly roll schedule used by the naked
backtester and TSMOM backtester is a correct match to how these instruments actually
trade — not just a simplifying assumption that happens not to matter, but empirically
confirmed against real volume data.

**Grains do NOT follow the financial quarterly cycle, but they are NOT like CL either —
they have their own fixed set of 5 active months, none of which is a clean subset of
{H, M, U, Z}.** ZC/ZW's winners (`{Z, H, N, K, U}` / `{Z, H, N, U, K}`) and ZL/ZS's
(`{Z, N, H, K, F}` / `{X, N, H, F, K}`) match the standard CBOT grain listing calendar
(corn/wheat: Mar/May/Jul/Sep/Dec; soybeans/soy oil: Jan/Mar/May/Jul/Nov, roughly) — this
is real, well-known agricultural-contract structure, not a data artifact. Concretely:
**only 2 of ZC's 5 active months (H=Mar, Z=Dec) coincide with the quarterly cycle
`tsmom_backtester`/`FuturesSignalGenerator` use for every symbol uniformly** — K(May),
N(Jul), U(Sep) are all real, liquid, non-quarterly-cycle months for corn that the
schedule has no way to represent. This is the concrete confirmation the background asked
for: **the fixed quarterly roll schedule does not apply uniformly and correctly to every
symbol in this project's universe** — it happens to be correct for rates/equity/2-of-3
FX, and wrong (misses real active months, though it doesn't necessarily roll into an
*illiquid* month — see caveat below) for grains, metals, and presumably the CBOT micro
grains that borrow their full-size siblings' `signal_symbol` (`instruments.py:141-160`).

**GC/SI (metals) reproduce the original finding almost exactly** — GC's `{Z, Q, G, M,
J}` here matches the background's `{Z, G, Q, M, J}` (same 5 months, small day-count
differences from a slightly different date window), and SI's `{Z, H, U, N, K}` matches
`{H, Z, N, K, U}`. `V` (Oct) never won a single day for either in ~11 years, as
originally reported.

**CL has no off-months** — all 12 letters win a comparable number of days (271-294),
re-confirming the background's finding and the "crude trades actively every calendar
month" characterization directly.

**6L (BRE) is the outlier, and not in the "CL-like, no off-months" sense.** All 12
letters win at least some days, but unlike CL's tight, roughly-flat 271-294 range, 6L's
spread (250-292) sits on much thinner absolute volume overall and — per §1.2 — is
exactly the symbol independently confirmed to have a real, still-open sticky-anchor bug
driven by one-off small prints winning a daily volume race they don't deserve to win.
**6L's "12 active months" reading should not be interpreted as "BRE genuinely trades all
12 months like crude does"** — it's more consistent with 6L being thin enough that the
naive daily-volume-winner metric itself is noisy at this instrument, the same root cause
as §1.2's hijack. This wasn't independently re-diagnosed contract-by-contract in this
pass (the query above is a coarse letter-level histogram, not a per-instrument-id trace
like the ZN/GC contract-level checks elsewhere in this project's history) — flagged as a
plausible but not exhaustively re-verified read.

**Caveat on "5 active months ≠ automatically dangerous."** GC/SI/grains each have a
*fixed, small, known* set of active months — the risk this document is about isn't that
these products trade year-round (they don't, and that's normal, expected commodity
market structure), it's that neither the live contract-resolution path (§3) nor the
fixed-quarterly backtest roll schedule (§4.2, §4.3.2) has any concept of *which* 5 months
those are for a given symbol — so both are exposed to picking an off-month contract by
construction, not just as a remote edge case.

---

## 3. The live-trading gap: nearest-expiration routing has no liquidity concept

This is the same underlying defect as the pre-fix backtest bug (§1), but in a materially
higher-stakes location: live order routing, not historical price reconstruction.

### 3.1 `get_nearest_quarterly_expiry` / `_resolve_contract`

`get_nearest_quarterly_expiry` (`src/derivatives_bt_engine/live/tsmom_rebalance.py:100-130`),
despite its name, is used generically for **every** symbol resolved via `expiry='auto'`
— not just the quarterly-cycle ones. Its logic: call IB's `req_contract_details` for the
bare symbol (no expiration specified), collect every listed contract month IB returns,
filter to those at least `min_days` (default 7) from expiry, and take whichever is
**soonest by calendar date** (`expiries[0]` after a plain `sorted()`,
`tsmom_rebalance.py:117-128`). There is no volume, open-interest, or liquidity check
anywhere in this function — it is architecturally identical to the pre-fix
`_CONTINUOUS_FRONT_MONTH_SQL`'s `ORDER BY expiration ASC` behavior (§1.1), just against
IB's live contract-details API instead of historical bars.

`_resolve_contract` (`tsmom_rebalance.py:584-599`) calls this whenever
`instr.get('expiry', 'auto') == 'auto'`, which is the only mode `_build_instruments`
ever produces for the standard `--instruments SYM1,SYM2,...` CLI path (§3.2). It is
called on **every rebalance run**, not once and cached — see §4.3's discussion of what
this means for "how does roll happen live."

### 3.2 `_build_instruments` hardcodes `expiry: 'auto'` for every symbol

`_build_instruments` (`src/derivatives_bt_engine/live/run_tsmom_rebalance.py:80-116`)
is what turns a `--instruments MGC,SIL,...`-style CLI argument into the instrument dicts
`compute_rebalance_targets` consumes. For every symbol built from that comma-separated
path, `'expiry': 'auto'` is set unconditionally (`run_tsmom_rebalance.py:107`) — there is
no per-symbol override mechanism in this path for "use the known active month." The
alternative — a hand-written JSON instrument config
(`run_tsmom_rebalance.py:81-84`) — *could* set a literal `expiry` value instead of
`'auto'`, since `_resolve_contract` only calls `get_nearest_quarterly_expiry` when
`expiry == 'auto'` (`tsmom_rebalance.py:592-595`), but nothing in the standard symbol-list
path ever produces that, and no existing JSON config in this repo was found doing so
either (none exists in the repo as tracked files — this would have to be hand-authored
per run).

Both `MGC` (micro gold) and `SIL` (micro silver) are in `INSTRUMENTS`
(`instruments.py:120-125`) and are genuinely live-tradeable — §2.2 already confirmed
their full-size siblings GC/SI each have exactly 5 active months out of a much larger
listed set, with GC's off-month (Jan'26, `GCF6`) topping out at 3,039 lots lifetime
max volume against its neighbors' hundreds of thousands (§2.2's GC verification below).

### 3.3 What this means in practice, and what could/couldn't be verified

**What the code shows directly, with high confidence:** if `run_tsmom_rebalance.py` is
ever invoked for MGC or SIL (or any other symbol whose CME listing includes off-months)
at a moment when "nearest calendar expiration ≥ min_days" happens to resolve to one of
those thin months rather than the genuinely active one, `_resolve_contract` will return
that thin contract, `ib.qualify_contracts` will accept it (there's nothing in
`qualify_contracts`'s job to reject a real, validly-listed-but-illiquid contract — see
below), and `_execute_rebalance_order` (`run_tsmom_rebalance.py:161-180`) will attempt to
route a real limit-at-mid-or-market order into it. This is exactly the slippage/thin-book
risk a careful trader avoids — wide bid/ask, thin depth, and a mid-price limit order that
either doesn't fill or fills at a bad clip, or a market-order fallback
(`run_tsmom_rebalance.py:175-176`, triggered when `ticker.bid`/`ticker.ask` are missing —
plausible on a thin contract) that has essentially no protection at all.

**Whether this has ever actually fired in practice:** not verifiable from the code alone
— this depends on IB's live contract-details response at whatever moment a real run
executes, and no live/paper run was performed as part of this research. Also not
verifiable statically: whether `get_nearest_quarterly_expiry`'s specific `min_days=7`
default combined with real-world CME listing/delisting timing has, historically, ever
actually landed on an off-month for MGC/SIL specifically — that would require either a
run log or a day-by-day replay against IB's historical contract-details responses,
neither of which was available here.

**Whether IB's own contract qualification implicitly filters to active months —
investigated, and the answer is no, not at this layer.** `IBPySync.future`
(`/home/dev/projects/fin-tools/ib-tools/src/ib_tools/ibpysync.py:351-353`) and
`req_contract_details`/`qualify_contracts`
(`ibpysync.py:169-176`) are both thin pass-throughs to `ib_insync`'s
`reqContractDetailsAsync`/`qualifyContractsAsync` with no filtering logic of this
project's own — `req_contract_details(contract)` just forwards whatever IB's API
returns for a bare (no-expiration) `Future(symbol=..., exchange=...)` contract spec.
IB's `reqContractDetails` for a partial futures contract is documented (and it's
well-established practitioner knowledge, not something this session could re-verify
without a live connection) to return **every currently-listed contract month** for
that root symbol — exchange listing and trading liquidity are separate concepts at the
exchange level, and IB's contract-details API reflects listing, not liquidity. Nothing
observed in this codebase or `ib_tools` contradicts that, and nothing in either
provides a liquidity/volume filter as a substitute. **This much could be confirmed from
the code and is stated with confidence; the exact live response shape for MGC/SIL
specifically could not be independently re-verified without an IB connection, which
this research task did not have.**

### 3.4 An additional gap found while reading this path: no explicit old-contract unwind on roll

While tracing `_resolve_contract`/`get_nearest_quarterly_expiry`'s call sites to
understand how a roll actually happens live (§4.3), a further gap was found, reading the
code alone (not verified live): `_current_contracts`
(`tsmom_rebalance.py:212-223`) computes "current position" by summing IB account
positions whose `contract.conId` **exactly matches** the just-resolved contract from
*this run* (`compute_rebalance_targets` → `_compute_signal` → `_resolve_contract` →
`_current_contracts(ib, s['contract'])`, `tsmom_rebalance.py:241,306,521`). There is no
step anywhere in this module that aggregates a symbol's position across *all* of its
dated contracts, and no step that explicitly closes an old, no-longer-resolved contract.

Concretely: as long as consecutive runs resolve to the *same* `conId` (true for most of
a quarterly contract's ~3-month life, since `min_days` only forces a change once the
held contract is within that many days of expiry), `current_contracts` correctly reflects
the account's real exposure and the delta-based rebalance in `main()`
(`run_tsmom_rebalance.py:305-323`) works as intended. But in the specific run where
`get_nearest_quarterly_expiry`'s answer changes from contract A (still possibly holding
a real position) to contract B, `_current_contracts(ib, contract_B)` returns 0 for
contract B specifically, and the code proceeds to size and trade contract B from a
current-position baseline of zero — **with nothing in this module ever inspecting or
closing whatever is still held in contract A.** This reads as a real, structural gap in
how — or whether — this live path actually executes a "roll" at all, as opposed to
simply starting to manage a new contract while an old position sits unmanaged. This
finding comes from static code reading only; it was not exercised against a live or
paper account, and it's possible some other layer of the live trading system (outside
this repo, e.g. a broader position-reconciliation job) catches this — nothing of that
kind was found in this repo or in `ib_tools`, but this repo is not necessarily the whole
live-trading stack.

---

## 4. How rolling actually works today: all three paths

### 4.1 Path 1 — the naked single-symbol backtester

**Model: roll = an ordinary early close plus a same-day reopen, driven by a per-position
calendar attribute (`roll_date`), against the *same* continuous-front-month series
described in §1.**

`FuturesSignalGenerator.generate_futures_signals`
(`src/derivatives_bt_engine/domain/futures_signal_generator.py:35-89`) is what actually
schedules rolls for this path: it computes `_get_quarterly_roll_dates` (Monday before the
third Friday of Mar/Jun/Sep/Dec, `futures_signal_generator.py:97-127`) over the
backtest's date range, then `join_asof`s every underlying bar to the **next** roll date
strictly after that bar (`futures_signal_generator.py:71-84` — the `+1 day` shift before
the join exists specifically so a bar dated exactly on a roll date rolls into the *next*
cycle rather than opening-and-immediately-closing same-day). Every row of `signals`
therefore carries a `roll_date` column — that's the entire signal for this strategy type:
"hold whatever position until the next quarterly roll."

`FuturesPosition.roll_date` (`position.py:1692`) stores this per-position; its
`expire_date` property (`position.py:1698-1702`) is simply `self.roll_date` — futures
have no option-style expiration in this model, `roll_date` **is** the trigger.
`TradeManager._close_expired_positions` (`trade_manager.py:375-505`) is what actually
fires it: it iterates open positions each day and closes any where
`current_date >= pos.expire_date` (i.e. `>= roll_date`), or where a VIX/signal gate fired
early (`trade_manager.py:421-425`). `close_reason_arg` is set to `reason or 'roll'` for a
`FuturesPosition` specifically (`trade_manager.py:481`) — so a position closing on its own
schedule, with no VIX/signal override, is always recorded with `close_reason='roll'`.
Immediately after closing, `TradeManager.construct_position_from_signal`
(`trade_manager.py:507-551`, called from the main day-loop in
`construct_and_execute_trades_from_signals`, `trade_manager.py:301-353`) opens a *new*
`FuturesPosition` the same day from that day's row in `signals` — which already carries
the *next* roll date, from the `join_asof` shift above. So a roll here really is: close
today at today's continuous-series price (which, thanks to §1's fix, is itself the
correctly-liquidity-selected contract for that day), reopen today at the same price under
a fresh `roll_date`.

**Fees at roll**: `FuturesPosition.calculate_pnl`
(`position.py:1721-1742`) charges `commission * 2 * quantity` unconditionally whenever a
position closes — a roll is charged exactly the same round-trip commission as any other
close, no roll-specific discount or surcharge. `get_spec(futures_type)['commission']`
(per-contract-per-side; doubled here for the round trip) is passed in explicitly at the
close-site (`position.py:1857`). The newly-reopened position, symmetrically, pays no
commission on its own open — that cost is only realized when *it* eventually closes,
matching the fee convention documented in the Part 2 research doc's correction #4
(`research_trend_strength_crossover_signal.md`).

**Relation to the "stuck-forever roll" bug** (fixed in commit `fed7287`, this session):
`FuturesPosition._update_closing_data`
(`position.py:1751-1796`) is what resolves `roll_date` (or any other `close_date`) to an
actual priced bar, by an exact `ts_event == close_date` match against
`underlying_price_history` — the same continuous series §1 fixed. Before `fed7287`, a
miss on that exact date (a real gap in a thin contract, e.g. BRE/6L again) returned
`None` outright; since `roll_date` is a fixed, never-advancing attribute, every
subsequent day retried the identical missing date and failed identically, "stranding" a
position for as long as 422 days until an unrelated VIX/signal gate happened to close it.
The fix (`position.py:1782-1789`) snaps forward to the next available bar instead of
failing. **This is the same underlying data source as §1's fix (both read from
`FuturesDataLoader.daily`/`_CONTINUOUS_FRONT_MONTH_SQL`), but a genuinely different bug**
— §1's fix is about *which contract's* price appears in the continuous series on a given
date; the stuck-forever bug was about how a single already-selected position handles a
*missing date* in that series. Both bugs are downstream symptoms of the same root cause
(thin, gappy contracts, especially BRE/6L), but they live in different modules and were
fixed independently — one in `futures_dataloader.py`'s query, the other in
`position.py`'s date-resolution logic.

### 4.2 Path 2 — the TSMOM multi-symbol backtester

**Model: no discrete open/close lifecycle at all — a symbol's exposure is a continuously
resized contract count, monthly. The quarterly roll is a mechanical side-event forced
independent of the signal, not a position-level attribute.**

`run_tsmom_backtest` (`src/derivatives_bt_engine/domain/tsmom_backtester.py:409-780`)
computes `sorted_roll_dates` once, up front, via the exact same
`FuturesSignalGenerator._get_quarterly_roll_dates` static method Path 1 uses
(`tsmom_backtester.py:466-467`) — this is the one piece of roll-scheduling logic actually
shared between the two backtest paths. Rather than attaching a roll date to each
position, this module walks a monotonic pointer (`roll_ptr`,
`tsmom_backtester.py:468,680-683`) through that sorted list inside the main day-loop:
"on the first actual trading day on/after each scheduled roll date, force a roll for
every currently-held symbol, unconditionally" (`tsmom_backtester.py:673-683`).

`_process_roll` (`tsmom_backtester.py:594-630`) is the roll itself: close the expiring
contract at `prior_close[symbol]` (yesterday's marked price — there is no lookup against
a specific date here the way Path 1's `_update_closing_data` does, since this loop
already walks day by day and tracks the last-seen close per symbol) and immediately
reopen the *identical* size at the same price, net zero PnL/size effect from the roll
itself — cost is the commission alone. The docstring is explicit that this deliberately
bypasses `_rebalance_to` (`tsmom_backtester.py:601-606`) specifically to avoid
double-charging: `_rebalance_to`'s own fee logic (`tsmom_backtester.py:508-524`) already
implements the "open is free, only the closing leg is charged, `commission * 2 *
closed_qty`" convention (matching `FuturesPosition.calculate_pnl`'s convention exactly,
per §4.1) — routing a roll through it would incorrectly charge the "reopen" leg a second
time.

Crucially, **the roll fires regardless of `signal_gate_mode`/`fixed_quantities`**
(`tsmom_backtester.py:594-598`'s docstring) — it is a pure calendar mechanism, wholly
independent of whatever the trend signal says that day, mirroring `FuturesPosition`'s own
`roll_date` being unconditional in Path 1.

**Does this module's fixed-quarterly schedule apply uniformly and correctly to every
symbol it trades?** No — and §2.2/§2.3's empirical survey confirms this directly, not
just as a hypothetical. `sorted_roll_dates` (`tsmom_backtester.py:466-468`) is computed
**once**, from the same `_get_quarterly_roll_dates` call, and applied identically to
every symbol in `config.symbols` inside the single `for symbol in config.symbols:
_process_roll(symbol, d)` loop (`tsmom_backtester.py:681-682`) — there is no per-symbol
branching on cluster or actual listing calendar anywhere in this function. §2.2 already
showed GC/SI (5 active months each, none of which is a clean subset of the quarterly
cycle beyond Z=Dec) and the four grains (5 active months each, with corn/wheat sharing
only 2 of their 5 with the quarterly cycle and soybeans/soy oil sharing fewer still) do
**not** follow this schedule in real trading. For those symbols, `_process_roll` is
forcing a close-and-reopen of the *modeled* position on a date that has no necessary
relationship to when that specific instrument's front-month contract actually changes in
reality — the position is still priced off the correctly-selected (post-§1-fix)
continuous series on that date, so this isn't a pricing bug, but it means the backtest's
*number* of rolls (and therefore its total commission drag) for GC, SI, and the four
grains is set by a calendar convention borrowed from an unrelated asset class (financial
quarterly futures), not by those instruments' own real roll cadence. This is a genuine,
now-empirically-confirmed mismatch, worth treating as a real finding rather than an
implied one, exactly as the task background anticipated.

**Fees at roll**: identical convention to Path 1 — `commission * 2 * abs(prior)`
(`tsmom_backtester.py:612`), sourced from the same `get_spec(symbol)['commission']`
registry (`futures_types = {s: get_spec(s) for s in config.symbols}`,
`tsmom_backtester.py:441`).

### 4.3 Path 3 — the live rebalancing path

**Model: no periodic "roll" concept at all, as a first-class idea — contract resolution
is simply re-run fresh, from scratch, on every invocation.**

There is no `roll_date`, no `sorted_roll_dates`, no scheduled-event mechanism of any kind
in `live/tsmom_rebalance.py` or `live/run_tsmom_rebalance.py`. Every run of
`run_tsmom_rebalance.py` calls `_resolve_contract` for every instrument
(`compute_rebalance_targets` → `_compute_signal` → `_resolve_contract`,
`tsmom_rebalance.py:241`, and again directly in `main()` before order placement,
`run_tsmom_rebalance.py:318`), and `_resolve_contract` with `expiry='auto'` always calls
`get_nearest_quarterly_expiry` fresh (§3.1) — there is no caching of "which contract did
we trade last time" anywhere in this module. In that narrow sense, **this path does
implicitly handle rolling** — because it re-resolves "nearest expiry ≥ min_days out"
every single run, the day a previously-nearest contract falls inside the `min_days`
window, the next run automatically starts targeting the next one, with zero explicit
roll-scheduling code required.

But — per §3.4 — that "implicit roll" is only correct in the specific sense of "which
contract will be sized/quoted next"; it does **not** include any explicit mechanism for
unwinding whatever position was actually held in the previous contract. Whether that
gap is masked in practice (e.g. because `_current_contracts` for the *old* contract, if
checked, would show it and some other operational process handles it outside this
module) could not be determined from this repository alone (§3.4).

This module's contract-resolution logic is also entirely independent of §1's
continuous-series construction — `get_nearest_quarterly_expiry` never touches
`FuturesDataLoader`/`_CONTINUOUS_FRONT_MONTH_SQL`/the local duckdb at all; it queries IB's
own live contract-details API directly (§3.1). The two constructions (backtest continuous
series, live contract resolution) are fully separate code paths that happen to share the
same underlying real-world problem (picking the right contract month) and, per this
document's core finding, the same class of blind spot (no liquidity awareness) — but a
fix to one would not touch the other. `resolve_signal_symbol`
(`instruments.py:246-266`), used for the historical-bars fetch that feeds the trend
signal itself (`tsmom_rebalance.py:254-263`), is the one place this path *does* touch
duckdb-adjacent naming logic, but it resolves which *signal history* to use (e.g. a thin
new MZC borrowing ZC's history), not which contract to actually trade — a fully separate
concern from `_resolve_contract`.

**Fees**: this module computes sizing/targets only — `compute_rebalance_targets` never
touches commission, margin cost, or any fee concept at all; `_execute_rebalance_order`
(`run_tsmom_rebalance.py:161-180`) places a bare limit-at-mid (or market-fallback) order
for the raw share-delta with no commission accounting in this codebase at all (that's
between IB and the broker's own fee schedule, not something this repo models or
predicts for the live path the way `get_spec(...)['commission']` is used for backtest
accounting in Paths 1 and 2).

---

## 5. Design proposal (sketch only — not implemented)

The user asked to understand existing mechanics first and will decide separately whether
to build this; nothing below was implemented or wired into any live code path.

**Concept: an explicit `active_months` field on `INSTRUMENTS` entries**
(`instruments.py`), using standard CME month-letter codes:

```python
'GC':  {'exchange': 'COMEX', 'multiplier': 100, 'cluster': 'metal',
        'initial_margin': 40701.95, 'commission': 2.24,
        'active_months': ['G', 'J', 'M', 'Q', 'Z']},   # Feb/Apr/Jun/Aug/Dec -- confirmed §2.2
'ZC':  {'exchange': 'CBOT', 'multiplier': 50, 'cluster': 'grain',
        'initial_margin': 1855.76, 'commission': 3.01,
        'active_months': ['H', 'K', 'N', 'U', 'Z']},   # Mar/May/Jul/Sep/Dec -- confirmed §2.2
'ES':  {'exchange': 'CME', 'multiplier': 50, 'cluster': 'equity',
        'initial_margin': 34068.38, 'commission': 2.24,
        'active_months': ['H', 'M', 'U', 'Z']},        # standard financial quarterly cycle
```

Left unset (`None`/absent) for anything not yet empirically confirmed — same
missing-means-no-data convention the module's docstring already uses for
`initial_margin`/`commission` (`instruments.py:13-20`), not "assume quarterly."

**Two candidate consumers, sketched, not built:**

1. **Live contract resolution** (`get_nearest_quarterly_expiry`,
   `tsmom_rebalance.py:100-130`): filter IB's returned `expiries` list to only those
   whose month letter is in `instr['active_months']` (when set) *before* taking the
   soonest, rather than taking the soonest of every listed month unconditionally. This
   directly closes §3's live-routing gap for any instrument with the field populated,
   with no IB-side liquidity query needed at all — the known-active-month list already
   encodes what §2's volume survey worked out empirically. Contracts with no
   `active_months` set would keep today's behavior exactly (a deliberately conservative
   rollout — nothing changes silently for an unconfirmed symbol).

2. **Backtest continuous-series validation** (`_CONTINUOUS_FRONT_MONTH_SQL`,
   `futures_dataloader.py:77-99`): could be used as an assertion/warning layer analogous
   to `assert_monotonic_expiration` — flag (not silently drop) any date where the
   volume-selected front-month contract's letter falls outside the configured
   `active_months` list, which would have caught the original off-month contamination
   bug (§1.1) as an automated check rather than requiring the manual row-level
   investigation this session actually used, and would give an automated early-warning
   signal for a similar issue on a symbol not yet manually audited.

Neither consumer was built. Any real implementation would need, at minimum: confirming
`active_months` for the full live-tradeable universe (only GC/SI/grains/CL plus the six
quarterly-cycle symbols were checked here — MGC/SIL/MZL/MZC/MZS/MZW/MTN/MCL/MES/MNQ etc.
were assumed to inherit their full-size sibling's calendar via `signal_symbol`/`db_symbol`,
which is a reasonable inference but wasn't independently re-queried per-symbol in this
pass), a decision on what should happen when IB's live listing for a given month
disagrees with a stale `active_months` entry (exchanges do add/drop listed months over
time), and a decision on whether a §1.2-style thin-but-technically-"active"-month
contract (e.g. an active month early in its own life, before volume has migrated to it)
should be excluded by the same mechanism or is a separate problem.

---

## Synthesis / recommendation

- The continuous front-month series fix (§1.1) is real, committed, and validated against
  multiple confirmed failure modes (ZN flip-flop, GC off-month contamination). A second
  issue in the same query — the BRE/6L sticky-anchor hijack (§1.2) — is real, reproduced
  directly against the db, and **not fixed**; both attempted mitigations (volume floor,
  naive fallback) have documented downsides. This is **larger than a narrow edge case**:
  decomposing the production query stage by stage shows 26.6% of 6L's entire history
  (1,184 of 4,446 dates) is currently dropped from the continuous series by this
  mechanism, concentrated in one 1,534-day (four-year) contiguous gap fully explained by
  two single-day noise-driven sticky-anchor jumps — and, on every one of those dropped
  dates, the real near-month contract has perfectly ordinary, fully-populated data
  (volume/instrument_class/security_type/expiration all present, same shape as any
  healthy ES row); the dates are dropped by an exact-match join key pointing at the wrong
  contract, not by any actual absence of usable data. Flag as open and higher-priority
  than the original characterization suggested, not a minor residual case.
- The "which months are actually liquid" question, checked directly rather than assumed
  per-cluster (§2): rates/equity/2-of-3-FX are genuinely, exactly quarterly (`{H,M,U,Z}`)
  — the existing fixed schedule is empirically correct for them, not just convenient.
  Grains and metals share a *different* structural pattern from each other in specifics
  but the same shape — a small, fixed, non-quarterly set of real active months — and CL
  (and, more ambiguously, the thin/noisy 6L) trade close to every calendar month. No
  single roll-schedule assumption is safe to apply universally.
- The live contract-resolution path (`get_nearest_quarterly_expiry`, §3) has the
  identical structural blind spot the pre-fix backtest query had — nearest expiration,
  zero liquidity awareness — but sits in a materially higher-stakes position (real order
  routing, not historical reconstruction), and applies unconditionally to every symbol
  including MGC/SIL, which §2's data confirms genuinely have off-months a naive
  nearest-expiration pick could land on. No liquidity safeguard was found anywhere in
  this repo or in `ib_tools`'s thin `ib_insync` wrappers; IB's own contract-details API is
  understood (with reasonable confidence, though not independently re-verified live) to
  return every listed month regardless of liquidity, meaning nothing upstream filters
  this for the code either.
- A further, separate gap was found reading the live path (§3.4): it has no explicit
  mechanism for closing out a position in a previously-resolved contract once
  `get_nearest_quarterly_expiry`'s answer changes to a new one — current-position
  tracking is keyed to the freshly-resolved contract's own `conId`, not aggregated across
  a symbol's full dated-contract history. This is a static-code-reading finding, not
  verified against a live run, but it reads as a real structural risk independent of the
  off-month liquidity question — worth investigating before (or alongside) any
  liquidity-focused fix to this path.
- All three roll mechanisms (§4) were traced from source and differ meaningfully in
  model, not just implementation detail: Path 1 (naked) treats a roll as an ordinary
  early-close-and-reopen keyed to a per-position calendar attribute; Path 2 (TSMOM)
  has no per-position lifecycle at all and instead forces a mechanical, signal-independent
  close/reopen event at fixed calendar points shared identically across every symbol;
  Path 3 (live) has no explicit roll concept whatsoever, relying entirely on fresh
  per-run contract re-resolution, which (per §3.4) may not be a complete substitute for
  one. Paths 1 and 2 both price rolls off the same (now-fixed, §1) continuous series and
  share the same "open free, close costs `2×commission×quantity`" fee convention; Path 3
  computes no fee model at all.
- The `active_months` field sketch (§5) is a plausible, narrowly-scoped way to encode
  §2's empirical findings and let both the live and backtest paths consult them, but it
  is a proposal only — not implemented, and deliberately left for the user to decide
  whether/when to build, per the task's explicit framing.

## 6. `splice_live_price` (§5's proposal, now implemented) — two live bugs, and a full
   `active_months` re-verification

§5's proposal was eventually implemented as `TsmomLiveConfig.splice_live_price` /
`_splice_live_front_month_bar` (`live/tsmom_rebalance.py`) — splices one fresh IB daily
bar for the DB's own currently-considered-front dated contract onto the tail of the local
duckdb-sourced series, closing a real staleness gap (the local cache found ~3 weeks stale
during this session). Running it live against real IB surfaced two bugs, both fixed and
covered by regression tests (`tests/live/test_tsmom_rebalance.py`):

**6.1 Wrong IB contract multiplier.** This project's internal `multiplier` field is a
$-per-point P&L-scaling convention (used for `position_risk`, notional sizing, etc.), not
IB's own `Contract.multiplier`. It happened to coincide for ES/NQ/GC (both conventions
land on the same number), but diverged for grains/silver/JPY — IB rejected every one with
"No security definition has been found for the request" until `multiplier` was dropped
from the qualification request entirely. This matches `_resolve_contract`'s own existing,
proven pattern (`live/tsmom_rebalance.py`, live-order-routing contract resolution): it
only ever passes `multiplier` for a genuinely ticker-renamed instrument
(`ib_symbol != symbol`), empty otherwise — `db_symbol` in the splice path is always
already the canonical root, so symbol + exchange + an explicit contract month is
sufficient for IB to resolve it unambiguously without one.

**6.2 DB cache stale by more than one roll.** The splice originally assumed the DB's
last-known front-month contract was at most one roll behind reality, and queried it
unconditionally. Confirmed live: `ZL`'s DB-cached front month was `202607` (July),
already fully expired by the time this ran in mid-August — IB correctly rejected it, and
since the original two-candidate comparison wasn't per-candidate fault-tolerant, the
whole splice aborted right there without ever trying December (the genuinely correct next
month). Fixed with `_live_front_month_candidates`, which walks forward past any
already-expired candidate (approximated via calendar month, not exact listed expiry day)
before ever calling IB, with each candidate now pulled independently so one dead contract
can't hide a working later one.

**6.3 Full `active_months` re-verification, all 12 confirmed-list symbols.** Diagnosing
6.2 raised a live question — "is the roll from GC's August contract straight to December,
skipping October, actually correct, or is `active_months` itself wrong/incomplete?" —
re-run directly against `/home/dev/fin/db/globex_mdp_3.0.duckdb` using the EXACT
production `_CONTINUOUS_FRONT_MONTH_SQL` query (not §2's naive, non-sticky version), for
every symbol currently carrying a confirmed `active_months` list in `instruments.py`:
grouping the resulting sticky front-month series' own `expiration` month across its full
~2010–2026 history and comparing the set of months that ever actually win against the
registry's claimed list.

11 of 12 matched exactly:
- `ES`/`NQ`/`ZN`/`ZT`/`6J`(JPY)/`6M`: all `{03,06,09,12}` = `['H','M','U','Z']`, confirmed.
- `GC`: `{02,04,06,08,12}` = `['G','J','M','Q','Z']`, confirmed. October (`V`) never once
  won the crossover in the full history, despite carrying real (if much thinner) volume
  — 2025-10 alone totaled 482K vs. 7-19M for the confirmed months that same period.
- `ZL`: `{01,03,05,07,12}` = `['F','H','K','N','Z']`, confirmed. August/September/October
  never won either, despite each carrying real, non-trivial volume (roughly 15-25% of an
  active month's total in a typical year) — this was the ORIGINAL live symptom (a stale
  `202607` DB row skipping straight to December), and it turns out that skip is correct,
  not a registry gap; the actual bug was 6.2 (querying the stale month unconditionally),
  not the registry.
- `ZS`, `ZW`, `SI`: all matched their registry lists exactly.

**One genuine registry bug found**: `ZC` (corn) claims `active_months: ['H', 'K', 'N',
'U', 'Z']` (includes `U`/September), but the DB's actual sticky-crossover history shows
September has **never once** won the front-month crossover across the full dataset
(2010–2026) — only `{03,05,07,12}` (`H`,`K`,`N`,`Z`) ever appear. This isn't a data
availability artifact either: September ZC is genuinely liquid in its own right (91.7M
total raw volume across the dataset, the same order of magnitude as March/May/July's
130-180M) — it just never manages to out-volume whatever the currently-sticky contract is
on any single day, likely because corn's trading activity concentrates disproportionately
onto the "big" months (December alone: 316.9M). Fixed in `instruments.py`: `ZC`'s
`active_months` is now `['H', 'K', 'N', 'Z']`.

Caveats specific to this section: single DB snapshot, no live/paper IB cross-check beyond
what 6.1/6.2 already confirmed empirically against real IB during this session; the
GC/ZL "never wins despite real volume" framing is aggregate-total-vs-daily-crossover
reasoning, not a symbol-by-symbol replay of every individual day (the ZC finding IS a
full, exhaustive day-by-day sticky-series re-derivation via the actual production SQL,
not a sample).

## Caveats on verification

- §1.2's severity figures (1,184 of 4,446 dates dropped, 26.6%; the 1,534-day contiguous
  gap and its two-step sticky-anchor decomposition) were derived by manually decomposing
  `_CONTINUOUS_FRONT_MONTH_SQL` stage-by-stage (`bars` → `naive_ranked`/`naive_front` →
  final join) against 6L's full history, run directly during this session and confirmed
  interactively — the CTE-by-CTE row counts, the day-by-day `sticky_expiration` trace
  around 2011-05-24/2011-07-13, and the segment breakdown were all independently
  reproduced, not taken from an estimate. This materially sharpens (and is a larger,
  more severe finding than) the original characterization of this bug earlier in this
  session, which described a single 2010-07-01 event and a narrower ~2010-2016 window —
  that earlier framing undersold how much of 6L's history this currently affects.
- All duckdb queries in §2 were re-run directly against
  `/home/dev/fin/db/globex_mdp_3.0.duckdb` while writing this document (not reused from
  an earlier session's cached output) — exact query text is inline in §2.1. The GC
  Dec'25→Feb'26 skip and ES Dec'25→Mar'26 crossover examples cited in the task background
  were independently re-verified against the same table (day-by-day top-volume rows for
  each window); the reported numbers matched closely (GCZ5 224,220→281 lots Nov 20→27;
  GCG6 crossing over Nov 25 at 149,622 vs GCZ5's 56,246; GCF6/Jan'26 lifetime max volume
  3,039; ES crossover from `294973`/Dec'25 to `42140878`/Mar'26 landing exactly on
  2025-12-15) — small numeric variances from the background's summary are attributable to
  slightly different date-window boundaries, not a discrepancy in the underlying finding.
- §2's month-letter histogram used the plain naive daily-volume-winner (no stickiness),
  deliberately, to answer "which months are ever genuinely liquid" rather than "what the
  production sticky series looks like" — this is the right tool for the liquidity-survey
  question but means the 6L "12 months" result (§2.3) reflects raw noise, not the
  post-sticky-fix production series; not independently re-diagnosed contract-by-contract
  the way the ZN/GC failures were during the original fix.
- §3.3's claim about IB's `reqContractDetails` returning every listed month regardless of
  liquidity is stated with reasonable confidence (it is well-established behavior of that
  API, and nothing in this codebase or `ib_tools` contradicts or filters it) but was not
  independently re-confirmed via a live or paper IB connection as part of this research
  task — flagged explicitly per the task's own instruction to state what could and
  couldn't be verified from the code alone.
- §3.4 (no explicit old-contract unwind on roll in the live path) is a static-code-reading
  finding only. It was not exercised against a live/paper account, and it's possible some
  process outside this repository (a broader position-reconciliation layer, a human
  operator's manual check, etc.) mitigates it in practice — nothing of that kind was found
  in this repo or in `ib_tools`, but the live trading stack is not necessarily fully
  contained in either.
- §5's proposal was not implemented, and its "which symbols need `active_months`"
  discussion is itself an inference (full-size siblings' calendars assumed to transfer to
  their micros/minis via `signal_symbol`/`db_symbol`) rather than an independently
  re-queried per-symbol confirmation for every live-tradeable instrument in
  `INSTRUMENTS` — only the symbols explicitly named in §2.1 were actually queried.
