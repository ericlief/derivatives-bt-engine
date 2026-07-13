"""
Central futures contract registry: multiplier, margin, commission, and the
symbol-resolution metadata (IBKR/Globex ticker divergence, thin-history
substitution) needed across both the live TSMOM system and the general
backtest path.

Each `INSTRUMENTS` entry carries:

  exchange       -- IB exchange for contract resolution
  multiplier     -- contract notional multiplier (USD per point/cent/etc.)
  cluster        -- risk factor bucket for cluster-cap allocation (live
                    TSMOM only -- unused by the general backtest path)
  initial_margin -- CME SPAN maintenance margin, USD. Moves with volatility/
                    exchange resets -- only ES/MES were directly given;
                    every other value below is a rough estimate, scaled
                    from typical CME margin levels for that product. Verify
                    against current CME/broker figures before relying on
                    them for sizing. Not every symbol has this field --
                    only ones with a confirmed or estimated figure; missing
                    means no data exists yet, not zero.
  commission     -- per contract, per side (i.e. half of round trip); the
                    backtester's calculate_pnl() doubles it for the full
                    round trip. Reuses per-contract tiers (standard vs
                    micro) across sibling products. Same missing-means-
                    no-data convention as initial_margin.
  ib_symbol      -- IBKR contract ticker; only set when it differs from the
                    dict key (e.g. SIL trades under IBKR root 'SI',
                    disambiguated by multiplier)
  signal_symbol  -- IB continuous-front-month ticker for LIVE signal/
                    covariance history; only set when the traded contract's
                    own history is too short/thin for a reliable estimate
                    (e.g. J7→JPY, MZC→ZC for CBOT micro grains launched
                    ~2025, MES/MNQ/MTN/MCL→ES/NQ/ZN/CL -- CME Micro
                    products launched 2019-2021, comparably new to J7).
                    Read by resolve_signal_symbol() (options_bt.live.
                    tsmom_rebalance) for the live IB continuous-bars fetch
                    -- this is the SAME fetch tsmom_risk_budget_diagnostic.py
                    uses for its covariance analysis, so setting this also
                    gives that diagnostic the full-size contract's longer
                    history, not just live rebalancing.
  db_symbol      -- Globex root symbol in daily.asset (Databento CME
                    MDP3.0 feed); only set when it differs from the key AND
                    signal_symbol doesn't already resolve it (resolve_price_
                    symbol falls through db_symbol > signal_symbol >
                    ib_symbol, so MES etc. don't need an explicit db_symbol
                    once signal_symbol is set). Needed on its own only for a
                    pure IBKR/Globex ticker naming divergence with NO
                    history problem -- J7→6J, BRE→6L, JPY→6J (these
                    symbols' own IB history is genuinely fine; the local
                    duckdb just happens to store them under a different
                    ticker).
                    Used by the duckdb continuous-front-month queries in
                    the backtester, diagnostic, and data-quality scripts.

`BACKTEST_ONLY_SPECS` holds multiplier/margin/commission for contracts that
exist only on the general single/multi-symbol backtest path (naked_futures.py,
tsmom_backtester.py) and have never been part of the live TSMOM instrument
universe -- see its own docstring for why this must stay a separate dict
from INSTRUMENTS.

get_spec(symbol) is the single lookup point for a contract's multiplier/
margin/commission -- there is no per-instrument enum (deliberately: an
underlying instrument isn't a distinct *type*, any more than an option's
underlying is -- OptionsType is just CALL/PUT, not one member per
underlying). FuturesStrategy (LONG_FUTURES/SHORT_FUTURES) remains the
correct futures analog to OptionsStrategy: it describes position *shape*,
orthogonal to the traded symbol.

Imported by:
  options_bt.domain.position            -- FuturesPosition margin/mult/
                                            commission (via get_spec)
  options_bt.domain.strategy_config     -- FuturesStrategyConfig validation
                                            (via known_futures_symbols)
  options_bt.domain.futures_signal_generator -- signal margin (via get_spec)
  options_bt.live.run_tsmom_rebalance   -- live rebalancing + order execution
  options_bt.domain.tsmom_backtester    -- historical backtest (via
                                            get_spec, resolve_price_symbol)
  options_bt.strats.naked_futures       -- single-symbol backtest CLI (via
                                            resolve_price_symbol)
  scripts.tsmom_risk_budget_diagnostic  -- ERC/HRP vs cluster-cap diagnostic
  scripts.futures_data_quality          -- data quality checks
"""

# ── Tunable defaults ────────────────────────────────────────────────────────
DEFAULT_DB_PATH = '/home/dev/fin/db/globex_mdp_3.0.duckdb'

