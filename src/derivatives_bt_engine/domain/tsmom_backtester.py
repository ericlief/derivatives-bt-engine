"""
Multi-symbol TSMOM backtest: monthly rebalance to a vol-targeted contract
count per symbol, with a spot-VIX regime gate, in a simple form.

Deliberately separate from Backtester/TradeManager/FuturesPosition: those
are built around discrete "open position, hold until roll/expiry, then
close" trades, shared with the still-pandas option backtest path. TSMOM's
lifecycle (continuously-sized monthly rebalance toward a target contract
count, no roll/expiry-driven open-close cycle) doesn't fit that model, and
retrofitting it would risk regressing the shared option path. This module
reuses the existing pure signal math (signal.py) and FuturesDataLoader
but implements its own portfolio loop.

No VX futures (CFE) history is available locally (the Globex MDP3.0 duckdb
is CME-only) -- the regime gate below uses spot VIX vs its own trailing
63-day MA as the closest available analog to the live system's VX
front-month / VX-63d-MA ratio (see derivatives_bt_engine.live.tsmom_rebalance).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import duckdb
import polars as pl

from derivatives_bt_engine.domain.allocation import (
    ALLOCATION_MODES,
    NOTIONAL_WEIGHTING_SCHEMES,
    apply_cluster_risk_cap,
    build_returns_wide,
    compute_realized_portfolio_risk,
    compute_position_scalar,
    compute_symbol_notional_budget,
)
from derivatives_bt_engine.domain.enums import VolRegime
from derivatives_bt_engine.domain.futures_dataloader import (
    FuturesDataLoader,
    assert_monotonic_expiration,
    globex_daily_cache_path,
)
from derivatives_bt_engine.domain.futures_history import (
    DEFAULT_FUTURES_CACHE_ROOT,
    DEFAULT_GLOBEX_DB_PATH,
    DEFAULT_PYSYSTEMTRADE_DB_PATH,
    HISTORY_SCHEMA_VERSION,
    PysystemtradeHistoryProvider,
)
from derivatives_bt_engine.domain.instruments import (
    CME_MONTH_NUM_TO_LETTER, get_spec, resolve_active_months, resolve_annualization_days, resolve_price_symbol,
)
from derivatives_bt_engine.domain.signal import (
    DEFAULT_FAST_WINDOW,
    DEFAULT_SLOW_WINDOW,
    EWMAC_FORECAST_CAP,
    EWMAC_FORECAST_TARGET_ABS,
    EWMAC_SCALAR_MIN_PERIODS,
    EWMAC_SCALAR_POOLS,
    GOULDING_SIGNAL_MODES,
    SignalSpec,
    build_features,
    build_monthly_state_return_history,
    carver_ewmac,
    cluster_conviction_score,
    continuous_momentum,
    estimate_ewmac_scalar_history,
    estimate_goulding_forecast_scalar,
    estimate_mixing_params,
    goulding_continuous_raw,
    goulding_monthly,
    resolve_trend_direction,
)
from derivatives_bt_engine.domain.tsmom_history import (
    SOURCE_NEUTRAL_DATA_SOURCES,
    load_source_neutral_histories,
)
from derivatives_bt_engine.utils.logger import setup_logger

logger = setup_logger()

EWMAC_SCALAR_UNIVERSES = ('backtest', 'pysystemtrade')
EWMAC_NORMALIZATION_CACHE_VERSION = 1

# Same VIX_PATH convention as naked_futures.py/the options strategies -- a
# directory resolves to {dir}/processed/vix.parquet (see
# BaseDataLoader._resolve_source_paths). The old hardcoded
# .../VIX/historical/vix.parquet path no longer exists (stale, pre-dates a
# data-directory reorg) and would raise FileNotFoundError.
VIX_FILE_PATH = os.path.join(
    os.path.expanduser(os.getenv('VIX_PATH', '~/data/fin/market/index/VIX/eod')),
    'processed', 'vix.parquet',
)

# Same band thresholds as derivatives_bt_engine.live.tsmom_rebalance's VX-futures gate,
# applied to spot-VIX-current / spot-VIX-63d-MA instead.
VIX_ELEVATED_RATIO = 1.3
VIX_SPIKE_RATIO = 1.5
VIX_EXTREME_RATIO = 2.0
VIX_ELEVATED_SCALE = 0.6
# Trailing window (calendar days) for the spot-VIX moving average
# vix_ratio is computed against -- see TsmomBacktestConfig.vix_ma_window_days.
# The bands above were calibrated against this default; changing it changes
# what "elevated"/"spike"/"extreme" actually mean.
DEFAULT_VIX_MA_WINDOW_DAYS = 63
# Decimal places for genuinely PRICE-scale fields (entry_price/exit_price/
# transaction price/close/peak) -- NOT dollar amounts (fees/pnl/capital,
# which stay at 2dp) or ratios/percentages (ts_fast/vix_ratio/etc., which
# keep their own existing precision). 2dp was fine for equity-index-scale
# instruments (MES ~3700) but silently collapsed FX futures like J7 (quoted
# ~0.0097, USD per JPY) to a flat 0.01 for every single row -- confirmed
# directly: a real -0.0001315 price move (a genuine, correctly-computed
# -$824.60 PnL on full-precision internal math) displayed as
# entry_price == exit_price == 0.01, making a real loss look like a
# flat/impossible trade. 6dp keeps equity/metal/grain instruments perfectly
# readable (just trailing zeros) while actually distinguishing consecutive
# FX-scale price observations from each other.
_PRICE_ROUND_NDIGITS = 6


@dataclass
class TsmomBacktestConfig:
    symbols: list[str]
    initial_capital: float = 100_000.0
    vol_target: float = 0.15
    max_contracts: int = 5
    max_notional: float = 25_000.0
    # 'risk-targeted' is the established CTA sizing pipeline. 'ew' is a
    # strict DeMiguel-style benchmark adapted to directional futures: each
    # configured symbol receives 1/N of gross account notional, only the
    # signal sign is used, and a valid zero signal leaves its share in cash
    # (missing/untradable remains a distinct unresolved state). It bypasses
    # vol scaling, IDM, forecast magnitude, VIX/confidence
    # sizing, active-set renormalization, and cluster allocation.
    allocation_mode: str = 'risk-targeted'
    long_only: bool = False
    regime_discount: float = 0.5
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    # Signal-based entry/exit gate -- same mechanism as FuturesStrategyConfig/
    # TradeManager's ts_exit_threshold/ts_entry_threshold/exit_on_ts_crossover
    # (domain/trade_manager.py), adapted here for TSMOM's variable-direction
    # sizing: direction is derived from whichever side actually matters (the
    # currently-held position for exit, the newly-proposed target for entry)
    # rather than a fixed config field, since a TSMOM symbol can go long or
    # short depending on the sign of its own signal. signal_gate_mode picks
    # the cadence at which the gate is checked -- 'monthly' only at the
    # existing rebalance points (near-zero extra cost, reuses signal/ts_fast/
    # ts_slow _compute_signal_row already computed that day); 'daily'
    # additionally checks both entry AND exit every day in between,
    # off-cycle from the monthly resize, so a flat symbol can open the day
    # its entry gate first clears (not just at month-end) and a held
    # symbol can flatten the day its exit gate fires. Resizing (magnitude
    # changes to an already-open position) stays strictly monthly-only in
    # BOTH modes either way -- 'daily' only adds off-cycle open/flatten
    # transitions, never off-cycle resizing.
    ts_exit_threshold: Optional[float] = None
    ts_entry_threshold: Optional[float] = None
    exit_on_ts_crossover: bool = False
    signal_gate_mode: str = 'off'  # 'off' | 'monthly' | 'daily'
    # Opt-in "no rebalancing" mode: a positional list of fixed contract
    # counts, one per symbol (index-aligned with `symbols`, e.g.
    # symbols=['ES','GC','CL'], fixed_quantities=[4,3,2] means ES always
    # trades in units of 4, GC in units of 3, CL in units of 2). When set,
    # _compute_target skips vol-targeted/notional-scaled sizing entirely --
    # direction still comes from the sign of that symbol's own raw signal
    # (there's no other principled way to know when to go short without
    # it), but magnitude is just this fixed count (times vix_scalar
    # during an elevated-VIX regime, rounded, same as the vol-targeted
    # path's own elevated-VIX scaling) instead of vol_target/max_notional
    # math. Entry/exit gates (ts_exit_threshold/ts_entry_threshold/
    # exit_on_ts_crossover) and the VIX spike/extreme hold-or-halve
    # override still apply exactly as before -- this only replaces the
    # continuous vol-targeted scalar with a constant, it doesn't touch
    # the rest of the day loop.
    fixed_quantities: Optional[list[int]] = None
    # Portfolio-wide VIX regime gate (spot-VIX-vs-63d-MA spike/extreme
    # hold-or-halve override, elevated -> vix_scalar de-risking)
    # -- on by default (matches all prior behavior). Toggle off to isolate
    # the effect of ts_exit_threshold/ts_entry_threshold/exit_on_ts_crossover
    # alone, without VIX-driven interference -- particularly relevant for
    # signal_gate_mode='daily', which is already a departure from
    # traditional monthly-only TSMOM, and where mixing in a second,
    # differently-cadenced portfolio-wide gate makes it harder to isolate
    # what's actually being tested.
    vix_gating: bool = True
    # Trailing window (calendar days) for the spot-VIX moving average
    # vix_ratio is computed against -- see DEFAULT_VIX_MA_WINDOW_DAYS above
    # and derivatives_bt_engine.live.tsmom_rebalance's own
    # TsmomLiveConfig.vx_ma_window_days, which this mirrors so a live run
    # and a backtest comparison use the same window when you want them to.
    vix_ma_window_days: int = DEFAULT_VIX_MA_WINDOW_DAYS
    # Correlation-aware sizing -- None (default) preserves this module's
    # original behaviour exactly: every symbol independently sized to
    # config.max_notional * scalar / one_contract_notional, with NOTHING
    # scaling the book down for holding multiple symbols at once. Confirmed
    # directly (2026-07) that this has no diversification correction of any
    # kind anywhere -- no n_effective, no sqrt(N), no correlation term --
    # which is exactly why a correlated multi-symbol backtest run here
    # overstated realized vol 82-90% against a 15% target (see
    # derivatives_bt_engine.strats.tsmom_binary_vol_parity_backtest's own
    # module docstring, which documents this as the reason that script was
    # built with a deliberately simpler sizing scheme instead of reusing
    # this one).
    #
    # When set (e.g. 0.15), run_tsmom_backtest instead derives EACH
    # rebalance's own per-symbol notional_budget from
    # domain.allocation.compute_idm/build_returns_wide/
    # _bounded_ewm_correlation_matrix: total_budget = current capital *
    # target_portfolio_vol * IDM (IDM computed from that rebalance's own
    # signal-active symbols' REAL correlation, over a bounded trailing EWM
    # window -- corr_window_years/corr_halflife_days below), split across
    # those active symbols per notional_weighting below (flat by default).
    # At the degenerate zero-correlation case
    # this reduces exactly to
    # account_equity * target_portfolio_vol / sqrt(n_effective) -- the same
    # formula the live system's own compute_desired_risk_budget already
    # uses (see derivatives_bt_engine.domain.allocation) -- so this is a
    # strict generalization of that existing, live-validated formula to
    # the REAL measured correlation, not a novel scheme invented here.
    #
    # Scope: only affects the standard monthly-rebalance vol-targeted path
    # (the branch this module's documented overstated-vol finding was
    # actually measured on). Deliberately NOT wired into the pre-start_date
    # seed rebalance or the signal_gate_mode='daily' off-cycle path -- both
    # are separate, less-used code paths; extending this into them is a
    # deliberate follow-up, not an oversight, kept out of this change to
    # stay scoped to the diagnosed bug. Has no effect when fixed_quantities
    # is set (that mode never reads max_notional/notional_budget at all).
    #
    # Confirmed directly this FIXES the unbounded-overstatement direction of
    # the bug (a 12-symbol/$500k-notional run that overstated realized vol
    # at 25.37% against a 15% target came down to 7.16% with this on) but is
    # NOT precisely calibrated to target_portfolio_vol -- it undershot by
    # roughly 2x in that same test. Same root cause as the single-shot
    # calibration imprecision already documented in
    # tsmom_binary_vol_parity_backtest.py's own target_portfolio_vol
    # feature: a point-in-time bounded-window IDM/correlation estimate,
    # re-estimated at each rebalance, won't exactly match whatever
    # correlation structure the FULL backtest period actually realizes.
    # Treat this as "no longer structurally broken," not "hits its target
    # precisely" -- tightening that (e.g. an iterated rescale, matching the
    # sibling script's own calibration pattern) is a deliberate follow-up,
    # not implemented here.
    target_portfolio_vol: Optional[float] = None
    corr_window_years: float = 3.0
    corr_halflife_days: float = 63.0
    # How the total IDM-derived dollar-vol budget is split ACROSS active
    # symbols -- only matters when target_portfolio_vol is set. 'flat'
    # (default, unchanged prior behavior): every active symbol gets the
    # same budget, regardless of correlation structure. 'erc'/'hrp':
    # data-driven alternatives (allocation.compute_erc_weights/
    # compute_hrp_weights) that split by each symbol's OWN measured
    # correlation to the rest of the active set, so a correlated cluster
    # collectively earns roughly one undiversified bet's worth of budget
    # instead of each member separately claiming an equal share -- see
    # compute_symbol_notional_budget's own docstring for the full
    # derivation. When use_idm=True (below), this SAME split is also fed
    # into IDM as its weight vector -- not a flat 1/n vector regardless of
    # this choice, which would double-count diversification (IDM crediting
    # the active set's correlation once for the total, 'erc'/'hrp'
    # crediting it again for the split) -- see compute_symbol_notional_
    # budget's own docstring for the full argument.
    notional_weighting: str = 'flat'
    # Whether the total dollar-vol budget (capital * target_portfolio_vol)
    # gets scaled by IDM at all before being split per notional_weighting
    # above. True (default): total_budget = capital * target_portfolio_vol
    # * IDM, IDM computed with weights=<the notional_weighting split
    # itself> (see notional_weighting's own comment and compute_symbol_
    # notional_budget's docstring for why the weights must match). False:
    # total_budget = capital * target_portfolio_vol, no correlation-based
    # up/down-sizing of the total -- for 'erc'/'hrp' this isolates
    # diversification to the split alone instead of applying it twice at
    # two different levels; for 'flat' it removes correlation-awareness
    # from sizing altogether. Only matters when target_portfolio_vol is
    # set. See research/cta-layer-separation-risk-budgeting.md for the
    # IDM-vs-allocation-level framing this toggle exists to let you compare.
    use_idm: bool = True
    # Signal DIRECTION source -- 'continuous' (default, unchanged prior
    # behaviour): continuous_momentum's daily, vol-normalized trend_strength
    # + classify_regime(ts_fast, ts_slow) + a flat regime_discount in
    # Correction/Rebound. 'goulding': Goulding/Harvey/Mazzoleni (2023)'s own
    # monthly Bull/Correction/Bear/Rebound classification (goulding_monthly)
    # with a_Co/a_Re mixing weights re-estimated at EVERY rebalance from all
    # prior pooled history (domain.signal's build_monthly_state_return_
    # history/estimate_mixing_params, no lookahead) blending the slow/fast
    # direction in Correction/Rebound instead of a flat discount --
    # regime_discount is ignored in this mode (the a_Co/a_Re blend IS the
    # discount mechanism; applying a second flat one on top would double-
    # discount). Position SIZE/vol-targeting is unaffected either way --
    # this only changes which model decides the +-1/0 direction, mirroring
    # tsmom_binary_vol_parity_backtest.py's own weighting_mode='dynamic'
    # ("Goulding decides direction, vol-parity decides size"), now shared
    # via domain/signal.py instead of being that script's own local
    # implementation.
    signal_weighting: str = 'continuous'
    # Goulding forecast shape. 'binary' preserves the original paper-like
    # +/-1 direction. 'continuous' retains the fast/slow or equation-7
    # magnitude, causally rescales pooled prior forecasts to mean absolute
    # 0.5, and caps them at +/-1 before any risk/sizing overlays.
    goulding_signal_mode: str = 'binary'
    # Only matters when signal_weighting == 'goulding'. 'cluster' (default):
    # a_Co/a_Re re-estimated separately per instruments.py cluster (each
    # symbol using only its own cluster's pooled Correction/Rebound
    # history -- pooling across unrelated clusters would blend one asset
    # class's behavior into another's). 'global': one shared a_Co/a_Re
    # pooled across every symbol regardless of cluster, kept for direct
    # comparison. See estimate_mixing_params's own docstring.
    mixing_pool: str = 'cluster'
    # continuous_momentum's own return-horizon windows -- only matters
    # when signal_weighting == 'continuous' (goulding_monthly ignores
    # these entirely, using fast_months/slow_months instead, hardcoded
    # elsewhere -- not plumbed through this dataclass since nothing here
    # currently overrides them). vol_fast_window/vol_slow_window default
    # to None -> horizon-matched to fast_window/slow_window (see
    # continuous_momentum's own docstring on why that pairing matters:
    # ts_fast/ts_slow are horizon Sharpe-like statistics, and an
    # unmatched vol window breaks that clean n-day-return-over-n-day-vol
    # correspondence) -- pass them explicitly only to deliberately
    # decouple the two.
    fast_window: int = DEFAULT_FAST_WINDOW
    slow_window: int = DEFAULT_SLOW_WINDOW
    vol_fast_window: Optional[int] = None
    vol_slow_window: Optional[int] = None
    # Optional live-parity cluster policy. Selection happens before IDM/ERC
    # budgeting; the cap then redistributes an over-budget cluster by that
    # same raw signal conviction. Off by default so established backtests
    # retain their unconstrained universe.
    apply_cluster_cap: bool = False
    max_active_per_cluster: Optional[int] = None
    max_cluster_risk_pct: float = 0.25
    max_lot_overrun_pct: float = 0.5
    # Data plumbing. ``legacy_globex`` is the unchanged historical default.
    # The other modes use FuturesHistory's separated raw mark, return index,
    # Panama, and provenance streams.
    data_source: str = 'legacy_globex'
    globex_db_path: Path | str = DEFAULT_GLOBEX_DB_PATH
    pysystemtrade_db_path: Path | str = DEFAULT_PYSYSTEMTRADE_DB_PATH
    pysystemtrade_mapping_path: Optional[Path | str] = None
    hybrid_handoff_date: Optional[date] = None
    allow_candidate_mappings: bool = False
    # Native Carver EWMAC rule parameters. Estimated modes pool the raw
    # forecast causally through t-1; ``fixed`` retains the explicit scalar
    # for parity tests against an externally calibrated Carver value.
    ewmac_fast_span: int = 16
    ewmac_slow_span: int = 64
    ewmac_vol_span: int = 35
    ewmac_scalar_pool: str = 'global'
    ewmac_scalar_universe: str = 'pysystemtrade'
    ewmac_scalar_min_periods: int = EWMAC_SCALAR_MIN_PERIODS
    ewmac_forecast_target_abs: float = EWMAC_FORECAST_TARGET_ABS
    ewmac_forecast_scalar: float = 1.0
    ewmac_forecast_cap: float = EWMAC_FORECAST_CAP

    def __post_init__(self):
        if self.allocation_mode not in ALLOCATION_MODES:
            raise ValueError(f"allocation_mode must be one of {ALLOCATION_MODES}, got {self.allocation_mode!r}")
        if self.signal_gate_mode not in ('off', 'monthly', 'daily'):
            raise ValueError(f"signal_gate_mode must be 'off', 'monthly', or 'daily', got {self.signal_gate_mode!r}")
        if self.signal_weighting not in ('continuous', 'goulding', 'carver_ewmac'):
            raise ValueError(
                "signal_weighting must be 'continuous', 'goulding', or "
                f"'carver_ewmac', got {self.signal_weighting!r}"
            )
        if self.data_source not in ('legacy_globex', *SOURCE_NEUTRAL_DATA_SOURCES):
            raise ValueError(
                "data_source must be legacy_globex, globex, pysystemtrade, or hybrid, "
                f"got {self.data_source!r}"
            )
        if self.signal_weighting == 'carver_ewmac' and self.data_source == 'legacy_globex':
            raise ValueError("carver_ewmac requires a source-neutral data_source")
        if self.ewmac_fast_span <= 0 or self.ewmac_slow_span <= 0 or self.ewmac_vol_span <= 0:
            raise ValueError("EWMAC spans must be positive")
        if self.ewmac_fast_span >= self.ewmac_slow_span:
            raise ValueError("ewmac_fast_span must be less than ewmac_slow_span")
        if self.ewmac_scalar_pool not in EWMAC_SCALAR_POOLS:
            raise ValueError(
                f"ewmac_scalar_pool must be one of {EWMAC_SCALAR_POOLS}, "
                f"got {self.ewmac_scalar_pool!r}"
            )
        if self.ewmac_scalar_universe not in EWMAC_SCALAR_UNIVERSES:
            raise ValueError(
                f"ewmac_scalar_universe must be one of {EWMAC_SCALAR_UNIVERSES}, "
                f"got {self.ewmac_scalar_universe!r}"
            )
        if (self.signal_weighting == 'carver_ewmac'
                and self.ewmac_scalar_universe == 'pysystemtrade'
                and self.ewmac_scalar_pool not in ('global', 'fixed')):
            raise ValueError(
                "ewmac_scalar_universe='pysystemtrade' currently requires "
                "ewmac_scalar_pool='global' or 'fixed'; use universe='backtest' "
                "for cluster or instrument scalars"
            )
        if self.ewmac_scalar_min_periods <= 0:
            raise ValueError("ewmac_scalar_min_periods must be positive")
        if (not math.isfinite(self.ewmac_forecast_target_abs)
                or self.ewmac_forecast_target_abs <= 0):
            raise ValueError("ewmac_forecast_target_abs must be finite and positive")
        if not math.isfinite(self.ewmac_forecast_scalar) or self.ewmac_forecast_scalar <= 0:
            raise ValueError("ewmac_forecast_scalar must be finite and positive")
        if not math.isfinite(self.ewmac_forecast_cap) or self.ewmac_forecast_cap <= 0:
            raise ValueError("ewmac_forecast_cap must be positive")
        if self.goulding_signal_mode not in GOULDING_SIGNAL_MODES:
            raise ValueError(f"goulding_signal_mode must be one of {GOULDING_SIGNAL_MODES}, "
                             f"got {self.goulding_signal_mode!r}")
        if self.fast_window <= 0 or self.slow_window <= 0:
            raise ValueError("fast_window/slow_window must be positive")
        if self.fast_window >= self.slow_window:
            raise ValueError(f"fast_window ({self.fast_window}) must be < slow_window ({self.slow_window})")
        if self.mixing_pool not in ('cluster', 'global'):
            raise ValueError(f"mixing_pool must be 'cluster' or 'global', got {self.mixing_pool!r}")
        if self.notional_weighting not in NOTIONAL_WEIGHTING_SCHEMES:
            raise ValueError(f"notional_weighting must be one of {NOTIONAL_WEIGHTING_SCHEMES}, "
                              f"got {self.notional_weighting!r}")
        if self.fixed_quantities is not None and len(self.fixed_quantities) != len(self.symbols):
            raise ValueError(
                f"fixed_quantities must have exactly one entry per symbol (positional, same order): "
                f"got {len(self.fixed_quantities)} quantities for {len(self.symbols)} symbols "
                f"({self.symbols})"
            )
        if self.allocation_mode == 'ew' and self.fixed_quantities is not None:
            raise ValueError("allocation_mode='ew' is incompatible with fixed_quantities")
        if self.max_active_per_cluster is not None and self.max_active_per_cluster <= 0:
            raise ValueError("max_active_per_cluster must be positive when set")
        if not 0 < self.max_cluster_risk_pct <= 1:
            raise ValueError("max_cluster_risk_pct must be in (0, 1]")
        if self.max_lot_overrun_pct < 0:
            raise ValueError("max_lot_overrun_pct must be non-negative")


def check_vol_regime(vix_ratio: Optional[float]) -> VolRegime:
    """Normal | Elevated | Spike | Extreme from vix_current / vix_ma.

    Deliberately one-sided (mirrors derivatives_bt_engine.live.tsmom_rebalance's
    version): every threshold checks vix_ratio being HIGH -- no symmetric
    low-vix_ratio bucket, not an oversight. This is a portfolio-wide risk-
    management gate (feeds vix_scalar / the spike-extreme hold-
    or-halve bypass), not a regime-confidence detector. Per-instrument,
    asset-specific vol state (including a low-vol bucket) is a separate
    mechanism -- see SignalConfidenceRegime in signal.py."""
    if vix_ratio is None:
        return VolRegime.NORMAL
    if vix_ratio > VIX_EXTREME_RATIO:
        return VolRegime.EXTREME
    if vix_ratio > VIX_SPIKE_RATIO:
        return VolRegime.SPIKE
    if vix_ratio > VIX_ELEVATED_RATIO:
        return VolRegime.ELEVATED
    return VolRegime.NORMAL


def load_portfolio_data(symbols: list[str]) -> tuple[dict[str, pl.DataFrame], pl.DataFrame]:
    """Loads each symbol's continuous front-month OHLCV (via the existing
    FuturesDataLoader, parquet-cached) plus one shared spot-VIX series,
    read directly as polars (covers 1990-present, unlike the older
    pandas/CSV vix_file BaseDataLoader.vix_data still uses for the option
    path, which is stale past 2024-12-31).

    Some micros (MES, MNQ, MTN, ...) have no db history under their own
    symbol -- resolve_price_symbol borrows the full-size sibling's (ES,
    NQ, ZN, ...) via instruments.py's db_symbol field, so the cache/query
    below runs against the resolved symbol while price_data stays keyed by
    the raw traded symbol (matching futures_types/windowed elsewhere in
    this module, which always use the traded symbol for margin/commission/
    mult -- MES stays sized as MES, never as ES)."""
    cache_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.cache', 'futures'))
    os.makedirs(cache_dir, exist_ok=True)
    price_symbols = {s: resolve_price_symbol(s) for s in symbols}
    _validate_symbols_exist(set(price_symbols.values()), cache_dir)
    price_data = {s: FuturesDataLoader(asset=price_symbols[s], data_dir=cache_dir, use_preprocessed=True, save_preprocessed=True).daily
                  for s in symbols}
    # Defense-in-depth: FuturesDataLoader.daily already validates this at the
    # source (keyed by the resolved price symbol), but re-check here keyed by
    # the raw traded symbol (MES, not ES) so a failure is unambiguous about
    # which of this multi-symbol portfolio's series is the problem.
    for s, df in price_data.items():
        assert_monotonic_expiration(df, s)
    vix = pl.read_parquet(VIX_FILE_PATH).select(['date', 'close']).rename({'close': 'vix_close'}).sort('date')
    return price_data, vix


def _load_backtest_data(
    config: TsmomBacktestConfig,
) -> tuple[dict[str, pl.DataFrame], pl.DataFrame, dict[str, object]]:
    """Load legacy or source-neutral histories behind one backtest contract."""
    if config.data_source == 'legacy_globex':
        price_data, vix = load_portfolio_data(config.symbols)
        price_data = {
            symbol: frame.with_columns(
                pl.col('close').alias('pnl_close'),
                pl.col('close').alias('signal_index'),
                pl.col('close').alias('panama_price'),
                pl.lit('globex_legacy').alias('source_segment'),
                pl.lit('legacy_contract_marks').alias('pnl_quality'),
            )
            for symbol, frame in price_data.items()
        }
        return price_data, vix, {
            'data_source': 'legacy_globex',
            'instruments': {
                symbol: {
                    'source': 'globex_legacy',
                    'resolved_globex_symbol': resolve_price_symbol(symbol),
                    'start_date': frame.get_column('ts_event').min(),
                    'end_date': frame.get_column('ts_event').max(),
                    'invalid_return_rows': 0,
                }
                for symbol, frame in price_data.items()
            },
        }
    price_data, manifest = load_source_neutral_histories(
        config.symbols,
        data_source=config.data_source,
        globex_db_path=config.globex_db_path,
        pysystemtrade_db_path=config.pysystemtrade_db_path,
        mapping_path=config.pysystemtrade_mapping_path,
        handoff_date=config.hybrid_handoff_date,
        allow_candidate_mappings=config.allow_candidate_mappings,
    )
    vix = pl.read_parquet(VIX_FILE_PATH).select(['date', 'close']).rename(
        {'close': 'vix_close'}
    ).sort('date')
    return price_data, vix, manifest


def _return_signal_bars(frame: pl.DataFrame) -> pl.DataFrame:
    eligible = frame
    if 'return_valid' in frame.columns:
        eligible = frame.filter(
            pl.col('return_valid') | (pl.col('quality_flag') == 'initial_observation')
        )
    columns = ['ts_event', pl.col('signal_index').alias('close')]
    if 'ret_1d' in eligible.columns:
        columns.append('ret_1d')
    return eligible.select(columns).sort('ts_event')


def _ewmac_pool_key(symbol: str, config: TsmomBacktestConfig) -> str:
    if config.ewmac_scalar_pool in ('fixed', 'global'):
        return 'global'
    if config.ewmac_scalar_pool == 'cluster':
        return str(get_spec(symbol)['cluster'])
    return symbol


def _atomic_write_parquet(frame: pl.DataFrame, path: Path) -> None:
    """Publish a derived cache only after its Parquet write completes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    frame.write_parquet(temporary)
    os.replace(temporary, path)


