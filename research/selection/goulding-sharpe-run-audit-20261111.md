# Goulding Sharpe-like run audit: November 11, 2026

This audit covers Goulding's volatility-normalized continuous signal in
selection run `20261110_230706`, with a $75,000 starting account and the yearly
selection configuration recorded in the scenario name. The run artifacts are
under
`/home/dev/data/fin/misc/results/selection_v1/goulding/selection_run_20261110_230706/`.
Comparisons use the closest available runs with the same selector constraints:
Goulding continuous `20261110_151448`, EWMAC `20261110_203706`, and return
TSMOM 32/256 and 64/256 `20261110_205549` and `20261110_211651`.

## Signal and data checks

The forecast audit records `g_mode=sharpe`, fast/slow volatility windows of 2
and 12 completed months, forecast target absolute value 1, and forecast cap 2.
Raw fast/slow arithmetic returns remain the inputs to regime classification
and to the historical state outcomes used for `a_co`/`a_re`. The normalized
legs change the continuous magnitude blend. Of 169 audited instruments, 161
are forecast eligible; eight are excluded for insufficient valid forecast
observations. The audit reports 205 post-warmup invalid observations, all for
M1MSA, which has no valid forecast observations.

The Sharpe and continuous forecasts agree in sign on 99.1% of their common
post-2024 observations. On dates when either model has an active position, the
mean absolute forecasts are nearly identical (0.907 Sharpe, 0.905 continuous),
while the Sharpe version reaches the cap less often (8.2% versus 15.7%). This
supports that normalization reduced saturation without materially changing
the dominant direction signal.

## Performance comparison

All rows use the common end date 2026-11-10. Full-history statistics begin on
the first valid portfolio return; return TSMOM begins in 1975 while Goulding
and EWMAC begin in 1973. Annualized return mean and volatility are calculated
from daily `port_net_pct` returns using 256 business days per year; Sharpe is
the ratio of those annualized moments.

| Model | Valid history | Ending net equity | CAGR | Ann. mean | Ann. vol | Sharpe | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|---:|
| Goulding Sharpe | 1973–2026 | $199.1M | 15.8% | 16.0% | 18.2% | 0.88 | −43.2% |
| Goulding continuous | 1973–2026 | $52.2M | 12.9% | 13.4% | 17.0% | 0.78 | −38.0% |
| EWMAC | 1973–2026 | $1.85B | 20.7% | 20.0% | 18.1% | 1.11 | −29.9% |
| Return TSMOM 32/256 | 1975–2026 | $2.07B | 21.8% | 21.2% | 19.1% | 1.11 | −38.9% |
| Return TSMOM 64/256 | 1975–2026 | $1.52B | 21.1% | 20.4% | 18.2% | 1.13 | −37.5% |

The Sharpe-like version improves on continuous Goulding over the full history:
ending equity is about 3.8 times larger and Sharpe rises by 0.10. Its maximum
drawdown is nevertheless deeper. Both Goulding results remain well below the
other trend runs in ending equity and Sharpe. Those equity differences also
change later selection feasibility, so this is not a controlled fixed-book
signal-only comparison.

### Recent returns

The newest Sharpe book's coverage is complete through October 26, 2026. Later
dates have partial books, so the through-November column is shown as a
diagnostic only.

| Model | 2024 | 2025 | 2026 through Oct 26 | 2024–Oct 26, 2026 |
|---|---:|---:|---:|---:|
| Goulding Sharpe | −11.6% | +13.3% | −6.2% | −6.1% |
| Goulding continuous | −5.8% | +23.3% | −4.6% | +10.7% |
| EWMAC | −8.4% | +1.1% | +9.1% | +1.0% |
| Return TSMOM 32/256 | +2.1% | +22.7% | +0.2% | +25.4% |
| Return TSMOM 64/256 | −6.0% | +0.4% | +18.5% | +11.9% |

Sharpe Goulding's 2025 recovery did not persist: it lost 6.2% through October
26, 2026. For 2024 through that date, it is down 6.1% while continuous
Goulding is up 10.7%. The Sharpe-like normalization therefore improves the
long sample but does not resolve the recent performance decay.

