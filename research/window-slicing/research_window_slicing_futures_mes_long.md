# Window-slicing scheme comparison: MES long futures

Fixed strategy: naked long MES futures, 1 contract(s), $100,000 capital, fill_price='mid'. Data: 2010-01-01 through 2026-01-15.

## Results by scheme

| Scheme | Windows | Sharpe mean | Sharpe std | MTM Sharpe mean | MTM Sharpe std | ret_yr mean | ret_yr std |
|---|---|---|---|---|---|---|---|
| A_rolling_90d_slide | 62 | 5.009 | 9.434 | n/a | n/a | 41.49 | 59.24 |
| B_expanding | 17 | 0.941 | 0.564 | n/a | n/a | 31.96 | 11.58 |
| C_capped_rolling | 17 | 0.993 | 0.598 | n/a | n/a | 42.00 | 24.46 |

## Scheme A: lag-N autocorrelation (90-day slide)

| Lag (x90 days) | Sharpe autocorr | ret_yr autocorr |
|---|---|---|
| 1 | 0.547 | 0.739 |
| 2 | 0.273 | 0.441 |
| 3 | -0.044 | 0.201 |
| 4 | -0.140 | -0.058 |
| 5 | -0.194 | -0.199 |

## Scheme B: cumulative running average

| Window (k) | End date | Window years | Sharpe | Cumulative avg Sharpe |
|---|---|---|---|---|
| 1 | 2011-01-01 | 1.0 | 2.903 | 2.903 |
| 2 | 2012-01-01 | 2.0 | 0.542 | 1.722 |
| 3 | 2013-01-01 | 3.0 | 0.680 | 1.375 |
| 4 | 2014-01-01 | 4.0 | 1.091 | 1.304 |
| 5 | 2015-01-01 | 5.0 | 1.246 | 1.292 |
| 6 | 2016-01-01 | 6.0 | 0.983 | 1.241 |
| 7 | 2017-01-01 | 7.0 | 1.104 | 1.221 |
| 8 | 2018-01-01 | 8.0 | 1.270 | 1.227 |
| 9 | 2019-01-01 | 9.0 | 0.801 | 1.180 |
| 10 | 2020-01-01 | 10.0 | 0.985 | 1.160 |
| 11 | 2021-01-01 | 11.0 | 0.589 | 1.109 |
| 12 | 2022-01-01 | 12.0 | 0.738 | 1.078 |
| 13 | 2023-01-01 | 13.0 | 0.486 | 1.032 |
| 14 | 2024-01-01 | 14.0 | 0.544 | 0.997 |
| 15 | 2025-01-01 | 15.0 | 0.675 | 0.976 |
| 16 | 2026-01-01 | 16.0 | 0.682 | 0.957 |
| 17 | 2026-01-15 | 16.0 | 0.682 | 0.941 |
