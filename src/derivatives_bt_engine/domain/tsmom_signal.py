"""
Pure TSMOM (time-series momentum) signal computation — no IB dependency.

calculate_trend_strength() was the OLD (now retired) canonical, finalized signal function 
It expects a pre-fetched daily-bar Polars DataFrame (one
instrument) with at least a 'close' column, and is intentionally side-effect
free so it can be unit tested without an IB connection.

Horizon choice (do not revisit):
  - 3-month (63 bars) and 12-month (252 bars) only.
  - 6-month is excluded: sits in the dead zone between the autocorrelation
    and drift regimes, and is highly correlated with the 12-month signal —
    low diversification value for the extra column.
  - 1-month is excluded for monthly rebalancing: a single rebalance-period
    observation is too noisy to trust; 3-month is the minimum reliable fast
    signal at this cadence.

Naming convention (applies wherever these columns are consumed, e.g.
Backtester.calculate_futures_mtm_drawdown's hv_fast/sharpe_fast): "fast"/
"slow" denote the rolling estimation window (fast_window/slow_window,
whatever those are configured to), not a fixed reporting horizon -- renamed
from the original ts3m/ts1y/r3m/r1y/avg3m/avg1y/avg_r3m/avg_r1y naming
(2026-07) once fast_window/slow_window became genuinely configurable
parameters rather than fixed constants: a column still called "ts3m" when
fast_window=21 would be actively misleading about what it contains. Every
call site and test was updated to match; see git history for the mechanical
rename. Volatility, Sharpe, and avg_r_fast/avg_r_slow all remain
annualized -- e.g. hv_fast is annualized vol estimated from the last
fast_window days, not a "3-month vol" figure; avg_r_fast is the annualized
mean daily return estimated from the last fast_window days; sharpe_fast is
annualized Sharpe estimated over the same window.

annualization_days vs. fast_window/slow_window (2026-07, added once this
project confirmed real trading-days/year differs by cluster -- CBOT grains
at 252, the rest of this project's universe at 259, post Sunday-session-
merge fix -- see research/research_futures_roll_logic_and_active_months.md):
these are two independent knobs, not one derived from the other.
fast_window/slow_window (default 63/252, unchanged -- see "Horizon choice"
above, still not being revisited) govern how many rows of history
fast_return/slow_return/avg_fast/avg_slow/ts_fast/ts_slow's own internal
same-horizon vol-scaling look back over; annualization_days governs only
the genuinely per-calendar-year terms (hv, avg_r_fast, avg_r_slow, and
compute_position_scalar's current_realized_vol). ts_fast/ts_slow's own
sqrt(fast_window)/sqrt(slow_window) terms are NOT annualization -- they
rescale the fast/current daily_std estimate up to the SAME horizon as
fast_return/slow_return's own cumulative return, so they must track
fast_window/slow_window, never annualization_days, even when the two
window lengths still numerically coincide with a 252-day/year convention
by historical accident.
"""

import logging
import math

import polars as pl

from derivatives_bt_engine.domain.enums import SignalConfidenceRegime, TrendRegime

log = logging.getLogger(__name__)

# ── Tunable defaults ─────────────────────────────────────────────────────
# Fallback trading-days/year for callers that don't pass their own
# instrument-specific figure (see instruments.resolve_annualization_days) --
# kept as a plain local default (not imported from instruments.py) so this
# module stays usable standalone, per its own "no IB dependency" framing.
DEFAULT_ANNUALIZATION_DAYS = 252
# ts_fast/ts_slow's window lengths, in trading days -- "do not revisit" per the
# Horizon choice note above; exposed as parameters (not hardcoded) purely so
# a caller can experiment/compare, not because either default is expected to
# change.
DEFAULT_FAST_WINDOW = 63
DEFAULT_SLOW_WINDOW = 252


