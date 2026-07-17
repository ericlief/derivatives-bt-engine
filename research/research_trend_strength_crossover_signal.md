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
