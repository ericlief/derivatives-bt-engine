# Selection-grid EDA: capital fit, membership, and contract granularity

Research date: 2026-10-16.

## Scope and interpretation

This note analyzes the two completed construction grids under
`/home/dev/data/fin/misc/results`:

- the 18-run target-volatility grid in `selection_grid_20261016_123340`, which
  varies target volatility (`15%, 20%, 25%`), maximum instrument margin
  (`15%, 20%, 25%`), and the economic-subclass cap (`2` or unlimited); and
- the 18-run structural grid at the result-directory root, which holds target
  volatility at 20% and maximum instrument margin at 20%, then varies maximum
  book margin (`50%, 75%, 100%`), maximum weight times IDM
  (`0.15, 0.20, 0.25`), and the subclass cap (`2` or `3`).

All 36 runs use $75,000 initial capital, top-down classification weights, and
include roll cost in the construction score. The two central runs use the exact
same rule cache (SHA-256
`5865e8770bccfb2c2e73831166f2248a0a59f3989e39be1f213d28831b523ecb`)
and produce byte-identical 20% / 20% instrument-margin / 75% book-margin /
0.20 weight-IDM / cluster-2 books. The eligible universe contains 111
instruments in both grids. Every candidate passes the fixed input screens:

| Input screen | Fixed value |
|---|---:|
| Instrument one-way cost limit | 0.01 SR |
| Rule annual cost limit | 0.15 SR |
| Minimum daily contracts | 100 |
| Maximum market participation | 1% |
| Liquidity window | 256 days |

These are static construction robustness results. Construction scores below
are used only to explain the greedy stopping path, not to rank parameter
scenarios or claim performance. A performance comparison still requires a
separate point-in-time walk-forward selector.

## Main conclusions

1. **The 25% target is materially tighter for this capital.** The cluster-2
   book reaches $55,575, or 74.1% of capital and 98.8% of its 75% margin
   allowance. At the terminal iteration, 21 alternatives fail the portfolio
   margin gate. The continuous model targets 25% annual risk by construction;
   the additional instruments do not independently add risk because weights
   still sum to one. They do add integer-position tracking risk and operational
   complexity, while the higher target itself is explicitly more risk.
2. **The raw instrument count overstates useful breadth.** At 20%, the
   cluster-2 book has 19 instruments but an effective risk-weighted count of
   16.2. The unlimited book has 28 instruments but an effective count of 20.3
   and only seven subclasses, compared with ten subclasses in the smaller
   cluster-2 book.
3. **Granularity is poor at both ends.** In the central 19-instrument book,
   eight instruments can hold at most one contract at forecast 20 and carry
   34.7% of risk weight. Nine more can hold only two. CHF micro is the only
   6-to-10-contract instrument, while Ether micro has an average position of
   28.8 and a rounded maximum of 58. No member lies in the 3-to-5 maximum-
   contract band.
4. **The 90% greedy tolerance creates much of the long tail.** The central
   cluster-2 score peaks at 15 instruments. Four later additions reduce it by
   7.1%, from 1.3426 to 1.2468, but remain inside the current tolerance. The
   15-instrument book has 20.8% of risk weight in the 3-to-5 maximum-contract
   band; this falls to zero when the 17th instrument enters.
5. **A subclass cap of two is doing useful work.** Across target-volatility
   levels it produces 18–21 instruments and 9–12 subclasses. Unlimited books
   contain 25–28 instruments but only 7–9 subclasses. The cap removes nominal
   breadth concentrated in the same economic exposures.
6. **The instrument-margin sweep is inactive, and 75% versus 100% book margin
   is redundant for selected membership.** The largest selected instrument
   margin is $9,030, or 12.0% of capital, so even the 15% instrument cap never
   binds. Every matched 75% and 100% structural scenario selects the same
   book. The 50% cap is binding and changes membership.