# def calculate_trend_strength(df: pl.DataFrame, w3m: float = 0.4, w1y: float = 0.6) -> pl.DataFrame:
#     """Canonical TSMOM signal. tanh (not sigmoid) so the sign is preserved —
#     this is what drives long vs. short, not just conviction magnitude."""
    # df = df.with_columns(
    #     log_price=pl.col('close').log(),
    #     peak=pl.col('close').cum_max(),
    # )
    # df = df.with_columns(
    #     dd=((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
    #     r1d=pl.col('log_price').diff(1),
    #     r3m=pl.col('log_price').diff(63),
    #     r1y=pl.col('log_price').diff(252),
    # )
    # df = df.with_columns(
    #     daily_std=pl.col('r1d').rolling_std(63),
    #     # Rolling mean of daily returns (not price -- avg3m/avg1y used to
    #     # be a rolling mean of close, but that's not comparable to r3m/r1y
    #     # or daily_std, which are both return-based), over the same 3m/1y
    #     # windows as ts3m/ts1y, annualized (*252, linear -- means scale
    #     # with time, unlike std which scales with sqrt(time)) so the
    #     # values are readable instead of tiny daily-return fractions.
    #     avg_r3m=pl.col('r1d').rolling_mean(63) * 252,
    #     avg_r1y=pl.col('r1d').rolling_mean(252) * 252,
    # )
    # df = df.with_columns(
    #     ts3m=pl.col('r3m') / (pl.col('daily_std') * math.sqrt(63)),
    #     ts1y=pl.col('r1y') / (pl.col('daily_std') * math.sqrt(252)),
    # )
    # df = df.with_columns(
    #     w3=pl.col('ts3m').is_not_null().cast(pl.Float64) * w3m,
    #     w1=pl.col('ts1y').is_not_null().cast(pl.Float64) * w1y,
    # )
    # df = df.with_columns(
    #     signal=(
    #         pl.when(pl.col('ts3m').is_not_null())
    #         .then(
    #             ((
    #                 pl.col('w3') * pl.col('ts3m').fill_null(0) +
    #                 pl.col('w1') * pl.col('ts1y').fill_null(0)
    #             ) / (pl.col('w3') + pl.col('w1')).clip(lower_bound=1e-12)).tanh()
    #         )
    #         .otherwise(None)
    #     ),
    #     r1y_pct=(100 * (pl.col('r1y').exp() - 1)).round(2),
    # )
    # # daily_std is kept (the notebook's original drop list removes it) —
    # # the position-sizing layer needs daily_std_last from the last row to
    # # compute current_realized_vol for vol targeting.
    # df = df.drop(['open', 'high', 'low', 
    #               'barCount', 'volume', 'average', 'w3', 'w1'],
    #              strict=False)
    # return df
# import math

