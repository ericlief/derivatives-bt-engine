"""
Tests for derivatives_bt_engine.live.tsmom_rebalance -- focused on
data_source='database' (no IB dependency at all, monkeypatched
FuturesDataLoader/VIX in place of a real duckdb/parquet read) plus
TsmomLiveConfig validation. The data_source='ib' path makes real IBPySync
calls (contract resolution, live bars/prices) with no local equivalent to
fake out cheaply -- 'database' is what makes this module testable, and is
also the notebook-runnable, no-live-account path the tests below exercise
end to end.
"""

import subprocess
import sys
import textwrap
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from derivatives_bt_engine.live import tsmom_rebalance as tr
from derivatives_bt_engine.live.tsmom_rebalance import TsmomLiveConfig, build_instruments, compute_rebalance_targets


def _trading_dates(start: date, n: int) -> list[date]:
    dates = []
    d = start
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _price_df(start: date, n: int, drift: float, vol: float = 0.01, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    dates = _trading_dates(start, n)
    return pl.DataFrame({'ts_event': dates, 'close': close})


def _instrument(symbol: str, cluster: str = 'other', multiplier: float = 50.0) -> dict:
    return {
        'symbol': symbol, 'ib_symbol': symbol, 'signal_symbol': symbol, 'db_symbol': symbol,
        'exchange': 'CME', 'expiry': 'auto', 'multiplier': multiplier, 'cluster': cluster,
        'max_contracts': 50, 'max_notional': None,
    }


def _patch_db(monkeypatch, price_data: dict[str, pl.DataFrame], vx: tuple[float, float] = (15.0, 15.0)):
    """Fakes both data sources compute_rebalance_targets(data_source=
    'database') reads: FuturesDataLoader (per-instrument bars) and
    _vx_spike_ratio_from_db (local spot-VIX gate) -- no duckdb/parquet
    file needed."""
    class _FakeLoader:
        def __init__(self, asset, **kwargs):
            self.asset = asset

        @property
        def daily(self):
            return price_data[self.asset]

    monkeypatch.setattr(tr, 'FuturesDataLoader', _FakeLoader)
    monkeypatch.setattr(tr, 'assert_monotonic_expiration', lambda df, sym: None)
    monkeypatch.setattr(tr, '_vx_spike_ratio_from_db', lambda as_of=None, ma_window_days=63: vx)


# ── No ib_tools dependency for data_source='database' ────────────────────────

def test_module_imports_and_runs_database_mode_without_ib_tools_installed():
    # Regression test for the module-level `from ib_tools.ibpysync import
    # IBPySync` this module used to have -- that made even importing
    # TsmomLiveConfig/compute_rebalance_targets require ib_tools/ib_insync
    # to be installed, defeating the point of a notebook-runnable, no-IB
    # data_source='database' path for an environment that doesn't have
    # them. IBPySync is now imported lazily, only inside the functions that
    # actually touch it (all on the data_source='ib' path) -- verified here
    # in a genuinely fresh subprocess (this test file's own module-level
    # import already pulled ib_tools in for THIS process, so testing it
    # in-process would prove nothing) with ib_tools/ib_insync imports
    # blocked outright.
    script = textwrap.dedent("""
        import builtins
        real_import = builtins.__import__
        def blocked_import(name, *args, **kwargs):
            if name == 'ib_tools' or name.startswith('ib_tools.') or name == 'ib_insync':
                raise ImportError(f'blocked for test: {name}')
            return real_import(name, *args, **kwargs)
        builtins.__import__ = blocked_import

        from derivatives_bt_engine.live.tsmom_rebalance import TsmomLiveConfig, compute_rebalance_targets

        instr = {'symbol': 'X', 'ib_symbol': 'X', 'signal_symbol': 'X', 'db_symbol': 'X',
                 'exchange': 'CME', 'expiry': 'auto', 'multiplier': 1.0, 'cluster': 'other',
                 'max_contracts': 50, 'max_notional': None}
        config = TsmomLiveConfig(account_equity=100_000, data_source='database', vix_gating=False)
        # No FuturesDataLoader available (real duckdb may not be cached for
        # 'X') is fine to fail on -- the point is the IMPORT and the
        # up-front data_source/ib dispatch succeed without ib_tools; a
        # RuntimeError from the instrument-not-found path is an acceptable
        # (expected) outcome here, an ImportError of ib_tools is not.
        try:
            compute_rebalance_targets([instr], config, ib=None)
        except ImportError as exc:
            if 'ib_tools' in str(exc) or 'ib_insync' in str(exc):
                raise
        except Exception:
            pass
        print('OK')
    """)
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'OK' in result.stdout


# ── build_instruments ─────────────────────────────────────────────────────

def test_build_instruments_resolves_known_symbols():
    instruments = build_instruments(['MES', 'J7'])
    by_symbol = {i['symbol']: i for i in instruments}
    assert set(by_symbol) == {'MES', 'J7'}
    # MES: db_symbol falls back through signal_symbol to the full-size ES
    # sibling's own duckdb history.
    assert by_symbol['MES']['signal_symbol'] == 'ES'
    assert by_symbol['MES']['db_symbol'] == 'ES'
    # J7: explicit db_symbol divergence (IBKR ticker vs Globex root).
    assert by_symbol['J7']['db_symbol'] == '6J'
    for i in instruments:
        assert i['expiry'] == 'auto'
        assert i['max_notional'] is None
        assert i['max_contracts'] == tr.DEFAULT_MAX_CONTRACTS


def test_build_instruments_rejects_unknown_symbol():
    with pytest.raises(ValueError):
        build_instruments(['NOT_A_REAL_SYMBOL'])


def test_build_instruments_passes_through_max_notional_and_max_contracts():
    instruments = build_instruments(['MES'], max_notional=25_000.0, max_contracts=7)
    assert instruments[0]['max_notional'] == 25_000.0
    assert instruments[0]['max_contracts'] == 7


# ── TsmomLiveConfig validation ───────────────────────────────────────────────

@pytest.mark.parametrize('field,value', [
    ('signal_weighting', 'bogus'),
    ('mixing_pool', 'bogus'),
    ('risk_budget_mode', 'bogus'),
    ('notional_weighting', 'bogus'),
    ('data_source', 'bogus'),
])
def test_tsmom_live_config_rejects_unknown_values(field, value):
    with pytest.raises(ValueError):
        TsmomLiveConfig(**{field: value})


def test_tsmom_live_config_defaults_match_prior_behavior():
    config = TsmomLiveConfig()
    assert config.signal_weighting == 'continuous'
    assert config.risk_budget_mode == 'cluster'
    assert config.data_source == 'ib'
    assert config.notional_weighting == 'flat'
    assert config.use_idm is True


# ── compute_rebalance_targets: ib/database dispatch ──────────────────────────

def test_compute_rebalance_targets_requires_ib_connection_for_ib_data_source():
    config = TsmomLiveConfig(account_equity=100_000, data_source='ib')
    with pytest.raises(ValueError):
        compute_rebalance_targets([_instrument('X')], config, ib=None)


def test_compute_rebalance_targets_database_mode_needs_no_ib(monkeypatch):
    price_data = {'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)}
    _patch_db(monkeypatch, price_data)

    config = TsmomLiveConfig(account_equity=100_000, data_source='database')
    targets = compute_rebalance_targets([_instrument('X')], config, ib=None)

    assert len(targets) == 1
    t = targets[0]
    assert t.get('error') is None
    # No IB connection anywhere in this mode -- current_contracts is
    # unknowable without one, reported as None rather than a misleading 0.
    assert t['current_contracts'] is None
    assert t['target_contracts'] is not None


def test_compute_rebalance_targets_database_mode_respects_as_of(monkeypatch):
    price_data = {'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)}
    _patch_db(monkeypatch, price_data)

    early_as_of = price_data['X']['ts_event'][300]
    config = TsmomLiveConfig(account_equity=100_000, data_source='database', as_of=early_as_of)
    targets = compute_rebalance_targets([_instrument('X')], config, ib=None)

    assert targets[0].get('error') is None
    assert targets[0]['close'] == pytest.approx(price_data['X'].filter(pl.col('ts_event') <= early_as_of)
                                                 .tail(1)['close'][0])


def test_vix_gating_false_skips_the_vx_read_entirely(monkeypatch):
    # No VIX/VX source configured at all (unlike _patch_db's other callers,
    # _vx_spike_ratio_from_db is deliberately left unpatched here) --
    # vix_gating=False must mean compute_rebalance_targets never calls it,
    # not just that it tolerates it failing.
    price_data = {'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)}

    class _FakeLoader:
        def __init__(self, asset, **kwargs):
            self.asset = asset

        @property
        def daily(self):
            return price_data[self.asset]

    monkeypatch.setattr(tr, 'FuturesDataLoader', _FakeLoader)
    monkeypatch.setattr(tr, 'assert_monotonic_expiration', lambda df, sym: None)

    def _unreachable(*args, **kwargs):
        raise AssertionError("_vx_spike_ratio_from_db should not be called when vix_gating=False")

    monkeypatch.setattr(tr, '_vx_spike_ratio_from_db', _unreachable)

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', vix_gating=False)
    targets = compute_rebalance_targets([_instrument('X')], config, ib=None)

    assert targets[0].get('error') is None
    assert targets[0]['vol_regime'] == tr.VolRegime.NORMAL
    assert targets[0]['vx_current'] is None
    assert targets[0]['vx_ma'] is None
    assert targets[0]['vix_scalar'] == 1.0


# ── risk_budget_mode: 'cluster' vs 'idm' ─────────────────────────────────────

def test_risk_budget_mode_cluster_gives_every_active_instrument_the_same_budget(monkeypatch):
    price_data = {
        'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1),
        'Y': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=2),
    }
    _patch_db(monkeypatch, price_data)

    config = TsmomLiveConfig(account_equity=1_000_000, data_source='database', risk_budget_mode='cluster')
    targets = compute_rebalance_targets([_instrument('X'), _instrument('Y')], config, ib=None)

    budgets = {t['symbol']: t['budget_constant'] for t in targets if t.get('error') is None}
    assert len(budgets) == 2
    assert budgets['X'] == pytest.approx(budgets['Y'])


def test_risk_budget_mode_idm_favors_independent_symbol_over_correlated_pair(monkeypatch):
    # A and B are the SAME series (correlation exactly 1.0), C is
    # independent -- same "correlated cluster vs. lone diversifier" setup
    # as domain.allocation's own compute_erc_weights tests, now exercised
    # end to end through the live rebalance's 'idm' risk_budget_mode.
    same_series = _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)
    price_data = {
        'A': same_series,
        'B': same_series,
        'C': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=2),
    }
    _patch_db(monkeypatch, price_data)

    # as_of pinned to the synthetic data's own last date -- the bounded EWM
    # correlation window (default 3y back from as_of) would otherwise find
    # zero overlap against config.as_of's default of date.today(), miles
    # past this synthetic 2018-19 series, and silently fall back to a flat
    # split (compute_erc_weights' own no-corr-data default) instead of
    # actually exercising the correlation-aware path this test is for.
    as_of = price_data['A']['ts_event'][-1]
    config = TsmomLiveConfig(account_equity=1_000_000, data_source='database', risk_budget_mode='idm',
                              notional_weighting='erc', as_of=as_of)
    targets = compute_rebalance_targets(
        [_instrument('A'), _instrument('B'), _instrument('C')], config, ib=None)

    budgets = {t['symbol']: t['budget_constant'] for t in targets if t.get('error') is None}
    assert set(budgets) == {'A', 'B', 'C'}
    assert budgets['C'] > budgets['A']
    assert budgets['C'] > budgets['B']


def test_risk_budget_mode_idm_use_idm_false_skips_multiplier(monkeypatch):
    price_data = {
        'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1),
        'Y': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=2),
    }
    _patch_db(monkeypatch, price_data)

    config = TsmomLiveConfig(account_equity=1_000_000, target_portfolio_vol=0.15, vol_target=0.15,
                              data_source='database', risk_budget_mode='idm', notional_weighting='flat',
                              use_idm=False)
    targets = compute_rebalance_targets([_instrument('X'), _instrument('Y')], config, ib=None)

    budgets = [t['budget_constant'] for t in targets if t.get('error') is None]
    assert len(budgets) == 2
    # No IDM adjustment, flat split -- total dollar-vol budget is exactly
    # account_equity * target_portfolio_vol, split evenly.
    assert sum(budgets) * 0.15 == pytest.approx(1_000_000 * 0.15, rel=1e-6)