# ── Instrument universe ─────────────────────────────────────────────────────
INSTRUMENTS: dict[str, dict] = {
    # ── Equity index ────────────────────────────────────────────────────────
    # MES/MNQ (launched May 2019, ~6y of history) borrow ES/NQ's via
    # signal_symbol -- both for backtesting (resolve_price_symbol falls
    # through to signal_symbol) and for live/diagnostic IB covariance
    # history (resolve_signal_symbol), matching the J7/MZC pattern.
    'ES':  {'exchange': 'CME',   'multiplier': 50,         'cluster': 'equity',
            'initial_margin': 34068.38, 'commission': 2.24},
    'MES': {'exchange': 'CME',   'multiplier': 5,          'cluster': 'equity',
            'initial_margin': 3429.11, 'commission': 0.61, 'signal_symbol': 'ES'},
    'NQ':  {'exchange': 'CME',   'multiplier': 20,         'cluster': 'equity',
            'initial_margin': 67582.55, 'commission': 2.24},
    'MNQ': {'exchange': 'CME',   'multiplier': 2,          'cluster': 'equity',
            'initial_margin': 6607.19, 'commission': 0.61, 'signal_symbol': 'NQ'},

    # ── Energy ──────────────────────────────────────────────────────────────
    # CL/MCL clear on NYMEX (crude oil), not COMEX (metals only). MCL
    # (launched 2021) borrows CL's via signal_symbol, same reasoning as
    # MES/MNQ above.
    'CL':  {'exchange': 'NYMEX', 'multiplier': 1000,       'cluster': 'energy',
            'initial_margin': 16602.40, 'commission': 2.36},
    'MCL': {'exchange': 'NYMEX', 'multiplier': 100,        'cluster': 'energy',
            'initial_margin': 1660.24, 'commission': 0.76, 'signal_symbol': 'CL'},

    # ── Metals ──────────────────────────────────────────────────────────────
    # SIL: IBKR has no separate Micro Silver ticker -- it trades under the
    # same root 'SI' as full-size silver, disambiguated by multiplier.
    # resolve_price_symbol already falls through to ib_symbol for SIL, so no
    # separate db_symbol/signal_symbol is needed. No margin/commission for
    # SIL yet (not sourced).
    'GC':  {'exchange': 'COMEX', 'multiplier': 100,        'cluster': 'metal',
            'initial_margin': 40701.95, 'commission': 2.24},
    'MGC': {'exchange': 'COMEX', 'multiplier': 10,         'cluster': 'metal',
            'initial_margin': 4084.79, 'commission': 0.96, 'signal_symbol': 'GC'},
    'SI':  {'exchange': 'COMEX', 'multiplier': 5000,       'cluster': 'metal',
            'initial_margin': 74299.37, 'commission': 1.70},
    'SIL': {'exchange': 'COMEX', 'multiplier': 1000,       'cluster': 'metal', 
             'initial_margin': 13038.92, 'commission': 1.36, 'ib_symbol': 'SI'},

    # ── Rates ───────────────────────────────────────────────────────────────
    # MTN borrows ZN's via signal_symbol (the standard 10-Year, not TN the
    # Ultra 10-Year -- a different duration/contract, not MTN's full-size
    # sibling), same reasoning as MES/MNQ above.
    'ZN':  {'exchange': 'CBOT',  'multiplier': 1000,       'cluster': 'rates', # notional ~= 109K
            'initial_margin': 2156.25, 'commission': 1.66},
    'TN':  {'exchange': 'CBOT',  'multiplier': 1000,       'cluster': 'rates',
            'initial_margin': 2932.50, 'commission': 1.66},
    'MTN': {'exchange': 'CBOT',  'multiplier': 100,        'cluster': 'rates', # notional ~= 11K
            'initial_margin': 769.16, 'commission': 0.56, 'signal_symbol': 'ZN'},
    'ZT':  {'exchange': 'CBOT',  'multiplier': 2000,       'cluster': 'rates', # notional ~= 205K
            'initial_margin': 1380.00, 'commission': 1.51},

    # ── Grains ──────────────────────────────────────────────────────────────
    # CBOT micro grains (MZL/MZC/MZS/MZW) launched ~Feb 2025 -- too short a
    # history for the 252-day TSMOM lookback. signal_symbol borrows the
    # full-size contract's continuous IB bars; db_symbol falls back to
    # signal_symbol so the duckdb path uses the same full-size asset.
    'ZL':  {'exchange': 'CBOT',  'multiplier': 600,        'cluster': 'grain', # notional ~= 43K
            'initial_margin': 5102.79, 'commission': 3.01},
    'MZL': {'exchange': 'CBOT',  'multiplier': 60,         'cluster': 'grain', 
            'initial_margin': 525.16, 'commission': 0.76, 'signal_symbol': 'ZL'},
    'ZC':  {'exchange': 'CBOT',  'multiplier': 50,         'cluster': 'grain',
            'initial_margin': 1855.76, 'commission': 3.01},
    'MZC': {'exchange': 'CBOT',  'multiplier': 5,          'cluster': 'grain', 
            'initial_margin': 166.51, 'commission': 0.76, 'signal_symbol': 'ZC'},
    'ZS':  {'exchange': 'CBOT',  'multiplier': 50,         'cluster': 'grain',
            'initial_margin': 4038.46, 'commission': 3.01},
    'MZS': {'exchange': 'CBOT',  'multiplier': 5,          'cluster': 'grain', 
            'initial_margin': 382.95, 'commission':  0.76, 'signal_symbol': 'ZS'},
    'ZW':  {'exchange': 'CBOT',  'multiplier': 50,         'cluster': 'grain',
            'initial_margin': 3321.39, 'commission': 3.01},
    'MZW': {'exchange': 'CBOT',  'multiplier': 5,          'cluster': 'grain', 
             'initial_margin': 332.14, 'commission': 0.76, 'signal_symbol': 'ZW'},

    # ── International equity ─────────────────────────────────────────────────
    # Nikkei: its own factor (Japan equity, JPY-adjacent), not lumped with
    # US equity. NKD/MNK (dollar-denominated) are a DIFFERENT contract from
    # BACKTEST_ONLY_SPECS' NIY (yen-denominated).
    'NKD': {'exchange': 'CME',   'multiplier': 5,          'cluster': 'intl_equity',
            'initial_margin': 43347.93, 'commission': 3.01},
    'MNK': {'exchange': 'CME',   'multiplier': 0.5,        'cluster': 'intl_equity',
             'initial_margin': 4334.76, 'commission': 0.69, 'signal_symbol': 'NKD'},

    # ── FX ──────────────────────────────────────────────────────────────────
    # IBKR tickers differ from Globex root symbols in the duckdb:
    #   JPY / J7  → Globex '6J'  (J7 is the mini; same underlying, ÷2 size)
    #   BRE       → Globex '6L'  (Brazilian Real)
    #   6M        → Globex '6M'  (Mexican Peso; name matches, no mapping)
    # signal_symbol on J7: borrows full-size JPY continuous IB history for
    # signal/covariance (same MZC→ZC pattern -- mini listed ~2019, too thin
    # for a reliable 252-day signal on its own).
    'JPY': {'exchange': 'CME',   'multiplier': 12_500_000, 'cluster': 'fx', 'db_symbol': '6J',
            'initial_margin': 3051.83, 'commission': 2.46},
    'J7':  {'exchange': 'CME',   'multiplier': 6_250_000,  'cluster': 'fx', 'db_symbol': '6J', # notional ~= 39K
            'initial_margin': 1525.92, 'commission': 1.36, 'signal_symbol': 'JPY'},
    'BRE': {'exchange': 'CME',   'multiplier': 100_000,    'cluster': 'fx', 'db_symbol': '6L', # notional ~= 19K
            'initial_margin': 5023.68, 'commission': 2.46},
    '6M':  {'exchange': 'CME',   'multiplier': 500_000,    'cluster': 'fx', # notional ~= 28K
            'initial_margin': 1962.36, 'commission': 2.46},
}