def _cache_float(value: float) -> str:
    return format(value, '.12g').replace('-', 'm').replace('.', 'p')


def _pysystemtrade_ewmac_cache_paths(
    config: TsmomBacktestConfig,
    source_commit: str,
) -> dict[str, Path]:
    directory = (
        Path(DEFAULT_FUTURES_CACHE_ROOT)
        / 'pysystemtrade'
        / f'v{HISTORY_SCHEMA_VERSION}'
        / source_commit[:12]
        / f'ewmac_normalization_v{EWMAC_NORMALIZATION_CACHE_VERSION}'
        / (
            f'fast{config.ewmac_fast_span}_slow{config.ewmac_slow_span}'
            f'_vol{config.ewmac_vol_span}'
        )
    )
    scalar_key = (
        f'global_target{_cache_float(config.ewmac_forecast_target_abs)}'
        f'_min{config.ewmac_scalar_min_periods}'
    )
    return {
        'raw_forecasts': directory / 'raw_forecasts.parquet',
        'coverage': directory / 'instrument_coverage.parquet',
        'scalar': directory / f'{scalar_key}_scalar.parquet',
    }


def _eligible_pysystemtrade_instruments(db_path: Path | str) -> list[str]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT s.instrument_code
            FROM qa.series_coverage s
            WHERE s.multiple_rows > 0
              AND s.adjusted_rows > 0
              AND EXISTS (
                  SELECT 1 FROM raw.instrument_config i
                  WHERE i.instrument_code = s.instrument_code
              )
              AND EXISTS (
                  SELECT 1 FROM raw.roll_config r
                  WHERE r.instrument_code = s.instrument_code
              )
            ORDER BY s.instrument_code
            """
        ).fetchall()
    finally:
        con.close()
    return [str(row[0]) for row in rows]


def _load_pysystemtrade_ewmac_universe(
    config: TsmomBacktestConfig,
) -> tuple[pl.DataFrame, pl.DataFrame, str, bool]:
    """Load or build the full Carver raw-forecast panel and coverage.

    The underlying four-stream histories remain owned by
    :class:`PysystemtradeHistoryProvider`.  This adds a derived cache for one
    EWMAC speed so subsequent backtests do not reread 252 Panama files or
    recompute their EMA and point-volatility histories.
    """
    provider = PysystemtradeHistoryProvider(db_path=config.pysystemtrade_db_path)
    _, source_commit = provider._database_metadata()
    paths = _pysystemtrade_ewmac_cache_paths(config, source_commit)
    panel_cached = paths['raw_forecasts'].exists() and paths['coverage'].exists()
    if panel_cached:
        logger.info(
            'ewmac_universe cache_hit source=pysystemtrade fast=%d slow=%d vol=%d',
            config.ewmac_fast_span, config.ewmac_slow_span, config.ewmac_vol_span,
        )
        panel = pl.read_parquet(paths['raw_forecasts'])
        coverage = pl.read_parquet(paths['coverage'])
    else:
        instrument_codes = _eligible_pysystemtrade_instruments(
            config.pysystemtrade_db_path
        )
        if not instrument_codes:
            raise ValueError('pysystemtrade normalization universe is empty')
        logger.info(
            'ewmac_universe build_start source=pysystemtrade instruments=%d '
            'fast=%d slow=%d vol=%d',
            len(instrument_codes), config.ewmac_fast_span,
            config.ewmac_slow_span, config.ewmac_vol_span,
        )
        panel_rows: list[pl.DataFrame] = []
        coverage_rows: list[dict[str, object]] = []
        for position, instrument_code in enumerate(instrument_codes, start=1):
            history = provider.load(instrument_code)
            bars = history.panama_bars()
            raw = carver_ewmac(
                bars,
                fast_span=config.ewmac_fast_span,
                slow_span=config.ewmac_slow_span,
                vol_span=config.ewmac_vol_span,
                forecast_scalar=1.0,
                forecast_cap=config.ewmac_forecast_cap,
            ).select('ts_event', 'raw_forecast')
            usable = raw.filter(pl.col('raw_forecast').is_not_null())
            panel_rows.append(
                raw.with_columns(
                    pl.lit(instrument_code).alias('instrument_code'),
                    pl.lit('global').alias('pool_key'),
                ).select(
                    'ts_event', 'instrument_code', 'pool_key', 'raw_forecast'
                )
            )
            coverage_rows.append({
                'instrument_code': instrument_code,
                'pool_key': 'global',
                'data_source': 'pysystemtrade',
                'source_segments': 'pysystemtrade',
                'asset_class': history.metadata.get('asset_class'),
                'ts_start': bars.get_column('ts_event').min(),
                'ts_end': bars.get_column('ts_event').max(),
                'forecast_ts_start': usable.get_column('ts_event').min(),
                'forecast_ts_end': usable.get_column('ts_event').max(),
                'history_observations': bars.height,
                'usable_forecast_observations': usable.height,
                'source_commit': source_commit,
            })
            if position % 25 == 0 or position == len(instrument_codes):
                logger.info(
                    'ewmac_universe build_progress completed=%d total=%d',
                    position, len(instrument_codes),
                )
        panel = pl.concat(panel_rows, how='vertical').sort(
            'instrument_code', 'ts_event'
        )
        coverage = pl.DataFrame(coverage_rows).sort('instrument_code')
        _atomic_write_parquet(panel, paths['raw_forecasts'])
        _atomic_write_parquet(coverage, paths['coverage'])
        logger.info(
            'ewmac_universe build_complete instruments=%d forecast_rows=%d '
            'cache=%s',
            coverage.height, panel.height, paths['raw_forecasts'],
        )
    return panel, coverage, source_commit, panel_cached


def _load_pysystemtrade_scalar_history(
    panel: pl.DataFrame,
    config: TsmomBacktestConfig,
    source_commit: str,
) -> tuple[pl.DataFrame, bool]:
    paths = _pysystemtrade_ewmac_cache_paths(config, source_commit)
    if paths['scalar'].exists():
        logger.info('ewmac_scalar cache_hit path=%s', paths['scalar'])
        return pl.read_parquet(paths['scalar']), True
    scalar = estimate_ewmac_scalar_history(
        panel,
        target_abs_forecast=config.ewmac_forecast_target_abs,
        min_periods=config.ewmac_scalar_min_periods,
    )
    _atomic_write_parquet(scalar, paths['scalar'])
    logger.info(
        'ewmac_scalar build_complete rows=%d cache=%s',
        scalar.height, paths['scalar'],
    )
    return scalar, False


def _precompute_ewmac_normalization(
    full_price_data: dict[str, pl.DataFrame],
    config: TsmomBacktestConfig,
    data_manifest: Optional[dict[str, object]] = None,
) -> tuple[dict[str, pl.DataFrame], pl.DataFrame, pl.DataFrame]:
    """Build raw rules, causal pooled scalars, and scaled EWMAC forecasts.

    Forecasts are estimated from the full unbounded histories once, but every
    estimated scalar row is shifted internally and therefore uses only prior
    dates.  The returned report is deliberately separate from rebalance-event
    output so calibration can be audited at its native daily frequency.
    """
    raw_by_symbol: dict[str, pl.DataFrame] = {}
    panel_rows: list[pl.DataFrame] = []
    coverage_rows: list[dict[str, object]] = []
    manifest_instruments = (
        data_manifest.get('instruments', {}) if data_manifest is not None else {}
    )
    for symbol, frame in full_price_data.items():
        pool_key = _ewmac_pool_key(symbol, config)
        raw = carver_ewmac(
            frame.select('ts_event', pl.col('panama_price').alias('close')),
            fast_span=config.ewmac_fast_span,
            slow_span=config.ewmac_slow_span,
            vol_span=config.ewmac_vol_span,
            forecast_scalar=1.0,
            forecast_cap=config.ewmac_forecast_cap,
        ).select('ts_event', 'raw_ewmac', 'point_vol', 'raw_forecast')
        raw_by_symbol[symbol] = raw.with_columns(
            pl.lit(pool_key).alias('pool_key')
        )
        usable_forecasts = raw.filter(pl.col('raw_forecast').is_not_null())
        instrument_manifest = manifest_instruments.get(symbol, {})
        instrument_metadata = instrument_manifest.get('metadata', {})
        source_segments = (
            sorted(str(value) for value in frame['source_segment'].drop_nulls().unique())
            if 'source_segment' in frame.columns else [config.data_source]
        )
        coverage_rows.append({
            'traded_symbol': symbol,
            'instrument_code': instrument_manifest.get('history_instrument', symbol),
            'pool_key': pool_key,
            'data_source': config.data_source,
            'source_segments': ','.join(source_segments),
            'asset_class': instrument_metadata.get('asset_class'),
            'ts_start': frame.get_column('ts_event').min(),
            'ts_end': frame.get_column('ts_event').max(),
            'forecast_ts_start': usable_forecasts.get_column('ts_event').min(),
            'forecast_ts_end': usable_forecasts.get_column('ts_event').max(),
            'history_observations': frame.height,
            'usable_forecast_observations': usable_forecasts.height,
            'source_commit': instrument_metadata.get('source_git_commit'),
        })
        panel_rows.append(
            raw.select('ts_event', 'raw_forecast').with_columns(
                pl.lit(symbol).alias('instrument_code'),
                pl.lit(pool_key).alias('pool_key'),
            ).select('ts_event', 'instrument_code', 'pool_key', 'raw_forecast')
        )

    traded_panel = pl.concat(panel_rows, how='vertical')
    traded_coverage = pl.DataFrame(coverage_rows).sort(
        'pool_key', 'instrument_code'
    )
    panel_cache_hit = False
    scalar_cache_hit = False
    source_commit: Optional[str] = None
    if (config.ewmac_scalar_universe == 'pysystemtrade'
            and config.ewmac_scalar_pool == 'global'):
        panel, coverage, source_commit, panel_cache_hit = (
            _load_pysystemtrade_ewmac_universe(config)
        )
        scalar_history, scalar_cache_hit = _load_pysystemtrade_scalar_history(
            panel, config, source_commit
        )
        normalization_universe = 'pysystemtrade_full'
        history_to_traded = {}
        for symbol, details in manifest_instruments.items():
            crosswalk = details.get('crosswalk') or {}
            history_code = (
                crosswalk.get('carver_instrument')
                or details.get('history_instrument')
                or symbol
            )
            history_to_traded[str(history_code)] = symbol
        coverage = pl.DataFrame(
            [
                {
                    'traded_symbol': history_to_traded.get(row['instrument_code']),
                    **row,
                }
                for row in coverage.to_dicts()
            ],
            infer_schema_length=None,
        )
    else:
        panel = traded_panel
        coverage = traded_coverage
        normalization_universe = 'configured_backtest_symbols'
        estimation_min_periods = (
            1 if config.ewmac_scalar_pool == 'fixed'
            else config.ewmac_scalar_min_periods
        )
        scalar_history = estimate_ewmac_scalar_history(
            panel,
            target_abs_forecast=config.ewmac_forecast_target_abs,
            min_periods=estimation_min_periods,
        )
    if config.ewmac_scalar_pool == 'fixed':
        scalar_history = scalar_history.with_columns(
            pl.lit(config.ewmac_forecast_scalar).alias('forecast_scalar'),
            pl.lit(True).alias('scalar_valid'),
        )
    scalar_history = scalar_history.with_columns(
        pl.lit(config.ewmac_scalar_pool).alias('scalar_pool'),
        pl.lit(normalization_universe).alias('normalization_universe'),
        pl.lit(len(full_price_data)).alias('configured_instrument_count'),
        pl.lit(coverage.height).alias('normalization_instrument_count'),
        pl.lit(config.ewmac_fast_span).alias('fast_span'),
        pl.lit(config.ewmac_slow_span).alias('slow_span'),
        pl.lit(config.ewmac_vol_span).alias('vol_span'),
        pl.lit(config.ewmac_forecast_target_abs).alias('target_abs_forecast'),
        pl.lit(config.ewmac_forecast_cap).alias('forecast_cap'),
        pl.lit(config.ewmac_scalar_min_periods).alias('configured_min_periods'),
        pl.lit(source_commit).cast(pl.String).alias('normalization_source_commit'),
        pl.lit(panel_cache_hit).alias('forecast_panel_cache_hit'),
        pl.lit(scalar_cache_hit).alias('scalar_cache_hit'),
    )

    forecasts: dict[str, pl.DataFrame] = {}
    scalar_join = scalar_history.select(
        'ts_event', 'pool_key', 'forecast_scalar', 'scalar_valid'
    )
    for symbol, raw in raw_by_symbol.items():
        pool_key = raw.get_column('pool_key')[0]
        pool_scalar = scalar_join.filter(
            pl.col('pool_key') == pool_key
        ).select('ts_event', 'forecast_scalar', 'scalar_valid').sort('ts_event')
        forecasts[symbol] = (
            raw.sort('ts_event').join_asof(
                pool_scalar,
                on='ts_event',
                strategy='backward',
            )
            .with_columns(
                (pl.col('raw_forecast') * pl.col('forecast_scalar'))
                .clip(-config.ewmac_forecast_cap, config.ewmac_forecast_cap)
                .alias('ewmac_forecast')
            )
        )
    return forecasts, scalar_history, coverage


def _precompute_signal(
    frame: pl.DataFrame,
    config: TsmomBacktestConfig,
    annualization_days: int,
    ewmac: Optional[pl.DataFrame] = None,
) -> pl.DataFrame:
    """Build a common signal/sizing frame for all three signal classes."""
    return_bars = _return_signal_bars(frame)
    base = continuous_momentum(
        build_features(return_bars),
        fast_window=config.fast_window,
        slow_window=config.slow_window,
        vol_fast_window=config.vol_fast_window,
        vol_slow_window=config.vol_slow_window,
        annualization_days=annualization_days,
        discount=config.regime_discount,
    )
    marks = frame.select(
        'ts_event', 'close', 'pnl_close', 'source_segment', 'pnl_quality'
    )
    base = base.rename({'close': 'signal_close'})
    if config.signal_weighting != 'carver_ewmac':
        # Return-invalid observations do not advance a return-defined signal,
        # but they remain real trading sessions. Hold the last valid signal
        # state while retaining today's raw mark and roll-neutral P&L level.
        state_columns = [column for column in base.columns if column != 'ts_event']
        return marks.join(base, on='ts_event', how='left').with_columns(
            [pl.col(column).forward_fill() for column in state_columns]
        )
    if ewmac is None:
        ewmac = carver_ewmac(
            frame.select('ts_event', pl.col('panama_price').alias('close')),
            fast_span=config.ewmac_fast_span,
            slow_span=config.ewmac_slow_span,
            vol_span=config.ewmac_vol_span,
            forecast_scalar=config.ewmac_forecast_scalar,
            forecast_cap=config.ewmac_forecast_cap,
        ).select(
            'ts_event', 'raw_ewmac', 'point_vol', 'raw_forecast',
            pl.col('signal').alias('ewmac_forecast'),
        ).with_columns(
            pl.lit(config.ewmac_forecast_scalar).alias('forecast_scalar'),
            pl.lit(config.ewmac_scalar_pool).alias('pool_key'),
        )
    ewmac = ewmac.select(
        'ts_event', 'raw_ewmac', 'point_vol', 'raw_forecast',
        'forecast_scalar', 'pool_key', 'ewmac_forecast',
    )
    state_columns = [column for column in base.columns if column != 'ts_event']
    return ewmac.join(base, on='ts_event', how='left').with_columns(
        [pl.col(column).forward_fill() for column in state_columns]
    ).join(marks, on='ts_event', how='left').with_columns(
        (pl.col('ewmac_forecast') / config.ewmac_forecast_cap).alias('signal'),
        (pl.col('ewmac_forecast') / config.ewmac_forecast_cap).alias('ts'),
        (pl.col('ewmac_forecast') / config.ewmac_forecast_cap).alias('ts_fast'),
        (pl.col('ewmac_forecast') / config.ewmac_forecast_cap).alias('ts_slow'),
        pl.when(pl.col('ewmac_forecast') > 0)
        .then(pl.lit('bull'))
        .when(pl.col('ewmac_forecast') < 0)
        .then(pl.lit('bear'))
        .otherwise(pl.lit('unknown'))
        .alias('regime'),
    )


def _validate_symbols_exist(price_symbols, cache_dir: str) -> None:
    """The continuous-front-month query (FuturesDataLoader.daily) has no
    early-exit for a non-matching asset -- it's an unindexed full-table scan
    that takes minutes either way, so a typo'd or IB-only symbol (e.g. the
    live rebalance's IBKR ticker 'JPY'/'BRE' rather than this db's real
    CME/Globex root '6J'/'6L') would otherwise silently come back as an
    empty frame after minutes of waiting. Check the cheap `DISTINCT asset`
    list up front instead, skipping symbols that are already parquet-cached
    (no need to hit duckdb at all for those). Takes already-resolved price
    symbols (see load_portfolio_data), not raw traded symbols -- a micro
    like MES is expected to be absent from `daily` and shouldn't raise."""
    uncached = [
        s for s in price_symbols
        if not os.path.exists(globex_daily_cache_path(cache_dir, s))
    ]
    if not uncached:
        return
    # FuturesDataLoader.db_path is a dataclass field with a default_factory,
    # so it isn't readable as a class attribute (FuturesDataLoader.db_path
    # raises AttributeError) -- call the same factory instances use, so this
    # doesn't duplicate the GLOBEX_DB_PATH env-var/default logic.
    db_path = FuturesDataLoader.__dataclass_fields__['db_path'].default_factory()
    con = duckdb.connect(db_path, read_only=True)
    try:
        known = set(con.sql('SELECT DISTINCT asset FROM daily').pl()['asset'].to_list())
    finally:
        con.close()
    missing = [s for s in uncached if s not in known]
    if missing:
        raise ValueError(
            f'No data found for symbol(s) {missing} in the futures db -- '
            f'note this must be the real CME/Globex ticker (e.g. 6J, 6L, '
            f'6M), not whatever symbol IBKR uses for live contract resolution.'
        )


