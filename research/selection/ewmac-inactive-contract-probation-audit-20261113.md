# EWMAC inactive-contract and probation audit — corrected 2026-11-16

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

The audit reads the saved book, greedy trials, trial allocations, selection
passes, annual review, forecast audit, and daily cash P&L Parquet files. It
does not rerun or overwrite the backtest.

This corrected version distinguishes the selector's stable 15-market
`ref_max_pos` from the iteration-specific and final-book `max_position`.
The original audit incorrectly used final-book positions as though they were
candidate-admission positions; in particular, it incorrectly classified `RS`
as structurally sub-one-contract.

## Finding

All five instruments are **selection-active** in 2026 and every active cash
row has valid P&L inputs. They are **position-flat**, not data-inactive. Their
largest continuous targets remain inside the buffered whole-contract band
that includes zero, so the backtest correctly leaves `held_con` at zero.

The run uses a 10% position buffer and trades to the edge. From a flat
position, a positive target does not enter one contract while the lower edge
of its no-trade band still rounds to zero; the negative case is symmetric.
Merely crossing an unbuffered half contract is therefore insufficient.

| Symbol | 15-market reference maximum | Maximum when admitted | Final-book maximum | 2026 mean absolute forecast | 2026 maximum absolute target | Held days / active days |
|---|---:|---:|---:|---:|---:|---:|
| HE | 0.761 | 0.544 | 0.544 | 0.779 | 0.526 | 0 / 217 |
| MCH.HK | 2.277 | 1.220 | 0.813 | 0.382 | 0.507 | 0 / 213 |
| BTS | 3.194 | 1.141 | 1.141 | 0.704 | 0.531 | 0 / 221 |
| TSEMOTHR | 4.510 | 4.833 | 1.611 | 0.404 | 0.558 | 0 / 224 |
| SRFXON3 | 6.579 | 3.524 | 2.350 | 0.308 | 0.534 | 0 / 219 |

The stable reference assumes `1 / 15` weight and caps weight times IDM at
0.1667. The admitted maximum comes from the full trial book at the iteration
where the candidate wins. The final maximum comes after all 23 members have
entered and top-down weights and IDM have been recomputed.

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

## Greedy trial audit

`RS` demonstrates why final-book `max_position` cannot define intrinsic
candidate eligibility:

- Singleton trial: reference maximum 2.659, actual singleton maximum 3.191;
  it loses the seed decision to `M2K`.
- Iteration 1: `RS` is admitted as the second member of `M2K,RS`, still with
  an actual maximum of 3.191.
- Final 23-member book: its allocation falls to a 0.317 average position and
  a 0.633 forecast-cap maximum.

The selected instruments follow the same entry-to-final mechanism:

| Symbol | Entry step | Reference maximum | Entry maximum | Final maximum |
|---|---:|---:|---:|---:|
| RS | 1 | 2.659 | 3.191 | 0.633 |
| TSEMOTHR | 7 | 4.510 | 4.833 | 1.611 |
| SONIA.N | 9 | 2.333 | 2.500 | 0.833 |
| MGC | 10 | 0.595 | 0.637 | 0.637 |
| HE | 11 | 0.761 | 0.544 | 0.544 |
| MCH.HK | 14 | 2.277 | 1.220 | 0.813 |
| BTS | 18 | 3.194 | 1.141 | 1.141 |
| SRFXON3 | 21 | 6.579 | 3.524 | 2.350 |

Every addition rescales and checks the entire hypothetical book. There is no
missing revalidation bug at the existing 0.5 physical floor. The conceptual
error was treating the final allocation as the stable candidate reference.

## Structural granularity versus a dormant forecast

The five names should not receive one common exclusion rule.

### Structurally too coarse under the stable candidate reference

Of the five questioned instruments, only `HE` is below one forecast-cap
contract under the stable 15-market candidate reference. `MCH.HK` is not:
its reference maximum is 2.277 and it enters the greedy book with a 1.220
maximum. Its maximum falls to 0.813 only after later additions reduce its
final allocation.

Recent use still shows that both names deserve attention, but for different
reasons:

| Symbol | Selected days since 2016 | Nonzero-held days | Held share | Interpretation |
|---|---:|---:|---:|---|
| HE | 972 | 0 | 0.0% | Reference and actual allocation are both coarse |
| MCH.HK | 439 | 7 | 1.6% | Viable reference candidate, underused after final allocation |

