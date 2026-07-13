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
                    own history is too short/thin (e.g. J7→JPY, MZC→ZC for
                    CBOT micro grains). Read by resolve_signal_symbol()
                    (options_bt.live.tsmom_rebalance) for the live IB
                    continuous-bars fetch -- deliberately NOT used for the
                    db_symbol fallback below when the divergence is a pure
                    duckdb-coverage gap rather than a genuine thin-history
                    problem (see MES/MNQ/MTN/MCL).
  db_symbol      -- Globex root symbol in daily.asset (Databento CME
                    MDP3.0 feed); only set when it differs from the key.
                    Two distinct reasons a symbol needs this:
                      (a) IBKR/Globex ticker naming divergence -- J7→6J,
                          BRE→6L, JPY→6J (unrelated to data completeness,
                          this ticker just doesn't exist in the db).
                      (b) Pure duckdb-coverage gap -- MES/MNQ/MTN/MCL have
                          no db history under their own symbol at all, but
                          their full-size sibling (ES/NQ/ZN/CL) does,
                          confirmed by direct query. Set here so backtests
                          can borrow that history while still using the
                          MICRO's own multiplier/margin/commission for
                          sizing and PnL (see resolve_price_symbol below)
                          -- deliberately NOT mirrored into signal_symbol,
                          since these symbols' LIVE IB continuous-bar
                          history is fine on its own; only the local
                          duckdb is missing it.
                    Used by the duckdb continuous-front-month queries in
                    the backtester, diagnostic, and data-quality scripts.

`BACKTEST_ONLY_SPECS` holds multiplier/margin/commission for contracts that
exist only on the general single/multi-symbol backtest path (naked_futures.py,
tsmom_backtester.py via FuturesType) and have never been part of the live
TSMOM instrument universe -- see its own docstring for why this must stay a
separate dict from INSTRUMENTS.

Imported by:
  options_bt.domain.enums.FuturesType   -- mult/margin/commission for every
                                            member (both dicts)
  options_bt.live.run_tsmom_rebalance   -- live rebalancing + order execution
  options_bt.domain.tsmom_backtester    -- historical backtest (via
                                            resolve_price_symbol)
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
    # MES/MNQ have no db history under their own symbol (confirmed) -- db_symbol
    # borrows ES/NQ's for backtesting; live IB signal history is unaffected
    # (resolve_signal_symbol doesn't read db_symbol).
    'ES':  {'exchange': 'CME',   'multiplier': 50,         'cluster': 'equity',
            'initial_margin': 34068.38, 'commission': 2.24},
    'MES': {'exchange': 'CME',   'multiplier': 5,          'cluster': 'equity',
            'initial_margin': 3429.11, 'commission': 0.61, 'db_symbol': 'ES'},
    'NQ':  {'exchange': 'CME',   'multiplier': 20,         'cluster': 'equity',
            'initial_margin': 67582.55, 'commission': 2.24},
    'MNQ': {'exchange': 'CME',   'multiplier': 2,          'cluster': 'equity',
            'initial_margin': 6607.19, 'commission': 0.61, 'db_symbol': 'NQ'},

    # ── Energy ──────────────────────────────────────────────────────────────
    # CL/MCL clear on NYMEX (crude oil), not COMEX (metals only). MCL has no
    # db history under its own symbol (confirmed) -- db_symbol borrows CL's.
    # No initial_margin/commission for MCL: never had a FuturesType entry to
    # relocate from, and fabricating new CME figures is out of scope here --
    # this db_symbol addition helps the live/diagnostic duckdb path, not
    # general single-symbol backtesting (which still needs those fields).
    'CL':  {'exchange': 'NYMEX', 'multiplier': 1000,       'cluster': 'energy',
            'initial_margin': 18750.0, 'commission': 1.70},
    'MCL': {'exchange': 'NYMEX', 'multiplier': 100,        'cluster': 'energy', 'db_symbol': 'CL'},

    # ── Metals ──────────────────────────────────────────────────────────────
    # SIL: IBKR has no separate Micro Silver ticker -- it trades under the
    # same root 'SI' as full-size silver, disambiguated by multiplier.
    # resolve_price_symbol already falls through to ib_symbol for SIL, so no
    # separate db_symbol is needed. No margin/commission for MGC/SIL, same
    # "never had FuturesType data to relocate" reasoning as MCL above.
    'GC':  {'exchange': 'COMEX', 'multiplier': 100,        'cluster': 'metal',
            'initial_margin': 48345.79, 'commission': 1.70},
    'MGC': {'exchange': 'COMEX', 'multiplier': 10,         'cluster': 'metal'},
    'SI':  {'exchange': 'COMEX', 'multiplier': 5000,       'cluster': 'metal',
            'initial_margin': 74299.37, 'commission': 1.70},
    'SIL': {'exchange': 'COMEX', 'multiplier': 1000,       'cluster': 'metal', 'ib_symbol': 'SI'},

    # ── Rates ───────────────────────────────────────────────────────────────
    # MTN has no db history under its own symbol (confirmed) -- db_symbol
    # borrows ZN's (the standard 10-Year, not TN the Ultra 10-Year -- a
    # different duration/contract, not MTN's full-size sibling).
    'ZN':  {'exchange': 'CBOT',  'multiplier': 1000,       'cluster': 'rates',
            'initial_margin': 2156.25, 'commission': 1.67},
    'TN':  {'exchange': 'CBOT',  'multiplier': 1000,       'cluster': 'rates',
            'initial_margin': 2935.79, 'commission': 1.67},
    'MTN': {'exchange': 'CBOT',  'multiplier': 100,        'cluster': 'rates',
            'initial_margin': 725.80, 'commission': 0.57, 'db_symbol': 'ZN'},
    'ZT':  {'exchange': 'CBOT',  'multiplier': 2000,       'cluster': 'rates',
            'initial_margin': 1380.75, 'commission': 3.04},

    # ── Grains ──────────────────────────────────────────────────────────────
    # CBOT micro grains (MZL/MZC/MZS/MZW) launched ~Feb 2025 -- too short a
    # history for the 252-day TSMOM lookback. signal_symbol borrows the
    # full-size contract's continuous IB bars; db_symbol falls back to
    # signal_symbol so the duckdb path uses the same full-size asset.
    'ZL':  {'exchange': 'CBOT',  'multiplier': 600,        'cluster': 'grain',
            'initial_margin': 4603.97, 'commission': 3.02},
    'MZL': {'exchange': 'CBOT',  'multiplier': 60,         'cluster': 'grain', 'signal_symbol': 'ZL'},
    'ZC':  {'exchange': 'CBOT',  'multiplier': 50,         'cluster': 'grain',
            'initial_margin': 1638.35, 'commission': 3.02},
    'MZC': {'exchange': 'CBOT',  'multiplier': 5,          'cluster': 'grain', 'signal_symbol': 'ZC'},
    'ZS':  {'exchange': 'CBOT',  'multiplier': 50,         'cluster': 'grain',
            'initial_margin': 4130.84, 'commission': 3.02},
    'MZS': {'exchange': 'CBOT',  'multiplier': 5,          'cluster': 'grain', 'signal_symbol': 'ZS'},
    'ZW':  {'exchange': 'CBOT',  'multiplier': 50,         'cluster': 'grain',
            'initial_margin': 2948.24, 'commission': 3.02},
    'MZW': {'exchange': 'CBOT',  'multiplier': 5,          'cluster': 'grain', 'signal_symbol': 'ZW'},

    # ── International equity ─────────────────────────────────────────────────
    # Nikkei: its own factor (Japan equity, JPY-adjacent), not lumped with
    # US equity. NKD/MNK (dollar-denominated) are a DIFFERENT contract from
    # FuturesType's NIY (yen-denominated) -- see BACKTEST_ONLY_SPECS. No
    # margin/commission for NKD/MNK, same "never had data to relocate"
    # reasoning as MCL/MGC/SIL above.
    'NKD': {'exchange': 'CME',   'multiplier': 5,          'cluster': 'intl_equity'},
    'MNK': {'exchange': 'CME',   'multiplier': 0.5,        'cluster': 'intl_equity'},

    # ── FX ──────────────────────────────────────────────────────────────────
    # IBKR tickers differ from Globex root symbols in the duckdb:
    #   JPY / J7  → Globex '6J'  (J7 is the mini; same underlying, ÷2 size)
    #   BRE       → Globex '6L'  (Brazilian Real)
    #   6M        → Globex '6M'  (Mexican Peso; name matches, no mapping)
    # signal_symbol on J7: borrows full-size JPY continuous IB history for
    # signal/covariance (same MZC→ZC pattern -- mini listed ~2019, too thin
    # for a reliable 252-day signal on its own). No margin/commission for J7:
    # never had a FuturesType entry (only full-size JPY did), same
    # "never had data to relocate" reasoning as MCL/MGC/SIL/NKD/MNK above.
    'JPY': {'exchange': 'CME',   'multiplier': 12_500_000, 'cluster': 'fx', 'db_symbol': '6J',
            'initial_margin': 3015.0, 'commission': 2.47},
    'J7':  {'exchange': 'CME',   'multiplier': 6_250_000,  'cluster': 'fx', 'db_symbol': '6J', 'signal_symbol': 'JPY'},
    'BRE': {'exchange': 'CME',   'multiplier': 100_000,    'cluster': 'fx', 'db_symbol': '6L',
            'initial_margin': 5034.80, 'commission': 2.47},
    '6M':  {'exchange': 'CME',   'multiplier': 500_000,    'cluster': 'fx',
            'initial_margin': 1971.67, 'commission': 2.47},
}

# ── Backtest-only contracts ─────────────────────────────────────────────────
# Multiplier/margin/commission for FuturesType members with NO live TSMOM
# counterpart -- never part of INSTRUMENTS, never traded/analyzed by the live
# system. Used only by ad-hoc single-symbol backtests (naked_futures.py,
# profile_bt.py) via FuturesType.
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
    commission), which always come from `symbol` itself via
    FuturesType.from_symbol(symbol) -- e.g. MES borrows ES's price series
    but stays sized/margined as MES, never as ES.

    Returns `symbol` unchanged for anything not in INSTRUMENTS (including
    BACKTEST_ONLY_SPECS members, which have no db-coverage gap to resolve).
    """
    instr = INSTRUMENTS.get(symbol, {})
    return instr.get('db_symbol') or instr.get('signal_symbol') or instr.get('ib_symbol') or symbol