def _compute_vix_regime_series(vix: pl.DataFrame,
                                window_days: int = DEFAULT_VIX_MA_WINDOW_DAYS) -> pl.DataFrame:
    """Adds vix_ma / vix_ratio / vol_regime columns to a raw VIX frame --
    the column stays named vix_ma regardless of window_days (matches this
    project's live counterpart, derivatives_bt_engine.live.tsmom_rebalance's
    own vx_current/vx_ma fields, which keep their names the same way); only
    the rolling window LENGTH used to compute it varies."""
    vix = vix.with_columns(vix_ma=pl.col('vix_close').rolling_mean(window_days))
    vix = vix.with_columns(
        vix_ratio=pl.when(pl.col('vix_ma') > 0)
        .then(pl.col('vix_close') / pl.col('vix_ma'))
        .otherwise(None)
    )
    regimes = [check_vol_regime(r) for r in vix['vix_ratio'].to_list()]
    return vix.with_columns(vol_regime=pl.Series(regimes))


def _round(x: Optional[float], ndigits: int) -> Optional[float]:
    """round() that passes None through, for optional diagnostic fields
    that may not have been computable (e.g. gated/skipped rebalances)."""
    return None if x is None else round(x, ndigits)


def _signal_gate_reason(sig_val, ts_fast_val, ts_slow_val, is_long: bool, threshold: Optional[float],
                         exit_on_ts_crossover: bool) -> Optional[str]:
    """Same shape as TradeManager's per-position gate check
    (domain/trade_manager.py's _signal_gate_reason), standalone here since
    TSMOM has no per-position `self.config` to close over and this module
    is deliberately kept separate from TradeManager. Bails out (never
    gates) if either ts_fast or ts_slow is still null -- continuous_momentum
    only requires ts_fast to be non-null to emit a `signal` value at all, so
    early in any backtest window `signal` can look like a real number
    while being an unreliable ts_fast-only estimate."""
    if ts_fast_val is None or ts_slow_val is None:
        return None
    if threshold is not None and sig_val is not None:
        if is_long and sig_val < threshold:
            return 'signal_ts_threshold'
        if not is_long and sig_val > -threshold:
            return 'signal_ts_threshold'
    if exit_on_ts_crossover:
        if is_long and ts_fast_val < ts_slow_val:
            return 'signal_crossover'
        if not is_long and ts_fast_val > ts_slow_val:
            return 'signal_crossover'
    return None


