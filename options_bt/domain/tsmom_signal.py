"""
Pure TSMOM (time-series momentum) signal computation — no IB dependency.

calculate_trend_strength() is the canonical, finalized signal function — do
not redesign it. It expects a pre-fetched daily-bar Polars DataFrame (one
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
"""

import math

import polars as pl

from options_bt.domain.enums import TrendRegime


def calculate_trend_strength(df: pl.DataFrame, w3m: float = 0.4, w1y: float = 0.6) -> pl.DataFrame:
    """Canonical TSMOM signal. tanh (not sigmoid) so the sign is preserved —
    this is what drives long vs. short, not just conviction magnitude."""
    df = df.with_columns(
        log_price=pl.col('close').log(),
        peak=pl.col('close').cum_max(),
    )
    df = df.with_columns(
        dd=((pl.col('close') - pl.col('peak')) / pl.col('peak')).round(2),
        r1d=pl.col('log_price').diff(1),
        avg3m=pl.col('close').rolling_mean(63).round(2),
        avg1y=pl.col('close').rolling_mean(252).round(2),
        r3m=pl.col('log_price').diff(63),
        r1y=pl.col('log_price').diff(252),
    )
    df = df.with_columns(
        daily_std=pl.col('r1d').rolling_std(63)
    )
    df = df.with_columns(
        ts3m=pl.col('r3m') / (pl.col('daily_std') * math.sqrt(63)),
        ts1y=pl.col('r1y') / (pl.col('daily_std') * math.sqrt(252)),
    )
    df = df.with_columns(
        w3=pl.col('ts3m').is_not_null().cast(pl.Float64) * w3m,
        w1=pl.col('ts1y').is_not_null().cast(pl.Float64) * w1y,
    )
    df = df.with_columns(
        trend_strength=(
            pl.when(pl.col('ts3m').is_not_null())
            .then(
                ((
                    pl.col('w3') * pl.col('ts3m').fill_null(0) +
                    pl.col('w1') * pl.col('ts1y').fill_null(0)
                ) / (pl.col('w3') + pl.col('w1')).clip(lower_bound=1e-12)).tanh()
            )
            .otherwise(None)
        ),
        r1y_pct=(100 * (pl.col('r1y').exp() - 1)).round(2),
    )
    # daily_std is kept (the notebook's original drop list removes it) —
    # the position-sizing layer needs daily_std_last from the last row to
    # compute current_realized_vol for vol targeting.
    df = df.drop(['open', 'high', 'low', 'r1d', 'log_price',
                  'barCount', 'volume', 'average', 'w3', 'w1'],
                 strict=False)
    return df


def classify_regime(ts3m, ts1y) -> TrendRegime:
    """
    Classify into Bull/Correction/Bear/Rebound from the sign of the fast
    (~3mo) and slow (~12mo) trend-strength scores.

        ts1y  ts3m  state        meaning
         +     +    Bull         strong trend, high-confidence long
         +     -    Correction   short-term dip in uptrend (61% revert to Bull)
         -     -    Bear         strong downtrend, high-confidence short/flat
         -     +    Rebound      short-term recovery in downtrend (55% up next)

    Exactly zero, None, or NaN on either input is ambiguous -> Unknown.
    """
    if ts3m is None or ts1y is None:
        return TrendRegime.UNKNOWN
    if (isinstance(ts3m, float) and math.isnan(ts3m)) or (isinstance(ts1y, float) and math.isnan(ts1y)):
        return TrendRegime.UNKNOWN
    if ts3m == 0 or ts1y == 0:
        return TrendRegime.UNKNOWN

    slow_up = ts1y > 0
    fast_up = ts3m > 0

    if slow_up and fast_up:
        return TrendRegime.BULL
    if slow_up and not fast_up:
        return TrendRegime.CORRECTION
    if not slow_up and not fast_up:
        return TrendRegime.BEAR
    return TrendRegime.REBOUND   # not slow_up and fast_up


