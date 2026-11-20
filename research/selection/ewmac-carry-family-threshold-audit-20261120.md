# Carry versus trend forecast activity thresholds

Carry is larger in the typical observation and has a much heavier weak tail at review. In the latest run the median 256-session mean absolute forecast was **1.056 for carry and 0.836 for trend**. At the .25 hard floor, **13.6% of carry windows** fell below the threshold versus **0.2% of trend windows**. Carry data expiry explains about a third of those carry breaches. The gap persists on complete carry windows.

The first separate-threshold test I would run is **carry .40 probation / .25 hard**, with trend still at **.50 / .25**. The activity-only replay let 156 more symbol-review slots through, including six at the 2026 review. Lowering the carry hard floor alone to .20 let eight more slots through and none in 2026. These results measure eligibility. The portfolio effect depends on which of those instruments clears the other gates and takes a book slot.

## Run and measurement

The source is the saved `selection_run_20261120_153928` EWMAC and carry run. It uses fixed $75,000 capital, 20% risk target, annual reviews, a 15% individual initial-margin limit, a minimum maximum position of 1.0, and an eight-calendar-day forecast expiry. Both families use .50 probation and .25 hard thresholds, a 256-session lookback, and hard exclusion on the second consecutive weak review in the same family. An incumbent can remain on its first weak review while a challenger is blocked.

Daily comparisons use the absolute saved, scaled family forecasts before the blend weights. The matched-row table keeps dates where both families are present. Review scores use the 256 instrument sessions strictly before each saved review, counting unavailable family values as zero. The review distributions use the 175 instruments with subsystem eligibility, including challengers and excluded instruments. Of 9,450 possible symbol-review slots over 54 completed reviews, 4,430 had a full 256-session window.

All 2,268 saved review-instrument rows with both family scores matched the reconstruction within 0.000054 forecast units. The reconstructed weak streaks and hard-exclusion flags matched those rows exactly. The 2026 replay also reproduced the saved totals of 46 hard exclusions and 34 probation flags.

## Daily forecast magnitude

| Segment | Matched instrument-sessions | Median trend | Median carry | Mean trend | Mean carry | Carry smaller on matched rows |
|---|---:|---:|---:|---:|---:|---:|
| Full stitched panel | 1,068,449 | 0.791 | 1.066 | 0.869 | 1.110 | 37.5% |
| PST | 965,919 | 0.803 | 1.104 | 0.878 | 1.127 | 36.9% |
| IB | 102,530 | 0.691 | 0.825 | 0.781 | 0.945 | 43.3% |
| IB in 2026 through November 20 | 38,128 | 0.744 | 0.813 | 0.815 | 0.948 | 45.3% |

Across all 1,178,497 daily forecast rows, counting missing forecasts as zero gives mean absolute values of **0.864 trend and 1.012 carry**. For IB alone the corresponding means are 0.787 and 0.849. Carry's magnitude advantage narrows in the IB tail.

## What the annual activity gate sees

| Review years | Full windows | Median trend / carry | 10th percentile trend / carry | Trend / carry below .50 | Trend / carry below .25 |
|---|---:|---:|---:|---:|---:|
| 1973–1999 | 1,161 | 0.874 / 1.323 | 0.499 / 0.428 | 10.2% / 12.9% | 0.4% / 5.3% |
| 2000–2019 | 2,124 | 0.840 / 1.027 | 0.510 / 0.126 | 9.1% / 26.9% | <0.1% / 17.2% |
| 2020–2025 | 970 | 0.780 / 0.785 | 0.461 / 0.152 | 13.6% / 32.2% | 0.2% / 16.1% |
| 2026 | 175 | 0.711 / 0.864 | 0.415 / 0.242 | 26.9% / 25.7% | 1.1% / 10.9% |
| All | 4,430 | 0.836 / 1.056 | 0.488 / 0.181 | 11.1% / 24.4% | 0.2% / 13.6% |
| All, 256 carry forecasts present | 3,098 | 0.833 / 1.180 | 0.488 / 0.317 | 11.1% / 17.4% | 0.3% / 7.7% |

In the latest review, 45 carry scores and 47 trend scores were below .50. At .25 the count was 19 carry and two trend. Carry also triggered 14 hard exclusions through repeated weakness above its direct floor, matching trend's 14 such triggers. Three instruments triggered hard flags in both families, leaving 46 unique hard exclusions.

