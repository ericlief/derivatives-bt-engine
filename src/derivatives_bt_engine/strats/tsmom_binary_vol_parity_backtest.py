"""
Stripped-down TSMOM test: binary sign(signal) direction, flat per-asset
vol-parity sizing (Levine & Pedersen 2016, "Which Trend Is Your Friend?",
Table 1 methodology -- every asset gets the SAME flat annualized-$-vol
target, no cluster/bucket hierarchy, no risk_scalar clamp, no cluster risk
cap, no max_notional/max_contracts ceiling). Monthly rebalance, matching
tsmom_backtester.py's cadence. Reuses only pure data-loading/signal
functions (load_portfolio_data, _month_end_dates, and signal_spec.py's
build_features()/continuous_momentum()/goulding_monthly() -- both models
computed independently from the same raw OHLCV, per that module's own
"no model depends on another model's intermediate columns" design) --
none of tsmom_backtester.py's own position-sizing.

Written to answer a specific question (see
research/research_trend_strength_crossover_signal.md, Part 2 §6): does
regime_discount (the flat Correction/Rebound de-risking multiplier in
tsmom_signal.py's compute_position_scalar) actually help on this project's
own recent data? The existing tsmom-bt CLI (tsmom_backtester.py) turned out
to be unsuitable for this -- it sizes each symbol independently against its
own vol_target/max_notional/max_contracts with NO cross-instrument risk
cap (unlike live/tsmom_rebalance.py's compute_desired_risk_budget/
apply_cluster_risk_cap), so a correlated multi-symbol run there produces
wildly overstated realized vol (82-90% against a 15% target was observed).
This script sidesteps that gap entirely with a much simpler, literature-
literal sizing scheme instead of trying to patch it.

Sharpe is invariant to FLAT_PER_ASSET_VOL_TARGET_USD's absolute level (a
uniform leverage rescale) -- the specific value only matters for realistic
contract-count rounding, not for the Sharpe comparison across
--regime-discounts. An earlier version also reported a return figure
post-hoc rescaled to a 10% vol target (matching Figure 4's own stated
methodology in Goulding/Harvey/Mazzoleni, "returns scaled to achieve 10%
annualized monthly volatility") -- this was REMOVED after it was flagged
as misleading: the rescale multiplied only the return figure, while
total_fees (reported alongside it) stayed at its actual, un-rescaled
dollar value. That's only truly consistent under an idealized assumption
(fees scale exactly linearly with position size, same as gross P&L) that
integer contract-count rounding breaks in practice. Sharpe alone is the
valid, scale-invariant comparison and needs no such rescale/asterisk --
report raw ann_ret/ann_vol/fees together instead, all at the same actual
scale, or explicitly rescale fees by the same factor if a vol-normalized
return figure is ever needed again.

Transaction costs: get_spec(symbol)['commission'] is charged using the
SAME asymmetric convention as tsmom_backtester.py's own _rebalance_to
(mirrored from FuturesPosition.calculate_pnl) -- opening or adding to a
position is free; only the closing/shrinking leg is charged, at
2 * commission * closed_qty (both round-trip legs bundled into the
close). A same-direction resize charges only the portion that shrinks
back toward zero; a full close or a sign flip charges the entire prior
side. Plus a mandatory quarterly roll charge (2 * abs(held) * commission,
same Mon-before-3rd-Friday Mar/Jun/Sep/Dec schedule
FuturesPosition.roll_date uses, same "close old + reopen new, reopening
is free" logic as tsmom_backtester.py's _process_roll) -- a roll is a
real close-old/open-new round trip and costs commission twice even on a
quarter where the continuous price series happens not to jump.

Two corrections here, both flagged directly by the user rather than
caught in review: (1) an earlier version of this script had NO
transaction costs at all, which inflated its Sharpe/return figures versus
a realistic backtest; (2) the version right after that charged commission
symmetrically on every monthly resize (abs(new_target - held) * 1x
commission, in both directions) -- this overcharged every position
INCREASE (which should be free, same as opening) and used the wrong
quantity/multiplier on decreases and flips (e.g. a full sign flip from
+5 to -3 charged abs(-3-5)=8 units at 1x, instead of the correct
closed_qty=5 at 2x -- closing the whole long side, with the new short
open free). Fixed to match tsmom_backtester.py's _rebalance_to exactly.

Known limitation NOT fixed here, and not unique to this script: the
continuous front-month price series this project uses everywhere
(FuturesDataLoader.daily / futures_dataloader._CONTINUOUS_FRONT_MONTH_SQL)
picks, independently for EVERY date, whichever not-yet-expired contract
has the soonest expiration AMONG THOSE WITH A PRINTED BAR THAT DAY
(`row_number() OVER (PARTITION BY ts_event ORDER BY expiration ASC)`).
This has no memory of which contract was "front" yesterday -- it's
recomputed from scratch each day. Confirmed directly against ZN, March
2023: the Mar'23 contract (instrument_id 397730) trades a genuinely thin
few hundred contracts/day even at its most active, has NO printed bar at
all on 2023-03-19 (so that day's front-month price is silently read from
Jun'23 instead, already the real liquid contract by volume), reappears
with one more trade on 2023-03-20 (so the ranking flips BACK to Mar'23,
a lower price level), then goes quiet for good from 2023-03-21 onward
(ranking settles on Jun'23). The result is a pure contract-switch
artifact -- not a real price move -- read by every downstream consumer as
if it were one instrument's continuous price path. Same root phenomenon
(thin trading right before a contract's own expiration) as the BRE/6L
stuck-roll bug fixed earlier this project's history, surfacing as a
different defect: there, a position got stuck waiting for an exact-date
match that never came; here, the continuous *price series itself*
silently swaps which contract it's quoting. Both naked_futures.py's
Backtester/FuturesPosition path and tsmom_backtester.py's own multi-symbol
path read from this exact same series, so this affects all three backtest
paths equally -- worth its own separate investigation/fix, not patched
here.

Run (registered console script, works from any directory -- see
pyproject.toml's [project.scripts]; moved here from scripts/ specifically
because that directory isn't an installed package, so
tsmom_vol_parity_window_scheme.py's `from ... import run` couldn't resolve
except when invoked as `python -m scripts.X` from the repo root):
    tsmom-vol-parity
    tsmom-vol-parity --regime-discounts 0.5,1.0 --years 2023-2026
    tsmom-vol-parity --symbols ES,NQ,GC --years 2015-2026
"""
from __future__ import annotations

import argparse
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import polars as pl

from derivatives_bt_engine.domain.allocation import _bounded_ewm_correlation_matrix, build_returns_wide, compute_idm
from derivatives_bt_engine.domain.instruments import get_spec, resolve_active_months, resolve_annualization_days
from derivatives_bt_engine.domain.signal import (
    SignalSpec,
    _goulding_blend,
    _goulding_weight,
    build_features,
    build_monthly_state_return_history,
    continuous_momentum,
    estimate_mixing_params,
    goulding_monthly,
)
from derivatives_bt_engine.domain.tsmom_backtester import _detect_roll_dates, _month_end_dates, load_portfolio_data
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()

# ── Tunable defaults ────────────────────────────────────────────────────────
# BRE (J7's/JPY's fellow FX symbol) deliberately excluded from both universe
# lists below -- its continuous series has a known, unresolved sticky-anchor
# bug (only 71.5% of dates survive the sticky join, vs 100% for every other
# symbol; see research/research_futures_roll_logic_and_active_months.md
# §1.2/§2). 6M stands in for FX-EM exposure instead: correlated, better
# volume, no known data-quality issue.
# DEFAULT_SYMBOLS = ['ES', 'NQ', 'CL', 'GC', 'SI', 'ZN', 'ZT', 'ZL', 'ZC', 'ZS', 'ZW', 'JPY', '6M']
DEFAULT_SYMBOLS = ['MES', 'MNQ', 'MCL', 'MGC', 'SIL', 'MTN', 'MZL', 'MZC', 'MZS', 'MZW', 'J7', '6M']
DEFAULT_YEARS = '2010-2026'
# Signal history buffer before --years' own start, so ts_fast/ts_slow/
# c_fast/c_slow/g_fast/g_slow are non-null from the test window's first
# rebalance -- a
# FIXED offset from `start`, not a fixed absolute date: an earlier version
# hardcoded 2018-01-01 regardless of --years, which silently capped the
# effective test window at 2018+ even when --years asked for an earlier
# start (e.g. --years 2010-2026 tested the same window as --years
# 2015-2026 until this was fixed -- confirmed directly, both gave
# n_days=2636).
WARMUP_DAYS_BEFORE_START = 400  # > 252 (slow_return's own lookback) with headroom
DEFAULT_INITIAL_CAPITAL = 1_000_000.0
# Fraction of `initial_capital`, NOT a fixed dollar figure -- every asset
# still gets the exact same flat target (that's the whole point of vol
# parity, Levine & Pedersen 2016 Table 1's own methodology; this constant
# doesn't vary by instrument), but it must scale with whatever capital a
# given run actually uses. A prior version hardcoded a fixed $10,000
# figure (documented only in a comment as "1% of default capital") that
# stayed fixed regardless of --initial-capital -- passing e.g.
# --initial-capital 100000 without also rescaling --flat-vol-target
# silently turned "1% of capital per asset" into 10% per asset (12x the
# intended leverage across the default 12-symbol universe), with no
# warning. run() derives the actual USD figure from whatever capital it's
# given unless a caller explicitly overrides it (see
# flat_per_asset_vol_target_usd=None's own handling below).
DEFAULT_VOL_TARGET_PCT_OF_CAPITAL = 0.01
DEFAULT_REGIME_DISCOUNTS = [0.5, 1.0]