The existing selector checks an iteration-specific 0.5 minimum for every
member of every proposed trial book. Consequently it does reprice and
revalidate `RS` as the book grows; `RS` remains feasible because its final
maximum is 0.633, still above 0.5. The selector also applies the AFTS size
penalty `0.125 / max_position^2`.

The stable reference position currently supports contract-band comparison
and near-tie selection. It is not a hard candidate-universe gate. A new
reference gate must therefore be a separate parameter from the existing
iteration-specific physical floor.

### Usable contract, currently dormant signal

`MCH.HK`, `BTS`, `TSEMOTHR`, and `SRFXON3` all have stable reference maxima
above one contract. The latter three have expressed contracts materially in
prior selected years:

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

Make the existing whole-book physical floor configurable:

```text
max_position >= selection_min_max_position
```

The current default of `0.5` preserves the half-contract rule. Sensitivities
of `1.0` and `2.0` correspond, under the conventional 20/10 forecast ratio,
to average-position floors of `0.5` and `1.0` contracts. The calculation uses
each trial member's recalculated `limit_weight * IDM`, not the stable
15-market reference position.

This allocation dependence is intentional: the policy asks whether every
member remains usable in the proposed whole book after the new candidate is
added. The selector must reject that trial and evaluate the remaining
candidates, rather than cap the book at a fixed count or delete undersized
members in a one-shot final pass. If no alternative clears the floor, the
book stops or reports an unmet coverage target.

On the saved accepted 2026 path, a floor of `1.0` rejects the proposed 11th
member `MGC`, whose own maximum position is `0.637`. A floor of `2.0` changes
the path earlier because the proposed fourth member `MHG` has a maximum
position of `1.442`. These observations do not determine the new final book
counts because each rejection reopens competition among the remaining
candidates.

The stable `ref_max_pos` remains useful for near-tie comparisons and audit,
but it does not prevent later allocation dilution. It therefore cannot solve
the inactive-slot problem by itself. The CLI setting is
`--selection-min-max-position`; the grid form is
`--selection-min-max-positions .5,1,2`. Persist the active floor in scenario,
trial, accepted-pass, and annual-review reports.

### 2. Causal utilization probation

Add a probation diagnostic for markets that pass the structural floor but
still rarely offer an executable position. At each annual review:

1. Build the final provisional book and use each member's proposed final
   weight, IDM, dated dollar volatility, and forecast history available only
   before the review date. Retain `ref_max_pos` beside this allocation metric
   so structural and allocation-induced underuse remain distinguishable.
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

An approximate audit using the 2026 final proposed average position and prior
2025 forecasts gives 0% entry opportunity for `HE`, but 11% for `BTS`, 21%
for `MCH.HK`, 41% for `TSEMOTHR`, and 72% for `SRFXON3`. This illustrates why
the structural whole-book floor and allocation-utilization probation are
complementary: the floor controls achievable contract scale while probation
measures whether the historical signal actually presented executable entry
opportunities. At the proposed
5% threshold, however, its 21% prior-year opportunity would not have predicted
the 2026 dormancy; that dormancy is new signal behavior, not grounds for a
retrospective eligibility label. `RS` is likewise a probation or
allocation-efficiency question after applying the configured whole-book floor
failure.

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

For this USD 75,000 fixed-cap book, keep the actual trial-book physical floor
at 0.5 and add a separate stable `ref_max_pos >= 1.0` candidate gate based on
the configured 15-market equal-weight reference. That reference gate flags
`HE` and `MGC`, not `RS` or `MCH.HK`. Treat small final allocations such as
`RS` and `MCH.HK` as utilization/probation diagnostics rather than intrinsic
contract failures. Add causal probation with hysteresis, but do not remove
`BTS`, `TSEMOTHR`, or SARONA solely because their 2026 forecasts are small.
Their zero positions are valid signal outcomes, not evidence of broken data
or execution.

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
multi_speed_ewmac_selection_passes_20261113_153041.parquet
multi_speed_ewmac_selection_review_instruments_20261113_153041.parquet
multi_speed_ewmac_selection_trial_allocations_20261113_153041.parquet
multi_speed_ewmac_selection_trials_20261113_153041.parquet
```