def test_apply_cluster_cap_defaults_to_false():
    assert TsmomLiveConfig(account_equity=100_000).apply_cluster_cap is False


def test_apply_cluster_cap_wiring_reaches_apply_cluster_risk_cap(monkeypatch):
    # Spy on the real apply_cluster_risk_cap to confirm config.
    # apply_cluster_cap actually reaches its apply_cap kwarg, not just that
    # the config field exists.
    price_data = {
        'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1),
        'Y': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=2),
    }
    _patch_db(monkeypatch, price_data)

    captured = {}
    original = tr.apply_cluster_risk_cap

    def spy(*args, **kwargs):
        captured['apply_cap'] = kwargs.get('apply_cap')
        return original(*args, **kwargs)

    monkeypatch.setattr(tr, 'apply_cluster_risk_cap', spy)

    config = TsmomLiveConfig(account_equity=1_000_000, data_source='database', risk_budget_mode='idm',
                             notional_weighting='erc', apply_cluster_cap=True)
    compute_rebalance_targets([_instrument('X'), _instrument('Y')], config, ib=None)

    assert captured['apply_cap'] is True


def test_total_risk_target_scaled_by_idm_multiplier_when_cap_enabled(monkeypatch):
    # When apply_cluster_cap=True and risk_budget_mode='idm', the cap's own
    # total_risk_target must be scaled by the same idm_multiplier used to
    # size positions -- otherwise the cap silently claws back IDM's own
    # diversification credit (the bug this whole feature exists to fix).
    same_series = _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)
    price_data = {
        'A': same_series, 'B': same_series,
        'C': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=2),
    }
    _patch_db(monkeypatch, price_data)
    as_of = price_data['A']['ts_event'][-1]

    captured = {}
    original = tr.apply_cluster_risk_cap

    def spy(targets, max_cluster_risk_pct, total_risk_target, n_active_clusters, **kwargs):
        captured['total_risk_target'] = total_risk_target
        return original(targets, max_cluster_risk_pct, total_risk_target, n_active_clusters, **kwargs)

    monkeypatch.setattr(tr, 'apply_cluster_risk_cap', spy)

    config = TsmomLiveConfig(account_equity=1_000_000, data_source='database', risk_budget_mode='idm',
                             notional_weighting='erc', as_of=as_of, apply_cluster_cap=True)
    compute_rebalance_targets([_instrument('A'), _instrument('B'), _instrument('C')], config, ib=None)

    flat_total_risk_target = 1_000_000 * config.target_portfolio_vol
    # Real diversification exists (A/B correlated, C independent) -- the
    # scaled cap must be strictly bigger than the flat, uncredited figure.
    assert captured['total_risk_target'] > flat_total_risk_target