# ── Infrastructure ──────────────────────────────────────────────────────────
# Anchored to the project root, not a bare relative "results" -- now that
# this lives in strats/ as a registered console script (tsmom-vol-parity),
# it can be invoked from any CWD, and a bare relative path would silently
# create results/results if ever run from inside results/ itself (e.g.
# after cd-ing there to inspect prior output) -- same reasoning
# tsmom_backtest.py's own main() already uses for the identical problem.
RESULTS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'results'))

# Goulding, Harvey & Mazzoleni (2023), "Breaking Bad Trends" -- eq. 4's
# Bull/Correction/Bear/Rebound state classification and eq. 8-10's a_Co/a_Re
# mixing-parameter estimator, kept separate from calculate_trend_strength's
# existing 3m/12m ts_fast/ts_slow (which stay canonical/untouched per that
# function's own docstring). The paper's own 2m/12m fast/slow horizons and
# eq. 4/7 state/weight logic come from domain/signal.py's
# build_features()/goulding_monthly()/_goulding_weight (genuine calendar-
# month aggregation) and build_monthly_state_return_history/
# estimate_mixing_params (the pooled, expanding-window a_Co/a_Re
# ESTIMATION, moved there from this script once tsmom_backtester.py needed
# the same estimation logic too -- one canonical implementation, not two).
DEFAULT_MIXING_POOL = 'cluster'  # 'cluster' or 'global' -- see run()'s own docstring
# Warn when a symbol rounds to 0 target contracts on more than this
# fraction of its own rebalances -- see the sizing_diag block in run()
SIZING_ZERO_WARN_THRESHOLD = 0.2
# Default for run()'s active_set_redistribution param -- see that param's
# own docstring and the inline comment at its point of use in run() for the
# full rationale (empirically confirmed structural opt-out at low capital,
# not a novel idea being introduced speculatively). True is the default
# because it strictly improves capital utilization at any capital level (at
# high capital, zeroed_symbols is empty every rebalance, so this is a no-op)
# -- kept toggleable, not made unconditional, so a before/after comparison
# stays possible the same way mixing_pool='cluster' vs 'global' already is.
DEFAULT_ACTIVE_SET_REDISTRIBUTION = True
# Default for run()'s guarantee_cluster_representation param -- see that
# param's own docstring and the inline comment at its point of use in run()
# for the full rationale. Confirmed empirically that active_set_redistribution
# ALONE (a plain equal-split across the active set) doesn't rescue
# equity/energy/metal/fx at low capital -- it just leverages up whichever
# grains already cleared rounding, since no single symbol gets enough of an
# equal-split boost to individually clear its own much higher per-contract
# dollar vol. This layers a per-cluster minimum floor on top, so at least one
# member of every cluster with a live signal actually trades, instead of the
# portfolio silently collapsing into just one or two cheap clusters. Default
# True for the same reason as DEFAULT_ACTIVE_SET_REDISTRIBUTION -- at high
# capital every cluster already clears its floor at the flat target, so this
# is a no-op there; kept toggleable for before/after comparison.
DEFAULT_GUARANTEE_CLUSTER_REPRESENTATION = True
# Ratio (just above 0.5, not exactly 0.5) used to compute a cluster-floor
# rep's reserved budget -- see the inline comment at that computation's
# point of use in run() for why exactly 0.5 silently fails (Python's
# round() is round-half-to-even, so round(0.5) == 0).
_CLUSTER_FLOOR_RATIO = 0.51
# Defaults for run()'s idm_scaling feature -- Carver-style Instrument
# Diversification Multiplier (IDM = 1/sqrt(W H W_t)), see compute_idm's own
# docstring. New/unvalidated relative to active_set_redistribution and
# guarantee_cluster_representation (both already confirmed to help at low
# capital), so DEFAULT_IDM_SCALING is False -- opt in explicitly for
# comparison, not a silent behavior change.
DEFAULT_IDM_SCALING = False
DEFAULT_IDM_WINDOW_YEARS = 3.0      # bounded trailing window -- see
                                     # _bounded_ewm_correlation_matrix's own
                                     # docstring for why bounded, not an
                                     # unbounded full-history EWM
DEFAULT_IDM_HALFLIFE_DAYS = 63.0    # matches this project's existing
                                     # per-instrument vol-estimation default
                                     # (tsmom_risk_budget_diagnostic.py) --
                                     # not independently tuned for
                                     # correlation specifically; a longer
                                     # halflife is plausibly more
                                     # appropriate (correlation is slower-
                                     # moving than vol) but untested here