7. **Cost and liquidity sensitivity cannot be inferred from these grids.** All
   runs use the same limits and every saved candidate has already passed them.
   The central book nevertheless has meaningful cost concentration: five
   instruments with annual cost of at least 0.10 SR carry 25.0% of risk weight
   and 52.4% of the book's weight-adjusted annual cost.

## Target-volatility and subclass-cap grid

Changing the maximum instrument-margin cap from 15% to 20% or 25% has no
effect on membership, weights, positions, or margins. The 18 scenarios reduce
to six unique results:

| Target vol | Subclass cap | Instruments | Effective instruments | Asset classes | Subclasses | Book margin / capital | One-contract weight | Weight below 1 average contract | Weight in 3–5 max contracts |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 15% | 2 | 18 | 16.0 | 6 | 9 | 50.9% | 50.0% | 75.0% | 12.5% |
| 15% | unlimited | 25 | 19.2 | 6 | 7 | 52.4% | 32.5% | 73.4% | 12.8% |
| 20% | 2 | 19 | 16.2 | 6 | 10 | 64.2% | 34.7% | 55.6% | 0.0% |
| 20% | unlimited | 28 | 20.3 | 6 | 7 | 61.0% | 38.1% | 42.9% | 15.5% |
| 25% | 2 | 21 | 18.5 | 7 | 12 | 74.1% | 40.5% | 58.3% | 10.7% |
| 25% | unlimited | 28 | 15.7 | 6 | 9 | 70.2% | 44.0% | 58.3% | 35.4% |

`Effective instruments` is `1 / sum(weight^2)`. `One-contract weight` is the
sum of portfolio risk weights for rows with `max_cons == 1`. Average contracts
use the forecast-10 `avg_cons`; maximum contracts use the rounded forecast-20
`max_cons`.

The 15% target does not solve the small-account problem. It reduces intended
risk and margin, but smaller positions make contract granularity worse: 75.0%
of the cluster-2 risk weight has an average position below one contract. The
25% target improves some position sizes but changes the selected set sharply
and nearly exhausts the 75% book-margin allowance.

### Membership stability

The three instrument-margin values are exact duplicates. Membership changes
come from target volatility and the subclass cap:

| Comparison | Cap | Book sizes | Jaccard | Additions | Removals |
|---|---:|---:|---:|---:|---:|
| 15% to 20% target | 2 | 18 to 19 | 0.682 | 4 | 3 |
| 20% to 25% target | 2 | 19 to 21 | 0.538 | 7 | 5 |
| 15% to 20% target | unlimited | 25 to 28 | 0.893 | 3 | 0 |
| 20% to 25% target | unlimited | 28 to 28 | 0.514 | 9 | 9 |
| Cap 2 to unlimited at 15% | — | 18 to 25 | 0.483 | 11 | 4 |
| Cap 2 to unlimited at 20% | — | 19 to 28 | 0.424 | 14 | 5 |
| Cap 2 to unlimited at 25% | — | 21 to 28 | 0.441 | 13 | 6 |

Only eight instruments appear in all 18 target-volatility scenarios:
`BITCOIN`, `BRE`, `CHF_micro`, `CRUDE_W_micro`, `GAS_US_mini`,
`HANGENT_mini`, `JGB-mini`, and `MUMMY`. The 20-to-25% transition is a regime
change in construction membership, not a small expansion of the 20% book.

## Structural grid

The 75% and 100% book-margin results are identical for every matched
weight-IDM and subclass-cap combination. Relative to 75%, the 50% margin cap
produces the following changes:

| Weight × IDM cap | Subclass cap | Instruments at 50% / 75% | Jaccard | Added / removed at 50% | Final use of 50% allowance |
|---:|---:|---:|---:|---:|---:|
| 0.15 | 2 | 16 / 19 | 0.842 | 0 / 3 | 97.6% |
| 0.15 | 3 | 21 / 22 | 0.792 | 2 / 3 | 96.0% |
| 0.20 | 2 | 18 / 19 | 0.762 | 2 / 3 | 92.9% |
| 0.20 | 3 | 21 / 22 | 0.720 | 3 / 4 | 98.3% |
| 0.25 | 2 | 18 / 19 | 0.682 | 3 / 4 | 99.9% |
| 0.25 | 3 | 17 / 24 | 0.577 | 2 / 9 | 97.9% |

The 50% cap is a real construction constraint: terminal iterations contain
6–43 portfolio-margin rejections, depending on the other settings. The 75%
cap can still block alternatives in the largest cluster-3 books, but it never
changes the selected set relative to 100%.

The subclass cap is also path-sensitive. At 75% or 100% book margin, changing
the cap from two to three gives Jaccard overlap of 0.708–0.720 and usually adds
five or six instruments while removing one or two. At 50% book margin, overlap
falls to 0.560–0.609 for weight caps 0.15 and 0.20. Allowing one more member per
subclass therefore changes the greedy path well beyond the directly affected
subclass.

Two current scenarios are useful construction candidates for the next
sensitivity round:

| Book margin | Weight × IDM | Subclass cap | Instruments | Classes / subclasses | Margin / capital | One-contract weight | 3–5 max-contract weight |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 50% | 0.15 | 2 | 16 | 6 / 9 | 48.8% | 34.7% | 20.8% |
| 50% | 0.20 | 2 | 18 | 7 / 11 | 46.5% | 33.3% | 21.4% |

The first is more compact. The second retains all seven observed classes and
broader subclass coverage. These are feasibility anchors, not performance
winners.

## Central 20% book: positions, weights, and costs

The central 19-member cluster-2 book assigns approximately one sixth of risk
to each of six selected asset classes. Crypto, energy, and FX each have two
members at 8.33% apiece. Agriculture has five members with weights from 2.78%
to 5.56%. This classification logic explains how low-granularity agriculture
rows can enter with small individual weights while Ether micro remains a large
contract-count outlier with an 8.33% weight.

### Maximum-contract distribution

| Rounded maximum contracts | Instruments | Risk-weight mass | Share of construction-score contribution |
|---:|---:|---:|---:|
| 1 | 8 | 34.7% | 27.1% |
| 2 | 9 | 48.6% | 53.9% |
| 3–5 | 0 | 0.0% | 0.0% |
| 6–10 | 1 | 8.3% | 10.6% |
| More than 10 | 1 | 8.3% | 8.4% |

The construction-score shares normalize `scaled_contrib` by the final book
score. They are explanatory only.

### Average-contract distribution

| Average contracts | Instruments | Risk-weight mass |
|---:|---:|---:|
| Below 0.5 | 5 | 18.1% |
| 0.5 to below 1 | 7 | 37.5% |
| 1 to below 2 | 5 | 27.8% |
| 2 to below 3 | 1 | 8.3% |
| 3 to 5 | 0 | 0.0% |
| More than 10 | 1 | 8.3% |

The median average position is 0.879 contracts. The weight-averaged mean is
3.31 only because Ether micro contributes 28.78 average contracts.

### Selected extremes

| Instrument | Weight | Average contracts | Continuous max | Rounded max | Size penalty SR | Annual cost SR |
|---|---:|---:|---:|---:|---:|---:|
| `RUBBER` | 2.78% | 0.281 | 0.563 | 1 | 0.3947 | 0.0934 |
| `LUMBER-new` | 2.78% | 0.328 | 0.656 | 1 | 0.2902 | 0.1120 |
| `SP500_micro` | 4.17% | 0.423 | 0.846 | 1 | 0.1746 | 0.0069 |
| `CANOLA` | 2.78% | 0.456 | 0.911 | 1 | 0.1506 | 0.0346 |
| `CHEESE` | 5.56% | 0.466 | 0.931 | 1 | 0.1442 | 0.1368 |
| `CHF_micro` | 8.33% | 2.850 | 5.701 | 6 | 0.0038 | 0.0217 |
| `ETHER-micro` | 8.33% | 28.777 | 57.554 | 58 | approximately 0 | 0.1247 |