# ── Backtest-only contracts ─────────────────────────────────────────────────
# Multiplier/margin/commission for contracts with NO live TSMOM
# counterpart -- never part of INSTRUMENTS, never traded/analyzed by the live
# system. Used only by ad-hoc single-symbol backtests (naked_futures.py,
# profile_bt.py) via get_spec().
#
# MUST NEVER be merged into INSTRUMENTS or unioned into any
# "default to every known instrument" fallback (e.g. run_tsmom_rebalance.py's
# `args.instruments or ','.join(sorted(KNOWN_INSTRUMENTS))`, mirrored in
# tsmom_risk_budget_diagnostic.py/futures_data_quality.py) -- doing so would
# silently expand what those live/diagnostic tools trade or analyze by
# default to symbols that were never vetted for the live TSMOM strategy.
#
# db data status (confirmed by direct query): NIY has real duckdb history
# (asset='NIY'), genuinely backtestable via naked_futures.py --symbol NIY.
# MYM/YM/M2K/RTY have none -- neither the micro nor its full-size sibling
# (YM/RTY) appears in this Databento feed at all, so these four entries are
# inert until/unless that data exists; no signal_symbol/db_symbol
# substitution can fix a family with zero data on either side.
BACKTEST_ONLY_SPECS: dict[str, dict] = {
    'MYM': {'multiplier': 0.5, 'initial_margin': 1727.20, 'commission': 1.24},  # Micro E-mini Dow -- margin estimated, verify; no db data
    'YM':  {'multiplier': 5,   'initial_margin': 12503.0, 'commission': 1.70},  # E-mini Dow -- margin estimated, verify; no db data
    'M2K': {'multiplier': 5,   'initial_margin': 1250.05, 'commission': 1.24},  # Micro E-mini Russell 2000 -- margin estimated, verify; no db data
    'RTY': {'multiplier': 50,  'initial_margin': 12593.0, 'commission': 1.70},  # E-mini Russell 2000 -- margin estimated, verify; no db data
    'NIY': {'multiplier': 500, 'initial_margin': 10000.0, 'commission': 1.70},  # Nikkei 225 Yen-denominated (CME) -- margin estimated, verify.
                                                                                 # NOTE: contract is JPY-denominated (Y500/point); this codebase's
                                                                                 # PnL math has no FX conversion, so PnL comes out in JPY, not USD,
                                                                                 # unless that's added separately. Has real duckdb history.
    # SOX = {...} # Not added: genuinely unsure of this contract's point
    # value/margin (possibly a Small Exchange product, not a standard CME
    # index future I have reliable specs for) -- ask before adding rather
    # than guess.
}