def calculate_trend_strength(contract, w3m=0.4, w1y=0.6, discount=0.5,
                              annualization_days=DEFAULT_ANNUALIZATION_DAYS,
                              fast_window=DEFAULT_FAST_WINDOW, slow_window=DEFAULT_SLOW_WINDOW):
    """See this module's own docstring for the annualization_days vs.
    fast_window/slow_window distinction -- annualization_days scales only
    the genuinely per-calendar-year terms (hv, avg_r_fast, avg_r_slow);
    fast_window/slow_window govern fast_return/slow_return/avg_fast/avg_slow/
    daily_std's rolling windows AND ts_fast/ts_slow's own same-horizon
    vol-scaling, independently of annualization_days. All three default to
    this project's long-standing values (252, 63, 252) -- passing nothing
    reproduces prior behavior exactly.

    Column names (fast/slow, not the old fixed-horizon 3m/1y labels) reflect
    fast_window/slow_window being genuinely configurable -- a caller passing
    fast_window=21 gets a column named ts_fast, not a column still called
    ts3m that's secretly a 21-day figure. Renamed from ts3m/ts1y/r3m/r1y/
    avg3m/avg1y/avg_r3m/avg_r1y (every call site and test updated to match;
    see git history for the mechanical rename)."""
    # df = ib.cont_future(contract)
    # df = ib.get_historical_bars(contract, duration=duration)
    df = contract
    df = df.with_columns(
        log_price = pl.col('close').log(),
        peak      = pl.col('close').cum_max(),
    )
    df = df.with_columns(
        dd        = ((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
        r1d       = pl.col('log_price').diff(1),
        avg_fast  = pl.col('close').rolling_mean(fast_window).round(2),
        avg_slow  = pl.col('close').rolling_mean(slow_window).round(2),

        fast_return = pl.col('log_price').diff(fast_window),
        slow_return = pl.col('log_price').diff(slow_window),
    )

    df = df.with_columns(
        avg_r_fast = (pl.col('r1d').rolling_mean(fast_window) * annualization_days).round(2),
        avg_r_slow = (pl.col('r1d').rolling_mean(slow_window) * annualization_days).round(2),

        daily_std = pl.col('r1d').rolling_std(fast_window)
    )

    df = df.with_columns(
        hv = pl.col('daily_std') * annualization_days ** 0.5,
        ts_fast = pl.col('fast_return') / (pl.col('daily_std') * math.sqrt(fast_window)),
        ts_slow = pl.col('slow_return') / (pl.col('daily_std') * math.sqrt(slow_window)),
    )

    df = df.with_columns(
        w3 = pl.col('ts_fast').is_not_null().cast(pl.Float64) * w3m,
        w1 = pl.col('ts_slow').is_not_null().cast(pl.Float64) * w1y,
    )

    df = df.with_columns(
        ts = (
            pl.when(pl.col('ts_slow').is_not_null())
            .then(
                ((
                    pl.col('w3') * pl.col('ts_fast').fill_null(0) +
                    pl.col('w1') * pl.col('ts_slow').fill_null(0)
                ) / (pl.col('w3') + pl.col('w1')).clip(lower_bound=1e-12)).tanh()
            )
            .otherwise(None)
        ),
        # slow_return_pct = (100 * (pl.col('slow_return').exp() - 1)).round(2),
        regime = (
            pl.when((pl.col('ts_fast') < 0) & (pl.col('ts_slow') < 0))
            .then(pl.lit('bear'))
            .when((pl.col('ts_fast') >= 0) & (pl.col('ts_slow') >= 0))
            .then(pl.lit('bull'))
            .when((pl.col('ts_fast') < 0) & (pl.col('ts_slow') >= 0))
            .then(pl.lit('correction'))
            .when((pl.col('ts_fast') >= 0) & (pl.col('ts_slow') < 0))
            .then(pl.lit('rebound'))
            # .otherwise(pl.lit('unknown'))
        ),
        mom = (pl.col('ts_fast') - pl.col('ts_slow')).tanh().round(2)

    )
    df = df.with_columns(
            signal = (
                pl.when(pl.col('regime').is_in(['correction', 'rebound']))
                .then(pl.col('ts') * discount)
                .otherwise(pl.col('ts'))
                )
        )


    df = df.drop(['open', 'high', 'low', 'log_price',
                   'volume', 'average', 'w3', 'w1'], strict=False)

    # Round only the bounded/display-scale columns (tanh scores, price
    # averages, drawdown pct) to 2dp. r1d/daily_std/fast_return/slow_return/
    # hv are return-scale (typically well under 0.01 for a quiet instrument
    # -- a rates future like MTN, a quiet FX pair like BRE) and MUST stay
    # full precision: a blanket round(2) here used to floor them to exactly
    # 0.0, which propagated downstream into a silently-zeroed hv_fast
    # (Backtester.calculate_futures_mtm_drawdown) and, worse, into
    # tsmom_backtester._compute_target/live.tsmom_rebalance._compute_signal
    # treating `daily_std_last == 0.0` as falsy and silently defaulting
    # risk_scalar to 1.0 (vol-targeting disabled) instead of the
    # up-scaled size a genuinely low-vol instrument should get.
    _DISPLAY_SCALE_COLS = ['dd', 'avg_fast', 'avg_slow', 'ts_fast', 'ts_slow', 'ts', 'mom', 'signal']
    df = df.with_columns([pl.col(c).round(2) for c in _DISPLAY_SCALE_COLS if c in df.columns])
    return df


def classify_regime(fast, slow) -> TrendRegime:
    """
    Classify into Bull/Correction/Bear/Rebound from the sign of the fast
    (~3mo, or whatever fast_window/fast_months a caller configured) and slow
    (~12mo) trend-strength scores. Args renamed from ts3m/ts1y -- positional
    only everywhere in this codebase (grep-confirmed no keyword-arg callers),
    so this rename doesn't break any call site.

        slow  fast  state        meaning
         +     +    Bull         strong trend, high-confidence long
         +     -    Correction   short-term dip in uptrend (61% revert to Bull)
         -     -    Bear         strong downtrend, high-confidence short/flat
         -     +    Rebound      short-term recovery in downtrend (55% up next)

    Exactly zero, None, or NaN on either input is ambiguous -> Unknown.
    """
    if fast is None or slow is None:
        return TrendRegime.UNKNOWN
    if (isinstance(fast, float) and math.isnan(fast)) or (isinstance(slow, float) and math.isnan(slow)):
        return TrendRegime.UNKNOWN
    if fast == 0 or slow == 0:
        return TrendRegime.UNKNOWN

    slow_up = slow > 0
    fast_up = fast > 0

    if slow_up and fast_up:
        return TrendRegime.BULL
    if slow_up and not fast_up:
        return TrendRegime.CORRECTION
    if not slow_up and not fast_up:
        return TrendRegime.BEAR
    return TrendRegime.REBOUND   # not slow_up and fast_up


def compute_vol_ratio(df: pl.DataFrame, short_window: int = 21, long_window: int = 252) -> pl.DataFrame:
    """
    Per-instrument, asset-specific vol-regime ratio: this instrument's own
    short-window realized vol / long-window realized vol of its daily log
    returns (short_window ~= 1 trading month, long_window ~= 1 trading
    year). NOT VIX/VX-driven -- this is what catches an instrument-
    specific vol spike (a corn-harvest shock, a JPY intervention) with
    broad-market VX/VIX staying calm, since VX/VIX only reflects S&P-
    linked vol and has no visibility into corn's or JPY's own vol at all.

    Deliberately a separate function from calculate_trend_strength (which
    stays canonical/finalized, not to be touched) rather than adding
    columns there -- callers chain this on demand instead of paying for it
    on every signal computation. Annualization factors cancel in the
    ratio, so this works directly off raw rolling std of daily log returns.

    Expects a DataFrame that already has an 'r1d' column (daily log-return
    diff) -- i.e. chain this onto calculate_trend_strength's output, which
    retains 'r1d'/'log_price' for exactly this purpose, rather than on raw
    bars directly. Returns the same frame plus 'hv_short', 'hv_long',
    'vol_ratio' columns (vol_ratio is None/null wherever hv_long isn't yet
    defined or is zero), with 'log_price'/'r1d' dropped again afterward.
    """
    df = df.with_columns(
        hv_short=pl.col('r1d').rolling_std(short_window),
        hv_long=pl.col('r1d').rolling_std(long_window),
    )
    df = df.with_columns(
        vol_ratio=pl.when(pl.col('hv_long') > 0)
        .then(pl.col('hv_short') / pl.col('hv_long'))
        .otherwise(None)
    )
    return df.drop(['log_price', 'r1d'], strict=False)


def classify_signal_confidence(vol_ratio, low_threshold: float, high_threshold: float) -> SignalConfidenceRegime:
    """
    Low | Normal | High from vol_ratio (hv_short/hv_long, see
    compute_vol_ratio) against configurable thresholds -- deliberately not
    hardcoded, since the right threshold is asset- and regime-dependent
    and there's no settled, universal value.

    None/NaN (insufficient history) -> Normal, i.e. no discount -- a
    missing-data gap shouldn't read as "unusual," just as "unknown."
    """
    if vol_ratio is None or (isinstance(vol_ratio, float) and math.isnan(vol_ratio)):
        log.warning("Signal confidence couldn't be computed because of NaN component/s in vol_ratio")
        return SignalConfidenceRegime.NORMAL
    if vol_ratio >= high_threshold:
        return SignalConfidenceRegime.HIGH
    if vol_ratio <= low_threshold:
        return SignalConfidenceRegime.LOW
    return SignalConfidenceRegime.NORMAL


def compute_signal_confidence(vol_ratio, low_threshold: float, high_threshold: float,
                               high_vol_discount: float = 0.5, low_vol_discount: float = 1.0) -> float:
    """
    Per-instrument discount on trust in THIS instrument's trend signal,
    triggered when its own vol_ratio is unusual relative to its own
    history -- distinct from momentum_discount (fast/slow sign
    disagreement) and from market_stress_scale (portfolio-wide, VX-
    driven; applied by the caller, not in here).

    high_vol_discount and low_vol_discount are independent, free
    parameters -- deliberately NOT assumed symmetric. The literature
    reviewed in cta-vol-scalar-clamping.md treats high-vol momentum
    unreliability and low-vol mean-variance leverage opportunities
    (Bongaerts et al.'s low-vol response is to increase exposure for
    alpha reasons specific to equity factor timing, not to discount trend
    confidence) as different phenomena for different reasons -- there is
    no settled answer for whether low vol should discount this system's
    trend signal at all, hence low_vol_discount's no-op default of 1.0,
    vs high_vol_discount's suggested 0.5 (vol spikes specifically damage
    momentum reliability, per the Mozes-article finding already
    established in this project's research).
    """
    regime = classify_signal_confidence(vol_ratio, low_threshold, high_threshold)
    if regime == SignalConfidenceRegime.HIGH:
        return high_vol_discount
    if regime == SignalConfidenceRegime.LOW:
        return low_vol_discount
    return 1.0


def compute_position_scalar(trend_strength, daily_std_last, vol_target: float,
                             regime: TrendRegime, momentum_discount: float = 0.5,
                             signal_confidence: float = 1.0,
                             annualization_days=DEFAULT_ANNUALIZATION_DAYS) -> float:
    """
    Layers 2-4 of the position sizing framework (plus the opt-in layer 5,
    signal_confidence), combined into a single scalar in [-1, +1]:

        scalar = trend_strength * risk_scalar * momentum_discount * signal_confidence

    Long-only filtering (signal_scalar = max(0, trend_strength)) is the
    caller's responsibility — pass the already-filtered trend_strength in
    for long-only accounts. This function stays pure w.r.t. direction.

    risk_scalar = vol_target / current_realized_vol, clamped to [0.25, 2.0]
    -- a risk-equalization ratio driven by THIS instrument's own realized
    vol, nothing regime- or market-wide about it.
    current_realized_vol = daily_std_last * sqrt(annualization_days) <= ** 63-day rolling **
    Callers should pass the SAME annualization_days used to compute
    daily_std_last in the first place (instruments.resolve_annualization_days)
    -- this project's confirmed universe splits 252 (CBOT grains) vs. 259
    (everything else checked); the plain 252 default here reproduces prior
    behavior exactly for anyone not passing an instrument-specific value.

    momentum_discount is applied only for Correction/Rebound (disagreement
    between the fast and slow momentum signal — lower conviction);
    Bull/Bear/Unknown get a discount factor of 1.0. Despite the similar
    "discount" shape, this is unrelated to market_stress_scale (the
    portfolio-wide, VX-driven de-risking lever applied by the caller in
    derivatives_bt_engine.live.tsmom_rebalance, not in here) -- the two were
    conflated in earlier design review, hence the explicit naming.

    signal_confidence (default 1.0, no-op) is a separate, opt-in, per-
    instrument discount on trust in THIS instrument's signal when its own
    vol_ratio (short-window/long-window realized vol, asset-specific, NOT
    VIX/VX-driven) is unusual relative to its own history -- see
    compute_signal_confidence(). Orthogonal to momentum_discount (which is
    about fast/slow sign disagreement, not vol) and to market_stress_scale
    (which is portfolio-wide, not per-instrument).
    """
    if trend_strength is None or (isinstance(trend_strength, float) and math.isnan(trend_strength)):
        return 0.0

    if daily_std_last is None or (isinstance(daily_std_last, float) and math.isnan(daily_std_last)) or daily_std_last <= 0:
        risk_scalar = 1.0   # insufficient history to size by vol — neutral
    else:
        current_realized_vol = daily_std_last * math.sqrt(annualization_days) # 63 day not last month
        risk_scalar = vol_target / current_realized_vol # 0.15/0.60 ~= 0.25, this is not hv_long here, but a param
        risk_scalar = max(0.25, min(2.0, risk_scalar))

    momentum_discount = momentum_discount if regime in (TrendRegime.CORRECTION, TrendRegime.REBOUND) else 1.0

    scalar = trend_strength * risk_scalar * momentum_discount * signal_confidence
    return max(-1.0, min(1.0, scalar))


def compute_n_effective(active_clusters: set) -> int:
    """Count of clusters carrying a live signal -- the portfolio's
    effective number of independent bets, not its raw instrument count
    (e.g. 4 grain micros moving on one shared ag-complex factor are one
    bet, not four)."""
    return len(active_clusters)


def compute_desired_risk_budget(account_equity: float, target_portfolio_vol: float,
                                 n_effective: int) -> float:
    """Per-cluster dollar risk budget, sqrt(N)-scaled so total portfolio
    risk (assuming roughly independent clusters) targets
    account_equity * target_portfolio_vol regardless of how many clusters
    are currently active."""
    if n_effective <= 0:
        return 0.0
    return account_equity * target_portfolio_vol / math.sqrt(n_effective)


def apply_cluster_risk_cap(targets: list[dict], max_cluster_risk_pct: float,
                            total_risk_target: float, n_active_clusters: int,
                            max_lot_overrun_pct: float = 0.5) -> list[dict]:
    """
    Second pass over an already-sized targets list: for any cluster whose
    aggregate dollar-vol risk exceeds its share of a fixed, account-equity-
    derived risk target, allocates that cluster's capped budget by
    conviction priority rather than a uniform haircut, so e.g. 4 grain
    micros that are each individually sized correctly don't collectively
    become one oversized bet on the ag-complex factor they all share.

    The cap is taken against total_risk_target (account_equity *
    target_portfolio_vol, computed by the caller) -- a fixed number, NOT
    the emergent sum of this run's pre-cap position risks. effective_cap_pct
    = max(max_cluster_risk_pct, 1/n_active_clusters) -- the 1/n floor means
    a single active cluster isn't capped below 100% of the target just for
    being the only trade in the book.

    Allocation within an over-budget cluster (replaces a uniform scale
    factor, which produces an all-or-nothing failure mode: scaling every
    instrument down by the same factor can push ALL of them below the 0.5
    rounding threshold at once, even when the top-conviction instrument
    alone would easily survive on the full cap):

      1. Sort the cluster's instruments by priority = abs(scalar),
         descending -- scalar (not raw signal) since it already folds in
         vol-targeting, regime discount, and the long-only filter.
      2. Walk the sorted list with remaining_budget starting at the cap.
         For each instrument: affordable_continuous = remaining_budget /
         single_contract_risk (0 if remaining_budget <= 0); usable =
         min(abs(original_continuous), affordable_continuous); contracts =
         round(usable) if usable >= 0.5 else 0.
      3. Lot-size exception, first instrument only (remaining_budget still
         == cap, i.e. nothing spent yet): if usable < 0.5 (would round to
         zero) but the instrument's own signal genuinely wants a full
         contract (abs(original_continuous) >= 0.5) and its single-
         contract risk isn't wildly over the cap (<= cap * (1 +
         max_lot_overrun_pct)), it still gets exactly 1 contract -- a
         clearly-wanted top-conviction position shouldn't be sacrificed
         purely to discrete contract granularity. remaining_budget can go
         negative after this fires, which is intentional: it guarantees
         every later instrument in the walk gets 0.
      4. remaining_budget -= contracts * single_contract_risk; continue
         down the sorted list.

    Clusters within budget (risk <= cap) are untouched. Mixed-sign
    clusters work without special-casing -- risk is abs()-based, priority
    is abs(scalar), direction is sign(original_continuous).

    infeasible is an OUTCOME-based flag, computed after every cluster's
    walk-down (or no-op) is final: True for every instrument in a cluster
    where ALL instruments end up at target_contracts == 0 despite at least
    one having abs(continuous_contracts) >= 0.5 pre-cap -- i.e. a real
    signal existed but the cluster genuinely captured no exposure. This is
    NOT decided by a cap-vs-single-contract-risk precomputation (the top-
    priority instrument may still land a contract via the lot exception
    even when that precomputed check would have said "infeasible") --
    only the actual result matters.

    Each target dict must carry 'cluster', 'continuous_contracts', 'scalar',
    'close', 'multiplier', 'hv' (already computed per-instrument by the
    caller). Targets with an 'error' key, or missing one of those fields,
    are left untouched and excluded from the risk totals. Mutates and
    returns the same list (adds/overwrites 'target_contracts' and
    'position_risk', and 'infeasible' where applicable).
    """
    valid = [
        t for t in targets
        if not t.get('error')
        and t.get('continuous_contracts') is not None
        and t.get('cluster') is not None
        and t.get('close') is not None
        and t.get('multiplier') is not None
    ]

    cluster_risk: dict[str, float] = {}
    for t in valid:
        hv = t.get('hv') or 0.0
        position_risk = abs(t['continuous_contracts']) * t['close'] * t['multiplier'] * hv
        cluster_risk[t['cluster']] = cluster_risk.get(t['cluster'], 0.0) + position_risk

    if total_risk_target is None or total_risk_target <= 0:
        return targets

    effective_cap_pct = max(max_cluster_risk_pct, 1.0 / n_active_clusters) if n_active_clusters > 0 else max_cluster_risk_pct
    cap = effective_cap_pct * total_risk_target

    over_budget_clusters = {cluster for cluster, risk in cluster_risk.items() if risk > cap}

    for cluster in over_budget_clusters:
        members = [t for t in valid if t['cluster'] == cluster]
        # Priority order is fixed before any mutation -- read each
        # instrument's original, unscaled continuous_contracts once, up
        # front, so later iterations of this loop can't see a value
        # another instrument's walk step already changed.
        members_sorted = sorted(members, key=lambda t: abs(t.get('scalar') or 0.0), reverse=True)
        original_continuous = {t['symbol']: t['continuous_contracts'] for t in members_sorted}

        remaining_budget = cap
        for i, t in enumerate(members_sorted):
            is_first = (i == 0)
            orig = original_continuous[t['symbol']]
            single_contract_risk = t['close'] * t['multiplier'] * (t.get('hv') or 0.0)

            if remaining_budget <= 0:
                contracts = 0
            else:
                affordable_continuous = remaining_budget / single_contract_risk if single_contract_risk else 0.0
                usable_continuous = min(abs(orig), affordable_continuous)

                if (is_first and usable_continuous < 0.5 and abs(orig) >= 0.5
                        and single_contract_risk <= cap * (1 + max_lot_overrun_pct)):
                    contracts = 1
                else:
                    contracts = round(usable_continuous) if usable_continuous >= 0.5 else 0

            sign = 1 if orig > 0 else (-1 if orig < 0 else 0)
            t['target_contracts'] = sign * contracts
            remaining_budget -= contracts * single_contract_risk

    # Clusters within budget (and any target not part of an over-budget
    # cluster) round their continuous value directly, once -- equivalent
    # to a no-op scale of 1.0 through the same single-rounding rule the
    # walk-down above uses.
    for t in valid:
        if t['cluster'] in over_budget_clusters:
            continue
        scaled = t['continuous_contracts']
        sign = 1 if scaled > 0 else (-1 if scaled < 0 else 0)
        magnitude = 0 if abs(scaled) < 0.5 else round(abs(scaled))
        t['target_contracts'] = sign * magnitude

    # max_contracts clamp is the true last step, after sizing is otherwise
    # final, then position_risk is recomputed from that final value.
    for t in valid:
        max_contracts = t.get('max_contracts')
        if max_contracts is not None:
            t['target_contracts'] = max(-max_contracts, min(max_contracts, t['target_contracts']))
        t['position_risk'] = abs(t['target_contracts']) * t['close'] * t['multiplier'] * (t.get('hv') or 0.0)

    # Outcome-based infeasibility: every instrument in the cluster ended up
    # at zero despite at least one having a genuine (>=0.5) pre-cap signal.
    for cluster in over_budget_clusters:
        members = [t for t in valid if t['cluster'] == cluster]
        had_real_signal = any(abs(t['continuous_contracts']) >= 0.5 for t in members)
        all_zero = all(t['target_contracts'] == 0 for t in members)
        if had_real_signal and all_zero:
            log.warning(
                "%s cluster captured zero exposure despite a live signal -- cap ($%.0f) "
                "could not accommodate any instrument in this cluster even with conviction-"
                "priority allocation; consider raising max_cluster_risk_pct, reducing "
                "n_effective's denominator effect, or excluding large-multiplier "
                "instruments from this account",
                cluster, cap,
            )
            for t in members:
                t['infeasible'] = True

    return targets