def run(symbols: list[str], start: date, end: date, regime_discount: float,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        flat_per_asset_vol_target_usd: Optional[float] = None,
        target_portfolio_vol: Optional[float] = None,
        warmup_start: Optional[date] = None,
        weighting_mode: str = 'flat_discount',
        mixing_pool: str = DEFAULT_MIXING_POOL,
        active_set_redistribution: bool = DEFAULT_ACTIVE_SET_REDISTRIBUTION,
        guarantee_cluster_representation: bool = DEFAULT_GUARANTEE_CLUSTER_REPRESENTATION,
        idm_scaling: bool = DEFAULT_IDM_SCALING,
        idm_window_years: float = DEFAULT_IDM_WINDOW_YEARS,
        idm_halflife_days: float = DEFAULT_IDM_HALFLIFE_DAYS,
        save_results: bool = False,
        results_tag: Optional[str] = None,
        _quiet: bool = False) -> dict:
    """target_portfolio_vol: None (default) leaves flat_per_asset_vol_target_usd
    exactly as given/derived (this project's original behaviour). When set
    (e.g. 0.15, matching the live system's own target_portfolio_vol
    convention -- see scripts/tsmom_risk_budget_diagnostic.py's identical
    default), run() instead calibrates flat_per_asset_vol_target_usd so the
    backtest's REALIZED annualized portfolio vol (ann_vol_pct) lands at this
    target: it first simulates once at the flat/uncalibrated budget purely
    to measure realized vol, then rescales the budget by
    (target_portfolio_vol / realized_vol) and simulates again for the
    actual, returned result. Implemented as a single internal recursive
    call (with _quiet=True, an internal-only flag that also suppresses this
    calibration pass's own console output) rather than an iterated fixed
    point -- this module's own top docstring already establishes Sharpe is
    invariant to a uniform leverage rescale in a frictionless world, so one
    rescale gets close; it isn't exact here because rounding/cluster-floor
    effects are nonlinear in budget, but re-running to full convergence
    would cost yet another full simulation for a typically small remaining
    error, not worth it for a backtest utility. If the calibration pass's
    own realized vol comes out exactly 0 (e.g. every symbol rounds to 0
    contracts even at the flat budget), there is nothing sensible to scale
    by, so flat_per_asset_vol_target_usd is left unchanged and only
    simulated once. Directly motivated by the capital-level Sharpe
    investigation earlier in this project's history: raising the flat
    per-asset budget at low capital (implicitly, more leverage) measurably
    improves Sharpe by shrinking contract-rounding distortion, but doing
    that via trial-and-error --flat-vol-target values has no natural
    stopping point -- targeting a specific portfolio vol (the same
    methodology already used live) gives a principled, comparable one.

    flat_per_asset_vol_target_usd: None (default) derives it as
    DEFAULT_VOL_TARGET_PCT_OF_CAPITAL * initial_capital -- the SAME flat
    USD figure applied to every asset either way (vol parity's whole
    point), but scaled to whatever capital this run actually uses instead
    of a fixed number that quietly stops meaning "1% of capital" the
    moment --initial-capital changes. Pass an explicit value to override
    this scaling entirely (e.g. to hold the USD target fixed while varying
    --initial-capital deliberately, for some other comparison).

    weighting_mode:
        'flat_discount' -- existing behaviour: ts_fast/ts_slow-based `ts`/`regime`
            from signal_spec.py's continuous_momentum, computed independently
            from raw OHLCV via build_features -- NOT the old calculate_trend_
            strength (which normalizes both ts_fast and ts_slow off a single
            fast-window daily_std; continuous_momentum's std_fast/std_slow are
            each horizon-matched to their own fast_window/slow_window instead).
            regime_discount applied as a flat multiplier in Correction/Rebound.
        'dynamic' -- Goulding/Harvey/Mazzoleni eq. 4/7-8-10: paper's own
            2m/12m raw-return state classification, and a_Co/a_Re mixing
            weights (re-estimated at every rebalance date from all PRIOR
            pooled history, no lookahead) blending the slow/fast direction
            in Correction/Rebound instead of a flat discount. `regime_discount`
            is ignored in this mode.

    mixing_pool (only matters when weighting_mode == 'dynamic'):
        'cluster' (default) -- a_Co/a_Re re-estimated separately per
            instruments.py `cluster` (grain/metal/equity/rates/fx/energy),
            each symbol using only its own cluster's pooled Correction/
            Rebound history. The point of estimating these weights at all
            is to capture how a given asset class behaves in each regime;
            pooling across unrelated clusters would blend that behavior
            into one number dominated by whichever cluster has the most
            history, defeating the purpose. A cluster with few symbols/
            little history of its own (e.g. a single-symbol cluster) stays
            at the uninformed (0.5, 0.5) fallback longer, or for the whole
            run -- an intentional consequence, not a bug (see
            _estimate_mixing_params).
        'global' -- original behaviour: one shared a_Co/a_Re pooled across
            every symbol in `symbols` regardless of cluster. Kept for
            direct before/after comparison against 'cluster', not the
            default.

    active_set_redistribution (default True): each rebalance, a symbol with
        a real nonzero directional weight whose own target still rounds to
        0 contracts (per-contract dollar vol > flat_per_asset_vol_target_usd)
        is dropped from that rebalance's active set, and its unused share of
        the flat budget is redistributed (equal-split, GLOBALLY across the
        whole symbol universe, not scoped per cluster -- same-cluster
        symbols tend to round to 0 together, so a cluster-scoped version
        would usually have no cluster-mate left to receive it) across
        whichever symbols DO clear a whole contract. Confirmed empirically
        that without this, some symbols (e.g. thin/expensive-per-contract
        ones like J7/6M/SIL at $80k) round to 0 on effectively 100% of
        rebalances for the WHOLE backtest -- a structural opt-out, not
        occasional rounding-down -- silently collapsing a nominally
        N-symbol diversified portfolio into whichever smaller-notional
        subset happens to survive rounding (e.g. just the grains), which
        directly hurts both mean Sharpe and its stability across capital
        levels/window-scheme runs. Set to False to reproduce the original
        (pre-redistribution) sizing behaviour, for direct comparison -- see
        the inline comment at this parameter's point of use in the
        rebalance loop for the full derivation.

    guarantee_cluster_representation (default True): layered on top of
        active_set_redistribution, not a replacement for it. Reserves
        enough budget for EACH instruments.py cluster with at least one
        live signal this month to fund its own cheapest-per-contract
        member up to 1 whole contract, BEFORE the equal-split
        redistribution above runs. Directly addresses a gap
        active_set_redistribution alone doesn't close: an equal split
        across survivors never gives any ONE symbol enough of a boost to
        individually clear its own much higher per-contract dollar vol, so
        at low capital it just leverages up whichever grains already
        cleared rounding rather than restoring breadth -- confirmed
        directly on $80k dynamic-mode runs (see results/*_sizing.csv:
        pct_zero for J7/6M/SIL/MCL/MGC/MNQ/MES was IDENTICAL with
        active_set_redistribution on vs off, meaning none of them were
        ever rescued by it alone). Set to False to disable this floor and
        rely solely on active_set_redistribution's plain equal-split.

    idm_scaling (default False -- new, unvalidated relative to
        active_set_redistribution/guarantee_cluster_representation, opt in
        explicitly): Carver-style Instrument Diversification Multiplier,
        IDM = 1/sqrt(W H W_t) -- see compute_idm's own docstring. Recomputed
        at EVERY rebalance date from that date's own signal-active symbols
        and a bounded trailing-window EWM correlation matrix (see
        _bounded_ewm_correlation_matrix, no lookahead), then multiplies
        that rebalance's own effective budget (every symbol's flat target,
        cluster-floor reservations, and active-set redistribution all
        scale off this SAME per-rebalance budget when idm_scaling is on --
        a pure no-op, idm_multiplier == 1.0 always, when off). Generalizes
        the live system's own compute_desired_risk_budget, whose
        1/sqrt(n_effective) is exactly this same formula under the
        assumption every active cluster is uncorrelated (rho=0) -- this
        uses the REAL measured correlation instead. idm_window_years
        (default 3.0) and idm_halflife_days (default 60.0, matching this
        project's existing per-instrument vol-estimation default -- NOT
        independently tuned for correlation, which is plausibly
        slower-moving and might want a longer halflife; untested here)
        control that bounded window.

    warmup_start defaults to WARMUP_DAYS_BEFORE_START before `start` (not a
    fixed absolute date -- see that constant's own comment for why).

    save_results, if True, writes CSVs into RESULTS_DIR ("results/",
    created if missing) -- no prefix required. Filenames are auto-tagged
    with `results_tag` (a datetime stamp, e.g. "20260728_140512") plus this
    run's own mode/discount label: "results/{results_tag}_{mode}_daily.csv"
    (date, capital, ret), "results/{results_tag}_{mode}_rebalances.csv"
    (one row per symbol actually rebalanced: date, state, cluster, a_co/
    a_re, ts/continuous_regime/c_fast/c_slow, g_regime/g_fast/g_slow,
    weight, prior->target contracts, fee), and, when weighting_mode ==
    'dynamic', "results/{results_tag}_{mode}_yearly.csv" (one row per
    (year, cluster): mean a_co/a_re actually used that year plus that
    year's own Bull/Bear/Correction/Rebound month counts -- lets a_co/
    a_re's evolution and each cluster's regime mix be read off directly
    instead of eyeballing 12+ rebalance rows per year). `results_tag`
    defaults to "now" (main() generates one shared tag up front instead
    and passes it to every run() call in a single CLI invocation, so a
    --regime-discounts sweep's several runs land under the same tag
    rather than each getting its own). None of this existed anywhere
    before, so there was no way to audit what a given run actually did
    after the fact, only the final summary numbers.
    """
    if mixing_pool not in ('cluster', 'global'):
        raise ValueError(f"mixing_pool must be 'cluster' or 'global', got {mixing_pool!r}")
    if flat_per_asset_vol_target_usd is None:
        flat_per_asset_vol_target_usd = DEFAULT_VOL_TARGET_PCT_OF_CAPITAL * initial_capital

    if target_portfolio_vol is not None and not _quiet:
        # See target_portfolio_vol's own docstring above for the full
        # rationale -- single calibration pass, not an iterated fixed
        # point. `not _quiet` guards against this calibration call itself
        # recursing again (it always passes _quiet=True below, so this
        # branch is never entered a second time no matter what
        # target_portfolio_vol the caller passed).
        baseline = run(symbols, start, end, regime_discount,
                        initial_capital=initial_capital,
                        flat_per_asset_vol_target_usd=flat_per_asset_vol_target_usd,
                        warmup_start=warmup_start, weighting_mode=weighting_mode,
                        mixing_pool=mixing_pool,
                        active_set_redistribution=active_set_redistribution,
                        guarantee_cluster_representation=guarantee_cluster_representation,
                        idm_scaling=idm_scaling, idm_window_years=idm_window_years,
                        idm_halflife_days=idm_halflife_days,
                        save_results=False, results_tag=None, _quiet=True)
        realized_vol = (baseline['ann_vol_pct'] or 0) / 100
        if realized_vol > 0:
            flat_per_asset_vol_target_usd = flat_per_asset_vol_target_usd * (target_portfolio_vol / realized_vol)
        # else: realized_vol == 0 (e.g. every symbol rounds to 0 contracts
        # even at the flat budget) -- nothing sensible to scale by; fall
        # through and simulate once, below, at the ORIGINAL
        # flat_per_asset_vol_target_usd unchanged.

    if warmup_start is None:
        warmup_start = start - timedelta(days=WARMUP_DAYS_BEFORE_START)
    price_data, _ = load_portfolio_data(symbols)
    # Built once (reusing the same price_data already loaded above) and
    # reused at every rebalance date's own bounded-window slice -- only
    # when idm_scaling is actually requested, since this is an extra
    # inner-join + pct_change pass over every symbol's full history that
    # every other weighting_mode/toggle combination has no use for.
    returns_wide = build_returns_wide(price_data) if idm_scaling else None
    # Resolved once per symbol and reused both for the signal itself (so
    # avg_r_fast/avg_r_slow/hv_fast/hv_slow report in each instrument's own
    # real trading-days/year, per continuous_momentum's own docstring) AND
    # for dollar_vol_per_contract's annualization below -- previously that
    # sizing step hardcoded 252 unconditionally, inconsistent with the 259
    # figure instruments.py carries for every non-grain cluster (equity/
    # metal/rates/fx), understating dollar_vol_per_contract by sqrt(252/259)
    # (~1.4%) and correspondingly oversizing positions for those symbols.
    annualization_by_symbol = {s: resolve_annualization_days(s) for s in symbols}
    # Computed before the per-symbol loop below -- needed there to build
    # each symbol's own forward-matched rebal_monthly lookup.
    rebal_dates = sorted(d for d in _month_end_dates(price_data) if start <= d <= end)
    rebal_dates_df = pl.DataFrame({'ts_event': rebal_dates}).sort('ts_event')

    signals = {}
    rebal_monthly = {}
    for sym, df in price_data.items():
        df = df.filter((pl.col('ts_event') >= warmup_start) & (pl.col('ts_event') <= end))
        # Both models computed independently from the same raw OHLCV via
        # build_features -- no dependence on the old calculate_trend_strength
        # (which this script no longer calls at all) and no model depends on
        # the other's intermediate columns, per signal_spec.py's own design.
        feat = build_features(df)
        sig = continuous_momentum(feat, **SignalSpec(annualization_days=annualization_by_symbol[sym]).continuous_kwargs())
        # continuous_momentum's own r_fast/r_slow (its 63d/252d continuous
        # returns) are renamed c_fast/c_slow here -- not dropped -- so they
        # sit alongside goulding_monthly's own g_fast/g_slow/g_regime below
        # for direct comparison (matching the original notebook's
        # contin_fast/contin_slow/reg_contin vs fast/slow/reg_monthly),
        # without colliding once joined (join_asof would otherwise silently
        # suffix one side to r_fast_right/r_slow_right and the final select
        # would silently pick the wrong model's returns under a shared name).
        signals[sym] = sig.select(['ts_event', 'close', 'ts', 'std_fast', 'std_slow', 'regime',
                                    pl.col('r_fast').alias('c_fast'), pl.col('r_slow').alias('c_slow')]
                                   ).sort('ts_event')
        # Paper's own genuine calendar-month Bull/Correction/Bear/Rebound
        # classification from signal_spec.py's goulding_monthly() (real
        # group_by_dynamic('1mo') aggregation, not a fixed-trading-day
        # approximation) instead of a duplicate hand-rolled computation
        # here -- this script computes its own weight from `g_regime` +
        # the separately-estimated a_co/a_re, below, via _goulding_weight.
        #
        # goulding_monthly returns one row per MONTH, labeled by that
        # month's own START date (e.g. 2023-11-01). rebal_dates are month-
        # END trading days (e.g. 2023-10-31) -- a rebalance on 2023-10-31
        # decides what to hold GOING FORWARD (i.e. during November), so it
        # needs November's own bucket (computed from October's now-complete
        # data), NOT October's own bucket (computed from September's data,
        # which is what a backward join_asof would silently pick, since
        # Oct-31 < Nov-01). strategy='forward' finds the first monthly
        # label >= each rebal date, which -- since a rebal date always
        # falls strictly inside its own month, one full month before the
        # NEXT month's label -- is always exactly that next month's bucket.
        # Confirmed directly: without this, every rebalance read a signal
        # one full calendar month stale.
        monthly = goulding_monthly(feat, **SignalSpec.goulding().goulding_kwargs())
        monthly = monthly.rename({'fast': 'g_fast', 'slow': 'g_slow', 'regime': 'g_regime'})
        # 'ret' kept -- goulding_monthly's own simple month-end-to-month-end
        # return for THIS bucket, already computed once; _build_monthly_
        # state_return_history reads it directly instead of recomputing a
        # return from daily closes (which would also mix log/simple
        # conventions -- this module uses simple returns throughout).
        monthly = monthly.select(['ts_event', 'ret', 'g_fast', 'g_slow', 'g_regime']).sort('ts_event')
        rebal_monthly[sym] = rebal_dates_df.join_asof(monthly, on='ts_event', strategy='forward')

    all_dates = sorted(set().union(*(set(df['ts_event'].to_list()) for df in signals.values())))
    all_dates = [d for d in all_dates if start <= d <= end]
    rebal_set = set(rebal_dates)

    monthly_history = (build_monthly_state_return_history(
                            rebal_monthly, rebal_dates, {s: get_spec(s)['cluster'] for s in symbols})
                        if weighting_mode == 'dynamic' else None)

    # Mandatory contract roll: a real close-old/open-new round trip that costs
    # commission twice even when this project's continuous front-month price
    # series (FuturesDataLoader.daily) doesn't itself show a price change that
    # day. Uses tsmom_backtester.py's _detect_roll_dates -- each symbol's own
    # real volume-driven front-month crossovers (FuturesDataLoader.daily's own
    # `expiration` column changes), cross-checked against instruments.
    # resolve_active_months(), NOT a single fixed quarterly schedule shared
    # across every symbol (empirically wrong for grains/metals -- see
    # research/research_futures_roll_logic_and_active_months.md §2, §4.2).
    # price_data[s] must be the unbounded series (not the warmup/end-windowed
    # `df` used for signals above) -- _detect_roll_dates' own docstring warns
    # a windowed slice's first row always looks like a false roll.
    roll_dates_by_symbol = {
        s: set(_detect_roll_dates(price_data[s], start, end, resolve_active_months(s), s))
        for s in symbols
    }

    held = {s: 0 for s in symbols}
    prev_close: dict[str, float] = {}
    capital = initial_capital
    total_fees = 0.0
    rows = []
    # One row per (rebalance date, symbol) actually acted on -- the only
    # way to audit *why* a run's number came out the way it did (state,
    # weight, a_Co/a_Re, prior->target, fee) instead of just trusting the
    # final Sharpe. Nothing was saved anywhere before this -- confirmed
    # there was no way to tell, after the fact, what a given run actually
    # did at each rebalance.
    rebalance_events = []

    for d in all_dates:
        # Mark-to-market with yesterday's held contracts against today's move.
        pnl = 0.0
        for s in symbols:
            row = signals[s].filter(pl.col('ts_event') == d)
            if row.height == 0:
                continue
            close = row['close'][0]
            if s in prev_close and held[s] != 0:
                spec = get_spec(s)
                pnl += held[s] * spec['multiplier'] * (close - prev_close[s])
            prev_close[s] = close
        capital += pnl

        # Mandatory roll: commission on a full close+reopen of whatever is
        # currently held, no price/quantity effect (see note above). Each
        # symbol rolls on its own detected date, not a shared calendar one.
        fees = 0.0
        for s in symbols:
            if held[s] != 0 and d in roll_dates_by_symbol[s]:
                fees += 2 * abs(held[s]) * get_spec(s)['commission']
        if fees:
            capital -= fees
            total_fees += fees

        # Rebalance at month-end using today's just-observed signal.
        if d in rebal_set:
            fees = 0.0
            # a_Co/a_Re re-estimated once per rebalance date -- per CLUSTER
            # under mixing_pool='cluster' (each symbol only sees its own
            # asset class's pooled Correction/Rebound history, since
            # pooling across clusters would blend unrelated asset classes'
            # behavior into one number), or once globally and shared by
            # every symbol under mixing_pool='global' (this project's
            # original behaviour, kept for direct comparison). See
            # domain/signal.py's build_monthly_state_return_history/estimate_mixing_params.
            if weighting_mode == 'dynamic':
                clusters_needed = {get_spec(s)['cluster'] for s in symbols}
                if mixing_pool == 'cluster':
                    mixing_params_by_cluster = {c: estimate_mixing_params(monthly_history, d, c)
                                                 for c in clusters_needed}
                else:
                    global_params = estimate_mixing_params(monthly_history, d, None)
                    mixing_params_by_cluster = {c: global_params for c in clusters_needed}
            else:
                mixing_params_by_cluster = {}
            # First pass: compute each symbol's weight/dollar_vol_per_contract
            # (everything needed to decide contract targets) WITHOUT yet
            # rounding to a final target -- active_set_redistribution (below)
            # needs to see every valid candidate's raw numbers before any
            # target is committed, since it reallocates budget FROM symbols
            # that would round to 0 TO the ones that survive. Skip-conditions
            # (invalid signal) are identical to the prior single-pass version
            # -- a symbol failing them here is excluded entirely, same as
            # before, not merely "zeroed by rounding."
            candidates = []
            for s in symbols:
                row = signals[s].filter(pl.col('ts_event') == d)
                if row.height == 0:
                    continue
                # Position SIZE always comes from the continuous model's
                # own daily std_fast (vol-parity), in BOTH weighting_modes
                # -- including 'dynamic', whose DIRECTION/weight instead
                # comes from Goulding's monthly g_regime/g_fast/g_slow via
                # _goulding_weight above. That's a deliberate split, not an
                # oversight: the paper itself doesn't size positions at
                # all (it studies raw dynamic-blend RETURNS, eq. 7, as a
                # standalone return series -- see the class docstring's
                # own "we form the ... portfolio return as a weighted
                # average of individual asset returns"); this project
                # layers that regime/blend logic onto its OWN vol-parity
                # sizing framework instead, since a position needs a
                # concrete contract count from *some* volatility estimate,
                # and daily std_fast is what every other mode/path in this
                # project already sizes off. Worth being explicit that
                # "dynamic" here means "Goulding decides direction,
                # vol-parity decides size" -- not a literal end-to-end
                # reproduction of the paper's own portfolio construction.
                dstd = row['std_fast'][0]
                dstd_slow = row['std_slow'][0]  # audit-only -- sizing itself still uses std_fast (dstd) above
                # `dstd <= 0` alone doesn't catch NaN -- comparisons with NaN
                # are always False in Python, so a NaN std_fast (e.g. from a
                # bad/missing price on some date) silently slipped through
                # this guard, propagated into dollar_vol_per_contract, and
                # crashed round() downstream with "cannot convert float NaN
                # to integer" -- confirmed on a real run.
                dstd_bad = dstd is None or (isinstance(dstd, float) and math.isnan(dstd)) or dstd <= 0

                # Both models' fast/slow and regime are computed
                # unconditionally above (regardless of weighting_mode) so
                # always read them all here too -- audit rows should let
                # continuous (ts/regime/c_fast/c_slow) and Goulding
                # (g_regime/g_fast/g_slow) be compared side by side on every
                # rebalance, not just whichever one is actually driving that
                # run's sizing. g_fast/g_slow/g_regime come from
                # rebal_monthly (forward-matched -- see its own construction
                # above), NOT from `row`/`signals[s]` -- those would be the
                # backward-joined, one-month-stale reading.
                ts_val, regime = row['ts'][0], row['regime'][0]
                c_fast_val, c_slow_val = row['c_fast'][0], row['c_slow'][0]
                g_row = rebal_monthly[s].filter(pl.col('ts_event') == d)
                g_fast_val = g_row['g_fast'][0] if g_row.height else None
                g_slow_val = g_row['g_slow'][0] if g_row.height else None
                g_regime_val = g_row['g_regime'][0] if g_row.height else None
                state = None
                cluster = a_co = a_re = r_dyn = None
                if weighting_mode == 'dynamic':
                    # g_regime is None whenever fast/slow lack enough completed
                    # months of history yet (goulding_monthly's own rolling_mean).
                    state = g_regime_val.lower() if g_regime_val else None
                    if state is None or dstd_bad:
                        logger.warning(f"{s} on {d}: skipping rebalance, invalid signal "
                                        f"(g_fast={g_fast_val}, g_slow={g_slow_val}, std_fast={dstd})")
                        continue
                    # This symbol's own cluster's mixing params -- NOT a
                    # shared global estimate -- see mixing_params_by_cluster
                    # above.
                    cluster = get_spec(s)['cluster']
                    a_co, a_re = mixing_params_by_cluster[cluster]
                    # (1-a_co)*g_slow+a_co*g_fast in Correction, mirrored in
                    # Rebound, sign of the blended RESULT taken as the
                    # position weight -- signal_spec.py's own (corrected)
                    # eq. 7 weight formula, reused here instead of a
                    # duplicate if/elif ladder. Passes the period's own
                    # actual g_fast/g_slow values -- NOT just regime/a_co/
                    # a_re -- see _goulding_weight's own docstring for why
                    # that distinction matters.
                    weight = _goulding_weight(g_regime_val, a_co, a_re, g_fast_val, g_slow_val)
                    # Raw pre-sign eq. 7 value, audit-only -- e.g. a_co=0.5
                    # ("uninformed") can still produce a nonzero +-1
                    # weight, because the blend of the ACTUAL g_fast/
                    # g_slow landed nonzero, not because a_co carried
                    # directional information at 0.5. None for Bull/Bear
                    # (no blend -- see _goulding_blend's own docstring).
                    r_dyn = _goulding_blend(g_regime_val, a_co, a_re, g_fast_val, g_slow_val)
                    if weight is None:
                        logger.warning(f"{s} on {d}: skipping rebalance, invalid signal "
                                        f"(g_fast={g_fast_val}, g_slow={g_slow_val} unusable for blend)")
                        continue
                else:
                    if (ts_val is None or (isinstance(ts_val, float) and math.isnan(ts_val)) or dstd_bad):
                        logger.warning(f"{s} on {d}: skipping rebalance, invalid signal "
                                        f"(ts={ts_val}, std_fast={dstd})")
                        continue
                    direction = 1.0 if ts_val > 0 else (-1.0 if ts_val < 0 else 0.0)
                    discount = regime_discount if regime in ('correction', 'rebound') else 1.0
                    weight = direction * discount

                spec = get_spec(s)
                close = row['close'][0]
                # This symbol's own resolved trading-days/year -- NOT a
                # hardcoded 252 -- consistent with the annualization_days
                # continuous_momentum was actually built with above.
                dollar_vol_per_contract = close * spec['multiplier'] * dstd * (annualization_by_symbol[s] ** 0.5)
                if dollar_vol_per_contract <= 0:
                    continue
                candidates.append({
                    's': s, 'spec': spec, 'close': close, 'weight': weight,
                    'dollar_vol_per_contract': dollar_vol_per_contract,
                    'cluster': cluster, 'c_regime': regime, 'c_fast': c_fast_val, 'c_slow': c_slow_val,
                    'sig': ts_val, 'g_regime': g_regime_val, 'g_fast': g_fast_val, 'g_slow': g_slow_val,
                    'a_co': a_co, 'a_re': a_re, 'r_dyn': r_dyn, 'std_fast': dstd, 'std_slow': dstd_slow,
                })

            # Carver-style IDM (Instrument Diversification Multiplier):
            # 1/sqrt(W H W_t), W the (equal, 1/n) weight vector over this
            # rebalance's own signal-active symbols, H their REAL pairwise
            # correlation matrix from a BOUNDED trailing EWM window ending
            # strictly before `d` (no lookahead -- see
            # _bounded_ewm_correlation_matrix's own docstring for why
            # bounded, not an unbounded full-history EWM). Scales this
            # rebalance's own effective budget UP when active instruments
            # are genuinely diversified (lower correlation), reflecting
            # that a diversified book can run bigger individual positions
            # while still landing on the SAME target portfolio vol --
            # exactly the sqrt(n_effective) logic compute_desired_risk_
            # budget already uses live, generalized from an assumed ρ=0 to
            # the REAL measured correlation. idm_scaling defaults to False
            # (new, not yet validated against the cluster-floor/active-set
            # baseline this run() already has) -- when off, idm_multiplier
            # is always 1.0, a pure no-op on every line below that uses it.
            idm_multiplier = 1.0
            if idm_scaling:
                active_symbols_for_idm = [c['s'] for c in candidates if c['weight'] != 0]
                corr_pairs = _bounded_ewm_correlation_matrix(
                    returns_wide, active_symbols_for_idm, d, idm_window_years, idm_halflife_days)
                idm_multiplier = compute_idm(active_symbols_for_idm, corr_pairs)
            budget_this_rebal = flat_per_asset_vol_target_usd * idm_multiplier

            # Cluster-floor guarantee: before any further redistribution,
            # reserve enough budget for EACH instruments.py `cluster` that
            # has at least one live (nonzero-weight) signal this month to
            # fund its own CHEAPEST-per-contract member up to 1 whole
            # contract. Without this, a plain equal-split redistribution
            # (below) naturally favors whichever symbols were ALREADY cheap
            # enough to clear rounding -- confirmed empirically at $80k: it
            # just leverages up the 4-5 grains further every time, never
            # rescues equity/energy/metal/fx, because an equal share never
            # gives any ONE of those symbols enough of a boost to
            # individually clear its own much higher per-contract dollar
            # vol. This step forces breadth ACROSS clusters instead (at
            # least one member trading per cluster, not "however many
            # grains happen to survive"), at the cost of a smaller position
            # in the clusters that didn't need the help.
            #
            # Picks the CHEAPEST member per cluster (not a fixed/preferred
            # symbol) since which member is cheapest can shift month to
            # month with price/vol -- e.g. metal's rep floats between MGC
            # and SIL depending on that month's own dollar_vol_per_contract.
            #
            # `needed = _CLUSTER_FLOOR_RATIO * dollar_vol / |weight|`, using
            # a ratio just ABOVE 0.5 (not exactly 0.5): Python's round() is
            # round-half-to-even, so round(0.5) == 0, not 1 -- computing
            # `needed` at EXACTLY the 0.5 boundary would round back down to
            # 0 and silently fail to guarantee anything (confirmed directly:
            # an earlier version of this used exactly 0.5 here, and every
            # cluster whose cheapest rep's own needed-budget landed near
            # that exact boundary stayed at pct_zero=1.0 in the sizing
            # diagnostic -- the guarantee never actually fired). A small
            # margin above 0.5 avoids relying on exact floating-point
            # equality at a rounding boundary at all. `max(flat, needed)`
            # never reduces a cluster's rep below the plain flat target --
            # clusters whose cheapest member already clears at the flat
            # budget (e.g. grain, rates) are left untouched by this step.
            reserved_budget: dict[str, float] = {}
            if guarantee_cluster_representation:
                signal_active_all = [c for c in candidates if c['weight'] != 0]
                by_cluster: dict[str, list[dict]] = {}
                for c in signal_active_all:
                    by_cluster.setdefault(c['cluster'], []).append(c)
                for members in by_cluster.values():
                    rep = min(members, key=lambda c: c['dollar_vol_per_contract'])
                    needed = _CLUSTER_FLOOR_RATIO * rep['dollar_vol_per_contract'] / abs(rep['weight'])
                    reserved_budget[rep['s']] = max(budget_this_rebal, needed)

                total_nominal = budget_this_rebal * len(signal_active_all)
                total_reserved = sum(reserved_budget.values())
                if total_reserved > total_nominal > 0:
                    # Can't fund every cluster's floor in full this month
                    # (too many clusters' cheapest members are still
                    # expensive relative to the total budget) -- scale every
                    # reservation down proportionally rather than fully
                    # funding some clusters while dropping others to zero,
                    # so "at least one member per cluster" stays the target
                    # even under a tight total budget, just at a smaller
                    # size for each rep.
                    scale = total_nominal / total_reserved
                    reserved_budget = {s: b * scale for s, b in reserved_budget.items()}

            # Active-set redistribution over the REMAINING (non-cluster-rep)
            # pool only -- reps already have their own guaranteed budget
            # above and are never touched by this step. A symbol here with a
            # real, nonzero directional weight whose OWN target still rounds
            # to 0 contracts at the flat budget is dropped from this
            # rebalance, and its unused share is redistributed (equal-split)
            # across whichever OTHER non-rep symbols DO clear a whole
            # contract, instead of quietly evaporating.
            #
            # Redistributed GLOBALLY across the remaining pool, not scoped
            # per cluster (unlike the floor step above, which is inherently
            # per-cluster by design) -- same reasoning as before: symbols
            # within one cluster tend to round to 0 together, so a
            # cluster-scoped redistribution here would often have no
            # surviving cluster-mate to receive the freed budget.
            #
            # `round(...) == 0` mirrors new_target's own rounding rule
            # exactly (not a separate |raw| < 0.5 threshold computed
            # independently) so "zeroed by rounding" is defined identically
            # to how new_target itself is actually computed below.
            zeroed_symbols: set[str] = set()
            effective_target_usd = budget_this_rebal
            if active_set_redistribution:
                signal_active = [c for c in candidates
                                  if c['weight'] != 0 and c['s'] not in reserved_budget]
                zeroed_symbols = {
                    c['s'] for c in signal_active
                    if round(c['weight'] * budget_this_rebal / c['dollar_vol_per_contract']) == 0
                }
                n_survivors = len(signal_active) - len(zeroed_symbols)
                if zeroed_symbols and n_survivors > 0:
                    # Equivalent to splitting the WHOLE (non-rep) signal-active
                    # budget (budget_this_rebal * len(signal_active)) equally
                    # across just the survivors, derived here as "keep the
                    # flat target, plus an equal share of what the zeroed
                    # symbols would have used."
                    freed = budget_this_rebal * len(zeroed_symbols)
                    effective_target_usd = budget_this_rebal + freed / n_survivors
                # If every non-rep signal-active symbol would zero out
                # (n_survivors == 0), there's no one to redistribute to --
                # leave effective_target_usd at the flat value; every such
                # candidate's target still rounds to 0 for this rebalance
                # either way.

            for c in candidates:
                s, spec, close = c['s'], c['spec'], c['close']
                weight, dollar_vol_per_contract = c['weight'], c['dollar_vol_per_contract']
                regime, c_fast_val, c_slow_val, ts_val = c['c_regime'], c['c_fast'], c['c_slow'], c['sig']
                g_regime_val, g_fast_val, g_slow_val = c['g_regime'], c['g_fast'], c['g_slow']
                a_co, a_re, r_dyn = c['a_co'], c['a_re'], c['r_dyn']
                dstd, dstd_slow = c['std_fast'], c['std_slow']
                cluster = c['cluster']

                # Resolution order: (1) this symbol is its cluster's
                # guaranteed rep -- use its own reserved_budget, which may
                # be smaller than effective_target_usd but is guaranteed
                # never to round to 0 (barring the tight-total-budget
                # scale-down above); (2) dropped by active-set
                # redistribution -- target forced to 0 rather than
                # recomputed off effective_target_usd (already confirmed,
                # above, to still round to 0 for this symbol at the
                # UNBOOSTED flat target -- boosting only the survivors'
                # budget, never this symbol's own, is the whole point of
                # "dropped"); (3) ordinary non-rep survivor -- gets
                # effective_target_usd (flat, or boosted by whatever the
                # active set freed up).
                if s in reserved_budget:
                    new_target = round(weight * reserved_budget[s] / dollar_vol_per_contract)
                elif s in zeroed_symbols:
                    new_target = 0
                else:
                    new_target = round(weight * effective_target_usd / dollar_vol_per_contract)
                prior = held[s]
                # Same asymmetric convention as tsmom_backtester.py's
                # _rebalance_to: opening or adding to a position is free;
                # only the closing/shrinking leg charges commission, at 2x
                # (both round-trip legs bundled into the close), matching
                # FuturesPosition.calculate_pnl's fee model. A same-
                # direction resize only charges for the portion that
                # shrinks back toward zero, not the portion added.
                if prior == 0:
                    closed_qty = 0
                elif new_target == 0 or (prior > 0) != (new_target > 0):
                    closed_qty = abs(prior)
                else:
                    closed_qty = max(0, abs(prior) - abs(new_target))
                event_fee = 2 * closed_qty * spec['commission']
                fees += event_fee
                rebalance_events.append({
                    'date': d, 'symbol': s, 'mode': weighting_mode,
                    # No separate 'state' column -- it was always exactly
                    # continuous_regime (flat_discount mode) or g_regime
                    # lowercased (dynamic mode), never independent
                    # information given continuous_regime/g_regime are both
                    # already here unconditionally.
                    'cluster': cluster,
                    'c_regime': regime, 'c_fast': c_fast_val, 'c_slow': c_slow_val, 'sig': ts_val,
                    'g_regime': g_regime_val, 'g_fast': g_fast_val, 'g_slow': g_slow_val,
                    'a_co': a_co, 'a_re': a_re,
                    'weight': round(weight, 4),
                    'r_dyn': round(r_dyn, 4) if r_dyn is not None else None,
                    'close': close, 'std_fast': dstd, 'std_slow': dstd_slow,
                    'prior': prior, 'target': new_target, 'dol_vol': round(dollar_vol_per_contract, 2),
                    # budget_usd -- the budget actually used for THIS
                    # symbol: reserved_budget[s] if it's a cluster-floor
                    # rep, 0.0 if dropped by active-set redistribution,
                    # otherwise effective_target_usd (flat, or boosted by
                    # whatever the active set freed up) -- audit-only, lets
                    # a saved rebalances.csv show exactly when/how much
                    # cluster-flooring or redistribution occurred without
                    # recomputing it by hand.
                    'budget_usd': round(
                        reserved_budget[s] if s in reserved_budget
                        else (0.0 if s in zeroed_symbols else effective_target_usd), 2
                    ),
                    'fee': round(event_fee, 2),
                })
                held[s] = new_target
            capital -= fees
            total_fees += fees

        rows.append({'date': d, 'capital': capital})

    stats = pl.DataFrame(rows).with_columns(
        ret=pl.col('capital') / pl.col('capital').shift(1) - 1
    ).drop_nulls('ret')

    mean_ret, std_ret = stats['ret'].mean(), stats['ret'].std()
    ann_ret, ann_vol = mean_ret * 252, std_ret * (252 ** 0.5)
    sharpe = ann_ret / ann_vol if ann_vol else None
    running_max = stats['capital'].cum_max()
    dd_pct = ((stats['capital'] - running_max) / running_max * 100).min()

    # Per-symbol contract-rounding diagnostic -- surfaced unconditionally
    # (not just under save_results) since this is a correctness-adjacent
    # warning, not an optional report: `round(weight * target_usd /
    # dollar_vol_per_contract)` silently returns 0 whenever a symbol's own
    # per-contract dollar vol exceeds flat_per_asset_vol_target_usd, and a
    # symbol stuck at 0 most/all of the time is quietly opting itself out
    # of the whole portfolio -- easy to miss without explicitly checking
    # for it (this exact scenario took a long manual back-and-forth to
    # diagnose by hand at low --initial-capital before this existed).
    sizing_diag = None
    if rebalance_events:
        sizing_diag = (
            pl.DataFrame(rebalance_events)
            .group_by('symbol')
            .agg(
                pl.len().alias('n_rebals'),
                (pl.col('target') == 0).mean().alias('pct_zero'),
                pl.col('dol_vol').median().alias('median_dollar_vol_per_contract'),
            )
            .sort('pct_zero', descending=True)
        )
        high_zero = sizing_diag.filter(pl.col('pct_zero') > SIZING_ZERO_WARN_THRESHOLD)
        if high_zero.height > 0 and not _quiet:
            # print(), not logger.warning() -- this project's shared
            # setup_logger() filters WARNING/ERROR out of console output
            # entirely (still reaches the log file, just not stdout), and
            # this diagnostic specifically needs to be seen without
            # digging through logs/ -- same reasoning as the summary/
            # comparison tables below already using print(). `not _quiet`
            # additionally suppresses this during target_portfolio_vol's
            # own internal calibration pass, whose diagnostics are about a
            # budget that's about to be rescaled away and would otherwise
            # print twice (once for the calibration pass, once for the
            # real, final result) for every calibrated run.
            print()
            print(f"=== WARNING: {weighting_mode} -- {high_zero.height}/{sizing_diag.height} symbol(s) round to "
                  f"0 contracts on >{SIZING_ZERO_WARN_THRESHOLD:.0%} of rebalances at "
                  f"flat_per_asset_vol_target_usd=${flat_per_asset_vol_target_usd:,.0f} "
                  f"(effectively opted out of the portfolio, not just occasionally rounding down) ===")
            print(high_zero.with_columns(pl.col(pl.Float64).round(4)))

    if save_results:
        tag = f"{weighting_mode}" + (f"_{regime_discount}" if weighting_mode == 'flat_discount' else '')
        ts = results_tag or datetime.now().strftime('%Y%m%d_%H%M%S')
        results_dir = Path(RESULTS_DIR)
        results_dir.mkdir(parents=True, exist_ok=True)
        daily_path = results_dir / f"{ts}_{tag}_daily.csv"
        events_path = results_dir / f"{ts}_{tag}_rebalances.csv"
        events_df = pl.DataFrame(rebalance_events)
        # Round every float column to 4dp for CSV readability -- the raw
        # values (e.g. daily_std=0.015278191541596027) are full float64
        # precision and unreadable in a spreadsheet/terminal.
        stats.with_columns(pl.col(pl.Float64).round(4)).write_csv(daily_path)
        events_df.with_columns(pl.col(pl.Float64).round(4)).write_csv(events_path)
        logger.info(f"Saved {daily_path} ({stats.height} rows) and {events_path} ({events_df.height} rows)")

        if weighting_mode == 'dynamic' and events_df.height:
            # One row per (year, cluster): mean a_co/a_re actually applied
            # that year (re-estimated at every rebalance, so this averages
            # however many distinct estimates fell in the year) alongside
            # that year's own Bull/Bear/Correction/Rebound month counts --
            # the same counts _estimate_mixing_params pools over, just
            # sliced by year for readability instead of only ever seeing
            # the final cumulative total.
            yearly = (
                events_df.with_columns(pl.col('date').dt.year().alias('year'))
                .group_by(['year', 'cluster'])
                .agg(
                    pl.col('a_co').mean().alias('a_co_mean'),
                    pl.col('a_re').mean().alias('a_re_mean'),
                    # g_regime directly -- goulding_monthly's own regime
                    # literals are already lowercase ('bull'/'bear'/
                    # 'correction'/'rebound'), same values 'state' used to
                    # duplicate in dynamic mode.
                    (pl.col('g_regime') == 'bull').sum().alias('n_bull'),
                    (pl.col('g_regime') == 'bear').sum().alias('n_bear'),
                    (pl.col('g_regime') == 'correction').sum().alias('n_correction'),
                    (pl.col('g_regime') == 'rebound').sum().alias('n_rebound'),
                )
                .sort(['cluster', 'year'])
            )
            yearly_path = results_dir / f"{ts}_{tag}_yearly.csv"
            yearly.with_columns(pl.col(pl.Float64).round(4)).write_csv(yearly_path)
            logger.info(f"Saved {yearly_path} ({yearly.height} rows)")

        if sizing_diag is not None:
            sizing_path = results_dir / f"{ts}_{tag}_sizing.csv"
            sizing_diag.with_columns(pl.col(pl.Float64).round(4)).write_csv(sizing_path)
            logger.info(f"Saved {sizing_path} ({sizing_diag.height} rows)")

    return {
        'mode': weighting_mode,
        'discount': regime_discount if weighting_mode == 'flat_discount' else None,
        'n_days': stats.height,
        'ann_ret_pct': round(ann_ret * 100, 2),
        'ann_vol_pct': round(ann_vol * 100, 2),
        'sharpe': round(sharpe, 2) if sharpe else None,
        'max_dd_pct': round(dd_pct, 2),
        'total_fees': round(total_fees, 2),
    }


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS),
                    help='Comma-separated futures symbols, must be known instruments.py symbols (default: %(default)s)')
    p.add_argument('--years', default=DEFAULT_YEARS, help='Year range as START-END, inclusive (default: %(default)s)')
    p.add_argument('--regime-discounts', default=','.join(str(d) for d in DEFAULT_REGIME_DISCOUNTS),
                    help='Comma-separated regime_discount values to compare, one run each (default: %(default)s)')
    p.add_argument('--initial-capital', type=float, default=DEFAULT_INITIAL_CAPITAL)
    p.add_argument('--flat-vol-target', type=float, default=None,
                    help='Flat annualized $ vol target, same for every asset -- no clustering. '
                         # argparse's own HelpFormatter does `help_string % params` --
                         # a literal "%" from the f-string (e.g. "1%") needs escaping to
                         # "%%" or that later substitution raises TypeError: %o format
                         # (confirmed directly -- this crashed --help entirely until fixed).
                         f'Default: derived as {DEFAULT_VOL_TARGET_PCT_OF_CAPITAL:.0%}% of --initial-capital '
                         '(scales with it) rather than a fixed number -- pass this explicitly to '
                         'override that scaling and hold the USD target fixed instead')
    p.add_argument('--target-portfolio-vol', type=float, default=None,
                    help="Calibrate --flat-vol-target so REALIZED annualized portfolio vol lands "
                         "at this target (e.g. 0.15 -- matches the live system's own "
                         "target_portfolio_vol convention, see "
                         "scripts/tsmom_risk_budget_diagnostic.py), instead of using --flat-vol-target's "
                         "own value/default as-is. Runs one internal calibration pass first (see run()'s "
                         "own docstring) -- roughly doubles this run's time. Default: off (--flat-vol-target "
                         "is used exactly as given/derived)")
    p.add_argument('--include-dynamic', action='store_true',
                    help="Also run the paper's own eq. 4/7-10 dynamic a_Co/a_Re "
                         "reweighting (Goulding/Harvey/Mazzoleni), alongside the "
                         "--regime-discounts flat-discount run(s) (default: off)")
    p.add_argument('--mixing-pool', choices=['cluster', 'global'], default=DEFAULT_MIXING_POOL,
                    help="Only affects --include-dynamic runs. 'cluster' (default): a_Co/a_Re "
                         "estimated separately per instruments.py cluster (grain/metal/equity/"
                         "rates/fx/energy). 'global': one shared estimate pooled across every "
                         "--symbols regardless of cluster (this project's original behaviour, "
                         "kept for comparison) (default: %(default)s)")
    p.add_argument('--no-active-set-redistribution', action='store_true',
                    help="Disable active-set redistribution (default: enabled -- see run()'s own "
                         "docstring). With it enabled (the default), a symbol whose target rounds "
                         "to 0 contracts at flat_per_asset_vol_target_usd is dropped from that "
                         "rebalance and its unused budget share is redistributed across whichever "
                         "symbols DO clear a whole contract, instead of silently evaporating -- "
                         "confirmed to matter most at low --initial-capital, where some symbols "
                         "(e.g. J7/6M/SIL at $80k) otherwise round to 0 on ~100%% of rebalances for "
                         "the whole backtest. Pass this flag to reproduce the original, "
                         "non-redistributed sizing for direct before/after comparison")
    p.add_argument('--no-cluster-floor', action='store_true',
                    help="Disable the cluster-representation floor (default: enabled -- see "
                         "run()'s own docstring). With it enabled (the default), each "
                         "instruments.py cluster with a live signal this month has its own "
                         "cheapest-per-contract member funded to at least 1 contract BEFORE "
                         "--no-active-set-redistribution's equal-split runs -- confirmed "
                         "necessary because active-set redistribution alone never rescues "
                         "equity/energy/metal/fx at low capital (it just leverages up whichever "
                         "grains already cleared rounding). Pass this flag to disable the floor "
                         "and rely solely on the plain active-set equal-split, for comparison")
    p.add_argument('--idm-scaling', action='store_true',
                    help="Enable Carver-style IDM (Instrument Diversification Multiplier, "
                         "1/sqrt(W H W_t)) scaling of this rebalance's own effective budget, from "
                         "a bounded trailing-window EWM correlation matrix over that rebalance's "
                         "own signal-active symbols (default: off -- new, not yet validated the "
                         "way --no-active-set-redistribution/--no-cluster-floor's defaults are). "
                         "See run()'s own docstring and compute_idm's docstring for the full "
                         "derivation")
    p.add_argument('--idm-window-years', type=float, default=DEFAULT_IDM_WINDOW_YEARS,
                    help='Only used with --idm-scaling: bounded trailing window for the EWM '
                         'correlation estimate -- data older than this contributes exactly 0, '
                         'unlike an unbounded full-history EWM (default: %(default)s)')
    p.add_argument('--idm-halflife-days', type=float, default=DEFAULT_IDM_HALFLIFE_DAYS,
                    help='Only used with --idm-scaling: EWM halflife within the bounded window '
                         '(default: %(default)s -- matches this project\'s existing per-instrument '
                         'vol-estimation default, not independently tuned for correlation)')
    p.add_argument('--save-results', action='store_true',
                    help='Write per-run CSVs into results/ (created if missing), auto-tagged '
                         'with a shared datetime stamp for this invocation -- no prefix needed. '
                         '"{tag}_{mode}_daily.csv" (date, capital, ret), "{tag}_{mode}_'
                         'rebalances.csv" (one row per symbol actually rebalanced: cluster, '
                         'a_co/a_re, ts/continuous_regime/c_fast/c_slow, g_regime/g_fast/g_slow, '
                         'weight, close, std_fast/std_slow, prior->target, fee), for '
                         '--include-dynamic runs "{tag}_{mode}_yearly.csv" (mean a_co/a_re and '
                         'Bull/Bear/Correction/Rebound month counts per year, per cluster), '
                         '"{tag}_summary.csv" (one row per run: ann_ret/ann_vol/sharpe/max_dd/'
                         'fees -- always printed too, saving is optional), and, when both a '
                         'dynamic and at least one flat_discount run were executed, '
                         '"{tag}_comparison.csv" (metric-by-metric dynamic vs. continuous '
                         'deltas, also printed) (default: off, everything above is still '
                         'printed regardless -- this only controls whether it is ALSO saved)')
    return p.parse_args()


