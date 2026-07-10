"""
Front-vs-next-month futures term structure diagnostic.

For each instrument in the TSMOM universe, ranks live contracts by soonest
expiration per (asset, date) and compares the front-month close against the
next-month close, then aggregates the front-minus-back spread by year. Used
to check whether "surprising" contango/backwardation in the DB reflects a
genuine market regime (e.g. equity index futures cost-of-carry, F = S *
e^((r-q)T)) rather than a data-quality issue.

See research/research_term_structure_backwardation.md for the writeup that
this script's output supports.

Run:
    python -m scripts.term_structure_diagnostic
    python -m scripts.term_structure_diagnostic --instruments ES,NQ --out /tmp/out.csv
"""
from __future__ import annotations

import argparse

import duckdb
import polars as pl

from options_bt.domain.instruments import INSTRUMENTS as KNOWN_INSTRUMENTS
from options_bt.domain.instruments import DEFAULT_DB_PATH

# ── Tunable defaults ────────────────────────────────────────────────────────
DEFAULT_INSTRUMENTS = 'ES,NQ,CL,ZL,ZC,ZS,ZW,GC,SI,JPY,BRE,6M'
DEFAULT_OUT_PATH = 'research/term_structure_by_year.csv'

# ── Infrastructure ──────────────────────────────────────────────────────────
_TERM_STRUCTURE_SQL = """
WITH bars AS (
    SELECT asset, ts_event, close, expiration
    FROM ohlcv_enriched
    WHERE asset IN ({placeholders})
      AND instrument_class = 'F' AND security_type = 'FUT'
      AND expiration IS NOT NULL
      AND ts_event < CAST(expiration AS DATE)
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY asset, ts_event ORDER BY expiration ASC) AS rn
    FROM bars
),
pivoted AS (
    SELECT asset, ts_event,
        MAX(CASE WHEN rn = 1 THEN close END) AS front,
        MAX(CASE WHEN rn = 2 THEN close END) AS back
    FROM ranked
    WHERE rn <= 2
    GROUP BY asset, ts_event
)
SELECT asset, date_part('year', ts_event)::int AS yr,
    count(*) AS n_days,
    ROUND(avg(front - back), 2) AS avg_spread,
    ROUND(avg((front - back) / back) * 100) AS avg_pct_spread, 
    ROUND(sum(CASE WHEN front > back THEN 1 ELSE 0 END)::double / count(*) * 100) AS pct_days_backwardated
FROM pivoted
WHERE front IS NOT NULL AND back IS NOT NULL
GROUP BY asset, yr
ORDER BY asset, yr
"""


def _db_symbols(instruments_spec: str) -> list[str]:
    """Resolve db_symbol for each comma-separated symbol (e.g. JPY -> 6J, BRE -> 6L)."""
    symbols = []
    for symbol in (s.strip().upper() for s in instruments_spec.split(',') if s.strip()):
        known = KNOWN_INSTRUMENTS[symbol]
        ib_symbol = known.get('ib_symbol') or symbol
        signal_symbol = known.get('signal_symbol') or ib_symbol
        symbols.append(known.get('db_symbol') or signal_symbol)
    return symbols


def compute_term_structure(instruments_spec: str = DEFAULT_INSTRUMENTS,
                            db_path: str = DEFAULT_DB_PATH) -> pl.DataFrame:
    """Front-minus-back close spread, aggregated by asset/year."""
    assets = _db_symbols(instruments_spec)
    placeholders = ', '.join('?' * len(assets))
    sql = _TERM_STRUCTURE_SQL.format(placeholders=placeholders)

    con = duckdb.connect(db_path, read_only=True)
    try:
        return con.execute(sql, assets).pl()
    finally:
        con.close()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--instruments', default=DEFAULT_INSTRUMENTS,
                   help='Comma-separated symbols (default: %(default)s)')
    p.add_argument('--db-path', default=DEFAULT_DB_PATH,
                   help=f'Path to local DuckDB file (default: {DEFAULT_DB_PATH})')
    p.add_argument('--out', default=DEFAULT_OUT_PATH,
                   help='CSV output path (default: %(default)s)')
    return p.parse_args(argv)


def main(argv=None) -> pl.DataFrame:
    args = parse_args(argv)
    df = compute_term_structure(args.instruments, args.db_path)
    df.write_csv(args.out)
    print(f'Wrote {df.height} rows to {args.out}')
    return df


if __name__ == '__main__':
    main()