def compute_position_scalar(trend_strength, daily_std_last, vol_target: float,
                             regime: TrendRegime, regime_discount: float = 0.5) -> float:
    """
    Layers 2-4 of the position sizing framework, combined into a single
    scalar in [-1, +1]:

        scalar = trend_strength * vol_scalar * regime_discount_factor

    Long-only filtering (signal_scalar = max(0, trend_strength)) is the
    caller's responsibility — pass the already-filtered trend_strength in
    for long-only accounts. This function stays pure w.r.t. direction.

    vol_scalar = vol_target / current_realized_vol, clamped to [0.25, 2.0].
    current_realized_vol = daily_std_last * sqrt(252).

    regime_discount is applied only for Correction/Rebound (disagreement
    between the fast and slow signal — lower conviction); Bull/Bear/Unknown
    get a discount factor of 1.0.
    """
    if trend_strength is None or (isinstance(trend_strength, float) and math.isnan(trend_strength)):
        return 0.0

    if daily_std_last is None or (isinstance(daily_std_last, float) and math.isnan(daily_std_last)) or daily_std_last <= 0:
        vol_scalar = 1.0   # insufficient history to size by vol — neutral
    else:
        current_realized_vol = daily_std_last * math.sqrt(252)  
        vol_scalar = vol_target / current_realized_vol # 0.15/0.60 ~= 0.25
        vol_scalar = max(0.25, min(2.0, vol_scalar))

    discount = regime_discount if regime in (TrendRegime.CORRECTION, TrendRegime.REBOUND) else 1.0

    scalar = trend_strength * vol_scalar * discount
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


def apply_cluster_risk_cap(targets: list[dict], max_cluster_risk_pct: float = 0.25) -> list[dict]:
    """
    Second pass over an already-sized targets list: rescales any cluster
    whose aggregate dollar-vol risk exceeds max_cluster_risk_pct of total
    portfolio risk, so e.g. 4 grain micros that are each individually
    sized correctly don't collectively become one oversized bet on the
    ag-complex factor they all share.

    Each target dict must carry 'cluster', 'target_contracts', 'close',
    'multiplier', 'hv' (already computed per-instrument by the caller).
    Targets with an 'error' key, or missing one of those fields, are left
    untouched and excluded from the risk totals. Mutates and returns the
    same list (adds a 'position_risk' field to the rescaled entries).
    """
    valid = [
        t for t in targets
        if not t.get('error')
        and t.get('target_contracts') is not None
        and t.get('cluster') is not None
        and t.get('close') is not None
        and t.get('multiplier') is not None
    ]

    cluster_risk: dict[str, float] = {}
    for t in valid:
        hv = t.get('hv') or 0.0
        position_risk = abs(t['target_contracts']) * t['close'] * t['multiplier'] * hv
        t['position_risk'] = position_risk
        cluster_risk[t['cluster']] = cluster_risk.get(t['cluster'], 0.0) + position_risk

    total_risk = sum(cluster_risk.values())
    if total_risk <= 0:
        return targets

    cap = max_cluster_risk_pct * total_risk
    for cluster, risk in cluster_risk.items():
        if risk <= cap:
            continue
        scale = cap / risk
        for t in valid:
            if t['cluster'] != cluster:
                continue
            scaled = t['target_contracts'] * scale
            sign = 1 if scaled > 0 else (-1 if scaled < 0 else 0)
            magnitude = 0 if abs(scaled) < 0.5 else round(abs(scaled))
            t['target_contracts'] = sign * magnitude
            # Recompute now that target_contracts changed -- otherwise
            # position_risk (and any downstream risk-share check) reflects
            # the pre-cap size, defeating the point of the cap.
            t['position_risk'] = abs(t['target_contracts']) * t['close'] * t['multiplier'] * (t.get('hv') or 0.0)

    return targets
