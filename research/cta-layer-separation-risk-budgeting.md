# Position Sizing vs. Cross-Asset Risk Budgeting: Are These the Same Problem?

Research date: 2026-06-26. Companion to [cta-vol-scalar-clamping.md](cta-vol-scalar-clamping.md) — that file covers whether/how to *bound* a vol-scaling ratio; this one resolves a separate, more fundamental question about what the ratio's *target* should be and whether the system's various "target volatility" constants are doing the same job or different jobs.

## The question

Does canonical TSMOM/CTA practice size positions using a **fixed, universal target volatility** (the same number applied to every instrument), or using **each asset's own long-run historical volatility** as the reference point? And is there a recognized separation between "per-instrument volatility normalization" and "cross-asset portfolio/risk-budget construction" as distinct problems?

### Why this needed resolving

This system uses `risk_scalar = clamp(vol_target / current_realized_vol, 0.25, 2.0)`, where `vol_target = 0.15` is a single fixed constant applied identically to every instrument. Separately, it added a `signal_confidence` mechanism modeled on Bongaerts, Kang & van Dijk's "Conditional Volatility Targeting" (2020), whose `σ_target` is explicitly **each asset's own historical average volatility**, not a shared constant. Surface-level, both formulas look like "target / current vol" — which produced extended design confusion about whether `vol_target` and `σ_target` were the same kind of object, and whether having both in one system was a conflation that needed fixing.

## (a) Canonical TSMOM uses a fixed, universal target — confirmed verbatim from primary text

**Moskowitz, Ooi & Pedersen, "Time Series Momentum," *Journal of Financial Economics* 104 (2012), 228–250.** Section 4.1, p. 236:

> "We size each position so that it has an ex ante annualized volatility of 40%. That is, the position size is chosen to be **40%/σ^s_{t−1}**, where σ^s_{t−1} is the estimate of the ex ante volatility of the contract as described above. The choice of 40% is inconsequential, but it makes it easier to intuitively compare our portfolios to others in the literature."

Formal return equation (Eq. 5, p. 236): `r^{TSMOM,s}_{t,t+1} = sign(r^s_{t−12,t}) · (40%/σ^s_t) · r^s_{t,t+1}`.

The **40% is a single fixed scalar, identical for every instrument** — explicitly not each asset's own historical average. What's asset-specific is the *denominator* (σ_t, current estimated vol via an exponentially-weighted lagged-squared-return model, Eq. 1, p. 233), not the target itself. This is structurally identical to this system's `vol_target / current_realized_vol`.

**AQR confirmation — Hurst, Ooi & Pedersen, "Demystifying Managed Futures," *Journal of Investment Management* 11(3), 2013, pp. 42–58.** Eq. 1, p. 46, is the same `40%/σ^s_t` formula, explicitly "following the methodology of Moskowitz et al. (2012)." Three stated reasons for fixed-target sizing (p. 46): it lets you "aggregate the different assets into a diversified portfolio which is not overly dependent on the riskier assets," "keeps the risk of each asset stable over time," and "minimizes the risk of data mining... since it does not use any free parameters or optimization in choosing the position sizes."

**The detail that resolves the whole confusion**: this same paper, Section 3.3, p. 47, describes a **second, separate, fixed target at the portfolio level**:

> "In each case, we scale all the positions such that the overall portfolio targets an ex ante volatility of 10% using an exponentially weighted variance–covariance matrix..."

Two different fixed numbers (40% instrument-level, 10% portfolio-level), for two different purposes, at two different levels of aggregation, in the same canonical methodology paper. This is direct primary-source evidence that **using multiple distinct "target volatility" constants for different jobs is standard practice, not a sign of conflation.**

## (b) Is "per-instrument normalization" vs. "cross-asset risk budgeting" a recognized, separate distinction?

Real and well-evidenced, but assembled across sources rather than codified in one canonical citation. Hurst/Ooi/Pedersen's own abstract lists "risk allocation across asset classes" as a separate implementation concern from instrument-level vol scaling. The two-stage 40%-then-10% structure (above) is the clearest direct evidence of a genuine two-stage pipeline, since the portfolio-level step requires a covariance matrix that the instrument-level step doesn't use.

The risk-parity literature (Asness, Frazzini & Pedersen, "Leverage Aversion and Risk Parity," *Financial Analysts Journal* 68(1), 2012) gives this its own named treatment, independent of any momentum signal: risk parity sets portfolio weight in each asset class "equal to the inverse of its volatility... multiplied by a constant to match the ex post realized volatility of [a] benchmark." This is structurally the **same** inverse-vol-then-rescale logic as TSMOM's instrument-level step, just applied one level up — meaning the literature treats "instrument normalization" and "cross-asset risk budgeting" as the *same technique recursively applied at two levels of aggregation*, not two categorically different methods. Accurate framing: **a well-evidenced informal simplification, not a single quotable source.**

