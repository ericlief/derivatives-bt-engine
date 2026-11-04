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

This saved run includes an extra April 30, 2024 handoff review after the
reference-history freshness window elapsed. The selector has since been
changed to review only on its configured schedule; the 31-day freshness check
still filters candidates at scheduled reviews.

## Annual-only rerun

Run `20261103_185405` removes the April handoff review. The January 1 book
therefore remains active through the end of 2024. Through the last fully
covered date, 2026-10-23, net equity is $275.53M versus $255.47M in the
handoff run. Return from 2024-01-01 improves from -6.97% to +0.34%, and the
maximum drawdown improves from 26.74% to 21.11%. The partial November 3
endpoint is $272.62M versus $253.11M, a $19.51M difference.

The runs are identical through 2024-04-29. From April 30 through December 31,
the annual-only book loses $22.02M versus $41.18M for the handoff book, a
$19.16M improvement. The ten instruments added by the April review lose
$19.96M in that segment. ETHUSDRR loses $7.45M, MCL loses $6.86M, HG loses
$2.22M, IXE loses $1.65M, and M2K loses $1.07M. Keeping the January book also
raises NQ P&L by $4.22M and CHF P&L by $2.69M, although retained RB performs
$8.30M worse. The result is an ex-post difference in market P&L from the two
books; the annual schedule did not use future returns to choose the January
book.

Most of the improvement remains after 2024. The equity advantage is $19.16M
at the end of 2024, narrows to $18.03M at the end of 2025, and widens to
$20.07M by 2026-10-23. Thus the later annual reviews and compounding preserve
the 2024 gain rather than create it.

## Cost attribution and contract size

From 2024-01-01 through 2026-10-23, the annual-only run earns $6.21M gross
and pays $5.28M in trading and roll costs, leaving $0.93M net. Trading costs
are $3.40M, or 64.4% of total costs; roll costs are $1.88M, or 35.6%. The
largest contributors are SCI ($757k), SONIA.N ($486k), ETHUSDRR ($297k),
TSR20 ($269k), and RB ($177k), together accounting for 37.6% of all costs.
Agriculture contributes $1.53M of cost, metals $1.02M, bonds $918k, and
equities $832k.

Lower costs do not explain the annual-only run's higher P&L. Its gross P&L is
$20.24M better than the handoff run through October 23, while its cumulative
cost is $170k higher. During the changed 2024 segment, avoiding MCL and M2K
saves only about $94k of their realized costs; avoiding their gross losses is
the material effect.

Larger contracts are cheaper per unit of risk in the main size families. For
example, reference trade-plus-roll cost SR is about 37% lower for ES than MES,
46% lower for RTY than M2K, 31% lower for NQ than MNQ, and 53% lower for YM
than MYM. In 2025, the annual-only run uses ES and pays $6.2k, while the
handoff run uses MES and pays $8.8k. This helps at the margin, but the
annual-only 2024-2026 books already select the larger NQ, ES, RTY, YM, GC,
HSI, IBEX35, JGB, and ETHUSDRR contracts when those families enter. The main
cost contributors SCI, SONIA.N, and TSR20 do not have a larger selected
substitute in this universe. Contract-size substitution therefore cannot
remove most of the $5.28M cost burden.

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

The corrected annual-only artifacts are under
`/home/dev/data/fin/misc/results/selection_v1/selection_run_20261103_185405/`
in the same scenario name. The corresponding artifact filenames end in
`20261103_185405`.
