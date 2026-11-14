# EWMAC inactive-contract and probation audit — 2026-11-13

## Scope

This audit explains why `HE`, `BTS`, `TSEMOTHR` (the persisted symbol; the
question called it `TSEMOTHER`), `MCH.HK`, and `SRFXON3` are selected in the
2026 fixed-cap EWMAC book but hold zero contracts. It also defines a causal
way to keep structurally unusable contracts out of a small-account book
without selecting markets from realized P&L or from the current direction of
their trend forecast.

The saved run is:

```text
selection_run_20261113_153041
tv0p2_im20_bm50_wi0p2_st0p85_fdm1_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone
```

The audit reads the saved book, annual review, forecast audit, and daily cash
P&L Parquet files. It does not rerun or overwrite the backtest.

## Finding

All five instruments are **selection-active** in 2026 and every active cash
row has valid P&L inputs. They are **position-flat**, not data-inactive. Their
largest continuous targets remain inside the buffered whole-contract band
that includes zero, so the backtest correctly leaves `held_con` at zero.

The run uses a 10% position buffer and trades to the edge. From a flat
position, a positive target does not enter one contract while the lower edge
of its no-trade band still rounds to zero; the negative case is symmetric.
Merely crossing an unbuffered half contract is therefore insufficient.

| Symbol | 2026-review average position | 2026-review forecast-cap position | 2026 mean absolute forecast | 2026 maximum absolute forecast | 2026 maximum absolute target | Held days / active days |
|---|---:|---:|---:|---:|---:|---:|
| HE | 0.272 | 0.544 | 0.779 | 1.841 | 0.526 | 0 / 217 |
| MCH.HK | 0.407 | 0.813 | 0.382 | 1.322 | 0.507 | 0 / 213 |
| BTS | 0.570 | 1.141 | 0.704 | 1.563 | 0.531 | 0 / 221 |
| TSEMOTHR | 0.805 | 1.611 | 0.404 | 0.850 | 0.558 | 0 / 224 |
| SRFXON3 | 1.175 | 2.350 | 0.308 | 0.692 | 0.534 | 0 / 219 |

Examples from the saved daily cash report make the rounding boundary explicit:

| Symbol | Date | Target | Buffer width | Buffered integer band | Held |
|---|---|---:|---:|---:|---:|
| HE | 2026-11-04 | -0.526 | 0.029 | -1 to 0 | 0 |
| MCH.HK | 2026-07-03 | -0.507 | 0.038 | -1 to 0 | 0 |
| BTS | 2026-09-30 | -0.531 | 0.034 | -1 to 0 | 0 |
| TSEMOTHR | 2026-10-07 | 0.558 | 0.070 | 0 to 1 | 0 |
| SRFXON3 | 2026-01-19 | -0.534 | 0.127 | -1 to 0 | 0 |

This is not caused by missing price sessions: valid-day counts equal active-day
counts for every row in the first table.

## Structural granularity versus a dormant forecast

The five names should not receive one common exclusion rule.

### Structurally too coarse at this capital

`HE` and `MCH.HK` have forecast-cap positions below one contract and average
positions below half a contract. They can trade only during unusually large
forecasts and favorable volatility. Their recent use confirms the structural
problem:

| Symbol | Selected days since 2016 | Nonzero-held days | Held share | Interpretation |
|---|---:|---:|---:|---|
| HE | 972 | 0 | 0.0% | No executable use in four selected recent years |
| MCH.HK | 439 | 7 | 1.6% | Almost never executable |

The existing selector only hard-rejects a forecast-cap position below 0.5.
It also applies the AFTS size penalty `0.125 / max_position^2`, but a penalized
candidate can still enter for diversification or coverage. That lower bound
proves that a contract is barely possible at the forecast cap; it does not
prove that a normal forecast is executable.

### Usable contract, currently dormant signal

`BTS`, `TSEMOTHR`, and `SRFXON3` have average positions above half a contract
and forecast-cap positions above one. They have expressed contracts in prior
selected years:

| Symbol | All selected days | Nonzero-held days | Held share | Relevant prior activity |
|---|---:|---:|---:|---|
| BTS | 985 | 416 | 42.2% | 80.8% in 2017 and 63.7% in 2021 |
| TSEMOTHR | 973 | 128 | 13.2% | 18.2% in 2024 and 29.0% in 2021 |
| SRFXON3 | 472 | 248 | 52.5% | 98.0% in 2024 |

SARONA is the clearest example. Its scalar is large enough, but its mean
absolute forecast fell from 1.521 in 2024 to 0.308 in 2026 and its 2026
maximum is only 0.692. Removing it because today's forecast is small would be
a tactical trend-strength filter, not an instrument-feasibility rule. It
would also prevent the system from entering if the trend subsequently grows.

No selected instrument should be removed or have its risk redistributed
intra-year merely because its current trend forecast is near zero. Cash is a
valid trend-following position, and reallocating dormant risk would change
the portfolio's intended risk behavior.

## Recommended policy

Use two separate controls.

### 1. Hard structural admission floor