## (c) Named methodologies for cross-asset risk-budget construction (Layer 3)

1. **Naive risk parity / inverse-volatility weighting** — `w_i ∝ 1/σ_i`, rescaled to a target portfolio vol. Ignores correlations. (Asness/Frazzini/Pedersen 2012; also AQR's portfolio-level overlay above.)
2. **Equal Risk Contribution (ERC)** — Maillard, Roncalli & Teiletche, *Journal of Portfolio Management*, Summer 2010. Solves for weights such that each asset's covariance-weighted marginal risk contribution is equal across all assets — the formally "correct" generalization of risk parity once correlations matter. *(Secondary-sourced in this research pass — primary PDF text wasn't extractable; treat with lower confidence than the items below.)*
3. **Hierarchical Risk Parity (HRP)** — López de Prado (2016). Hierarchical clustering on the correlation matrix, then recursive capital bisection down the resulting tree. Explicitly cluster-based, avoiding covariance-matrix inversion.
4. **Leverage-aversion risk parity** (Asness/Frazzini/Pedersen) — theoretical justification (CAPM fails under leverage constraints) for equal-risk-by-asset-class allocation.

**Verdict on this system's `√n_effective` + per-cluster cap + 1/n_active_clusters floor design**: it does **not** match any of these as a direct, named implementation. The cluster grouping (equity/energy/grain/metal/fx) is hand-assigned, not data-driven the way HRP's clustering is. The `√n_effective` scaling is a recognizable diversification heuristic (portfolio vol of N uncorrelated equal-risk bets scales like 1/√N) but is practitioner folklore, not tied to one citable paper, and holds only under an uncorrelated-bets assumption the cluster structure itself implicitly concedes is false. The per-cluster cap is a discretionary overlay, not an optimization toward any named objective function. Fair description: **risk-parity-inspired, cluster-capped vol budgeting — a bespoke combination borrowing real ingredients, not a citation-backed implementation of ERC, HRP, or classical risk parity.** Not a flaw — bespoke combinations are common in practice — but it shouldn't be described as "implementing risk parity."

## (d) Overall verdict: `risk_scalar` and `signal_confidence` are complementary, not competing

Confirmed with primary-source backing:

- `risk_scalar` (fixed `vol_target`, continuous, every instrument every period) **is** the canonical TSMOM/AQR position-sizing rule — not invented, not borrowed incorrectly. It answers "how big should this position be given current risk."
- `signal_confidence` (Bongaerts' asset-relative `σ_target`, intermittent, fires only in that specific asset's own extreme-quintile months) is a regime-detection/signal-quality filter, not a risk-equalization device. It answers "should I trust this signal right now."
- These operate at different cadences on different questions. Using both together is correct, not redundant — and AQR's own paper using two *different* fixed targets (40% and 10%) for two different purposes is itself evidence that multiple distinct target-like constants coexisting in one system is normal, not confused.

**The one place this research surfaces a real, unresolved gap**: Layer 3 (the cluster-cap/√N machinery) is this system's weakest-precedented component. If more rigor is wanted later, benchmarking it against actual ERC (which uses the real covariance matrix, unlike the hand-built cluster buckets) is the natural next step — that comparison hasn't been done and is the one place the system stands apart from rather than reproducing the academic canon.

## Sources

**Primary-verified (verbatim quotes confirmed from extracted PDF text):**
- Moskowitz, Ooi, Pedersen, "Time Series Momentum," *Journal of Financial Economics* 104(2), 2012: 228–250. Eq. 1 (p.233), Eq. 5 (p.236).
- Hurst, Ooi, Pedersen, "Demystifying Managed Futures," *Journal of Investment Management* 11(3), 2013: 42–58. Eq. 1 (p.46), Section 3.3 (p.47).
- Asness, Frazzini, Pedersen, "Leverage Aversion and Risk Parity," *Financial Analysts Journal* 68(1), 2012: 47–59.
- Bongaerts, Kang, van Dijk, "Conditional Volatility Targeting," *Financial Analysts Journal* 76(4), 2020 — already primary-verified in this project; see [cta-vol-scalar-clamping.md](cta-vol-scalar-clamping.md) for the full extracted text and verbatim quotes, not re-derived here.

**Secondary-sourced (description-level confidence only, primary text not extracted this pass):**
- Maillard, Roncalli, Teiletche, "On the Properties of Equally-Weighted Risk Contributions Portfolios," *Journal of Portfolio Management*, Summer 2010 (ERC).
- López de Prado, "Building Diversified Portfolios that Outperform Out-of-Sample" (Hierarchical Risk Parity), 2016.