def resolve_price_symbol(symbol: str) -> str:
    """Symbol whose price history (duckdb `daily` table) should be used for
    `symbol`'s signal/backtest data -- either its own (default) or a
    fuller-history sibling's, via the same db_symbol > signal_symbol >
    ib_symbol > symbol fallback chain run_tsmom_rebalance.py's
    _build_instruments uses to compute db_symbol -- generalized to work
    from a bare traded symbol (not an already-built instrument dict) so the
    general Backtester (naked_futures.py) and tsmom_backtester.py's
    multi-symbol backtests can reuse it too, not just live rebalancing.

    Distinct from the instrument's own contract specs (multiplier/margin/
    commission), which always come from `symbol` itself via get_spec(symbol)
    -- e.g. MES borrows ES's price series but stays sized/margined as MES,
    never as ES.

    Returns `symbol` unchanged for anything not in INSTRUMENTS (including
    BACKTEST_ONLY_SPECS members, which have no db-coverage gap to resolve).
    """
    instr = INSTRUMENTS.get(symbol, {})
    return instr.get('db_symbol') or instr.get('signal_symbol') or instr.get('ib_symbol') or symbol


# get_spec()'s Globex/db ticker -> INSTRUMENTS dict key, for the 3 FX
# symbols where they diverge (INSTRUMENTS keys by IBKR-facing ticker, not
# the raw Globex root -- see this module's docstring, db_symbol field).
_FX_TICKER_TO_KEY = {'6J': 'JPY', '6L': 'BRE', '6M': '6M'}


def get_spec(symbol: str) -> dict:
    """Full contract spec (multiplier/initial_margin/commission, plus
    exchange/cluster/etc. if present) for `symbol` -- the single lookup
    point for consumers that need a contract's multiplier/margin/
    commission (FuturesPosition, FuturesStrategyConfig,
    FuturesSignalGenerator, tsmom_backtester.py, naked_futures.py).
    `symbol` may be the real exchange/Globex ticker (e.g. '6J') for the 3
    FX contracts where that diverges from INSTRUMENTS' IBKR-facing key.

    Raises KeyError (with the full known-symbol list) if not found --
    callers wanting a friendlier error should validate against
    known_futures_symbols() first."""
    key = _FX_TICKER_TO_KEY.get(symbol.upper(), symbol.upper())
    info = INSTRUMENTS.get(key) or BACKTEST_ONLY_SPECS.get(key)
    if info is None:
        raise KeyError(f"Unknown futures symbol {symbol!r}. Known: {sorted(known_futures_symbols())}")
    return info


def known_futures_symbols() -> set[str]:
    """Every symbol get_spec() accepts -- INSTRUMENTS/BACKTEST_ONLY_SPECS
    keys plus the FX Globex tickers that map to a different INSTRUMENTS
    key (6J, 6L; 6M's Globex ticker matches its INSTRUMENTS key already)."""
    return set(INSTRUMENTS) | set(BACKTEST_ONLY_SPECS) | set(_FX_TICKER_TO_KEY)