For the primary book, require:

```text
average_position >= 0.5
```

which is equivalent under the conventional 20/10 forecast ratio to:

```text
forecast_cap_position >= 1.0
```

Keep the current 0.5 forecast-cap threshold only as a lower diagnostic bound.
At the proposed book weight and IDM, the new one-contract floor means an
ordinary forecast has a realistic chance to clear both integer rounding and
the 10% buffer.

Applied only to the saved 2026 final book, this sensitivity would flag five of
23 names: `HE`, `MCH.HK`, `RS`, `MGC`, and `SONIA.N`. Their 2026 held shares
are respectively 0.0%, 0.0%, 0.5%, 8.8%, and 3.2%, so the rule targets the
book's genuinely low-utilization tail. It would retain `BTS`, `TSEMOTHR`, and
`SRFXON3`.

This floor must be applied before class/subclass coverage is calculated.
Otherwise an unexecutable sole member, such as a coarse contract in a thin
subclass, can satisfy nominal coverage while contributing no actual market
risk. Coverage should describe the implementable candidate universe for this
account, not every market in the reference database.

The current `GreedySelectionConfig.min_max_position` default is 0.5 and is not
exposed as a selection CLI parameter. A future implementation should expose a
named CLI setting, persist it in scenario metadata and all annual review
reports, and use 1.0 as the explicit small-account trial value before changing
the general default.

### 2. Causal utilization probation

Add a probation diagnostic for markets that pass the structural floor but
still rarely offer an executable position. At each annual review:

1. Build a provisional candidate allocation with that review's proposed
   weight, IDM, dated dollar volatility, and forecast history available only
   before the review date.
2. Replay the same target, 10% buffer, whole-contract rounding, and
   trade-to-edge rule over the trailing 256 valid sessions, starting flat.
3. Count a `flat_entry_day` when zero is outside the buffered integer band.
   This measures executable opportunity and does not depend on whether the
   instrument happened to be selected in the prior book.
4. Mark the candidate `probation` when there are fewer than 10 entry days or
   the entry rate is below 5%.
5. Do not let a probationary candidate satisfy minimum-book or coverage
   requirements. It may be retained only as an explicitly reported fallback
   after non-probation candidates are exhausted.
6. Exclude after two consecutive probation reviews. Require at least 20 entry
   days and a 10% entry rate to leave probation, providing entry/exit
   hysteresis.

The exact 5%/10% and 10/20-day thresholds are trial values, not optimized
facts. They should be checked with narrow saved-run sensitivities before being
adopted. The diagnostic must persist its lookback start/end, valid days, entry
days, entry rate, status, consecutive-review count, and reason.

An approximate audit using the 2026 proposed average position and the prior
2025 forecasts gives 0% entry opportunity for `HE`, but 11% for `BTS`, 21%
for `MCH.HK`, 41% for `TSEMOTHR`, and 72% for `SRFXON3`. This illustrates why
the hard structural rule and probation rule are complementary: the structural
rule catches `MCH.HK` even when its prior forecast happened to be strong,
while probation catches persistent dormancy without reacting to one current
forecast observation.

## Rules that should not be used

- Do not gate on historical instrument Sharpe or cash P&L. The selector's
  constant gross-SR assumption deliberately avoids choosing markets from
  realized strategy performance; using the negative `TSEMOTHR` historical
  Sharpe or any other realized outcome would introduce selection overfit.
- Do not gate directly on the forecast at the annual review date. Its sign and
  magnitude are the trading signal, not evidence that the contract is an
  unsuitable instrument.
- Do not redistribute a flat instrument's unused target risk among active
  names. That would lever the portfolio when fewer independent trends exist.
- Do not count a structurally ineligible contract toward class/subclass
  coverage merely to make the coverage report pass.

## Conclusion

For this USD 75,000 fixed-cap book, raise the trial structural floor from a
0.5 to a 1.0 forecast-cap position, equivalently requiring an average position
of at least 0.5 contracts. This directly addresses `HE`, `MCH.HK`, and the
other nearly unused 2026 slots. Add causal utilization probation as an annual
selection diagnostic with hysteresis, but do not remove `BTS`, `TSEMOTHR`, or
SARONA solely because their 2026 forecasts are small. Their zero positions are
currently valid signal outcomes, not evidence of broken data or execution.

## Source artifacts

All files are under:

```text
/home/dev/data/fin/misc/results/selection_v1/ewmac/selection_run_20261113_153041/
  tv0p2_im20_bm50_wi0p2_st0p85_fdm1_ic20_minc1_mins1_mb15_cw50_sw25_full1_cl3_roll1_topdown_classif_capfixed_selyearly_hg31_eh25_mhnone/
```

Artifacts used:

```text
multi_speed_ewmac_selection_book_20261113_153041.parquet
multi_speed_ewmac_selection_cash_pnl_20261113_153041.parquet
multi_speed_ewmac_selection_forecast_audit_20261113_153041.parquet
multi_speed_ewmac_selection_review_instruments_20261113_153041.parquet
```