def _apply_signal_gate(prior_contracts: int, proposed_target: int, result: dict,
                        config: TsmomBacktestConfig) -> tuple[int, Optional[str]]:
    """Overrides `proposed_target` to 0 if the signal gate fires. Direction
    is derived from whichever side actually matters: the CURRENTLY-HELD
    position's sign for the exit check (an existing long/short that's
    weakened), the PROPOSED target's sign for the entry check (a new
    position sizing wants to open, in that direction) -- not a fixed
    config field, since a TSMOM symbol's direction comes from its own
    signal sign, unlike a naked single-direction FuturesStrategyConfig.
    Returns (final_target, gate_reason)."""
    if config.signal_gate_mode == 'off' or config.allocation_mode == 'ew':
        return proposed_target, None
    sig_val, ts_fast_val, ts_slow_val = result.get('signal'), result.get('ts_fast'), result.get('ts_slow')

    if prior_contracts != 0:
        is_long = prior_contracts > 0
        reason = _signal_gate_reason(sig_val, ts_fast_val, ts_slow_val, is_long,
                                      config.ts_exit_threshold, config.exit_on_ts_crossover)
        if reason is not None:
            return 0, reason
        if config.fixed_quantities is not None:
            # fixed_quantities has no "resize" concept -- magnitude is a
            # constant, so if the exit gate didn't fire, ANY difference
            # between prior_contracts and proposed_target here can only be
            # an implicit sign flip driven by the raw composite signal's
            # own sign changing independently of whatever specific exit
            # condition is configured (e.g. exit_on_ts_crossover checks
            # ts_fast-vs-ts_slow directly, which isn't guaranteed to coincide
            # with tanh(0.4*ts_fast+0.6*ts_slow) crossing zero) -- stay held,
            # unchanged; a flip must go through the gate, never happen
            # silently just because the vol-targeted path's own "let the
            # freshly computed target through" fallthrough doesn't
            # distinguish resizing from flipping.
            return prior_contracts, None

    if prior_contracts == 0 and proposed_target != 0:
        is_long = proposed_target > 0
        blocked = _signal_gate_reason(sig_val, ts_fast_val, ts_slow_val, is_long,
                                       config.ts_entry_threshold, config.exit_on_ts_crossover) is not None
        if blocked:
            return 0, 'signal_entry_blocked'

    return proposed_target, None


def _month_end_dates(price_data: dict[str, pl.DataFrame]) -> set[date]:
    """Last trading day of each calendar month, across the union of every
    loaded symbol's dates."""
    all_dates = sorted(set().union(*(set(df['ts_event'].to_list()) for df in price_data.values())))
    dates_df = pl.DataFrame({'ts_event': all_dates}).with_columns(
        ym=pl.col('ts_event').dt.strftime('%Y-%m')
    )
    month_ends = dates_df.group_by('ym').agg(pl.col('ts_event').max().alias('month_end'))
    return set(month_ends['month_end'].to_list())


def _detect_roll_dates(df: pl.DataFrame, start: date, end: date,
                        active_months: Optional[list[str]], symbol: str) -> list[date]:
    """Real per-symbol roll dates: every date within [start, end] where the
    continuous front-month series' own selected contract's `expiration`
    changes from the prior row -- an actual volume-driven front-month
    crossover in FuturesDataLoader.daily's sticky/volume-ranked query (see
    futures_dataloader.py's _CONTINUOUS_FRONT_MONTH_SQL), not a fixed
    calendar assumption applied uniformly regardless of a symbol's real
    roll cadence. Replaces the previous fixed-quarterly schedule
    (FuturesSignalGenerator._get_quarterly_roll_dates, still used
    unchanged by the naked single-symbol path), which was empirically
    confirmed wrong for a meaningful chunk of this project's universe: GC,
    SI, and the four grains each roll roughly monthly among their own 4-5
    real active months, not quarterly, and none of their active-month sets
    is a subset of Mar/Jun/Sep/Dec (see
    research/research_futures_roll_logic_and_active_months.md §2, §4.2).

    `df` must be the symbol's own unbounded (not date-windowed) continuous
    series -- the first row of any slice always looks like "a change" (its
    own prior row is unavailable), which would register a false roll right
    at whatever date happens to start the slice, if `df` had already been
    windowed before this runs.

    `active_months` (instruments.resolve_active_months(symbol), when
    confirmed) is used only as a validation guard on the DETECTED dates,
    not to generate them: each crossover's target contract's month-letter
    is checked against it, and a warning (not a raised error -- a hard
    failure here risks reintroducing the "stuck-forever roll" class of bug
    this project already hit once) is logged if a detected roll lands
    outside the confirmed active set. That mismatch is exactly the shape
    of a single spurious volume-spike hijacking the sticky series (the
    still-open BRE/6L bug, research doc §1.2) -- surfaced here rather than
    silently trusted, for every symbol this guard is available for, not
    just BRE.

    No-ops (returns an empty list, i.e. "no detected rolls") when `df` has
    no `expiration` column at all -- mirrors assert_monotonic_expiration's
    own defensive no-op for the same case (futures_dataloader.py), e.g. a
    hand-built or synthetic price series (some of this module's own test
    fixtures) that never carried contract-level metadata to begin with."""
    identity_column = 'contract_id' if 'contract_id' in df.columns else 'expiration'
    if identity_column not in df.columns:
        return []
    d = df.sort('ts_event')
    changed = d.filter(pl.col(identity_column) != pl.col(identity_column).shift(1))
    changed = changed.filter((pl.col('ts_event') >= start) & (pl.col('ts_event') <= end))
    if active_months:
        for row in changed.iter_rows(named=True):
            expiration = row.get('expiration')
            letter = CME_MONTH_NUM_TO_LETTER.get(expiration.month) if expiration else None
            if letter is not None and letter not in active_months:
                logger.warning(
                    "%s: detected roll on %s into a contract expiring %s (month %s) -- outside "
                    "this symbol's confirmed active_months %s; possible spurious volume-spike "
                    "crossover rather than a genuine roll.",
                    symbol, row['ts_event'], row['expiration'], letter, active_months,
                )
    return changed['ts_event'].to_list()


def _vix_regime_at(vix: pl.DataFrame, d: date) -> tuple[VolRegime, Optional[float], Optional[float]]:
    """(vol_regime, vix_close, vix_ratio) as of the latest available VIX
    row at or before `d`. (Normal, None, None) if no VIX data is
    available yet."""
    row = vix.filter(pl.col('date') <= d).tail(1)
    if row.height == 0:
        return VolRegime.NORMAL, None, None
    return VolRegime(row['vol_regime'][0]), row['vix_close'][0], row['vix_ratio'][0]


