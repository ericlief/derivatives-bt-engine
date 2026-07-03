# Cross-Maturity Momentum (2Y → 10Y) in U.S. Treasury Markets

Research date: 2026-07-02

## Question

Is there academic or empirical justification for using 2-year U.S. Treasury futures (ZT) past returns as a momentum signal to trade or time 10-year U.S. Treasury futures (ZN)? Or do valid momentum structures require same-instrument signals, or yield-curve/term-structure models instead of momentum transfer?

## Final classification: Partially supported but reclassified

No paper in the literature explicitly tests or supports using ZT past returns as a directional momentum signal to trade ZN. The specific structure does not exist in the academic record. What is documented instead falls into three structurally different categories, none of which is equivalent to a 2Y→10Y momentum transfer.

## Papers reviewed

### Sihvonen (2024) — "Yield Curve Momentum"
*Review of Finance*, Vol. 28, No. 3, May 2024. Bank of Finland DP 2115 (2021 working paper).

**Signal structure:** Same-instrument TSMOM. Past-month return of the same-maturity bond predicts next-month return across all 1–10 year maturities. Cross-maturity signals (average excess return across all other maturities) are also explicitly tested as an alternative predictor.

**Lookback:** 1-month (optimal). Also tests 3, 6, and 12-month windows — momentum is short-lived in Treasuries.

**Cross-maturity finding (critical):** Using the average return across all other maturities produces only a minor loss in predictive power relative to same-maturity signals. For longest-maturity bonds the R² marginally increases with cross-maturity signals. However, the underlying mechanism is decisive: **approximately 94% of Treasury momentum autocovariance is explained by the level factor** (first principal component of yields). Because all maturities load heavily on the same level factor, cross-maturity signals work — but only because both maturities are proxies for the same underlying movement. The 10Y's own past return already captures this equally well; 2Y adds no independent information.

**Does 2Y explicitly predict 10Y?** Not tested as a specific directional pair. The cross-maturity test uses an average across all maturities, not a 2Y→10Y mapping.

---

### Durham (2014) — "Momentum and the Term Structure of Interest Rates"
*NY Fed Staff Report SR 657.*

**Signal structure:** Cross-sectional (XS) momentum within the U.S. Treasury maturity spectrum. Six maturity buckets from Barclays total-return indexes: 1–3yr, 3–5yr, 5–7yr, 7–10yr, 10–20yr, 20–30yr. The strategy identifies the highest-recent-return bucket and goes long it.

**Lookback:** 2–13 month windows tested systematically.

**Cross-maturity finding:** Yes — relative ranking across maturities. Up to 120bps excess annual return, IR 0.79 under duration-neutral constraints; 207bps and IR 1.01 in a long-short version. Momentum portfolios concentrated at the front and back ends of the curve.

**Does 2Y predict 10Y directionally?** No. The structure is a relative ranking: if the 2Y bucket is the recent winner, the strategy goes long the 2Y bucket — not short or long the 10Y. 2Y outperformance is a signal to trade 2Y, not a predictor of 10Y direction.

---

### Martellini, Rebonato & Maeso (2022) — "Cross-Sectional and Time-Series Momentum in the US Sovereign Bond Market"
*Journal of Fixed Income*, Vol. 31, No. 3, Winter 2022. EDHEC-Risk.

**Signal structure:** Both XS-MOM and TS-MOM examined across the full nominal Treasury maturity spectrum over 40+ years of data. An exact identity linking TS-MOM and XS-MOM is presented.

**Lookback:** Multiple lookback and holding periods tested.

**Cross-maturity finding:** XS-MOM across maturities produces positive Sharpe ratios, but the cross-maturity **reversal** strategy (long the recently underperforming maturity, short the outperformer) outperforms momentum after duration adjustment. The mechanism is the mean-reverting properties of the yield curve slope — which works against momentum in the intermediate term.

**Does 2Y predict 10Y directionally?** No explicit directional test. The finding that reversal dominates further undermines a straightforward cross-maturity momentum transfer.

---

### Cochrane & Piazzesi (2005) — "Bond Risk Premia"
*American Economic Review*, Vol. 95, No. 1.

**Signal structure:** A single tent-shaped linear combination of forward rates at 1, 2, 3, 4, and 5-year maturities predicts excess holding-period returns on bonds of all maturities, with R² up to 44%. Not a momentum model — uses yield curve snapshot (forward rates today), not trailing price returns.

