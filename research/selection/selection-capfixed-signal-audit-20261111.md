# Fixed-cap selection signal audit: November 11, 2026

This audit compares the completed November 11 fixed-cap runs: multi-speed
EWMAC (`20261111_141909`), return TSMOM 32/256 (`20261111_143654`) and 64/256
(`20261111_142840`), Goulding Sharpe (`20261111_140659`), and Goulding
continuous (`20261111_154229`). All use $75,000 initial fixed sizing capital,
20% target volatility, yearly reviews, and the same class/subclass and
feasibility limits. The 14:58 Goulding continuous directory contains only
screen and candidate artifacts and is not treated as a completed run.

## Full-history results

The portfolio curves run through 2026-11-11. The first valid return is
1973-01-02 for EWMAC/Goulding and 1975-01-02 for return TSMOM. Annualized
arithmetic returns and volatility below use the saved daily `port_net_pct`
with 256 trading days; Sharpe is their ratio. Fixed-cap returns are measured
against the fixed sizing capital and are not reinvested CAGR.

| Signal | Net equity at end | Net P&L | Ann. mean | Ann. vol. | Sharpe | Max DD (pct of prior peak) | Max DD ($) |
|---|---:|---:|---:|---:|---:|---:|---:|
| EWMAC | $935,169 | $860,169 | 21.0% | 18.9% | 1.11 | −11.5% | −$20,339 |
| TSMOM 32/256 | $920,525 | $845,525 | 21.4% | 21.0% | 1.02 | −12.7% | −$11,164 |
| TSMOM 64/256 | $940,408 | $865,408 | 21.9% | 19.5% | 1.12 | −13.8% | −$11,749 |
| Goulding Sharpe | $670,869 | $595,869 | 14.5% | 19.0% | 0.77 | −14.9% | −$44,005 |
| Goulding continuous | $526,845 | $451,845 | 11.0% | 17.2% | 0.64 | −12.1% | −$23,017 |

On these fixed-cap outputs, TSMOM 64/256 has the highest annualized mean and
EWMAC nearly matches its Sharpe with lower realized volatility. Both Goulding
variants lag in annualized mean and Sharpe. Goulding Sharpe improves the mean
and Sharpe over continuous mode, but has the deepest maximum drawdown, both in
percentage and dollar terms. Drawdown percentage uses the cumulative net equity
curve; the largest dollar drawdown is also shown because the denominator
grows as fixed-cap profits accumulate.

| Signal | Max drawdown peak → trough | 2024 | 2025 | 2026 through Nov 11 |
|---|---|---:|---:|---:|
| EWMAC | 1974-02-26 → 1974-07-03 | +0.9% | +0.7% | −1.5% |
| TSMOM 32/256 | 1975-05-05 → 1976-01-14 | +0.5% | −1.1% | −1.6% |
| TSMOM 64/256 | 1975-06-16 → 1975-11-07 | +1.0% | +0.3% | −1.3% |
| Goulding Sharpe | 1980-01-21 → 1981-03-20 | +0.6% | +2.1% | −2.4% |
| Goulding continuous | 1974-02-26 → 1974-03-27 | +1.0% | +3.0% | −4.1% |

These calendar-year figures sum the fixed-cap daily return contributions. All
five are down in 2026 to date; losses are most pronounced in Goulding
continuous and Sharpe. All remain near flat over 2024–2026 through November
11: EWMAC +0.8%, TSMOM 32/256 −2.2%, TSMOM 64/256 +0.0%, Goulding Sharpe
+0.3%, and Goulding continuous −0.1% (daily fixed-cap return contributions
summed).

![Recent fixed-cap annual returns by signal](assets/selection-capfixed-signal-audit-20261111.svg)

| Signal | Forecast-eligible markets / 170 | Active markets at reviews 2022, 2023, 2024, 2025, 2026 |
|---|---:|---|
| EWMAC | 169 | 18, 13, 18, 17, 17 |
| TSMOM 32/256 | 162 | 17, 14, 16, 20, 19 |
| TSMOM 64/256 | 162 | 17, 14, 17, 21, 16 |
| Goulding Sharpe | 162 | 16, 12, 24, 22, 18 |
| Goulding continuous | 162 | 15, 12, 25, 21, 14 |

