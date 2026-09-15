# Goulding cluster-cap expanding-window check — 2026-09-15

This is a read-only anchored test of the live-policy parity path in the main
`tsmom` backtester. Each row starts on 2010-01-01 and extends its end date;
there is no rolling-window reset. At every monthly rebalance, Goulding's
`a_Co`/`a_Re` estimate uses only preceding monthly state/return observations,
so the signal-estimation window is expanding and causal inside every run.

## Configuration

The test uses the live 90K universe and relevant live parameters:

```bash
.venv/bin/tsmom \
  --symbols MES,MNQ,MCL,MZL,MZC,MZS,MZW,MGC,SIL,J7,6M,MTN \
  --years 2010-END_YEAR --initial-capital 90000 \
  --vol-target .15 --target-portfolio-vol .15 \
  --use-idm --notional-weighting erc \
  --signal-weighting goulding --mixing-pool cluster \
  --fast-window 42 --slow-window 252 --vol-fast-window 42 \
  --corr-window-years 3 --corr-halflife-days 42 \
  --disable-vix-gating --max-contracts 15 --max-notional 100000 \
  --apply-cluster-cap --max-active-per-cluster 2 \
  --max-cluster-risk-pct .25 --max-lot-overrun-pct .5 --no-save
```

`fast-window`/`vol-fast-window` determine the continuous model's volatility
estimate used for Goulding position sizing; they do **not** alter Goulding's
monthly fast/slow return horizons. `--splice-live-price`, `--client`, and the
live order-routing flags have no backtest equivalent.

## Results

| Anchored period | Final capital | Ann. return | Ann. vol | Sharpe | Max DD | Fees | Rebalance events |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2010–2015 | $99,440 | 2.15% | 9.13% | 0.24 | -12.34% | $1,181.44 | 641 |
| 2010–2018 | $111,277 | 2.84% | 9.32% | 0.30 | -15.05% | $2,331.30 | 1,073 |
| 2010–2021 | $145,537 | 4.55% | 10.22% | 0.45 | -15.05% | $3,389.56 | 1,493 |
| 2010–2024 | $165,681 | 4.56% | 10.00% | 0.46 | -15.05% | $4,330.68 | 1,925 |
| 2010–2026-06-18 | $179,369 | 4.66% | 9.90% | 0.47 | -15.05% | $4,946.90 | 2,141 |

The local continuous-futures data ends on 2026-06-18, so the last row is not
a full calendar-year result.

## Policy being tested

Before the correlation/IDM/ERC pass, active signals are ranked within each
instrument cluster and only the strongest two remain. Goulding's binary
Bull/Bear direction is not a ranking input: agreeing states rank by
`abs((g_fast + g_slow) / 2)` and Correction/Rebound by `abs(g_blend)`.
The resulting selected set is the only set supplied to the correlation matrix
and budget split. The optional cluster cap then applies the existing
whole-contract priority walk-down to that same selected set, using account
equity × target portfolio vol as its cap denominator.

This validates causal portfolio construction and integer cap behavior, not
execution: it uses backtest close prices and the backtest's normal monthly
integer rounding rather than IB orders or live-price splicing.
