# Selection run audit: November 3, 2026

## Class and subclass limits

The rerun used `max_instr_per_cls=20`, `max_instr_per_subcls=15`,
`max_cls_weight_pct=50`, and `max_subcls_weight_pct=25`. The 2026-01-01
review had 137 candidates and selected 59 instruments, compared with nine
instruments in the earlier run. Concentration improved: the top five held
42.8% of deployed risk (previously 59.8%), and effective instrument count
rose from 8.9 to 20.7. The equity class count limit and bonds subclass count
limit bind; the class and subclass weight caps do not. Maximum deployed class
weight is about 17.7%.

## Performance after 2024

Through 2026-10-23, the portfolio lost 6.97% from 2024-01-01, with 11.14%
annualized realized volatility, a -0.14 Sharpe ratio, and a 26.74%
peak-to-trough drawdown. The portfolio peaked at $286.33M on 2024-04-11 and
bottomed at $209.76M on 2025-08-02, recovering to $255.47M by 2026-10-23.
Calendar 2024 returned -14.80%, 2025 returned +1.54%, and 2026 returned
+7.53% through October 23. Since 2024, the largest instrument losses were
SCI (-$19.83M), SONIA.N (-$13.73M), DX (-$10.43M), ETHUSDRR (-$8.95M), and
HG (-$8.81M). Bond and FX classes lost about $23.0M and $21.9M respectively.
Gross P&L was -$16.34M and trading plus roll costs were -$5.16M through the
full November 3 endpoint; costs explain about 24% of the loss.

## Ending history coverage

The November 3 endpoint has incomplete instrument coverage and should not be
used as the comparable performance endpoint. All 59 selected instruments
have valid P&L through October 23; by November 3, only 48 remain current,
leaving 22.0% of planned risk weight missing. The surviving book has a 37.9%
top-three risk share and an effective count of 16.3. The refreshed IB history
database ends on October 23, which matches the last fully covered date.

The recent review equity was roughly $238M-$275M, so this run does not
estimate what a new $75k account can trade. Its selection performance and
contract granularity are specific to the much larger dynamic account.

## Artifacts

The rerun artifacts are under
`/home/dev/data/fin/misc/results/selection_v1/selection_run_20261103_122141/`
in scenario
`tv0p2_im20_bm50_wi0p2_st0p85_fdm1_ic20_cw50_sw25_cl15_roll1_topdown_classif_selyearly_hg31`.
The main audit files are:

- `multi_speed_ewmac_selection_portfolio_curve_20261103_122141.parquet`
- `multi_speed_ewmac_selection_review_instruments_20261103_122141.parquet`
- `multi_speed_ewmac_selection_cash_pnl_20261103_122141.parquet`