def test_risk_contribution_attached_to_targets_under_idm_mode(monkeypatch):
    same_series = _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)
    price_data = {
        'A': same_series, 'B': same_series,
        'C': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=2),
    }
    _patch_db(monkeypatch, price_data)
    as_of = price_data['A']['ts_event'][-1]

    config = TsmomLiveConfig(account_equity=1_000_000, data_source='database', risk_budget_mode='idm',
                             notional_weighting='erc', as_of=as_of)
    targets = compute_rebalance_targets([_instrument('A'), _instrument('B'), _instrument('C')], config, ib=None)

    for t in targets:
        if not t.get('error'):
            assert 'risk_contribution' in t

    report = tr.print_cluster_risk_report(targets)
    assert 'risk_contribution=' in report
    assert 'position_risk=' in report


def test_risk_contribution_absent_under_cluster_mode(monkeypatch):
    # 'cluster' mode never computes H (no correlation-aware sizing at
    # all), so there's nothing for compute_realized_portfolio_risk to use
    # -- print_cluster_risk_report should fall back to position_risk
    # totals only, no risk_contribution column.
    price_data = {
        'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1),
        'Y': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=2),
    }
    _patch_db(monkeypatch, price_data)

    config = TsmomLiveConfig(account_equity=1_000_000, data_source='database', risk_budget_mode='cluster')
    targets = compute_rebalance_targets([_instrument('X'), _instrument('Y')], config, ib=None)

    for t in targets:
        assert 'risk_contribution' not in t

    report = tr.print_cluster_risk_report(targets)
    assert 'position_risk=' in report
    assert 'risk_contribution=' not in report