def _compute_signal_row(symbol: str, precomputed: dict[str, pl.DataFrame], d: date,
                         futures_types: dict[str, dict], config: TsmomBacktestConfig,
                         vix_scalar: float, annualization_days: int,
                         notional_budget: Optional[float] = None,
                         g_regime_val: Optional[str] = None, g_fast_val: Optional[float] = None,
                         g_slow_val: Optional[float] = None, a_co: Optional[float] = None,
                         a_re: Optional[float] = None,
                         goulding_forecast_scalar: Optional[float] = None) -> Optional[dict]:
    """Signal + vol-targeted (or fixed-quantity) sizing for one symbol as of
    date `d`, reading from `precomputed` -- each symbol's full
    continuous_momentum output, computed ONCE for the whole unbounded
    history (see run_tsmom_backtest). continuous_momentum's rolling/
    diff functions are strictly backward-looking, so a given date's row is
    identical whether computed from the full series or from a series
    truncated to that date -- precomputing once and looking up by date is
    exactly equivalent to (and far cheaper than) this function's old
    per-call recompute, which used to run fresh at every rebalance and
    would otherwise need to run for every symbol on every calendar day to
    support daily entry/exit checking. None if there isn't yet enough
    history for a signal at all (continuous_momentum's own `signal`
    column is null until ts_fast has 63 bars, goulding_monthly's `g_regime`
    is null until fast/slow have enough completed months).

    notional_budget: None (default) uses config.max_notional, exactly as
    before. A caller doing correlation-aware sizing (see
    run_tsmom_backtest's own target_portfolio_vol handling) passes an
    explicit per-rebalance, IDM-derived override instead -- only affects
    the non-fixed_quantities branch below (fixed_quantities' own sizing
    never reads max_notional/notional_budget at all).

    g_regime_val/g_fast_val/g_slow_val/a_co/a_re: only read when
    config.signal_weighting == 'goulding' -- the caller resolves these from
    its own precomputed, forward-matched goulding_monthly output and that
    rebalance date's own (per-cluster or global) estimate_mixing_params
    result, since a_Co/a_Re is shared across every symbol in a cluster and
    only needs estimating once per rebalance date, not once per symbol
    call. g_fast_val/g_slow_val here are goulding_monthly's own `fast`/
    `slow` -- Goulding's lagged trailing-average momentum signals (already
    shift(1)'d, no lookahead), NOT the realized return of the period about
    to be traded; see _goulding_direction's own docstring for why that
    distinction matters."""
    row = precomputed[symbol].filter(pl.col('ts_event') == d)
    if row.height == 0:
        return None

    def _col(name):
        return row[name][0] if name in row.columns else None

    # Sizing inputs -- daily_std_last/hv/risk_scalar/close/dd -- ALWAYS
    # come from continuous_momentum's own output regardless of
    # signal_weighting: "Goulding decides direction, vol-parity decides
    # size" (mirrors tsmom_binary_vol_parity_backtest.py's own
    # weighting_mode='dynamic' design), not a literal end-to-end
    # reproduction of the paper's own portfolio construction (which
    # doesn't size positions at all -- it studies raw dynamic-blend
    # RETURNS as a standalone series). ts_fast/ts_slow/avg_r_fast/
    # avg_r_slow are read here unconditionally too (not just in
    # 'continuous' mode) so the returned diagnostic dict always has both
    # models' readings side by side for comparison, matching that same
    # script's own "both models computed unconditionally" convention.
    ts_fast, ts_slow = _col('ts_fast'), _col('ts_slow')
    daily_std_last = _col('std_fast')
    last_close = _col('close')
    # `dd` is already a (close - peak) / peak fraction from
    # continuous_momentum -- express as a percentage to match
    # `stats`' own drawdown_pct convention.
    dd_raw = _col('dd')
    dd_pct = dd_raw * 100 if dd_raw is not None else None
    hv = daily_std_last * math.sqrt(annualization_days) if daily_std_last and daily_std_last > 0 else None
    risk_scalar = max(0.25, min(2.0, config.vol_target / hv)) if hv else 1.0

    # resolve_trend_direction (domain.signal) -- shared with
    # live.tsmom_rebalance's own per-instrument signal computation, so both
    # modules implement this continuous-vs-goulding branch exactly once.
    g_raw_forecast = (
        goulding_continuous_raw(g_regime_val, a_co, a_re, g_fast_val, g_slow_val)
        if config.signal_weighting == 'goulding' and a_co is not None and a_re is not None
        else None
    )
    direction_model = (
        'continuous' if config.signal_weighting == 'carver_ewmac'
        else config.signal_weighting
    )
    resolved = resolve_trend_direction(
        direction_model, _col('signal'), ts_fast, ts_slow,
        config.regime_discount, g_regime_val, g_fast_val, g_slow_val,
        a_co if a_co is not None else 0.5, a_re if a_re is not None else 0.5,
        config.goulding_signal_mode, goulding_forecast_scalar,
    )
    if resolved is None:
        return None
    trend_strength, regime, regime_discount, g_blend = resolved

    signal_for_scalar = trend_strength
    if config.long_only and signal_for_scalar is not None and not (
        isinstance(signal_for_scalar, float) and math.isnan(signal_for_scalar)
    ):
        signal_for_scalar = max(0.0, signal_for_scalar)

    if config.allocation_mode == 'ew':
        # Literal 1/N benchmark: signal chooses direction only. All dynamic
        # magnitude and volatility/risk overlays are intentionally bypassed.
        scalar = (
            0.0 if signal_for_scalar is None or signal_for_scalar == 0
            else math.copysign(1.0, signal_for_scalar)
        )
        applied_vix_scalar = 1.0
    else:
        scalar = compute_position_scalar(
            signal_for_scalar, daily_std_last, config.vol_target, regime,
            regime_discount=regime_discount, annualization_days=annualization_days,
        ) * vix_scalar
        applied_vix_scalar = vix_scalar

    mult = futures_types[symbol]['multiplier']
    one_contract_notional = last_close * mult if last_close is not None else None
    fractional_target_contracts = None
    budget = None
    if config.fixed_quantities is not None:
        # No-rebalancing mode: direction is still signal-driven (there's no
        # other principled way to know when to go short without it), but
        # magnitude is this symbol's own fixed contract count -- scaled by
        # vix_scalar (the same elevated-VIX de-risking the vol-
        # targeted path applies) and rounded, not derived from vol_target/
        # max_notional at all. max_contracts still clamps as a sanity
        # backstop (raise it if it's below your configured fixed_quantities
        # -- it silently truncates otherwise, same as the vol-targeted path).
        fixed_qty = config.fixed_quantities[config.symbols.index(symbol)]
        if signal_for_scalar is None or (isinstance(signal_for_scalar, float) and math.isnan(signal_for_scalar)) or signal_for_scalar == 0:
            target = 0
        else:
            direction = 1 if signal_for_scalar > 0 else -1
            target = direction * round(fixed_qty * vix_scalar)
    else:
        budget = notional_budget if notional_budget is not None else config.max_notional
        fractional_target_contracts = (budget * scalar) / one_contract_notional if one_contract_notional else 0.0
        target = round(fractional_target_contracts)
    target = max(-config.max_contracts, min(config.max_contracts, target))

    return {
        'symbol': symbol, 'target': target, 'signal': trend_strength, 'regime': regime,
        # scalar itself (pre-notional-conversion, post-vix_scalar) --
        # not printed/logged anywhere before this, needed by
        # run_tsmom_backtest's target_portfolio_vol handling to decide which
        # symbols are genuinely signal-active (scalar != 0) independent of
        # any particular notional_budget's own rounding, since a symbol that
        # rounds to 0 contracts at one budget can still be "active" at a
        # bigger one (see run_tsmom_backtest's own two-pass comment).
        'scalar': scalar,
        # Preserve the continuous contract target independently of Python's
        # integer round() so the optional cluster-cap pass can allocate its
        # finite dollar-vol budget before one-lot discreteness takes over.
        'fractional_target_contracts': fractional_target_contracts,
        'combined_scalar': scalar,
        'mult': mult,
        'max_contracts': config.max_contracts,
        'cluster': get_spec(symbol)['cluster'],
        'contin_signal': _col('signal'),
        'ts': _col('ts'),
        'ts_regime': _col('regime'),
        'daily_std': daily_std_last,
        'vix_scalar': applied_vix_scalar,
        'allocation_mode': config.allocation_mode,
        'notional_allocation_weight': (
            1.0 / len(config.symbols)
            if config.allocation_mode == 'ew' and config.symbols else None
        ),
        'pre_scalar_notional_budget': budget,
        'fractional_target_notional': (
            fractional_target_contracts * one_contract_notional
            if fractional_target_contracts is not None and one_contract_notional is not None else None
        ),
        'hv': hv,
        'risk_scalar': risk_scalar * applied_vix_scalar if config.allocation_mode != 'ew' else 1.0,
        'regime_discount': regime_discount if config.allocation_mode != 'ew' else 1.0,
        'close': last_close, 'pnl_close': _col('pnl_close'), 'dd_pct': dd_pct,
        # Raw signal-row fields, straight from continuous_momentum, purely
        # for debugging/sanity-checking the sizing math end to end.
        # fast_return/slow_return named r_fast/r_slow in continuous_momentum's
        # own output -- kept under their old dict keys here since downstream
        # consumers (e.g. line ~655's _round(s.get(...))) already expect them.
        'peak': _col('peak'), 'avg_r_fast': _col('avg_r_fast'), 'avg_r_slow': _col('avg_r_slow'),
        'fast_return': _col('r_fast'), 'slow_return': _col('r_slow'), 'ts_fast': ts_fast, 'ts_slow': ts_slow,
        'r1y_pct': _col('r1y_pct'),
        # Goulding audit fields -- None in 'continuous' mode (nothing to
        # report), populated in 'goulding' mode so a saved trend_signals
        # CSV shows exactly what drove that rebalance's direction: this
        # rebalance's cluster's own a_Co/a_Re as of this date, the raw
        # g_fast/g_slow/g_regime inputs _goulding_direction blended, and
        # g_blend itself -- the raw pre-sign eq. 7 value (resolve_trend_
        # direction's own 4th return, via _goulding_direction), None in
        # Bull/Bear (eq. 7 doesn't apply there) even though trend_strength
        # still resolves in that case.
        'g_regime': g_regime_val, 'g_fast': g_fast_val, 'g_slow': g_slow_val,
        'g_blend': g_blend, 'a_co': a_co, 'a_re': a_re,
        'g_signal_mode': config.goulding_signal_mode if config.signal_weighting == 'goulding' else None,
        'g_raw_forecast': g_raw_forecast,
        'g_forecast_scalar': goulding_forecast_scalar,
        'ewmac_raw': _col('raw_ewmac'),
        'ewmac_raw_forecast': _col('raw_forecast'),
        'ewmac_point_vol': _col('point_vol'),
        'ewmac_forecast_scalar': _col('forecast_scalar'),
        'ewmac_scalar_pool': _col('pool_key'),
        'ewmac_forecast': _col('ewmac_forecast'),
        'signal_class': config.signal_weighting,
        'source_segment': _col('source_segment'),
        'pnl_quality': _col('pnl_quality'),
    }


def _mixing_params_for_date(config: TsmomBacktestConfig, monthly_history: Optional[pl.DataFrame],
                             d: date) -> dict[str, tuple[float, float]]:
    """{cluster: (a_co, a_re)} as of rebalance date `d` -- estimated once
    per rebalance date (shared across every symbol in that cluster), not
    once per symbol. Empty dict (never read) outside 'goulding' mode."""
    if config.signal_weighting != 'goulding':
        return {}
    clusters_needed = {get_spec(s)['cluster'] for s in config.symbols}
    if config.mixing_pool == 'cluster':
        return {c: estimate_mixing_params(monthly_history, d, c) for c in clusters_needed}
    global_params = estimate_mixing_params(monthly_history, d, None)
    return {c: global_params for c in clusters_needed}


def _goulding_kwargs_for(config: TsmomBacktestConfig, rebal_monthly: dict[str, pl.DataFrame],
                         symbol: str, d: date, mixing_params_by_cluster: dict[str, tuple[float, float]],
                         goulding_forecast_scalar: Optional[float] = None) -> dict:
    """This symbol's own g_regime_val/g_fast_val/g_slow_val/a_co/a_re as of
    `d`, ready to **-unpack straight into _compute_signal_row. Empty dict
    outside 'goulding' mode -- _compute_signal_row's own defaults (all
    None) then apply, matching its 'continuous'-mode behaviour exactly."""
    if config.signal_weighting != 'goulding':
        return {}
    g_row = rebal_monthly[symbol].filter(pl.col('ts_event') == d)
    g_regime_val = g_row['g_regime'][0] if g_row.height else None
    g_fast_val = g_row['g_fast'][0] if g_row.height else None
    g_slow_val = g_row['g_slow'][0] if g_row.height else None
    a_co, a_re = mixing_params_by_cluster.get(get_spec(symbol)['cluster'], (0.5, 0.5))
    return {'g_regime_val': g_regime_val, 'g_fast_val': g_fast_val, 'g_slow_val': g_slow_val,
            'a_co': a_co, 'a_re': a_re,
            'goulding_forecast_scalar': goulding_forecast_scalar}


def _goulding_forecast_scalar_for_date(
    config: TsmomBacktestConfig,
    monthly_by_symbol: dict[str, pl.DataFrame],
    d: date,
    mixing_params_by_cluster: dict[str, tuple[float, float]],
) -> Optional[float]:
    """Causal pooled Goulding scalar using forecast rows known before `d`.

    At a month-end rebalance the next month's current forecast has a label
    after `d`, so `ts_event <= d` contains prior forecasts only. Current
    a_Co/a_Re parameters are applied to that prior forecast history, just as
    a model calibration applies its currently estimated rule to training
    observations. The current forecast itself never enters its own scale.
    """
    if config.signal_weighting != 'goulding' or config.goulding_signal_mode != 'continuous':
        return None
    raw_forecasts = []
    for symbol, monthly in monthly_by_symbol.items():
        a_co, a_re = mixing_params_by_cluster.get(get_spec(symbol)['cluster'], (0.5, 0.5))
        history = monthly.filter(pl.col('ts_event') <= d)
        for row in history.iter_rows(named=True):
            raw_forecasts.append(goulding_continuous_raw(
                row.get('regime'), a_co, a_re, row.get('fast'), row.get('slow'),
            ))
    return estimate_goulding_forecast_scalar(raw_forecasts)


def _ew_notional_budget(config: TsmomBacktestConfig, capital: float) -> float:
    """One configured symbol's 1/N gross-notional share, before lot rounding."""
    equal_share = capital / len(config.symbols) if config.symbols else 0.0
    return min(config.max_notional, equal_share)


def _select_cluster_cap_universe(probe_results: dict[str, dict], active_symbols: list[str],
                                 config: TsmomBacktestConfig) -> tuple[set[str], dict[str, int], dict[str, float]]:
    """Keep the top raw-conviction active signals per cluster, if requested.

    This is intentionally performed on probe results before IDM/ERC: an
    excluded symbol must not enter the correlation matrix or consume any of
    the portfolio dollar-vol budget. The score is model evidence only, not
    a post-vol-targeting scalar.
    """
    by_cluster: dict[str, list[str]] = {}
    score_by_symbol: dict[str, float] = {}
    for symbol in active_symbols:
        result = probe_results[symbol]
        score_by_symbol[symbol] = cluster_conviction_score(config.signal_weighting, result)
        by_cluster.setdefault(result['cluster'], []).append(symbol)

    if config.max_active_per_cluster is None:
        return set(active_symbols), {}, score_by_symbol

    selected: set[str] = set()
    rank_by_symbol: dict[str, int] = {}
    for cluster, members in sorted(by_cluster.items()):
        ranked = sorted(members, key=lambda symbol: (-score_by_symbol[symbol], symbol))
        for rank, symbol in enumerate(ranked, start=1):
            rank_by_symbol[symbol] = rank
            if rank <= config.max_active_per_cluster:
                selected.add(symbol)
        logger.info(
            'Cluster-cap universe: cluster=%s max_active=%d ranked=[%s] selected=[%s]',
            cluster, config.max_active_per_cluster,
            ', '.join(f'{symbol} score={score_by_symbol[symbol]:.4f}' for symbol in ranked),
            ', '.join(ranked[:config.max_active_per_cluster]) or 'none',
        )
    return selected, rank_by_symbol, score_by_symbol