The current Carver size penalty is already severe at the low end:
`0.125 / max_position^2`. Rubber's net instrument SR is only about 0.012 after
cost and size penalty, yet it remains in the final book because the portfolio
stopping rule tolerates a decline from the best score. This is evidence that
the acceptance rule deserves attention before inventing a much stronger
instrument-level penalty.

The central weight-adjusted annual cost is 0.0584 SR: 0.0320 from trading and
0.0264 from rolls. The weight-adjusted size penalty is 0.0681 SR. `SONIA3`,
`CHEESE`, `ETHER-micro`, `EURIBOR-ICE`, and `LUMBER-new` each have annual cost
of at least 0.10 SR. Together they carry 25.0% of risk weight and 52.4% of the
weight-adjusted cost.

## Binding reasons and the greedy tail

At 20% with cap two, the terminal candidate set has 48 failures of the
half-contract gate and 38 subclass-cap failures. The individual margin gate
does not bind. At 25%, portfolio margin becomes binding: the cap-two terminal
iteration has 21 portfolio-margin failures, while the unlimited version has
four.

The current 90% rule accepts a candidate while its trial score remains at
least 90% of the best score seen. The already saved accepted-pass audit gives
the following same-path stopping sensitivity for the central books:

| Minimum fraction of best score | Cap-two book size | Unlimited book size |
|---:|---:|---:|
| 100% | 15 | 17 |
| 99% | 16 | 20 |
| 97.5% | 17 | 22 |
| 95% | 18 | 24 |
| 90% | 19 | 28 |

This is a local counterfactual along the observed greedy path. A rerun is still
required because a changed acceptance policy may also change later search
behavior if additional coverage rules are introduced.

For the cap-two book, the contract distribution evolves as follows:

| Book size | Score | One-contract weight | Two-contract weight | 3–5 contract weight | 6–10 contract weight | More-than-10 weight |
|---:|---:|---:|---:|---:|---:|---:|
| 15, score peak | 1.3426 | 25.0% | 37.5% | 20.8% | 8.3% | 8.3% |
| 16 | 1.3347 | 26.4% | 40.3% | 16.7% | 8.3% | 8.3% |
| 17 | 1.3125 | 26.4% | 57.0% | 0.0% | 8.3% | 8.3% |
| 18 | 1.2922 | 34.7% | 48.6% | 0.0% | 8.3% | 8.3% |
| 19, final | 1.2468 | 34.7% | 48.6% | 0.0% | 8.3% | 8.3% |

The 16th through 19th additions are `RUBBER`, `SP500_micro`, `DOW`, and
`LUMBER-new`. A tighter tolerance is therefore the cleanest existing control
for a smaller book with more weight in the 3-to-5 maximum-contract range.

## Recommended penalties and screens

### 1. Test the acceptance tolerance first

Expose and sweep the minimum fraction of the best score at `0.90, 0.95,
0.975, 0.99, 1.00`. Keep 0.90 as the documented AFTS baseline and label the
others as small-account construction sensitivities. Report the full accepted
path so the compact book is chosen from feasibility, coverage, membership
stability, and granularity rather than from the score level.

This parameter directly controls the weak marginal tail and, on the observed
path, spans 15–19 members with cap two. It is more transparent than adding a
generic cardinality penalty.

### 2. Add book-level granularity diagnostics and soft limits

For each trial book, report:

```text
one_contract_weight = sum(weight_i for max_cons_i == 1)
low_average_weight = sum(weight_i for avg_cons_i < 1)
target_band_weight = sum(weight_i for 3 <= max_cons_i <= 5)
effective_instruments = 1 / sum(weight_i**2)
```