**Lookback:** None in the momentum sense. The predictor is the current shape of the yield curve, not past returns.

**Cross-maturity finding:** This is the strongest academic evidence for cross-maturity information transfer, but it is a term premium model. The 2-year forward rate is one component of the tent-shaped factor that predicts returns at all maturities simultaneously — including 10-year bonds.

**Does 2Y predict 10Y?** The 2-year forward rate is a partial input to a joint forecasting model that predicts all maturities. This is yield-curve-shape-based risk premium forecasting, not return momentum transfer.

---

### Brooks & Moskowitz (2018) — "Yield Curve Premia"
*AQR working paper; Yale SOM.*

**Signal structure:** Three style factors — carry, value, and momentum — applied to yield curve positions organized by level, slope, and curvature. Momentum defined as past 12-month excess return of the **same-maturity bond** (same-instrument). Cross-maturity structure comes from the slope and curvature trade definitions (e.g., long 2Y / short 10Y), not from the momentum signal itself.

**Lookback:** 12-month (standard across TSMOM literature).

**Cross-maturity finding:** The cross-maturity interaction is implicit in slope trades, but the momentum signal driving each leg is same-maturity. Momentum, carry, and value factors jointly predict yield curve premia.

**Does 2Y predict 10Y?** No. Momentum is same-maturity for each position.

---

### Asness, Moskowitz & Pedersen (2013) — "Value and Momentum Everywhere"
*Journal of Finance*, Vol. 68, No. 3.

**Signal structure:** For government bond futures, momentum is the past 12-month return of the same country's 10-year bond futures contract. Momentum is cross-country, not cross-maturity within the U.S.

**Lookback:** 12-month.

**Does 2Y predict 10Y?** Not tested. Bond universe is country-level 10-year contracts; no within-country maturity structure.

---

### Moskowitz, Ooi & Pedersen (2012) — "Time Series Momentum"
*Journal of Financial Economics*, Vol. 104, No. 2.

**Signal structure:** Strictly same-instrument TSMOM. Past 12-month return predicts next 1-month return across 58 futures contracts including multiple bond futures.

**Lookback:** 12-month.

**Does 2Y predict 10Y?** Not tested. Each instrument is treated independently.

---

### Ilmanen (1995) — "Time-Varying Expected Returns in International Bond Markets"
*Journal of Finance*, Vol. 50, No. 2.

**Signal structure:** Yield spreads and global bond returns predict 4–12% of monthly variation in excess long-maturity government bond returns across 6 countries. The yield spread (implicitly the 2Y–10Y gap) predicts long bond returns.

**Lookback:** Monthly predictive regressions using yield level and slope predictors.

**Does 2Y predict 10Y?** Indirectly: yield curve slope (related to the 2Y–10Y gap) predicts long bond returns. But this is carry/term structure prediction (the slope represents carry), not return momentum transfer from 2Y to 10Y.

---

## Synthesis

### Signal structure summary

| Paper | Signal type | Lookback | Cross-maturity? |
|---|---|---|---|
| Sihvonen (2024) | Same-instrument TSMOM | 1-month | Tested; minor viability due to shared level factor |
| Durham (2014) | XS-MOM across maturity buckets | 2–13 month | Relative ranking, not directional 2Y→10Y |
| Martellini et al. (2022) | XS + TS MOM, full spectrum | Multiple | XS-MOM viable; reversal dominates after duration adj. |
| Cochrane-Piazzesi (2005) | Yield curve shape (forward rates) | N/A | Same factor predicts all maturities (risk premium model) |
| Brooks-Moskowitz (2018) | Same-maturity, 12-month | 12-month | Implicit via slope/curvature structure only |
| Asness et al. (2013) | Same-country bond futures | 12-month | Cross-country only |
| Moskowitz et al. (2012) | Same-instrument | 12-month | None |
| Ilmanen (1995) | Yield spread / global factors | Monthly | Yield spread (carry/slope) predicts long bonds |

### Why a naïve 2Y→10Y signal is redundant even if it "works"

Sihvonen's level-factor finding is the key mechanistic explanation: ~94% of Treasury momentum autocorrelation is driven by level factor changes. Because all maturities load heavily on the same level factor, 2Y past returns carry information about future 10Y returns — but *only* because both are proxies for the same underlying movement. The 10Y's own past return already captures this equally or better. 2Y adds no independent information.