The eight ineligible TSMOM/Goulding markets fail the valid-forecast-observation
gate; EWMAC excludes one. Goulding Sharp and continuous have the same eligible
universe, while their later books diverge, so their review differences arise
after eligibility.

## Fixed cap versus compounding runs

Closest prior compounding-cap runs with the same signal parameters are
EWMAC `20261110_203706`, TSMOM 32/256 `20261110_205549`, TSMOM 64/256
`20261110_211651`, Goulding Sharpe `20261110_230706`, and Goulding continuous
`20261110_151448`.

| Signal | Fixed-cap ending equity | Compounding ending equity | Fixed-cap max DD | Compounding max DD | Fixed-cap active book max / avg |
|---|---:|---:|---:|---:|---:|
| EWMAC | $0.94M | $1.85B | −11.5% | −29.9% | 23 / 17.0 |
| TSMOM 32/256 | $0.92M | $2.07B | −12.7% | −38.9% | 23 / 16.9 |
| TSMOM 64/256 | $0.94M | $1.52B | −13.8% | −37.5% | 24 / 17.0 |
| Goulding Sharpe | $0.67M | $199.1M | −14.9% | −43.2% | 24 / 17.1 |
| Goulding continuous | $0.53M | $52.2M | −12.1% | −38.0% | 25 / 16.8 |

The compounding runs grow into accounts far beyond $75,000, so terminal equity
and drawdown dollars are not directly comparable to the fixed-cap runs. The
fixed-cap regime holds sizing capital at $75,000 while accumulating P&L;
compounding sizing reinvests equity growth and changes later feasibility,
contract counts, market participation, and the books selected at annual
reviews. Thus the enormous terminal-equity differences are primarily account
sizing-path effects, not evidence of a signal-quality multiple. The fixed-cap
book also ends smaller than the compounding books (23–25 active markets versus
32 in the closest compounding runs), confirming a selection-feasibility
difference.

## Integrity and cost checks

All five portfolio curves have zero dates with an active book and invalid
instrument P&L. The latest daily outputs are valid through November 11. These
checks support using the endpoint, subject to source-gap omissions and
instrument-level roll/handoff checks.

| Signal | Trade costs | Roll costs | Total costs | Costs / net P&L |
|---|---:|---:|---:|---:|
| EWMAC | $42.5k | $21.9k | $64.4k | 7.5% |
| TSMOM 32/256 | $104.5k | $22.5k | $127.0k | 15.0% |
| TSMOM 64/256 | $66.7k | $21.7k | $88.5k | 10.2% |
| Goulding Sharpe | $44.8k | $21.2k | $66.0k | 11.1% |
| Goulding continuous | $34.0k | $20.6k | $54.6k | 12.1% |

Trade/roll amounts are reconstructed as positive costs from the negative cost
columns in the portfolio curve. TSMOM 32/256 has notably higher trade costs;
its weaker Sharpe versus 64/256 is not explained by roll costs, which are
similar across all five books.

## Conclusion

With sizing fixed at $75,000, EWMAC and TSMOM 64/256 are the strongest by
Sharpe (1.11 and 1.12), while TSMOM 64/256 has the highest annualized mean
(21.9%). TSMOM 32/256 takes more risk and incurs substantially higher trade
costs without improving Sharpe. Goulding Sharpe beats Goulding continuous on
mean return and Sharpe but carries a larger maximum drawdown. The apparent
multi-order-of-magnitude spread in compounding terminal equity is dominated by
the capital path and changing feasible books; use the fixed-cap comparison to
compare signal behavior at approximately comparable risk and size.

Primary artifacts are each run's `portfolio_curve`, `cash_pnl`,
`review_instruments`, `forecast_audit`, `reviews`, and
`review_final_allocations` Parquet files under
`/home/dev/data/fin/misc/results/selection_v1/{ewmac,return_tsmom,goulding}/`.
This report audits the saved runs and does not regenerate them.