Reasonable sensitivity values, based on the observed path rather than claimed
universal thresholds, are:

- soft cap `one_contract_weight` at 25%, 30%, and 35%; and
- soft floor `target_band_weight` at 15%, 20%, and 25%.

Apply these after minimum class coverage so a scarce diversifier is not
discarded solely because its executable contract is coarse. Save the penalty
and rejection reason separately from the AFTS size penalty.

### 3. Add a soft elbow below three maximum contracts

The current inverse-square penalty has a hard feasibility edge only below 0.5
continuous maximum contracts. A targeted sensitivity can add a hinge penalty
for continuous maximum position `P < 3`:

```text
granularity_penalty_i = lambda * max(0, 3 / P_i - 1)**2
```

Sweep `lambda` over `0.01, 0.025, 0.05`. This leaves positions of three or
more untouched, adds a modest penalty near two, and rises quickly near one.
Keep it as a separately reported construction penalty so its effect is not
confused with transaction cost or the published AFTS size penalty.

An alternative implementation sweep is to retain the existing functional
form while varying the size-penalty scale from `0.125` to `0.25` and `0.50`,
and the minimum continuous maximum position from `0.5` to `1.0` and `1.5`.
The hinge is more directly aligned with the desired 3-to-5 maximum-contract
band.

### 4. Measure integer tracking error from the saved forecast path

Contract counts are a proxy for the actual concern. For every trial book,
round the saved continuous targets using the production rule and report:

- annualized RMS dollar-risk tracking error divided by the portfolio risk
  target;
- the fraction of instrument-days rounded to zero;
- the fraction of risk weight attached to instruments that are zero more than
  half the time; and
- the maximum one-contract risk quantum as a fraction of the portfolio target.

A book-level tracking-error limit directly measures whether nominal
diversification survives integer execution. It also handles Ether micro
correctly: 58 maximum contracts are not a risk-granularity problem, although
they may be a cost or operational-load problem.

### 5. Penalize operational contract load separately

If Ether-sized positions are operationally undesirable, use a separate
contracts-traded budget such as annual trade contracts plus twice annual roll
contracts. Transaction cost already charges commissions and spreads; this
additional diagnostic should represent broker, monitoring, and execution
capacity rather than duplicate monetary cost.

When several executable routes share an economic family, add a route-level
preference for the contract whose average or maximum position lies in the
desired band, subject to cost, liquidity, and margin. A U-shaped route penalty
can discourage both one-lot full contracts and dozens of tiny micro contracts
without removing the economic exposure.

### 6. Keep cluster breadth and raw book size distinct

Retain the cap-two sensitivity. It creates fewer instruments with more
economic-subclass coverage than the unlimited books. Also report the ratio of
effective to raw instruments; a falling ratio identifies nominal additions
that contribute little independent risk weight.

A hard global instrument-count ceiling can be included as a transparent
operational sensitivity, for example 15, 18, and 21, but should follow class
coverage and should not replace the granularity and tracking-error measures.

## Next grid

Use the 20% target and subclass cap two as the center. Carry both 50% book-
margin anchors (`weight × IDM` 0.15 and 0.20) plus the 75% / 0.20 baseline.
Cross those with the acceptance-tolerance and granularity-penalty values above.
This isolates the small-account construction question before changing input
eligibility.

Then run the already planned boundary grids separately:

- instrument one-way cost limit `0.0075, 0.01, 0.015`;
- rule annual cost limit `0.10, 0.15, 0.20`, rebuilding the rule cache for each;
- maximum market participation `0.5%, 1%`; and
- minimum daily contracts `100, 250`.

For every grid, compare book size, effective count, Jaccard overlap, additions
and removals, class and subclass coverage, margin utilization, risk-weighted
contract bins, integer tracking error, cost concentration, and binding reasons.
Do not select a production scenario from the static construction score.