### What IS academically supported (three reclassified forms)

**1. Same-instrument TSMOM** (Moskowitz et al. 2012, Asness et al. 2013, Brooks-Moskowitz 2018, Sihvonen 2024): Each contract trades on its own past return. ZT and ZN each carry an independent same-instrument momentum signal. No transfer between them.

**2. Cross-sectional momentum across maturity buckets** (Durham 2014, Martellini et al. 2022): Rank all maturities by recent return; go long the winner. If the 2Y bucket outperformed, go long 2Y — not long or short 10Y. Martellini finds reversal outperforms momentum here after duration adjustment, which further undermines any straightforward maturity-level momentum transfer.

**3. Yield curve shape as term premium predictor** (Cochrane-Piazzesi 2005, Ilmanen 1995): A combination of forward rates or yield spreads predicts expected excess returns at all maturities simultaneously. The 2Y-area forward rate is one input to the Cochrane-Piazzesi tent factor — making it a partial predictor of 10Y returns, but through a risk premium / term structure model, not through price-return momentum.

## One-sentence conclusion

No paper supports using ZT's past return as a directional signal to trade ZN; cross-maturity information flow in Treasuries is documented only as a common level-factor effect (making 2Y signals weakly viable but redundant), as cross-sectional maturity-bucket ranking, or as yield-curve-shape-based term premium prediction — none of which is equivalent to a 2Y→10Y momentum transfer.

## Sources

**Accessed and text extracted:**
- Sihvonen, M., "Yield Curve Momentum," *Review of Finance* 28(3), 2024: 805–845. Bank of Finland DP 2115 (2021). [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3965229) · [Oxford Academic](https://academic.oup.com/rof/article-abstract/28/3/805/7606348)
- Durham, J.B., "Momentum and the Term Structure of Interest Rates," NY Fed Staff Report SR 657, 2014. [PDF](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr657.pdf) · [Liberty Street Economics](https://libertystreeteconomics.newyorkfed.org/2014/05/can-investors-use-momentum-to-beat-the-us-treasury-market/)
- Martellini, L., Rebonato, R. & Maeso, J.M., "Cross-Sectional and Time-Series Momentum in the US Sovereign Bond Market," *Journal of Fixed Income* 31(3), 2022. [EDHEC](https://climateinstitute.edhec.edu/publications/cross-sectional-and-time-series-momentum-us-sovereign-bond-market)
- Cochrane, J.H. & Piazzesi, M., "Bond Risk Premia," *American Economic Review* 95(1), 2005. [PDF](https://web.stanford.edu/~piazzesi/cp.pdf) · [NBER WP9178](https://www.nber.org/system/files/working_papers/w9178/w9178.pdf)
- Brooks, J. & Moskowitz, T.J., "Yield Curve Premia," AQR working paper, 2018. [PDF](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Yield-Curve-Premia.pdf)
- Asness, C.S., Moskowitz, T.J. & Pedersen, L.H., "Value and Momentum Everywhere," *Journal of Finance* 68(3), 2013. [NYU Stern PDF](https://pages.stern.nyu.edu/~lpederse/papers/ValMomEverywhere.pdf)
- Moskowitz, T.J., Ooi, Y.H. & Pedersen, L.H., "Time Series Momentum," *Journal of Financial Economics* 104(2), 2012. [PDF](https://elmwealth.com/wp-content/uploads/2017/06/timeseriesmomentum.pdf)
- Ilmanen, A., "Time-Varying Expected Returns in International Bond Markets," *Journal of Finance* 50(2), 1995. [AQR](https://www.aqr.com/Insights/Research/Journal-Article/TimeVarying-Expected-Returns-in-International-Bond-Markets)

**Additional sources located:**
- Rebonato, R. & Nyholm, K., "Why does the Cochrane-Piazzesi model predict Treasury returns?," *Journal of Empirical Finance*, 2025. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0927539825000726)
- QuantPedia, "2-Year Notes Momentum: Extracting Term Structure Anomalies from FOMC Cycles." [Link](https://quantpedia.com/2-year-notes-momentum-extracting-term-structure-anomalies-from-fomc-cycles/)
- "Duration Rotation in U.S. Treasury Fixed-Income ETFs," *FinTech* MDPI, 2026. [Link](https://www.mdpi.com/2674-1032/5/2/29)
