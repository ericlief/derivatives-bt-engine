# Trend-strength crossover (`ts3m − ts1y`) as a separate weighted TSMOM rule

Research question: we compute, per instrument, `ts3m` and `ts1y` (log return over
3m/1y, divided by realized daily-return std scaled to the same horizon — a
Sharpe-style t-stat of the trend at each horizon), blend them into
`trend_strength = tanh(w3·ts3m + w1·ts1y)` for vol-scaled position sizing, and
separately compute `mom = tanh(ts3m − ts1y)`. `mom` was observed to diverge in
sign from the prevailing trend fairly often (e.g. positive during a short-term
rebound inside a longer downtrend). Question: is there literature support for
using `mom` as a *separate, weighted* daily entry/exit rule alongside
`trend_strength`, and what does the literature say about that divergence?

Three research passes below (foundational TSMOM/crossover equivalence,
multi-horizon ensemble construction, and momentum-crash/MACD-style
crossover risk). All sources were checked live; anything not independently
verified is flagged inline rather than presented as fact.

---

## 1. What `mom` structurally *is*

**Levine & Pedersen (2016), "Which Trend Is Your Friend?"**, *Financial
Analysts Journal* 72(3), 51–66
([AQR](https://www.aqr.com/Insights/Research/Journal-Article/Which-Trend-Is-Your-Friend)).
Proves TSMOM and moving-average crossovers are equivalent representations
in the general linear-filter sense (alongside HP/Kalman filters), using
"trend signature plots" of the weight placed on past returns by lag. A
fast-MA-minus-slow-MA crossover is, structurally, a weighted difference of
returns over two horizons — the same object as `ts3m − ts1y`, just without
the explicit vol-normalization/t-stat framing.

**Baz, Granger, Harvey, Le Roux & Rattray (2015), "Dissecting Investment
Strategies in the Cross Section and Time Series,"** SSRN 2695101. The
standard CTA-literature reference for a **volatility-scaled MACD** (fast
EMA − slow EMA, normalized by rolling vol) — mathematically the same family
as `ts3m − ts1y`. Widely cited as how systematic trend-followers actually
build normalized MACD-type signals, and for combining several fast/slow
pairs to diversify signal risk. (Full text was paywalled at fetch time —
treating specific numeric claims from it as unverified, but its role as the
standard reference is well corroborated by secondary citations.)

**Zakamulin (2017 working paper) and Zakamulin & Giner (2020), "Trend
following with momentum versus moving averages: a tale of differences,"**
*Quantitative Finance* ([paywalled](https://www.tandfonline.com/doi/full/10.1080/14697688.2020.1716057),
relied on secondary summaries — flagged as such). Shows MOM can be
rewritten as an equal-weighted MA of price *changes*, formally linking MOM
and MA rules. Finds MA/MOM performance correlation **increases with trend
strength** — i.e. short- and long-horizon signals agree most when a trend
is unambiguous, and diverge most exactly in weak/transitional regimes. This
matches what was observed empirically with `mom`.

**Bottom line:** `mom` is not a novel construction — it's the vol-normalized
analogue of a MACD line / fast-minus-slow crossover, a well-studied object.
The divergence-from-trend behavior isn't a bug in the implementation; it's
the known, structural behavior of this class of signal in transitional
regimes.

## 2. Does the literature combine a "level" signal and a "spread" signal as two separately-weighted rules?

No direct precedent was found for exactly this architecture (a blended
`trend_strength` level signal *plus* a separately-weighted `ts3m − ts1y`
spread signal as two distinct rules). The closest analogues:

- **Dao, Nguyen, Deremble, Lempérière, Bouchaud & Potters (2016), "Tail
  Protection for Long Investors: Trend Convexity at Work,"** SSRN 2777657,
  and the public writeup **"The Convexity of Trend Following"** (CFM,
  [PDF](https://www.cfm.com/wp-content/uploads/2022/12/266-2018-The-Convexity-of-trend-following.pdf)).
  Aggregate CTA/trend performance is well approximated by the difference
  between long-term and short-term **realized variance** — a long-minus-short
  *variance* spread drives convexity/crisis-hedging. Conceptually parallel
  to using a fast-minus-slow *trend* spread as an independent factor, though
  their spread is on variance, not trend strength. Convexity emerges at the
  timescale of the trend filter itself (~6mo in their calibration) — a fast
  filter won't hedge a multi-week crash, which argues for horizon choice
  mattering more than naively expected.

- **Man AHL, "The Need for Speed in Trend-Following Strategies"** (Mackic,
  Jan 2023, [man.com](https://www.man.com/insights/need-for-speed-trend-following)).
  AHL runs five MAC models across a speed spectrum, combined via **equal-weighted
  sum**, not a fast-minus-slow spread as its own rule. Speeds are chosen to
  minimize inter-speed correlation (fastest-vs-slowest ≈ 0.17). Faster
  speeds give better crisis alpha despite lower standalone Sharpe. Vol
  normalization is applied *after* signal combination for sizing, not baked
  into each signal as a t-stat — a real architectural difference from
  `ts3m`/`ts1y`, worth not over-mapping.

- **Baltas & Kosowski (2013), "Demystifying Time-Series Momentum
  Strategies,"** SSRN 2140091. Builds a continuous [-1,1] trend-strength
  signal from a regression t-stat/R² of a fitted linear trend — conceptually
  close to the `tanh(t-stat)` construction here. Focused on single-horizon
  signal quality and pairwise correlation across trading rules; does not
  decompose into separately-weighted level/spread components.

- **arXiv 2510.23150, "Revisiting the Structure of Trend Premia: When
  Diversification Hides Redundancy"** (2025). Important caution before
  adding `mom` as an *additional* weighted rule: multi-horizon trend
  ensembles show substantial redundancy — apparent diversification across
  lookbacks often collapses onto fewer independent factors than the raw
  horizon count suggests, with medium-term horizons sitting in an unstable
  zone between short-term noise and long-term persistence. **Concretely:
  check the empirical correlation between `trend_strength` and `mom` in this
  dataset before assuming they contribute independent edge** — since `mom`
  is built from the same two inputs as `trend_strength`, some redundancy is
  close to guaranteed by construction.

- Standard MA-crossover practitioner literature treats the crossover as
  *the* primary signal, not a secondary overlay on top of a separate level
  signal — architecturally different from what's being proposed here and
  shouldn't be over-cited as support.

## 3. The specific divergence risk: momentum crashes and MACD whipsaws

**Daniel & Moskowitz (2016), "Momentum Crashes,"** *Journal of Financial
Economics* 122(2), 221–247
([PDF](https://www.kentdaniel.net/papers/published/jfe_16.pdf)). Momentum
strategies crash in "panic states" — after market declines, when volatility
is high, **contemporaneous with market rebounds** — because past losers
behave like out-of-the-money calls (high optionality post-drawdown) and
rally harder than winners on a snap-back, directly reversing the momentum
bet. This is mechanically the same scenario as `mom` flipping positive
during a rebound inside a longer downtrend: a continuous, leading-indicator
version of exactly the divergence that historically precedes momentum
crashes. Their mitigant is **dynamic vol/regime-conditioned exposure
scaling**, not a horizon-difference entry signal — i.e. their fix is to
throttle exposure in panic states, not to trade the divergence directly.
Roughly doubled realized Sharpe in their backtests.

**MACD literature (AAII, Fidelity, Britannica Money, and the peer-reviewed
"MACD – Analysis of weaknesses of the most powerful technical analysis
tool," ResearchGate).** Unanimous: MACD is a lagging indicator, works in
sustained trends, generates frequent false crossovers ("whipsaws") in
sideways/choppy/mean-reverting regimes because the fast and slow line cross
repeatedly with no follow-through. A study applying default MACD(12,26,9)
to Nikkei 225 futures (2011–2019) found negative performance. Consistent
recommendation: use MACD as a **secondary/confirming** signal (paired with
a trend/regime filter or longer MA), not a standalone trigger.

**General combination literature — Baltas & Kosowski (2013), Hurst, Ooi &
Pedersen (2017), "A Century of Evidence on Trend-Following Investing"**
(AQR). Combining fast and slow trend signals improves Sharpe and reduces
drawdowns vs. any single speed, because fast signals are responsive-but-noisy
and slow signals are steady-but-lagging. This is an argument for
*combining* horizons (as `trend_strength` already does), not for trading
their *difference* as a standalone directional bet.

No paper was found that explicitly names "subtract a short-horizon
momentum t-stat from a long-horizon one and trade the sign" as a studied,
validated construction — treat that specific framing as literature-adjacent
rather than literature-backed. What is well supported is the opposite
convention: practitioners (Alpha Architect's trend-filter series, ReSolve
Asset Management's managed-futures replication research, Quantpedia's
"Designing Robust Trend-Following System") favor **requiring sign agreement
across horizons as a filter** to suppress trades, rather than trading
*disagreement* between horizons directly — since a spread signal by
construction fires hardest exactly when horizons disagree, i.e. exactly the
regime where the crash and whipsaw literature says trend signals are least
reliable.

## Synthesis / recommendation

- `mom = tanh(ts3m − ts1y)` is structurally a vol-normalized MACD line. Its
  sign divergence from the prevailing trend is expected behavior for this
  signal class in transitional regimes, not an implementation issue.
- The literature does **not** support using it as a standalone directional
  entry/exit rule with its own independent weight — the closest validated
  use cases are (a) a **confirming/filtering** signal (require horizon
  agreement to take/hold a position, per the MACD and CTA-filter
  literature) or (b) a **risk-throttle** input (scale down exposure when
  `mom` and `trend_strength` disagree, in the spirit of Daniel & Moskowitz's
  dynamic exposure conditioning), rather than (c) a separately-weighted
  directional rule voted alongside `trend_strength`.
- Before adding it as any kind of separate rule, the arXiv 2510.23150
  redundancy finding argues for first checking the empirical correlation
  between `trend_strength` and `mom` in this project's own data — they
  share both inputs by construction, so some of the apparent
  "diversification" from adding `mom` as a second rule may not be real.
- If pursuing the "early-warning/inflection" framing (mom leads a genuine
  trend change) rather than the "whipsaw/crash-risk" framing (mom fires
  falsely in a rebound), that specific directional use was not found
  validated in the literature — it would need to be established
  empirically on this dataset, e.g. conditioning on how often a `mom`
  sign-flip is followed within N days by `trend_strength` flipping to
  agree, vs. reverting back.

## Caveats on source verification

- Baz et al. (2015) full text was SSRN-paywalled; relied on its
  well-corroborated role as the standard normalized-MACD reference rather
  than pulling specific numeric claims from it directly.
- Zakamulin & Giner (2020) was paywalled at Taylor & Francis; relied on
  secondary summaries for the MA/MOM correlation-vs-trend-strength finding.
- Avramov, Kaplanski & Subrahmanyam (2021), "Moving Average Distance as a
  Predictor of Equity Returns," *Review of Financial Economics* 39(2),
  127–145 (SSRN 3111334) is a cross-sectional (not time-series) analogue —
  21d-vs-200d price MA distance predicts equity returns beyond standard
  momentum/52-week-high effects. Noted for completeness; not load-bearing
  for the TSMOM recommendation above since it's a different signal family
  (cross-sectional stock selection vs. time-series entry/exit).
- No primary AHL/Winton/Campbell technical papers with full methodology
  were locatable beyond marketing-oriented insight pieces — treat the Man
  Group citation as directionally informative, not peer-reviewed rigor.
- No paper was found naming a "trend acceleration" or "second derivative of
  momentum" signal in the peer-reviewed TSMOM literature specifically — any
  such framing found online was blog/practitioner-level commentary, not
  verified research.

---

# Part 2: Combining TSMOM/MACROSS when sources disagree, front-end smoothing, regime-disagreement mechanism, price- vs return-space scaling

Follow-up research question, prompted by Levine & Pedersen (2016), "Which
Trend Is Your Friend?" (Table 1/Table 2, the TSMOM/MACROSS equivalence and
cross-regression tables) and a comparison against a second source (a
systematic-trading text using price-space dollar-vol scaling rather than
return-space). Four sub-questions: (1) how to empirically decide a
combination method when sources disagree, (2) whether the fast end of a
return-based TSMOM signal should be smoothed relative to a MACROSS
equivalent, (3) what "regime disagreement" (this codebase's
`TrendRegime.CORRECTION`/`REBOUND`) should actually do in practice — discount
or reweight, and at what cadence, (4) price-space vs. return-space vol
normalization, and correlation-only (not performance-based) signal
weighting.

## 4. Testing which TSMOM/MACROSS combination is "right"

Levine & Pedersen's equivalence proof is a proof about the *general linear-
filter class*, not a claim that any specific MACROSS speed and any specific
TSMOM speed are numerically interchangeable. Their own Table 2 (regressing
each MACROSS factor on the three TSMOM factors and vice versa) makes this
precise: R² ranges 81-86%, not 100% — i.e., even in their own dataset, a
specific MACROSS/TSMOM pair share the large majority but not all of their
variance. The 14-19% residual is exactly where "which recipe is right"
disagreement across sources lives — sources aren't contradicting each other
in error, they're parameterizing an approximate (not exact) equivalence
differently.

**Practical test, using the same device Levine & Pedersen use on themselves:**
regress candidate signal variants against each other directly on this
project's own data and read the R². Concretely: regress `ts3m`/`ts1y`
(`tsmom_signal.py`'s current 63d/252d t-stats) against an EWMA-crossover
built at equivalent speeds. High R² (>~80%, in the paper's range) means
they're functionally the same rule for this instrument set — blending both
adds cost, not diversification. Low R² means real incremental information —
a blend is justified. This also directly answers the arXiv 2510.23150
redundancy caution already flagged in Part 1: don't take `w3=0.4`/`w1=0.6`
on faith from any single paper's parameterization — check the empirical
`ts3m`/`ts1y` correlation in this dataset first, the same diagnostic
Levine & Pedersen's Table 2 exists to compute.

## 5. Front-end smoothing: point-to-point log-return vs. MA/EWMA crossover noise

Yes — and it's structural, not incidental. `ts3m` is built from `r3m =
log_price.diff(63)` (`tsmom_signal.py:106`): a pure two-point difference
between today's close and the close 63 days ago, blind to every day in
between. A single-day glitch or thin-volume gap on either endpoint (exactly
the class of day the futures-roll bug fix earlier this session dealt with —
BRE/6L's zero-trade days) moves the whole signal by that spike's full size,
undamortized. A MACROSS/EWMA crossover is a weighted average of *many* days'
prices, so the same single-day spike is diluted by roughly `1/window_length`
instead of landing at full weight. This is precisely what Levine &
Pedersen's "trend signature" plots (Part 1, §1) show structurally — pure
lookback-return signals place weight sharply at the window boundary; MA
crossovers spread weight smoothly across all lags — and matches Zakamulin's
finding (Part 1, §1) that MA/MOM correlation is highest exactly when a trend
is unambiguous and lowest in weak/transitional regimes, i.e. exactly where
endpoint noise in a 2-point signal would dominate real signal. Baz et al.
(2015)'s standard vol-scaled MACD construction (Part 1, §1) is built from
`EMA(fast) − EMA(slow)`, not a raw lookback-return difference — the
standard CTA answer to "should the front end be smoothed" is yes, in
construction, not as a post-hoc filter.

**Where this applies in this codebase:** the fast end (`ts3m`, 63d) is the
one exposed to this, not the slow end (`ts1y`, 252d) — a single glitch day
is proportionally much smaller against a 252-day move, and 252d realized vol
is already smoother by construction. The file's own docstring (`tsmom_signal.py:14-16`)
already treats a 1-month lookback as too noisy to trust and 3-month as "the
minimum reliable fast signal at this cadence" — that responsiveness floor is
the reason `ts3m` exists at all, so smoothing the *output* score (e.g.
rolling-averaging the finished `ts3m` t-stat) would just add lag on top of
an already-deliberately-fast signal and fight the reason it's there.
**If smoothing is pursued, do it at construction** — replace the
`log_price.diff(63)` numerator with an EMA-of-log-price-based difference
(literalizing `ts3m` into a MACROSS-equivalent at that speed) — not by
smoothing the finished score after the fact. No structural or literature
reason was found to smooth the slow end (`ts1y`) further; it isn't the noisy
one.

## 6. Regime disagreement in practice: reweight, not a flat discount

**Goulding, Harvey & Mazzoleni (2023), "Breaking Bad Trends,"** *Financial
Analysts Journal* 80(1), 84-98 ([Duke PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P167_Breaking_bad_trends.pdf),
full text obtained and read directly). This is almost certainly the direct
source of this codebase's `TrendRegime` framework — their four-state
partition, definitions, and even the "61%/55% revert/continue" figures
already in `classify_regime`'s docstring line up exactly:

> Bull if slow ≥0 and fast ≥0; Correction if slow ≥0 and fast <0; Bear if
> slow <0 and fast <0; Rebound if slow <0 and fast ≥0. (eq. 4)

**Their mechanism is not a flat discount on the blended signal** — which is
what `compute_position_scalar`'s `momentum_discount` currently does
(`ts * 0.5` whenever `regime ∈ {CORRECTION, REBOUND}`, one constant for
both states). Instead they dynamically **reweight fast vs. slow** (eq. 7):

```
r_DYN =  r_slow                              if Bull
      = -r_slow                              if Bear
      = (1 − a_Co)·r_slow + a_Co·r_fast       if Correction
      = (1 − a_Re)·r_slow + a_Re·r_fast       if Rebound
```

`a_Co` and `a_Re` are **not fixed at 0.5 or at each other** — each is
estimated ex ante, per asset, from that asset's own historical returns in
the months *following* a correction or rebound specifically. If returns
after a correction tend to keep following the slow trend (the dip mean-
reverts), `a_Co < 0.5` (tilt away from fast — trust slow more). If returns
after a rebound tend to follow the new (fast) direction, `a_Re > 0.5` (tilt
toward fast). A noisy/thin estimate shrinks toward the uninformed 0.5 — the
same shrinkage instinct the current flat multiplier has, but applied
per-state and per-asset rather than as one hardcoded global constant for
both correction and rebound alike.

**Exact estimator (Appendix C, eq. 8-10)**, for anyone implementing this
rather than just reading the intuition above. `AVG[r|s]`/`AVG[r²|s]` are the
average return / average squared *raw* asset return (not a signed
directional strategy return) over all months prior to month `m` in state
`s`; `FREQ[s]` is the frequency of state `s`:

```
a_Co = ½ · (1 − (1/C) · AVG[r|Co] / AVG[r²|Co])                          (8)
a_Re = ½ · (1 − (1/C) · AVG[r|Re] / AVG[r²|Re])                          (9, as scanned -- see errata)

C =  FREQ[Bu]/FREQ[Bu or Be] · AVG[r|Bu]/AVG[r²|Bu or Be]
   − FREQ[Be]/FREQ[Bu or Be] · AVG[r|Be]/AVG[r²|Bu or Be]                (10)
```

`C` is a single shared scalar (computed once from Bull/Bear months only,
not state-specific), "typically positive." Estimates update every 30 months
on an inception-to-prior-month basis, requiring ≥12 months of history in
each phase; either parameter falling outside `[0, 1]` gets clamped to the
nearest endpoint.

**Errata flag — eq. (9)'s sign as extracted does not match the paper's own
prose.** Both (8) and (9) were extracted (independently, twice — once via
this session's own PDF read, once via the user's separate copy-paste) with
*identical* form, `1 − (1/C)·AVG[r|s]/AVG[r²|s]`. Taken literally, that
makes `a_Co` and `a_Re` move in the *same* direction for a same-signed
`AVG[r|s]`, since `AVG[r²|s] ≥ 0` always and `C` is one shared, "typically
positive" scalar — e.g. positive `AVG[r|Re]` would push `a_Re` **below**
0.5. That directly contradicts the paper's own explicit prose two
paragraphs above these equations ("if returns tend to be positive [after
rebounds]... `a_Re > 0.5`"). The prose is unambiguous, stated as what the
equations are supposed to reflect, and mirrors the structural asymmetry
already established in this section (slow and fast swap which one is
"long" between Correction and Rebound) — so the prose, not the scanned
sign, should be trusted. The self-consistent resolution is that eq. (9)
actually carries a **`+`**, not a `−`:

```
a_Re = ½ · (1 + (1/C) · AVG[r|Re] / AVG[r²|Re])                          (9, corrected)
```

making (8) and (9) mirror-image formulas (tilt down for Correction, tilt up
for Rebound) rather than identical ones. A `+`/`−` glyph in a small inline
equation is genuinely hard to resolve at typical PDF-scan/render
resolution — this should be treated as an extraction artifact, not a claim
that the published paper contains an error. **Anyone implementing this
should verify the actual sign directly against a high-zoom read of their
own copy of the paper (Appendix C, eq. 9) before relying on it** — the
corrected (`+`) form is given here only because it's the only version
consistent with the paper's own stated intuition, not because the sign was
confirmed at the source.

**Cadence** — directly answers "immediately or monthly": their framework is
monthly throughout (monthly signals, monthly rebalance, monthly turning-
point observation) — a periodic reweight at the same cadence as the signal
itself, evaluated at the start of the state, held until the next
observation. **Nothing in this literature supports an immediate full exit**
on a regime flip — the strategy never goes flat in Correction/Rebound, it
reweights between the two signals it already has.

**Quantitative payoff** (their Figure 4, read directly from the paper): in
the 2009-2019 post-GFC expansion — the exact period where turning-point
frequency ran above the 33-year median in 9 of 11 years, and static
12-month trend-following's performance visibly degraded — static 12-month
trend returned only **+0.3% annualized in months following turning points**
(essentially noise) vs. the dynamic (reweighted) strategy's **+1.7%** in
those same months, at matched 10% target vol. That swing is effectively the
entire gap between the static portfolio's degraded period-average (0.3%
overall, 2009-2019) and the dynamic portfolio's 3.4% — i.e. the benefit of
reweighting-over-discounting is concentrated exactly in the disagreement
months, which is the mechanism working as designed, not a diffuse
improvement.

**Caveat — the relative outperformance is real but the absolute level is
weak, and that matters.** Table E.1, Panel C (their multi-asset portfolio,
Post-GFC 2009-2022, *unscaled* — i.e. not renormalized to 10% target vol
the way Figure 4's numbers are) reports Dynamic Trend at **1.2% annualized
excess return, 5.5% annualized vol, Sharpe 0.22** — beating static
12-month's 0.4%/6.3%/0.06, but both are weak in absolute terms (every
strategy in that panel, static or dynamic, is under 0.35 Sharpe). "Dynamic
beats static by 2-4x" is a true statement about relative ranking within a
period where trend-following broadly struggled — it is not, by itself,
evidence that the reweighting mechanism produces a strategy worth trading.
A refinement that turns a failing strategy into a slightly-less-failing one
is a weaker result than the Figure 4 framing (vol-normalized, decomposed
into bull/bear vs. turning-point months) makes it look. Before adopting the
`a_Co`/`a_Re` reweighting on the strength of this paper alone, this should
be tested directly against this project's own recent data — see the
2023-2026 backtest discussion below.

**Comparison to what's implemented now:** `momentum_discount` in
`compute_position_scalar` (`tsmom_signal.py:299-346`) is a coarser version
of the same underlying insight (de-risk during fast/slow disagreement) but
differs in two ways the paper's results suggest matter: (a) one fixed
constant vs. two separately-calibrated, empirically-estimated weights — the
paper's own asymmetry (`a_Co` typically <0.5, `a_Re` typically >0.5) means
corrections and rebounds are *not* interchangeable states and arguably
shouldn't get the same treatment; (b) a flat multiplier on the already-
blended `ts` score vs. a re-blend of the `ts3m`/`ts1y` components
themselves before combining. If this is worth pursuing further: estimate
`a_Co`/`a_Re` empirically (pooled across the futures universe, or per-
instrument if there's enough history) from realized forward returns
following each state, with shrinkage to 0.5 under a thin sample — a
concrete, literature-backed upgrade path, not merely "needs more research."

**Empirical test on this project's own data, 2023-2026.** The obvious next
step (per the caveat above) is testing the discount mechanism directly,
not leaning on the paper's numbers. First attempt used the existing
`tsmom-bt` CLI (`tsmom_backtester.py`) over the full live instrument
universe (`ES, NQ, CL, GC, SI, ZN, ZT, ZL, ZC, ZS, ZW, JPY, BRE, 6M` —
`NKD` excluded, confirmed no data in the local db) and was **discarded as
uninformative**: realized annualized vol came out at 82-90% against a 15%
target — a >5x overshoot. Root cause: `tsmom_backtester.py` is explicitly
documented as sizing each symbol independently against its own
`vol_target`/`max_notional`/`max_contracts`, with **no cross-instrument
risk cap** — unlike the live path (`live/tsmom_rebalance.py`), which runs
every position through `compute_desired_risk_budget`/
`apply_cluster_risk_cap`. With 14 correlated symbols each independently
allowed up to their own ceiling, nothing stops them all leaning the same
way at once and stacking leverage well past any stated target. This is a
real gap between the backtest path and the live path, not just a
parameter-tuning issue — worth fixing in `tsmom_backtester.py` independent
of this research question if the CLI is meant to be a faithful stand-in
for live sizing.

Given that, the test was rebuilt from scratch as a standalone script,
deliberately bypassing all of `tsmom_backtester.py`'s sizing machinery
(`risk_scalar` clamp, cluster cap, `max_notional`/`max_contracts`) in favor
of Levine & Pedersen's own literal methodology (Part 1 intro, Table 1):
binary `sign(signal)` direction (not magnitude-scaled), a **flat, equal,
per-asset annualized-$-vol target — no cluster/bucket hierarchy at
all** (confirmed from their own text: every asset gets the same 0.65% vol
target via an EWMA vol estimate; the ~10% portfolio-level figure they
report is what emerges from aggregating many such positions, not a
deliberate hierarchical split — that hierarchical equal-weight-by-cluster
scheme belongs to the *different* Goulding/Harvey/Mazzoleni multi-asset
construction, not to this paper). Monthly rebalance, same cadence as
`tsmom_backtester.py`. Positions sized as
`contracts = round(direction · discount · flat_target_usd / (close · multiplier · daily_std · sqrt(252)))`,
reusing only pure functions (`calculate_trend_strength`, `load_portfolio_data`,
`_month_end_dates`) — no other position-sizing code from this project.
Same 14-symbol universe, 2023-01-01 to 2026-06-18 (2018 warm-up start for
signal history):

| `momentum_discount` | ann. vol (raw) | Sharpe | max DD | ann. return @ 10% vol (rescaled) | total fees |
|---|---|---|---|---|---|
| 0.5 (current default) | 3.9% | **0.51** | -4.5% | 5.1% | $2,101 |
| 1.0 (discount disabled) | 4.1% | **0.46** | -5.7% | 4.6% | $2,352 |

(Table updated after two corrections flagged directly by the user — see
below; script at `scripts/tsmom_binary_vol_parity_backtest.py`.)

Realized vol (~4%) landed well under the paper's 10% because this
project's 14-symbol universe has less cross-asset diversification than
Levine & Pedersen's 58 assets at the same flat per-asset target — expected,
and irrelevant to the Sharpe comparison (Sharpe is invariant to a uniform
leverage rescale). The discount does help at the margin here — Sharpe
0.51 vs. 0.46, drawdown -4.5% vs. -5.7% — in the same *direction* the
literature predicts, but the **effect size is small and, over only ~4.3
years/1,079 days, not distinguishable from noise** (a Sharpe-difference
this size has no claim to statistical significance at this sample length).
Both absolute Sharpes (~0.5) are also modest, echoing the same point as
the Table E.1 caveat above — this recent period doesn't show a strong edge
either way, discount or no discount. This is a directionally-supportive,
not confirmatory, result: enough to justify keeping some form of
regime-based de-risking, not enough to justify strong claims about the
flat-0.5-discount vs. a fully re-estimated `a_Co`/`a_Re` mattering much at
this sample size.

**Two corrections made to the first version of this test, both flagged
directly by the user rather than caught in review:**

1. **Missing transaction costs.** The first version charged zero
   commission anywhere — not on monthly resizing, not on the quarterly
   contract roll — unlike both `naked_futures.py`'s `FuturesPosition`/
   `TradeManager` path and `tsmom_backtester.py` itself (`net_pnl =
   mtm_pnl − fees`), which charge `get_spec(symbol)['commission']` on every
   trade. Fixed: commission now applies to `abs(new_target − held)` on
   every monthly resize, plus a mandatory quarterly roll charge (`2 ×
   abs(held) × commission`, same Mon-before-3rd-Friday schedule
   `FuturesPosition.roll_date` uses) — a roll is a real close-old/open-new
   round trip and costs commission twice even when price doesn't move.
   Impact here was modest (Sharpe 0.52→0.51 and 0.47→0.46, ~$2.1-2.4k total
   fees over 4.3 years on $1M) because the flat per-asset vol target used
   here sizes fairly small contract counts — this would matter far more at
   larger size or with a tighter rebalance cadence.

2. **A deeper, pre-existing, shared data-layer issue — not fixed, flagged
   separately.** Checking whether "the naked backtester" handles rolling
   differently surfaced that it doesn't avoid this the way it might seem
   to: `FuturesDataLoader.daily` (`_CONTINUOUS_FRONT_MONTH_SQL`, "nearest
   not-yet-expired contract per date") is the *same* continuous price
   series feeding `naked_futures.py`, `tsmom_backtester.py`, and this
   script alike — none of them hold a single physical contract's own price
   series through a position's life. That series is not back-adjusted, and
   near expiration it can flip-flop between two different contracts on
   adjacent dates whenever the about-to-expire contract has a thin/no-
   volume day. Confirmed directly against ZN, March 2023:
   `2023-03-19` prices off the **Jun'23** contract (115.19), `2023-03-20`
   drops back to the **Mar'23** contract (114.42), `2023-03-22` jumps
   forward to **Jun'23** again (115.39) — a pure contract-switch artifact,
   not a real price move, and any held position across any of these three
   backtest paths gets marked-to-market against it. This affects all three
   paths equally and is a pre-existing bug in shared infrastructure, not
   something this test introduced — left unfixed here, flagged as a
   separate, higher-priority investigation.

## 7. Price-space vs. return-space scaling, and correlation-only signal weighting

**Robert Carver's EWMAC construction** (*Systematic Trading*, 2015 — the
standard open-source reference implementation, [`ewmac.py`](https://github.com/robcarver17/systematictradingexamples/blob/master/ewmac.py),
matches the user's second source): `raw = EMA_fast(price) − EMA_slow(price)`,
normalized by `ewmstd(price.diff())` — the exponentially-weighted standard
deviation of raw price *changes* (point/dollar terms), not a percentage or
log-return volatility. This is the same construction the user described
(`s = s / (daily_vol * close_price)`) in different notation — `daily_vol *
close_price` is a first-order approximation of the same price-change std,
since `d(log_price) ≈ pct_return ≈ price_diff / price` for small moves.

**Compared to what's implemented here:** `ts3m`/`ts1y` are built entirely in
*return space* — `log_price.diff(N) / (daily_std_of_log_returns *
sqrt(N))`, a dimensionless Sharpe-style t-stat, not a price-level measure.
For a well-behaved instrument the two converge (both representations get
converted to $-risk at the position-sizing stage anyway — see
`compute_position_scalar`'s `daily_std_last * sqrt(252)` and
`apply_cluster_risk_cap`'s `hv`-based `position_risk`, both of which re-
introduce price/multiplier terms downstream regardless of which space the
raw signal itself was computed in). Where they can diverge: instruments
with large discrete price jumps or fat-tailed return behavior — quarterly-
roll futures being exactly this project's domain, and exactly the class of
edge case the BRE/6L roll-bug fix earlier this session ran into — can make
a log-return-space vol estimate and a raw price-change-space vol estimate
disagree around a roll. Return-space is also the more directly comparable
choice across a diversified futures book with wildly different point
values (a bond future's ~$100k full point vs. a metals micro's ~$10/point)
without Carver's separate `block_value` normalization layer doing that work.
No strong reason to switch given the current architecture already handles
cross-instrument normalization this way — worth flagging as a deliberate,
defensible architectural choice rather than an oversight if it comes up in
review, not a bug to fix.

**Correlation-only (not performance-based) weighting is a real, named
method** — Carver's "handcrafting" (*Systematic Trading*, expanded in
*Smart Portfolios*, 2017; [qoppac.blogspot.com summary](https://qoppac.blogspot.com/2018/12/portfolio-construction-through_7.html)):
group signals/assets into clusters by correlation, weight equally within a
correlated cluster, then combine cluster-level weights — deliberately
**not** optimized against each rule's own backtested Sharpe, specifically to
avoid overfitting portfolio weights to noisy historical performance (the
same overfitting risk Carver separately documents when fitting EWMAC
forecast scalars — more-tuned rules generalize worse out of sample).
Contrasts with `pysystemtrade`'s other supported method (bootstrapped
Sharpe-optimization over pooled weekly rule returns); Carver treats
handcrafting as the more robust default and cites an empirical result where
handcrafted (in-sample, correlation-only) weights slightly *beat*
rolling out-of-sample bootstrap-optimized weights (Sharpe 0.54 vs. 0.52)
despite using no performance data at all.

**Relevance to this project's `w3=0.4`/`w1=0.6`:** currently a fixed,
judgment-set pair, derived from neither method. A handcrafting-style check
is the same empirical correlation test already recommended in §4 — compute
`ts3m`/`ts1y`'s realized correlation in this dataset and check whether
0.4/0.6 is defensible as roughly-equal-adjusted-for-correlation, or whether
it's silently overweighting one horizon beyond what the observed
correlation structure would justify.

## Synthesis / recommendation (Part 2)

- The TSMOM/MACROSS "disagreement" across sources isn't an error to
  resolve by picking the more authoritative paper — it's the expected shape
  of an *approximate* equivalence. Settle any specific combination choice
  empirically, via direct regression/correlation on this project's own
  instruments (§4), the same diagnostic Levine & Pedersen use on themselves.
- Smooth the fast end (`ts3m`) at construction (EMA-based numerator) if
  pursued, not the slow end, and not by post-hoc filtering the finished
  score (§5).
- For regime disagreement, the literature-backed upgrade from the current
  flat `momentum_discount=0.5` is an empirically-estimated, asymmetric
  reweighting between `ts3m` and `ts1y` (separate `a_Co`/`a_Re`), evaluated
  at the same (not necessarily immediate) cadence as the signal, with
  shrinkage to no-op under thin samples — not an immediate exit (§6). A
  direct 2023-2026 test on this project's own 14-symbol universe (§6,
  stripped-down binary-signal/flat-vol-parity methodology) found the
  current flat discount does help at the margin (Sharpe 0.52 vs. 0.47,
  same direction the literature predicts) but the effect isn't
  distinguishable from noise at only ~4.3 years of data — directionally
  supportive, not confirmatory. Both the paper's own Table E.1 numbers and
  this project's own test show a fairly weak absolute edge in this recent
  period regardless of which discount scheme is used — that's a separate,
  more important caveat than which reweighting scheme wins.
- Price-space vs. return-space vol normalization is a legitimate
  architectural fork, not a bug; the current return-space choice is
  defensible for a cross-instrument futures book and converges with the
  price-space alternative downstream at the position-sizing stage (§7).
- Correlation-only ("handcrafted") weighting is real, literature-backed,
  and directly testable here with the same `ts3m`/`ts1y` correlation
  check as §4/§6 — worth running before treating `w3`/`w1` as settled.

## Caveats on source verification (Part 2)

- Goulding, Harvey & Mazzoleni (2023) was fetched and read in full from the
  Duke-hosted PDF — eq. (4)/(7), Figure 4's numbers, and the 2009-2019
  period figures above are read directly from the paper, not a secondary
  summary.
- Carver's exact EWMAC normalization was verified directly against the
  `ewmac.py` reference source code (not just prose description) —
  `ewmstd(price.diff())`, confirmed price-change/point-space, not percent.
- The handcrafting-vs-bootstrap Sharpe comparison (0.54 vs. 0.52) and the
  diversification-multiplier formula came from qoppac.blogspot.com
  secondary summaries of *Systematic Trading*/*Smart Portfolios*, not the
  books' primary text directly — treat as well-corroborated but secondary.
- The claim that `TrendRegime`'s Bull/Correction/Bear/Rebound framework and
  its docstring's 61%/55% figures trace to Goulding/Harvey/Mazzoleni is
  inferred from the exact match in definitions and figures, not confirmed
  by this codebase's own history/commit messages — flagged as a strong
  structural inference, not a verified citation trail.
