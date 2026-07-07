"""
Central instrument universe for the TSMOM system.

Each entry carries everything needed to use an instrument across all three
contexts it appears in:

  exchange      -- IB exchange for contract resolution
  multiplier    -- contract notional multiplier (USD per point/cent/etc.)
  cluster       -- risk factor bucket for cluster-cap allocation
  ib_symbol     -- IBKR contract ticker; only set when it differs from the
                   dict key (e.g. SIL trades under IBKR root 'SI',
                   disambiguated by multiplier)
  signal_symbol -- IB continuous-front-month ticker for signal/history;
                   only set when the traded contract's own history is too
                   short/thin (e.g. J7→JPY, MZC→ZC for CBOT micro grains)
  db_symbol     -- Globex root symbol in ohlcv_enriched.asset (Databento
                   CME MDP3.0 feed); only set when it differs from the key
                   (e.g. J7→6J, BRE→6L, JPY→6J). Used by the duckdb
                   continuous-front-month queries in the backtester,
                   diagnostic, and data-quality scripts.

Imported by:
  options_bt.live.run_tsmom_rebalance  -- live rebalancing + order execution
  options_bt.domain.tsmom_backtester   -- historical backtest
  scripts.tsmom_risk_budget_diagnostic -- ERC/HRP vs cluster-cap diagnostic
  scripts.futures_data_quality         -- data quality checks
"""

# ── Tunable defaults ────────────────────────────────────────────────────────
DEFAULT_DB_PATH = '/home/dev/fin/db/globex_mdp_3.0.duckdb'

# ── Instrument universe ─────────────────────────────────────────────────────
INSTRUMENTS: dict[str, dict] = {
    # ── Equity index ────────────────────────────────────────────────────────
    'ES':  {'exchange': 'CME',   'multiplier': 50,         'cluster': 'equity'},
    'MES': {'exchange': 'CME',   'multiplier': 5,          'cluster': 'equity'},
    'NQ':  {'exchange': 'CME',   'multiplier': 20,         'cluster': 'equity'},
    'MNQ': {'exchange': 'CME',   'multiplier': 2,          'cluster': 'equity'},

    # ── Energy ──────────────────────────────────────────────────────────────
    # CL/MCL clear on NYMEX (crude oil), not COMEX (metals only).
    'CL':  {'exchange': 'NYMEX', 'multiplier': 1000,       'cluster': 'energy'},
    'MCL': {'exchange': 'NYMEX', 'multiplier': 100,        'cluster': 'energy'},

    # ── Metals ──────────────────────────────────────────────────────────────
    # SIL: IBKR has no separate Micro Silver ticker -- it trades under the
    # same root 'SI' as full-size silver, disambiguated by multiplier.
    'GC':  {'exchange': 'COMEX', 'multiplier': 100,        'cluster': 'metal'},
    'MGC': {'exchange': 'COMEX', 'multiplier': 10,         'cluster': 'metal'},
    'SI':  {'exchange': 'COMEX', 'multiplier': 5000,       'cluster': 'metal'},
    'SIL': {'exchange': 'COMEX', 'multiplier': 1000,       'cluster': 'metal', 'ib_symbol': 'SI'},

    # ── Rates ───────────────────────────────────────────────────────────────
    'ZN':  {'exchange': 'CBOT',  'multiplier': 1000,       'cluster': 'rates'},
    'TN':  {'exchange': 'CBOT',  'multiplier': 1000,       'cluster': 'rates'},
    'MTN': {'exchange': 'CBOT',  'multiplier': 100,        'cluster': 'rates'},
    'ZT':  {'exchange': 'CBOT',  'multiplier': 2000,       'cluster': 'rates'},

    # ── Grains ──────────────────────────────────────────────────────────────
    # CBOT micro grains (MZL/MZC/MZS/MZW) launched ~Feb 2025 -- too short a
    # history for the 252-day TSMOM lookback. signal_symbol borrows the
    # full-size contract's continuous IB bars; db_symbol falls back to
    # signal_symbol so the duckdb path uses the same full-size asset.
    'ZL':  {'exchange': 'CBOT',  'multiplier': 600,        'cluster': 'grain'},
    'MZL': {'exchange': 'CBOT',  'multiplier': 60,         'cluster': 'grain', 'signal_symbol': 'ZL'},
    'ZC':  {'exchange': 'CBOT',  'multiplier': 50,         'cluster': 'grain'},
    'MZC': {'exchange': 'CBOT',  'multiplier': 5,          'cluster': 'grain', 'signal_symbol': 'ZC'},
    'ZS':  {'exchange': 'CBOT',  'multiplier': 50,         'cluster': 'grain'},
    'MZS': {'exchange': 'CBOT',  'multiplier': 5,          'cluster': 'grain', 'signal_symbol': 'ZS'},
    'ZW':  {'exchange': 'CBOT',  'multiplier': 50,         'cluster': 'grain'},
    'MZW': {'exchange': 'CBOT',  'multiplier': 5,          'cluster': 'grain', 'signal_symbol': 'ZW'},

    # ── International equity ─────────────────────────────────────────────────
    # Nikkei: its own factor (Japan equity, JPY-adjacent), not lumped with
    # US equity.
    'NKD': {'exchange': 'CME',   'multiplier': 5,          'cluster': 'intl_equity'},
    'MNK': {'exchange': 'CME',   'multiplier': 0.5,        'cluster': 'intl_equity'},

    # ── FX ──────────────────────────────────────────────────────────────────
    # IBKR tickers differ from Globex root symbols in the duckdb:
    #   JPY / J7  → Globex '6J'  (J7 is the mini; same underlying, ÷2 size)
    #   BRE       → Globex '6L'  (Brazilian Real)
    #   6M        → Globex '6M'  (Mexican Peso; name matches, no mapping)
    # signal_symbol on J7: borrows full-size JPY continuous IB history for
    # signal/covariance (same MZC→ZC pattern -- mini listed ~2019, too thin
    # for a reliable 252-day signal on its own).
    'JPY': {'exchange': 'CME',   'multiplier': 12_500_000, 'cluster': 'fx', 'db_symbol': '6J'},
    'J7':  {'exchange': 'CME',   'multiplier': 6_250_000,  'cluster': 'fx', 'db_symbol': '6J', 'signal_symbol': 'JPY'},
    'BRE': {'exchange': 'CME',   'multiplier': 100_000,    'cluster': 'fx', 'db_symbol': '6L'},
    '6M':  {'exchange': 'CME',   'multiplier': 500_000,    'cluster': 'fx'},
}