## Annual selection and concentration

The annual books differ substantially even though the forecasts have similar
signs. Jaccard similarity is the intersection divided by the union of symbols
in each year's two books.

| Review date | Sharpe count | Continuous count | Jaccard |
|---|---:|---:|---:|
| 2022-01-03 | 32 | 24 | 40.0% |
| 2023-01-02 | 22 | 32 | 28.6% |
| 2024-01-01 | 9 | 32 | 17.1% |
| 2025-01-01 | 32 | 13 | 18.4% |
| 2026-01-01 | 29 | 21 | 31.6% |

At the 2024 review, Sharpe Goulding had 152 candidates but selected only nine,
one per asset class. The review explicitly records `coverage_satisfied=false`
with `coverage_unmet_hard_feasibility_gate`; the requested minimum book count
of 15 was not met. The saved review says the remaining coverage rules could
not be met under hard feasibility. In the 2024 trial history, participation
and subclass limits recur, and many additions are rejected by the 50%
portfolio-margin cap. The 2024 book had approximately 11.1% raw weight per
instrument, an effective count of 9, and 55.6% in its five largest names.

By 2026, the book has 29 names but an effective count of about 15.5. The two
largest allocations, ETHUSDRR and RB, each have 14.29% raw structural weight;
their `limit_weight` is 8%, and IDM 2.5 brings each to the configured 20%
instrument risk limit. The cap is working as configured, but singleton
classes can still receive large risk budgets relative to the rest of the
book.

## 2026 P&L and costs

Through November 10, 2026 portfolio net P&L is −$22.64M. Class contributions
are led by RB and offset by losses across several unrelated groups:

| Class | Net P&L |
|---|---:|
| Energy | +$20.47M |
| Equity | −$0.72M |
| Bonds | −$6.19M |
| Metals | −$7.10M |
| Crypto | −$7.49M |
| Agriculture | −$10.53M |
| FX | −$11.09M |

Largest instrument gains are RB (+$20.50M), TWN (+$5.38M), UC (+$2.66M),
BRE (+$1.58M), and HE (+$1.51M). Largest losses are NZD (−$15.38M), ETHUSDRR
(−$6.85M), SONIA.N (−$5.05M), HSI (−$4.30M), ZW (−$3.37M), IXU (−$3.36M),
and SCI (−$3.30M).

Through October 26, costs were about $1.75M against a $13.78M net equity
decline, so costs explain only a minority of the loss. Full-year-to-date
costs through November 10 are about $1.93M. SCI alone incurred about $285k
in trade and roll costs during 2026; SONIA.N about $232k, ETHUSDRR $199k, UC
$184k, and BRE $141k. These are noticeable but small relative to the largest
gross losses.

## Tail completeness limitation

The cash file ends on November 11, but that final row has no active book. The
latest common full-book date among the model comparison runs is November 10;
for the Sharpe run the last date with all 29 selected instruments contributing
is October 26. Its reported active count then falls to 27 on October 30, 12
on November 5, 3 on November 9, and 2 on November 10. From October 27 through
November 10, equity falls a further $8.86M while the book contracts. Treat
this tail as partial-history diagnostic output, not as a complete portfolio
return or a current forward estimate. Refreshing the missing IB tails or
truncating all comparisons to a common complete date is required before
making a current-book assessment.

## Conclusion and artifacts

The volatility-normalized magnitude is a useful Goulding challenger: it
improves full-history results over continuous mode and reduces forecast-cap
frequency. It has not demonstrated better recent robustness, has a deep
historical drawdown, and produces very different annual books once dynamic
equity changes feasibility. A controlled fixed-book or fixed-selection-capital
comparison is needed to isolate signal quality from selection-path effects.

Primary outputs are the run's `goulding_sharpe_selection_cash_pnl`,
`portfolio_curve`, `book`, `reviews`, `forecast_audit`, and
`review_final_allocations` Parquet files. This note audits saved outputs; it
does not regenerate the backtest.