The distribution varies across asset classes. In the 2020–2026 reviews, median carry versus trend was **0.55 versus 0.69 in metals** and **0.61 versus 0.81 in equities**, compared with **1.09 versus 0.68 in energy** and **1.14 versus 0.86 in agriculture**. Grouping executable aliases by history code and taking the median score per code and review gives the same overall pattern. Medians are 1.029 carry versus 0.835 trend, and 14.1% versus 0.2% of grouped windows breach .25.

Matching the share of trend scores below .50 would use a carry threshold near **.116 in 2000–2019, .201 in 2020–2025, and .520 in 2026**. The breach rate alone is an unstable calibration target.

### Weak carry versus missing carry

Comparing the same review windows against the pre-cutoff saved forecast panel shows 398 carry windows below .25 before expiry and 602 after. The eight-day cutoff pushed **204** windows below .25 and **196** below .50. In 2026 it pushed **three** below .25 and **one** below .50.

Of the 602 below .25 after expiry, **237 had all 256 carry forecasts** and **269 had fewer than 128**. The gate combines two effects. A family can be persistently weak when observed, and missing sessions further dilute the full-window mean. AIGCI had **0 of 256** available carry forecasts in the 2026 review. M6B had **256 of 256** and a mean absolute carry forecast of **0.0469**. Both fail the tested thresholds, for different reasons.

## Separating the two carry thresholds

The replay changes carry probation and hard thresholds, recomputes streaks on the annual review dates, and leaves trend, forecast expiry, and the saved incumbent path fixed. A newly passing slot means the activity gate permits entry or retention where it previously blocked it. The replay does not recalculate margin, ranking, correlations, contracts, or P&L.

| Carry probation / hard | Hard exclusions across full windows, either family | Newly passing symbol-review slots | 2026 hard exclusions | 2026 newly passing |
|---|---:|---:|---:|---:|
| .50 / .25, saved baseline | 946 | 0 | 46 | 0 |
| .50 / .20 | 921 | 8 | 46 | 0 |
| .45 / .225 | 875 | 92 | 44 | 3 |
| .40 / .25 | 829 | 156 | 42 | 6 |
| .40 / .20 | 792 | 165 | 41 | 6 |
| .35 / .175 | 693 | 256 | 34 | 12 |
| .30 / .15 | 630 | 321 | 29 | 15 |

The .40 probation threshold with the existing hard floor captures 156 of the 165 newly passing slots from .40 / .20, with the same six 2026 names. Lowering the hard floor alone can turn some direct exclusions into probation. A challenger with carry below .50 still cannot enter, and repeat weakness still hard-fails. That is why changing only the hard floor has little effect on entry.

| 2026 newly passing at .40 / .25 | Trend score | Carry score | Available carry sessions | Saved outcome |
|---|---:|---:|---:|---|
| C | 0.987 | 0.440 | 210 / 256 | Blocked weak challenger |
| ETHUSDRR | 0.749 | 0.485 | 256 / 256 | Blocked weak challenger |
| IXU | 0.665 | 0.446 | 256 / 256 | Blocked weak challenger |
| IXY | 0.826 | 0.495 | 256 / 256 | Repeated carry weakness |
| PL | 0.888 | 0.491 | 256 / 256 | Repeated carry weakness |
| XINA50 | 0.708 | 0.429 | 256 / 256 | Repeated carry weakness |

BTS, GBS, MCL and SCI still fail the trend gate at the 2026 review. AIGCI and M6B still fail carry. MNTPX, a saved 2025 incumbent, reopens at .30 / .15, with carry activity 0.329 on 249 available sessions and trend activity 1.038. The .40 / .25 variant is a narrower test of marginal carry weakness.

Among the 150 slots restored by .40 / .25 with a next-year review window available, 82 had carry at or above .50 at the next review and 19 fell below .25. That is a check on subsequent forecast activity from the saved panel. It cannot show whether adding or retaining those positions would have improved the selected book.

## Source artifacts

The source root is `/home/dev/data/fin/misc/results/selection_v1/ewmac_carry/`. The latest run's scenario contains the saved forecasts, forecast audit, candidates, reviews, review instruments, and portfolio curve Parquets used for this report. The matched expiry check uses the forecasts from `selection_run_20261120_145246`. Both runs use the November 20 snapshot and rule report. The companion audit has the actual realized book comparison and annual backtest results. [Per-family probation run audit](ewmac-carry-family-probation-audit-20261120.md)