def test_active_field_reflects_min_conviction_and_inactive_symbol_gets_clean_zero(monkeypatch):
    # Regression test: an 'idm'-mode symbol that fails min_conviction used
    # to fall through to budget_constant_by_symbol.get(symbol) is None ->
    # a spurious "account_equity not configured" ValueError, surfacing as
    # an ERROR row even though account_equity WAS configured -- the symbol
    # just wasn't in active_symbols. Must now be a clean target=0,
    # active=False row instead, with no 'error' key at all.
    price_data = {
        'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1),
        'Y': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=2),
    }
    _patch_db(monkeypatch, price_data)

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', risk_budget_mode='idm',
                              notional_weighting='erc', min_conviction=999.0)
    targets = compute_rebalance_targets([_instrument('X'), _instrument('Y')], config, ib=None)

    for t in targets:
        assert t.get('error') is None
        assert t['active'] is False
        assert t['target_contracts'] == 0
        assert t['budget_constant'] is None
        assert t['notional_weight'] is None


# ── signal_weighting: 'goulding' ─────────────────────────────────────────────

def test_signal_weighting_goulding_populates_audit_fields(monkeypatch):
    price_data = {'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)}
    _patch_db(monkeypatch, price_data)

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', signal_weighting='goulding')
    targets = compute_rebalance_targets([_instrument('X')], config, ib=None)

    assert targets[0].get('error') is None
    assert targets[0]['g_regime'] in ('bull', 'bear', 'correction', 'rebound')
    assert targets[0]['a_co'] is not None
    assert targets[0]['a_re'] is not None


def test_signal_weighting_continuous_leaves_audit_fields_none(monkeypatch):
    price_data = {'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)}
    _patch_db(monkeypatch, price_data)

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', signal_weighting='continuous')
    targets = compute_rebalance_targets([_instrument('X')], config, ib=None)

    assert targets[0]['g_regime'] is None
    assert targets[0]['a_co'] is None
    assert targets[0]['a_re'] is None


# ── splice_live_price: live IB bar spliced onto the DB series' tail ──────────

def _full_price_df(start: date, n: int, drift: float, expiration: date, instrument_id: int = 1,
                    vol: float = 0.01, seed: int = 0) -> pl.DataFrame:
    """Like _price_df but with the full FuturesDataLoader.daily schema
    (open/high/low/volume/instrument_id/expiration) the splice path reads
    from `bars.tail(1)` -- _price_df's bare ts_event/close is enough for
    every OTHER test in this file since splice_live_price defaults False."""
    base = _price_df(start, n, drift, vol=vol, seed=seed)
    return base.with_columns(
        pl.col('close').alias('open'), pl.col('close').alias('high'), pl.col('close').alias('low'),
        pl.lit(100_000).alias('volume'), pl.lit(instrument_id).alias('instrument_id'),
        pl.lit(expiration).alias('expiration'),
    )


class _FakeIB:
    """Fakes only the two IBPySync round-trip methods _qualify_and_pull_
    recent_bar touches (qualify_contracts, get_historical_bars).
    IBPySync.future(...) itself is called for real -- a pure, network-free
    ib_insync.Future construction (ib_tools is a real editable install in
    this dev env), so only the round-trip calls need faking.

    volumes_by_month/bar_by_month: {'YYYYMM': ...} keyed by the requested
    contract month. raise_for: months whose get_historical_bars call raises
    (simulates an IB error). mangle_month: a month whose qualified contract
    gets its lastTradeDateOrContractMonth corrupted, to trigger the
    resolved-month-mismatch guard."""

    def __init__(self, volumes_by_month, bar_by_month=None, raise_for=frozenset(), mangle_month=None):
        self.volumes_by_month = volumes_by_month
        self.bar_by_month = bar_by_month or {}
        self.raise_for = raise_for
        self.mangle_month = mangle_month
        self.calls: list[tuple] = []
        self.multipliers_seen: list[str] = []

    def qualify_contracts(self, contract):
        month = contract.lastTradeDateOrContractMonth[:6]
        self.calls.append(('qualify', month))
        self.multipliers_seen.append(contract.multiplier)
        if month == self.mangle_month:
            contract.lastTradeDateOrContractMonth = '209912'

    def get_historical_bars(self, contract, end_date='', duration='2 D', bar_size='1 day',
                             what_to_show='TRADES', use_rth=True):
        month = contract.lastTradeDateOrContractMonth[:6]
        self.calls.append(('bars', month))
        if month in self.raise_for:
            raise RuntimeError('simulated IB error')
        vol = self.volumes_by_month.get(month)
        if vol is None:
            return pl.DataFrame()
        ts_event, close = self.bar_by_month.get(month, (date(2026, 8, 15), 100.0))
        return pl.DataFrame({'date': [ts_event], 'open': [close], 'high': [close],
                             'low': [close], 'close': [close], 'volume': [vol]})


def test_next_active_month_yyyymm_mid_cycle():
    assert tr._next_active_month_yyyymm(['H', 'M', 'U', 'Z'], date(2026, 3, 18)) == '202606'


def test_next_active_month_yyyymm_wraps_to_next_year():
    assert tr._next_active_month_yyyymm(['H', 'M', 'U', 'Z'], date(2026, 12, 18)) == '202703'


def test_next_active_month_yyyymm_raises_when_month_not_in_cycle():
    with pytest.raises(ValueError):
        tr._next_active_month_yyyymm(['H', 'M', 'U', 'Z'], date(2026, 1, 18))


def test_splice_live_price_requires_ib_even_in_database_mode():
    config = TsmomLiveConfig(account_equity=100_000, data_source='database', splice_live_price=True)
    with pytest.raises(ValueError):
        compute_rebalance_targets([_instrument('X')], config, ib=None)


def test_splice_disabled_by_default_makes_no_ib_calls(monkeypatch):
    price_data = {'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)}
    _patch_db(monkeypatch, price_data)
    calls = []
    monkeypatch.setattr(tr, '_splice_live_front_month_bar', lambda *a, **k: calls.append(1) or a[-1])

    config = TsmomLiveConfig(account_equity=100_000, data_source='database')  # splice_live_price defaults False
    targets = compute_rebalance_targets([_instrument('X')], config, ib=None)

    assert calls == []
    assert targets[0].get('error') is None


def test_splice_enabled_db_contract_still_front_appends_new_row(monkeypatch):
    price_data = {'X': _full_price_df(date(2018, 1, 1), 500, drift=0.0015, expiration=date(2026, 3, 20), seed=1)}
    _patch_db(monkeypatch, price_data)
    monkeypatch.setattr(tr, 'resolve_active_months', lambda symbol: ['H', 'M', 'U', 'Z'])
    monkeypatch.setattr(tr, 'get_spec', lambda symbol: {'exchange': 'CME', 'multiplier': 50})
    fake_ib = _FakeIB(volumes_by_month={'202603': 1000, '202606': 500},
                      bar_by_month={'202603': (date(2026, 8, 15), 111.0)})

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', splice_live_price=True)
    targets = compute_rebalance_targets([_instrument('X')], config, ib=fake_ib)

    assert targets[0].get('error') is None
    assert targets[0]['close'] == pytest.approx(111.0)
    assert ('qualify', '202606') in fake_ib.calls  # next contract WAS checked...
    assert ('bars', '202606') in fake_ib.calls      # ...just lost the volume comparison


def test_splice_uses_db_symbol_own_multiplier_not_instrument_own(monkeypatch):
    # Regression test: a micro/mini instrument (e.g. real MES, multiplier=5)
    # borrowing its full-size sibling's history (real ES, multiplier=50)
    # must qualify IB contracts with the SIBLING's own multiplier, not the
    # calling instrument's -- confirmed live this session as the actual
    # cause of IB rejecting every splice-enabled symbol with "No security
    # definition has been found" (MES was requesting multiplier='5' against
    # root symbol 'ES', which only resolves under multiplier='50').
    price_data = {'ES': _full_price_df(date(2018, 1, 1), 500, drift=0.0015, expiration=date(2026, 3, 20), seed=1)}
    _patch_db(monkeypatch, price_data)
    monkeypatch.setattr(tr, 'resolve_active_months', lambda symbol: ['H', 'M', 'U', 'Z'])
    monkeypatch.setattr(tr, 'get_spec', lambda symbol: {'exchange': 'CME', 'multiplier': 50})
    fake_ib = _FakeIB(volumes_by_month={'202603': 1000, '202606': 500})

    mes = _instrument('MES', multiplier=5.0)
    mes['db_symbol'] = 'ES'
    mes['signal_symbol'] = 'ES'
    config = TsmomLiveConfig(account_equity=100_000, data_source='database', splice_live_price=True)
    targets = compute_rebalance_targets([mes], config, ib=fake_ib)

    assert targets[0].get('error') is None
    assert all(m == '50' for m in fake_ib.multipliers_seen)


def test_splice_enabled_roll_detected_uses_next_contract(monkeypatch, caplog):
    price_data = {'X': _full_price_df(date(2018, 1, 1), 500, drift=0.0015, expiration=date(2026, 3, 20), seed=1)}
    _patch_db(monkeypatch, price_data)
    monkeypatch.setattr(tr, 'resolve_active_months', lambda symbol: ['H', 'M', 'U', 'Z'])
    monkeypatch.setattr(tr, 'get_spec', lambda symbol: {'exchange': 'CME', 'multiplier': 50})
    fake_ib = _FakeIB(volumes_by_month={'202603': 500, '202606': 2000},
                      bar_by_month={'202606': (date(2026, 8, 15), 222.0)})

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', splice_live_price=True)
    with caplog.at_level('WARNING'):
        targets = compute_rebalance_targets([_instrument('X')], config, ib=fake_ib)

    assert targets[0].get('error') is None
    assert targets[0]['close'] == pytest.approx(222.0)
    assert any('roll detected' in r.message for r in caplog.records)


def test_splice_skipped_when_active_months_unconfirmed(monkeypatch):
    price_data = {'X': _full_price_df(date(2018, 1, 1), 500, drift=0.0015, expiration=date(2026, 3, 20), seed=1)}
    _patch_db(monkeypatch, price_data)
    monkeypatch.setattr(tr, 'resolve_active_months', lambda symbol: None)
    fake_ib = _FakeIB(volumes_by_month={})

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', splice_live_price=True)
    targets = compute_rebalance_targets([_instrument('X')], config, ib=fake_ib)

    assert targets[0].get('error') is None
    assert fake_ib.calls == []


def test_splice_falls_back_gracefully_on_ib_error(monkeypatch, caplog):
    price_data = {'X': _full_price_df(date(2018, 1, 1), 500, drift=0.0015, expiration=date(2026, 3, 20), seed=1)}
    _patch_db(monkeypatch, price_data)
    monkeypatch.setattr(tr, 'resolve_active_months', lambda symbol: ['H', 'M', 'U', 'Z'])
    monkeypatch.setattr(tr, 'get_spec', lambda symbol: {'exchange': 'CME', 'multiplier': 50})
    fake_ib = _FakeIB(volumes_by_month={'202603': 1000, '202606': 500}, raise_for={'202603'})

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', splice_live_price=True)
    with caplog.at_level('WARNING'):
        targets = compute_rebalance_targets([_instrument('X')], config, ib=fake_ib)

    # No exception propagates -- the whole rebalance still completes normally.
    assert targets[0].get('error') is None
    assert targets[0]['target_contracts'] is not None
    assert any('live price splice failed' in r.message for r in caplog.records)


def test_splice_replaces_existing_same_day_row_not_duplicates(monkeypatch):
    price_data = {'X': _full_price_df(date(2024, 1, 1), 500, drift=0.0015, expiration=date(2026, 3, 20), seed=1)}
    original_height = price_data['X'].height
    same_day = price_data['X'].tail(1)['ts_event'][0]
    _patch_db(monkeypatch, price_data)
    monkeypatch.setattr(tr, 'resolve_active_months', lambda symbol: ['H', 'M', 'U', 'Z'])
    monkeypatch.setattr(tr, 'get_spec', lambda symbol: {'exchange': 'CME', 'multiplier': 50})
    fake_ib = _FakeIB(volumes_by_month={'202603': 1000, '202606': 500},
                      bar_by_month={'202603': (same_day, 333.0)})

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', splice_live_price=True)
    targets = compute_rebalance_targets([_instrument('X')], config, ib=fake_ib)

    assert targets[0].get('error') is None
    assert targets[0]['close'] == pytest.approx(333.0)
    # Replaced, not appended -- row count unchanged.
    assert price_data['X'].height == original_height


def test_splice_skipped_when_as_of_is_set(monkeypatch):
    price_data = {'X': _price_df(date(2018, 1, 1), 500, drift=0.0015, vol=0.005, seed=1)}
    _patch_db(monkeypatch, price_data)
    calls = []
    monkeypatch.setattr(tr, '_splice_live_front_month_bar', lambda *a, **k: calls.append(1) or a[-1])
    early_as_of = price_data['X']['ts_event'][300]

    config = TsmomLiveConfig(account_equity=100_000, data_source='database', splice_live_price=True,
                              as_of=early_as_of)
    targets = compute_rebalance_targets([_instrument('X')], config, ib=_FakeIB(volumes_by_month={}))

    assert calls == []
    assert targets[0].get('error') is None