class _PortfolioLedger:
    """Owns every piece of run_tsmom_backtest's own mutable portfolio
    state (capital, held contracts, last-seen close, open trade spans,
    and the events/transactions/trades logs) plus every mutation of it
    (mark-to-market, rebalance, roll, close). Previously these were five
    separate closures (_close_trade/_rebalance_to/_process_roll plus the
    plain dicts/lists they closed over via `nonlocal`) defined inline
    inside run_tsmom_backtest, ahead of the day loop -- readable in
    isolation but, taken together with the goulding-mixing helpers, over
    200 lines a reader had to scroll past before reaching the loop itself.
    Pulling them out here lets the day loop read as a sequence of named
    steps against `ledger` instead."""

    def __init__(self, symbols: list[str], initial_capital: float, futures_types: dict[str, dict]):
        self.futures_types = futures_types
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.held_contracts: dict[str, int] = {s: 0 for s in symbols}
        self.prior_close: dict[str, Optional[float]] = {s: None for s in symbols}
        # Per-symbol open trade accumulator: None when flat, else a dict
        # tracking the currently-open span's entry info plus running
        # realized MTM pnl/fees, closed out (appended to `trades`) on a
        # return to flat, a direct sign flip, or a final force-close after
        # the day loop ends.
        self.open_trade: dict[str, Optional[dict]] = {s: None for s in symbols}
        self.events: list[dict] = []
        self.transactions: list[dict] = []
        self.trades: list[dict] = []

    def mark_to_market(self, symbol: str, close: float) -> None:
        """Today's close vs yesterday's, the same diff-based daily MTM
        approach Backtester.calculate_futures_mtm_drawdown already uses
        for single-symbol. No-op on capital/open_trade the first time a
        symbol is seen (prior_close still None) -- prior_close is still
        recorded so the NEXT call has something to diff against."""
        if self.prior_close[symbol] is not None and self.held_contracts[symbol] != 0:
            day_pnl = (self.held_contracts[symbol] * (close - self.prior_close[symbol])
                       * self.futures_types[symbol]['multiplier'])
            self.capital += day_pnl
            ot = self.open_trade[symbol]
            if ot is not None:
                ot['mtm_pnl'] += day_pnl
        self.prior_close[symbol] = close

    def close_trade(self, symbol: str, exit_date: date, exit_price: Optional[float]) -> None:
        ot = self.open_trade[symbol]
        if ot is None:
            return
        net_pnl = round(ot['mtm_pnl'] - ot['fees'], 2)
        self.trades.append({
            'symbol': symbol, 'direction': ot['direction'],
            'entry_date': ot['entry_date'], 'entry_price': _round(ot['entry_price'], _PRICE_ROUND_NDIGITS),
            'exit_date': exit_date, 'exit_price': _round(exit_price, _PRICE_ROUND_NDIGITS),
            'days_held': (exit_date - ot['entry_date']).days,
            'max_contracts': ot['max_contracts'],
            # Total contracts shed via MID-TRADE resizes (same-direction
            # downsizes), NOT counting the final close itself -- 0 whenever
            # the position was held at a constant size its whole life.
            # Exists specifically so entry_price/exit_price/max_contracts
            # alone don't invite a naive (exit-entry)*max_contracts*mult
            # sanity check that silently overstates PnL whenever the
            # position was actually smaller for part of its life (confirmed
            # directly: MZW opened at 7, resized down to 1 partway through,
            # and the naive full-max_contracts calc overstated the real PnL
            # by exactly the exposure lost in that resize) -- a nonzero
            # value here is a direct signal that max_contracts wasn't held
            # the entire time, so a manual check needs transactions.csv's
            # own resize-by-resize history, not this summary row alone.
            'lots_closed_pre_exit': ot['lots_closed_pre_exit'],
            'fees': round(ot['fees'], 2),
            'pnl': net_pnl, 'close_reason': ot['close_reason'],
            'entry_source_segment': ot.get('source_segment'),
            'entry_pnl_quality': ot.get('pnl_quality'),
        })
        self.open_trade[symbol] = None

    def rebalance_to(self, symbol: str, target: int, rebalance_date: date, vol_regime,
                      vix_close=None, vix_ratio=None, signal: Optional[dict] = None, is_seed=False) -> None:
        """`signal` is _compute_signal_row's full result dict (or None on a
        spike/extreme-gated event, where signal computation is skipped
        entirely -- every signal-derived field below is then None, same
        as before)."""
        s = signal or {}
        prior = self.held_contracts[symbol]
        fee = 0.0
        if target != prior:
            # Commission is charged only on the quantity actually closed out
            # -- opening a position, or adding to one, is free (matches
            # FuturesPosition.calculate_pnl / _process_roll's convention:
            # fees = commission * 2 * quantity, charged entirely at close).
            # A flip closes the *entire* prior side (the new opposite-
            # direction open that follows is then free, same as any other
            # open); a same-direction resize only charges for the portion
            # that shrinks back toward zero, not the portion added.
            if prior == 0:
                closed_qty = 0
            elif target == 0 or (prior > 0) != (target > 0):
                closed_qty = abs(prior)
            else:
                closed_qty = max(0, abs(prior) - abs(target))
            fee = self.futures_types[symbol]['commission'] * 2 * closed_qty
            self.capital -= fee
            price = s.get('close')
            self.transactions.append({
                'symbol': symbol, 'date': rebalance_date,
                'action': 'buy' if target > prior else 'sell',
                'quantity': abs(target - prior), 'price': _round(price, _PRICE_ROUND_NDIGITS),
                'fee': round(fee, 2), 'prior_contracts': prior, 'target_contracts': target,
                'gate_reason': s.get('gate_reason'), 'is_seed': is_seed,
                'source_segment': s.get('source_segment'),
                'pnl_quality': s.get('pnl_quality'),
                'signal_class': s.get('signal_class'),
            })

            flipped = prior != 0 and target != 0 and (prior > 0) != (target > 0)
            if flipped or target == 0:
                # This transaction's fee is the closing leg's cost alone (the
                # whole prior side on a flip -- the new opposite-direction
                # open that follows is free, same as any other open) --
                # must be folded in before close_trade reads ot['fees'], or
                # it's silently dropped from the trade's own total (portfolio
                # capital stays correct regardless, since that deduction
                # already happened above; only the per-trade fees/pnl
                # fields were at risk of undercounting).
                ot = self.open_trade[symbol]
                if ot is not None:
                    ot['fees'] += fee
                self.close_trade(symbol, rebalance_date, price)
            ot = self.open_trade[symbol]  # re-read post-close: close_trade above may have just cleared it
            if target != 0 and ot is None:
                self.open_trade[symbol] = {
                    'entry_date': rebalance_date, 'entry_price': price,
                    'direction': 'long' if target > 0 else 'short',
                    'max_contracts': abs(target), 'mtm_pnl': 0.0,
                    'fees': 0.0 if flipped else fee,
                    'close_reason': None, 'lots_closed_pre_exit': 0,
                    'source_segment': s.get('source_segment'),
                    'pnl_quality': s.get('pnl_quality'),
                }
            elif target != 0 and ot is not None:
                # Resize within the same direction -- extend the existing
                # span rather than starting a new trade; fold in this
                # resize's own fee (zero unless this shrank toward zero),
                # track the largest size held, and accumulate any quantity
                # shed by a downsize (closed_qty, computed above) into
                # lots_closed_pre_exit -- see close_trade's own comment for
                # why this is tracked separately from max_contracts.
                ot['fees'] += fee
                ot['max_contracts'] = max(ot['max_contracts'], abs(target))
                ot['lots_closed_pre_exit'] += closed_qty
            if (flipped or target == 0) and s.get('gate_reason'):
                # close_trade already ran above and cleared open_trade;
                # the reason belongs on the trade that just closed, so
                # patch the just-appended row rather than re-opening it.
                self.trades[-1]['close_reason'] = s.get('gate_reason')

        self.held_contracts[symbol] = target
        self.events.append({
            'date': rebalance_date, 'symbol': symbol,
            'close': _round(s.get('close'), _PRICE_ROUND_NDIGITS), 'peak': _round(s.get('peak'), _PRICE_ROUND_NDIGITS),
            'dd_pct': _round(s.get('dd_pct'), 2),
            'avg_r_fast': _round(s.get('avg_r_fast'), 4), 'avg_r_slow': _round(s.get('avg_r_slow'), 4),
            'fast_return': _round(s.get('fast_return'), 4), 'slow_return': _round(s.get('slow_return'), 4),
            'ts_fast': _round(s.get('ts_fast'), 4), 'ts_slow': _round(s.get('ts_slow'), 4),
            'signal': _round(s.get('signal'), 4), 'r1y_pct': _round(s.get('r1y_pct'), 2),
            'regime': s.get('regime'), 'vix_close': _round(vix_close, 2), 'vix_ratio': _round(vix_ratio, 4),
            'vol_regime': vol_regime, 'hv': _round(s.get('hv'), 4), 'risk_scalar': _round(s.get('risk_scalar'), 4),
            'regime_discount': _round(s.get('regime_discount'), 2),
            'prior_contracts': prior, 'target_contracts': target, 'is_seed': is_seed,
            'gate_reason': s.get('gate_reason'),
            # Goulding audit fields -- None in 'continuous' mode (nothing to
            # report; _compute_signal_row's own result dict already carries
            # None for all of these there), populated in 'goulding' mode.
            # Previously computed by _compute_signal_row but never actually
            # threaded through to this event dict, so a saved trend_signals
            # CSV in goulding mode silently never showed what drove a given
            # rebalance's direction, despite _compute_signal_row's own
            # docstring promising exactly that.
            'g_regime': s.get('g_regime'), 'g_fast': _round(s.get('g_fast'), 4),
            'g_slow': _round(s.get('g_slow'), 4),
            'a_co': _round(s.get('a_co'), 4), 'a_re': _round(s.get('a_re'), 4),
            'g_blend': _round(s.get('g_blend'), 4),
            'g_signal_mode': s.get('g_signal_mode'),
            'g_raw_forecast': _round(s.get('g_raw_forecast'), 6),
            'g_forecast_scalar': _round(s.get('g_forecast_scalar'), 6),
            'ewmac_raw': _round(s.get('ewmac_raw'), 6),
            'ewmac_raw_forecast': _round(s.get('ewmac_raw_forecast'), 6),
            'ewmac_point_vol': _round(s.get('ewmac_point_vol'), 6),
            'ewmac_forecast_scalar': _round(s.get('ewmac_forecast_scalar'), 6),
            'ewmac_scalar_pool': s.get('ewmac_scalar_pool'),
            'ewmac_forecast': _round(s.get('ewmac_forecast'), 6),
            'signal_class': s.get('signal_class'),
            'source_segment': s.get('source_segment'),
            'pnl_quality': s.get('pnl_quality'),
            'fractional_target_contracts': _round(s.get('fractional_target_contracts'), 4),
            'fractional_target_notional': _round(s.get('fractional_target_notional'), 2),
            'pre_scalar_notional_budget': _round(s.get('pre_scalar_notional_budget'), 2),
            'notional_weighting': s.get('notional_weighting'),
            'allocation_mode': s.get('allocation_mode'),
            'notional_allocation_weight': _round(s.get('notional_allocation_weight'), 6),
            'combined_scalar': _round(s.get('combined_scalar'), 6),
            'vix_scalar': _round(s.get('vix_scalar'), 4),
            'ts': _round(s.get('ts'), 4), 'contin_signal': _round(s.get('contin_signal'), 4),
            'ts_regime': s.get('ts_regime'), 'daily_std': _round(s.get('daily_std'), 6),
            'cluster': s.get('cluster'), 'mult': _round(s.get('mult'), 6),
            'cluster_universe_rank': s.get('cluster_universe_rank'),
            'cluster_universe_score': _round(s.get('cluster_universe_score'), 6),
            'cluster_universe_excluded': s.get('cluster_universe_excluded', False),
            'infeasible': s.get('infeasible', False),
            'portfolio_risk_target': _round(s.get('portfolio_risk_target'), 2),
            'idm_risk_target': _round(s.get('idm_risk_target'), 2),
            'realized_portfolio_risk': _round(s.get('realized_portfolio_risk'), 2),
            'idm_multiplier': _round(s.get('idm_multiplier'), 6),
            'portfolio_risk_contribution': _round(s.get('portfolio_risk_contribution'), 2),
            # Portfolio-level capital snapshot as of this event (after
            # today's mark-to-market and this event's own commission fee,
            # both already applied above) -- previously only available in
            # the separate daily `stats` table (keyed by date only, not
            # per-symbol), so reading an event required cross-referencing
            # a different table by date to see its $ context.
            'capital': round(self.capital, 2),
            'cum_pnl': round(self.capital - self.initial_capital, 2),
        })

    def process_roll(
        self, symbol: str, roll_date: date, execution_price: Optional[float] = None,
        source_segment: Optional[str] = None, pnl_quality: Optional[str] = None,
    ) -> None:
        """Mandatory quarterly contract roll for a currently-held symbol:
        close the expiring contract (full round-trip commission on its own
        quantity, close_reason='roll') and immediately reopen the identical
        size under the new contract at the same price -- net zero PnL/size
        effect, cost is the fee alone. Mirrors FuturesPosition.close()
        (position.py:1785-1877): a roll is a mechanical consequence of the
        contract's own expiration, not a signal decision, so it fires
        regardless of signal_gate_mode/fixed_quantities and doesn't touch
        held_contracts or go through rebalance_to (which would incorrectly
        charge a second commission for the "reopen" leg -- opening a
        position is free in this fee model, only closing charges, exactly
        as in FuturesPosition.calculate_pnl)."""
        prior = self.held_contracts[symbol]
        if prior == 0:
            return
        price = execution_price if execution_price is not None else self.prior_close[symbol]
        fee = self.futures_types[symbol]['commission'] * 2 * abs(prior)
        self.capital -= fee
        self.transactions.append({
            'symbol': symbol, 'date': roll_date, 'action': 'roll',
            'quantity': abs(prior), 'price': _round(price, _PRICE_ROUND_NDIGITS),
            'fee': round(fee, 2), 'prior_contracts': prior, 'target_contracts': prior,
            'gate_reason': None, 'is_seed': False,
            'source_segment': source_segment, 'pnl_quality': pnl_quality,
            'signal_class': None,
        })
        ot = self.open_trade[symbol]
        if ot is not None:
            ot['fees'] += fee
            ot['close_reason'] = 'roll'
            self.close_trade(symbol, roll_date, price)
        self.open_trade[symbol] = {
            'entry_date': roll_date, 'entry_price': price,
            'direction': 'long' if prior > 0 else 'short',
            'max_contracts': abs(prior), 'mtm_pnl': 0.0, 'lots_closed_pre_exit': 0,
            'fees': 0.0, 'close_reason': None,
            'source_segment': source_segment, 'pnl_quality': pnl_quality,
        }