def main():
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    start_year, end_year = args.years.split('-')
    start, end = date(int(start_year), 1, 1), date(int(end_year), 12, 31)
    discounts = [float(d.strip()) for d in args.regime_discounts.split(',') if d.strip()]
    # One shared tag for every run() call in this invocation, so a
    # --regime-discounts sweep (and an --include-dynamic run alongside
    # it) land together under the same results/ filename prefix instead of
    # each call minting its own timestamp.
    results_tag = datetime.now().strftime('%Y%m%d_%H%M%S')
    # One row per run() call below -- the per-run summary dict (ann_ret/
    # ann_vol/sharpe/max_dd/fees) was previously only ever printed to
    # stdout and lost, unlike tsmom_backtest.py/the options param-search
    # scripts (bull_put_param_search.py/iron_condor_param_search.py),
    # which both write a "backtest_summary_..." CSV comparing every config
    # tested in one table. Matches that convention here.
    summary_rows = []

    for discount in discounts:
        result = run(symbols, start, end, discount,
                      initial_capital=args.initial_capital,
                      flat_per_asset_vol_target_usd=args.flat_vol_target,
                      target_portfolio_vol=args.target_portfolio_vol,
                      active_set_redistribution=not args.no_active_set_redistribution,
                      guarantee_cluster_representation=not args.no_cluster_floor,
                      idm_scaling=args.idm_scaling, idm_window_years=args.idm_window_years,
                      idm_halflife_days=args.idm_halflife_days,
                      save_results=args.save_results, results_tag=results_tag)
        print(result)
        summary_rows.append(result)

    if args.include_dynamic:
        result = run(symbols, start, end, regime_discount=1.0,
                      initial_capital=args.initial_capital,
                      flat_per_asset_vol_target_usd=args.flat_vol_target,
                      target_portfolio_vol=args.target_portfolio_vol,
                      weighting_mode='dynamic', mixing_pool=args.mixing_pool,
                      active_set_redistribution=not args.no_active_set_redistribution,
                      guarantee_cluster_representation=not args.no_cluster_floor,
                      idm_scaling=args.idm_scaling, idm_window_years=args.idm_window_years,
                      idm_halflife_days=args.idm_halflife_days,
                      save_results=args.save_results, results_tag=results_tag)
        print(result)
        summary_rows.append(result)

    # Printed as one unified table here (not just the individual per-run
    # dicts already printed above as each run finished) -- and, when both
    # a dynamic run and at least one flat_discount ("continuous") run were
    # actually executed, a second table comparing them metric-by-metric,
    # since eyeballing the deltas across separately-printed dicts is easy
    # to get wrong by hand.
    summary_df = pl.DataFrame(summary_rows).with_columns(
        run_label=pl.when(pl.col('mode') == 'dynamic')
                    .then(pl.lit('dynamic'))
                    .otherwise(pl.lit('flat_discount_') + pl.col('discount').cast(pl.Utf8))
    )
    print()
    print("=== Summary (all runs) ===")
    print(summary_df)

    comparison_df = None
    run_labels = summary_df['run_label'].to_list()
    flat_labels = [lbl for lbl in run_labels if lbl != 'dynamic']
    if 'dynamic' in run_labels and flat_labels:
        metrics = ['ann_ret_pct', 'ann_vol_pct', 'sharpe', 'max_dd_pct', 'total_fees']
        # Transposed (one row per metric, one column per run) rather than
        # the long format above -- reading a metric across a row is the
        # actual comparison; reading it down a column of the long table
        # isn't.
        comparison_df = summary_df.select(['run_label'] + metrics).transpose(
            include_header=True, header_name='metric', column_names='run_label'
        )
        for lbl in flat_labels:
            comparison_df = comparison_df.with_columns(
                (pl.col('dynamic') - pl.col(lbl)).round(4).alias(f'dynamic_minus_{lbl}')
            )
        print()
        print("=== dynamic vs. continuous (flat_discount) ===")
        print(comparison_df)

    if args.save_results:
        results_dir = Path(RESULTS_DIR)
        results_dir.mkdir(parents=True, exist_ok=True)
        summary_path = results_dir / f"{results_tag}_summary.csv"
        summary_df.with_columns(pl.col(pl.Float64).round(4)).write_csv(summary_path)
        logger.info(f"Saved {summary_path} ({len(summary_rows)} rows)")
        if comparison_df is not None:
            comparison_path = results_dir / f"{results_tag}_comparison.csv"
            comparison_df.write_csv(comparison_path)
            logger.info(f"Saved {comparison_path} ({comparison_df.height} rows)")


if __name__ == '__main__':
    main()