def run_tsmom_backtest(config: TsmomBacktestConfig) -> dict:
    """Runs the monthly-rebalance TSMOM backtest. Returns a dict with
    'daily_mtm' (daily portfolio capital/drawdown, polars DataFrame -- same
    key name as the naked single-position path's Backtester.run() result),
    'trend_signals' (per-rebalance trend/signal diagnostic log: ts_fast, ts_slow,
    regime, risk_scalar, regime_discount, gate_reason, etc., list of
    dicts), 'transactions' (one row per
    rebalance that actually changed a symbol's contract count -- what was
    bought/sold, when, at what price/fee), and 'trades' (reconstructed
    round-trips: TSMOM has no discrete open/close lifecycle like
    FuturesPosition -- a symbol's exposure is continuously resized, not
    "opened then closed" -- so a trade here is defined as one continuous
    span of nonzero exposure in a single direction: 0->nonzero opens it,
    nonzero->0 or a direct sign flip closes it; resizing within the same
    direction extends the same trade rather than starting a new one. The
    quarterly contract roll is the one exception forced regardless of
    exposure direction: a held span is always closed and immediately
    reopened at each scheduled roll date (close_reason='roll'), same as
    FuturesPosition's own roll_date handling, so 'trades' never reports a
    holding period spanning more than one actual futures contract even
    when the signal itself never triggers a close)."""
    # `full_price_data` stays unbounded -- continuous_momentum's 252-day
    # lookback needs real history before config.start_date, not just
    # whatever falls inside the requested window. Only the iterated date
    # range (and what counts as a rebalance/MTM date) is bounded.
    full_price_data, vix, data_manifest = _load_backtest_data(config)
    vix = _compute_vix_regime_series(vix, config.vix_ma_window_days)
    # Real trading-days/year per symbol (instruments.resolve_annualization_days)
    # -- this project's confirmed universe splits 252 (CBOT grains) vs. 259
    # (everything else checked, post Sunday-session-merge fix); anything
    # unconfirmed falls back to 252 (DEFAULT_ANNUALIZATION_DAYS), unchanged
    # from this module's own prior universal-252 behavior.
    annualization_by_symbol = {s: resolve_annualization_days(s) for s in config.symbols}
    if config.signal_weighting == 'carver_ewmac':
        (
            ewmac_by_symbol,
            ewmac_scalar_history,
            ewmac_instrument_coverage,
        ) = _precompute_ewmac_normalization(
            full_price_data, config, data_manifest
        )
    else:
        ewmac_by_symbol, ewmac_scalar_history, ewmac_instrument_coverage = (
            {}, pl.DataFrame(), pl.DataFrame()
        )
    # Precomputed once per symbol, unconditionally (not just for
    # signal_gate_mode == 'daily') -- see _compute_signal_row's own
    # docstring for why this is exactly equivalent to (and much cheaper
    # than) recomputing continuous_momentum fresh at every rebalance.
    precomputed = {
        s: _precompute_signal(
            full_price_data[s].sort('ts_event'), config, annualization_by_symbol[s],
            ewmac=ewmac_by_symbol.get(s),
        )
        for s in config.symbols
    }
    futures_types = {s: get_spec(s) for s in config.symbols}
    # Built once (reusing full_price_data already loaded above), reused at
    # every rebalance date's own bounded-window slice -- only when
    # target_portfolio_vol is actually set, since this is an extra
    # inner-join + pct_change pass over every symbol's full history that
    # the module's original (default) sizing has no use for.
    returns_wide = (
        build_returns_wide({s: _return_signal_bars(df) for s, df in full_price_data.items()})
        if config.allocation_mode == 'risk-targeted' and config.target_portfolio_vol is not None
        else None
    )

    windowed = {}
    for symbol, df in full_price_data.items():
        if config.start_date:
            df = df.filter(pl.col('ts_event') >= config.start_date)
        if config.end_date:
            df = df.filter(pl.col('ts_event') <= config.end_date)
        windowed[symbol] = df.sort('ts_event')

    all_dates = sorted(set().union(*(set(df['ts_event'].to_list()) for df in windowed.values())))
    # A position opened on the very last date in the window has zero
    # subsequent days to mark to market -- it shows up as a pure commission
    # cost with no chance of P&L, which is misleading rather than meaningful.
    # Mirrors Backtester's own bounded-window handling (backtester.py:152-160)
    # in spirit: don't let the backtest take an action it can't show the
    # result of within the requested range.
    rebalance_dates = _month_end_dates(windowed) - ({all_dates[-1]} if all_dates else set())
    # Real per-symbol roll dates -- every date THIS symbol's own continuous
    # series actually switches contracts (volume-ranked/sticky crossover,
    # see _detect_roll_dates), not a single fixed calendar schedule shared
    # uniformly across every symbol regardless of its real roll cadence.
    # Computed from full_price_data (unbounded history), not windowed, so
    # the window's own start date can't masquerade as a false "roll" -- see
    # _detect_roll_dates' own docstring. active_months (when confirmed) is
    # consulted only as a validation guard on the detected dates here, not
    # to generate them.
    roll_dates_by_symbol: dict[str, set[date]] = {
        s: set(_detect_roll_dates(full_price_data[s], all_dates[0], all_dates[-1],
                                   resolve_active_months(s), s))
        for s in config.symbols
    } if all_dates else {s: set() for s in config.symbols}

    # Precomputed once, only when signal_weighting == 'goulding' -- Goulding's
    # own genuine calendar-month Bull/Correction/Bear/Rebound classification
    # (goulding_monthly), forward-matched to each rebalance date (a rebalance
    # on month-end date `d` decides what to hold GOING FORWARD, i.e. during
    # the NEXT month, so it needs the NEXT month's own bucket -- computed
    # from the just-completed month's data -- not the bucket already in
    # effect on `d` itself; strategy='forward' finds the first monthly label
    # >= d, which is always exactly that next month's bucket since a rebal
    # date always falls strictly inside its own month), plus the pooled,
    # expanding-window a_Co/a_Re estimation history built from every
    # symbol's forward-matched buckets. Mirrors
    # tsmom_binary_vol_parity_backtest.py's own construction (see that
    # script's run() for the fuller rationale), reusing domain/signal.py's
    # shared build_monthly_state_return_history/estimate_mixing_params
    # instead of a duplicate implementation.
    rebal_monthly: dict[str, pl.DataFrame] = {}
    monthly_by_symbol: dict[str, pl.DataFrame] = {}
    monthly_history: Optional[pl.DataFrame] = None
    if config.signal_weighting == 'goulding':
        rebal_dates_sorted = sorted(rebalance_dates)
        rebal_dates_df = pl.DataFrame({'ts_event': rebal_dates_sorted}).sort('ts_event')
        for s in config.symbols:
            feat = build_features(_return_signal_bars(full_price_data[s]))
            monthly = goulding_monthly(feat, **SignalSpec.goulding().goulding_kwargs())
            monthly_by_symbol[s] = monthly
            monthly = monthly.rename({'fast': 'g_fast', 'slow': 'g_slow', 'regime': 'g_regime'})
            monthly = monthly.select(['ts_event', 'ret', 'g_fast', 'g_slow', 'g_regime']).sort('ts_event')
            rebal_monthly[s] = rebal_dates_df.join_asof(monthly, on='ts_event', strategy='forward')
        cluster_by_symbol = {s: get_spec(s)['cluster'] for s in config.symbols}
        monthly_history = build_monthly_state_return_history(rebal_monthly, rebal_dates_sorted, cluster_by_symbol)

    ledger = _PortfolioLedger(config.symbols, config.initial_capital, futures_types)
    daily_rows = []

    # Seed the position from the last completed month-end *before*
    # start_date, using full unbounded history -- otherwise the backtest
    # starts flat and wastes its entire first calendar month sitting in
    # cash even when a perfectly valid prior-month signal already called
    # for a position, only entering at the window's first in-range
    # month-end. This makes start_date behave like "continuing an
    # already-running strategy," not "day one of trading."
    #
    # Deliberately NOT wired up for signal_weighting == 'goulding' (no
    # _goulding_kwargs_for call below, same scope limitation
    # target_portfolio_vol's own docstring already documents for this
    # block) -- seed_date falls strictly before config.start_date, outside
    # rebal_monthly's own forward-matched date range (built only over the
    # windowed rebalance_dates), so g_regime_val would always be missing
    # here regardless. Rather than extend the goulding precompute to cover
    # a date range it doesn't otherwise need, 'goulding' mode simply starts
    # flat (no pre-existing seed position) and takes its first real
    # position at the window's first in-range monthly rebalance instead --
    # a real gap, not a crash, and confirmed not to affect anything past
    # the first month or two of any reasonably long backtest.
    if config.start_date:
        prior_month_ends = [d for d in _month_end_dates(full_price_data) if d < config.start_date]
        if prior_month_ends:
            seed_date = max(prior_month_ends)
            vol_regime, vix_close, vix_ratio = _vix_regime_at(vix, seed_date)
            if not config.vix_gating or config.allocation_mode == 'ew':
                vol_regime = VolRegime.NORMAL  # vix_close/vix_ratio still logged, just not acted on
            if vol_regime not in (VolRegime.SPIKE, VolRegime.EXTREME):  # held_contracts are all 0 here -- hold/halve would be a no-op anyway
                vix_scalar = VIX_ELEVATED_SCALE if vol_regime == VolRegime.ELEVATED else 1.0
                for symbol in config.symbols:
                    result = _compute_signal_row(symbol, precomputed, seed_date, futures_types, config,
                                                  vix_scalar, annualization_by_symbol[symbol],
                                                  notional_budget=(
                                                      _ew_notional_budget(config, ledger.capital)
                                                      if config.allocation_mode == 'ew' else None
                                                  ))
                    if result is None:
                        continue
                    target, gate_reason = _apply_signal_gate(ledger.held_contracts[symbol], result['target'], result, config)
                    ledger.rebalance_to(symbol, target, seed_date, vol_regime, vix_close=vix_close,
                                        vix_ratio=vix_ratio, signal={**result, 'gate_reason': gate_reason}, is_seed=True)
                    if ledger.held_contracts[symbol] != 0:
                        # Confirmed bug fix (2026-07): without this,
                        # prior_close[symbol] stays None going into the day
                        # loop below, whose own "1. Mark existing holdings"
                        # step skips day 1's mark-to-market entirely when
                        # prior_close is None and only sets prior_close
                        # AFTER that skip, to the first IN-WINDOW day's own
                        # close -- silently dropping the ENTIRE
                        # seed_date -> first-in-window-day price move from
                        # PnL, while the trade record's own entry_price
                        # still (correctly) shows the seed's real price.
                        # Confirmed directly: a seeded MES long recorded
                        # entry 3748.75 -> exit 3696.5 (a real
                        # -52.25pt/-$783.75 loss on 3 contracts) but
                        # reported pnl=+$45.09 -- exactly the number that
                        # results from silently substituting the first
                        # trading day's close as the effective entry price
                        # instead of the seed's own. result['close'] here is
                        # the SAME value rebalance_to just used as this
                        # trade's entry_price (signal={**result, ...} ->
                        # s.get('close')), so this guarantees they agree.
                        ledger.prior_close[symbol] = result.get('pnl_close', result['close'])

    for d in all_dates:
        # 1. Mark existing holdings to market: today's close vs yesterday's,
        # the same diff-based daily MTM approach Backtester.
        # calculate_futures_mtm_drawdown already uses for single-symbol.
        for symbol in config.symbols:
            row = windowed[symbol].filter(pl.col('ts_event') == d)
            if row.height == 0:
                continue
            ledger.mark_to_market(symbol, row['pnl_close'][0])

        # 1.25. Mandatory per-symbol contract roll -- unconditional (not
        # gated by signal_gate_mode/fixed_quantities), since it's a
        # mechanical consequence of the contract's own expiration, exactly
        # like the naked path's FuturesPosition.roll_date. Each symbol rolls
        # on its OWN detected real crossover dates (roll_dates_by_symbol --
        # see _detect_roll_dates), not a single calendar schedule shared
        # across every symbol: a plain set-membership check is enough here
        # (no monotonic pointer needed) since each symbol's own set is
        # already restricted to real dates present in its own continuous
        # series.
        for symbol in config.symbols:
            if d in roll_dates_by_symbol[symbol]:
                mark_row = windowed[symbol].filter(pl.col('ts_event') == d)
                execution_price = (
                    float(mark_row['close'][0]) if mark_row.height else None
                )
                ledger.process_roll(
                    symbol,
                    d,
                    execution_price,
                    source_segment=(mark_row['source_segment'][0] if mark_row.height else None),
                    pnl_quality=(mark_row['pnl_quality'][0] if mark_row.height else None),
                )

        # 1.5. Daily signal-gate check (signal_gate_mode == 'daily' only),
        # off-cycle from the monthly resize below -- BOTH entry and exit,
        # not exit-only: a currently-held symbol can only be flattened here
        # (its own resize/magnitude stays monthly-only in both modes, so a
        # weakening vol-targeted position doesn't get continuously
        # rebalanced mid-month just because this loop now also checks
        # daily); a currently-flat symbol can open the very day its entry
        # gate first clears, instead of waiting for month-end. Skipped on
        # rebalance_dates themselves since the monthly block below already
        # re-evaluates the same gate that day (avoids a duplicate event).
        # VIX spike/extreme hold-or-halve intentionally stays a monthly-
        # only mechanism -- not extended to off-cycle days here.
        if (config.allocation_mode != 'ew'
                and config.signal_gate_mode == 'daily' and d not in rebalance_dates):
            vol_regime_d, vix_close_d, vix_ratio_d = _vix_regime_at(vix, d)
            if not config.vix_gating or config.allocation_mode == 'ew':
                vol_regime_d = VolRegime.NORMAL
            vix_scalar_d = VIX_ELEVATED_SCALE if vol_regime_d == VolRegime.ELEVATED else 1.0
            mixing_params_by_cluster_d = _mixing_params_for_date(config, monthly_history, d)
            goulding_forecast_scalar_d = _goulding_forecast_scalar_for_date(
                config, monthly_by_symbol, d, mixing_params_by_cluster_d,
            )

            for symbol in config.symbols:
                prior = ledger.held_contracts[symbol]
                result = _compute_signal_row(symbol, precomputed, d, futures_types, config,
                                              vix_scalar_d, annualization_by_symbol[symbol],
                                              notional_budget=(
                                                  _ew_notional_budget(config, ledger.capital)
                                                  if config.allocation_mode == 'ew' else None
                                              ),
                                              **_goulding_kwargs_for(
                                                  config, rebal_monthly, symbol, d,
                                                  mixing_params_by_cluster_d, goulding_forecast_scalar_d,
                                              ))
                if result is None:
                    continue

                if prior != 0:
                    is_long = prior > 0
                    reason = _signal_gate_reason(result['signal'], result['ts_fast'], result['ts_slow'], is_long,
                                                  config.ts_exit_threshold, config.exit_on_ts_crossover)
                    if reason is not None:
                        ledger.rebalance_to(symbol, 0, d, vol_regime_d, vix_close=vix_close_d, vix_ratio=vix_ratio_d,
                                            signal={**result, 'gate_reason': reason})
                elif result['target'] != 0:
                    target, gate_reason = _apply_signal_gate(0, result['target'], result, config)
                    if target != 0:
                        ledger.rebalance_to(symbol, target, d, vol_regime_d, vix_close=vix_close_d,
                                            vix_ratio=vix_ratio_d, signal={**result, 'gate_reason': gate_reason})

        # 2. On rebalance dates, resize toward the vol-targeted signal,
        # gated by the spot-VIX regime (mirrors
        # tsmom_rebalance.compute_rebalance_targets' early-return shape).
        if d in rebalance_dates:
            vol_regime, vix_close, vix_ratio = _vix_regime_at(vix, d)
            if not config.vix_gating or config.allocation_mode == 'ew':
                vol_regime = VolRegime.NORMAL  # vix_close/vix_ratio still logged, just not acted on

            if vol_regime in (VolRegime.SPIKE, VolRegime.EXTREME):
                for symbol in config.symbols:
                    prior = ledger.held_contracts[symbol]
                    target = round(prior / 2) if vol_regime == VolRegime.EXTREME else prior
                    close_row = full_price_data[symbol].filter(pl.col('ts_event') <= d).tail(1)
                    close = float(close_row['close'][0]) if close_row.height > 0 else None
                    ledger.rebalance_to(symbol, target, d, vol_regime, vix_close=vix_close, vix_ratio=vix_ratio,
                                        signal={
                                            'close': close,
                                            'pnl_close': (
                                                close_row['pnl_close'][0] if close_row.height else None
                                            ),
                                            'source_segment': (
                                                close_row['source_segment'][0] if close_row.height else None
                                            ),
                                            'pnl_quality': (
                                                close_row['pnl_quality'][0] if close_row.height else None
                                            ),
                                            'signal_class': config.signal_weighting,
                                        })
            else:
                vix_scalar = VIX_ELEVATED_SCALE if vol_regime == VolRegime.ELEVATED else 1.0
                # Computed once per rebalance date, shared by both branches
                # below -- a_Co/a_Re only needs estimating once per date,
                # not once per symbol or per branch.
                mixing_params_by_cluster = _mixing_params_for_date(config, monthly_history, d)
                goulding_forecast_scalar = _goulding_forecast_scalar_for_date(
                    config, monthly_by_symbol, d, mixing_params_by_cluster,
                )

                if (config.allocation_mode == 'risk-targeted'
                        and config.target_portfolio_vol is not None
                        and config.fixed_quantities is None):
                    # Correlation-aware sizing -- see TsmomBacktestConfig.
                    # target_portfolio_vol's own docstring for the full
                    # derivation. Two passes are needed because "which
                    # symbols are active" (and therefore n_effective/the
                    # correlation matrix/IDM) can only be known AFTER
                    # computing everyone's own scalar, but the FINAL target
                    # for those active symbols depends on the IDM-derived
                    # budget computed FROM that same active set -- a single
                    # pass can't do both in one order.
                    #
                    # Pass 1 (probe): config.max_notional stands in as a
                    # placeholder budget purely to get each symbol's own
                    # `scalar` (and other signal fields) -- never used for
                    # the FINAL target of an active symbol, only to decide
                    # who's active (scalar != 0). An inactive symbol's
                    # target is 0 regardless of budget (0 * anything == 0),
                    # so its probe result is reused as final directly, no
                    # second call needed.
                    probe_results = {}
                    for symbol in config.symbols:
                        result = _compute_signal_row(symbol, precomputed, d, futures_types, config,
                                                      vix_scalar, annualization_by_symbol[symbol],
                                                      notional_budget=(
                                                          _ew_notional_budget(config, ledger.capital)
                                                          if config.allocation_mode == 'ew' else None
                                                      ),
                                                      **_goulding_kwargs_for(
                                                          config, rebal_monthly, symbol, d,
                                                          mixing_params_by_cluster, goulding_forecast_scalar,
                                                      ))
                        if result is not None:
                            probe_results[symbol] = result

                    initial_active_symbols = [s for s, r in probe_results.items() if r['scalar'] != 0]
                    active_symbols, cluster_universe_rank, cluster_universe_score = _select_cluster_cap_universe(
                        probe_results, initial_active_symbols, config,
                    )
                    active_symbols = sorted(active_symbols)

                    # IDM-derived, correlation-aware per-symbol budget -- see
                    # compute_symbol_notional_budget's own docstring for the
                    # full derivation (capital * target_portfolio_vol * IDM,
                    # split across active_symbols per config.notional_weighting,
                    # converted back to a notional_budget by dividing out
                    # config.vol_target). One entry per active symbol -- NOT
                    # a single shared float -- since 'erc'/'hrp' give
                    # different symbols different budgets ('flat' is the
                    # only scheme where every symbol happens to get the same
                    # value). Returns {} if there are no active symbols, or
                    # too little history yet for a bounded-window correlation
                    # estimate -- nobody trades this month regardless (every
                    # probe result's own target is already 0 in that case).
                    budget_diagnostics: dict = {}
                    per_symbol_budget = compute_symbol_notional_budget(
                        active_symbols, returns_wide, d, ledger.capital, config.target_portfolio_vol,
                        config.vol_target, config.corr_window_years, config.corr_halflife_days,
                        config.notional_weighting, config.use_idm, diagnostics=budget_diagnostics)

                    final_results: dict[str, dict] = {}
                    for symbol in config.symbols:
                        result = probe_results.get(symbol)
                        if result is None:
                            continue
                        if symbol in active_symbols:
                            # Recompute with the REAL, IDM-derived budget --
                            # the probe pass's own target (implicitly sized
                            # off config.max_notional) is discarded here.
                            result = _compute_signal_row(symbol, precomputed, d, futures_types, config,
                                                          vix_scalar, annualization_by_symbol[symbol],
                                                          notional_budget=per_symbol_budget[symbol],
                                                          **_goulding_kwargs_for(
                                                              config, rebal_monthly, symbol, d,
                                                              mixing_params_by_cluster, goulding_forecast_scalar,
                                                          ))
                        result['cluster_universe_rank'] = cluster_universe_rank.get(symbol)
                        result['cluster_universe_score'] = cluster_universe_score.get(symbol)
                        result['cluster_universe_excluded'] = (
                            symbol in initial_active_symbols and symbol not in active_symbols
                        )
                        result['notional_weighting'] = config.notional_weighting
                        result['notional_allocation_weight'] = (
                            budget_diagnostics.get('notional_split', {}).get(symbol)
                        )
                        if symbol not in active_symbols:
                            # The raw model signal remains in the audit row,
                            # but it receives no IDM/ERC budget and must be
                            # flat before any cluster cap is considered.
                            result['target'] = 0
                            result['fractional_target_contracts'] = 0.0
                        final_results[symbol] = result

                    if config.apply_cluster_cap and active_symbols:
                        # Match the live cap's denominator: account equity
                        # times target portfolio vol, rather than the
                        # post-IDM sum of component dollar-vol budgets.
                        # The selected universe is already the actual IDM/
                        # ERC universe, so its cluster count is the one the
                        # 1/n floor should use.
                        n_active_clusters = len({final_results[s]['cluster'] for s in active_symbols})
                        apply_cluster_risk_cap(
                            list(final_results.values()), config.max_cluster_risk_pct,
                            ledger.capital * config.target_portfolio_vol, n_active_clusters,
                            max_lot_overrun_pct=config.max_lot_overrun_pct, apply_cap=True,
                        )
                        for symbol in active_symbols:
                            result = final_results[symbol]
                            result['target'] = result['final_target_contracts']

                    # Preserve the exact sizing diagnostics from the budget
                    # pass, then measure the final *integer* book against
                    # that same H. Re-estimating H here would risk reporting
                    # a different correlation window than the one actually
                    # used to allocate this rebalance.
                    portfolio_risk_target = ledger.capital * config.target_portfolio_vol
                    idm_multiplier = budget_diagnostics.get('idm_multiplier')
                    idm_risk_target = budget_diagnostics.get('total_dollar_vol_target')
                    H = budget_diagnostics.get('H')
                    realized_portfolio_risk = None
                    risk_contribution_by_symbol: dict[str, float] = {}
                    if H is not None and active_symbols:
                        dollar_exposure = {}
                        for symbol in active_symbols:
                            result = final_results[symbol]
                            one_contract_dvol = (
                                abs(float(result['close']) * float(result['mult']) * float(result['hv']))
                                if result.get('close') is not None and result.get('mult') is not None
                                and result.get('hv') is not None else 0.0
                            )
                            dollar_exposure[symbol] = math.copysign(
                                abs(float(result['target'])) * one_contract_dvol,
                                float(result['target']),
                            ) if result['target'] else 0.0
                        realized = compute_realized_portfolio_risk(active_symbols, H, dollar_exposure)
                        realized_portfolio_risk = realized['port_vol']
                        risk_contribution_by_symbol = realized['portfolio_risk_contribution']

                    for symbol, result in final_results.items():
                        result['portfolio_risk_target'] = portfolio_risk_target
                        result['idm_risk_target'] = idm_risk_target
                        result['idm_multiplier'] = idm_multiplier
                        result['realized_portfolio_risk'] = realized_portfolio_risk
                        result['portfolio_risk_contribution'] = risk_contribution_by_symbol.get(symbol)

                    for symbol in config.symbols:
                        result = final_results.get(symbol)
                        if result is None:
                            continue
                        target, gate_reason = _apply_signal_gate(ledger.held_contracts[symbol], result['target'], result, config)
                        ledger.rebalance_to(symbol, target, d, vol_regime, vix_close=vix_close,
                                            vix_ratio=vix_ratio, signal={**result, 'gate_reason': gate_reason})
                else:
                    for symbol in config.symbols:
                        result = _compute_signal_row(symbol, precomputed, d, futures_types, config,
                                                      vix_scalar, annualization_by_symbol[symbol],
                                                      notional_budget=(
                                                          _ew_notional_budget(config, ledger.capital)
                                                          if config.allocation_mode == 'ew' else None
                                                      ),
                                                      **_goulding_kwargs_for(
                                                          config, rebal_monthly, symbol, d,
                                                          mixing_params_by_cluster, goulding_forecast_scalar,
                                                      ))
                        if result is None:
                            continue
                        target, gate_reason = _apply_signal_gate(ledger.held_contracts[symbol], result['target'], result, config)
                        ledger.rebalance_to(symbol, target, d, vol_regime, vix_close=vix_close,
                                            vix_ratio=vix_ratio, signal={**result, 'gate_reason': gate_reason})

        daily_rows.append({'date': d, 'capital': round(ledger.capital, 2)})

    # Force-close any position still open at the end of the window -- same
    # spirit as Backtester's close_all sweep: a trade that's still running
    # when the backtest ends isn't abandoned, it's marked at the last
    # available price so `trades` doesn't silently drop it.
    if all_dates:
        last_date = all_dates[-1]
        for symbol in config.symbols:
            ot = ledger.open_trade[symbol]
            if ot is not None:
                close_row = full_price_data[symbol].filter(pl.col('ts_event') <= last_date).tail(1)
                last_price = float(close_row['close'][0]) if close_row.height > 0 else None
                ot['close_reason'] = 'end_of_backtest'
                ledger.close_trade(symbol, last_date, last_price)

    stats = pl.DataFrame(daily_rows)
    stats = stats.with_columns(running_max=pl.col('capital').cum_max())
    stats = stats.with_columns(
        cum_pnl=(pl.col('capital') - config.initial_capital).round(2),
        drawdown_usd=(pl.col('capital') - pl.col('running_max')).round(2),
    )
    stats = stats.with_columns(
        drawdown_pct=pl.when(pl.col('running_max') > 0)
        .then((pl.col('drawdown_usd') / pl.col('running_max') * 100).round(2))
        .otherwise(0.0)
    )

    # Summary stats -- same shape/naming as
    # tsmom_binary_vol_parity_backtest.py's own run() return dict
    # (ann_ret_pct/ann_vol_pct/sharpe/max_dd_pct/total_fees). This module
    # previously had no equivalent anywhere: main() printed final
    # capital/cum_pnl/max_dd_usd but never computed an actual Sharpe ratio,
    # and nothing was ever saved to a summary CSV -- confirmed there was no
    # way to compare runs' risk-adjusted performance without recomputing
    # this by hand from daily_mtm every time.
    daily_ret = stats.with_columns(
        ret=pl.col('capital') / pl.col('capital').shift(1) - 1
    ).drop_nulls('ret')
    mean_ret, std_ret = daily_ret['ret'].mean(), daily_ret['ret'].std()
    ann_ret = (mean_ret or 0.0) * 252
    ann_vol = (std_ret or 0.0) * (252 ** 0.5)
    sharpe = ann_ret / ann_vol if ann_vol else None
    total_fees = sum(t['fee'] for t in ledger.transactions)

    return {
        'daily_mtm': stats, 'trend_signals': ledger.events,
        'transactions': pl.DataFrame(ledger.transactions),
        'trades': pl.DataFrame(ledger.trades).sort('entry_date') if ledger.trades else pl.DataFrame(ledger.trades),
        'n_days': stats.height,
        'ann_ret_pct': round(ann_ret * 100, 2),
        'ann_vol_pct': round(ann_vol * 100, 2),
        'sharpe': round(sharpe, 2) if sharpe else None,
        'max_dd_pct': round(stats['drawdown_pct'].min(), 2) if stats.height else None,
        'total_fees': round(total_fees, 2),
        'data_manifest': data_manifest,
        'ewmac_scalar_history': ewmac_scalar_history,
        'ewmac_instrument_coverage': ewmac_instrument_coverage,
    }
