# Cross-Asset Algorithmic Strategies: Rates, Volatility, Equities, and Crude Oil

**Research date:** 2026-06-09  
**Purpose:** Deep overnight research for a systematic futures trader running combined_monitor.py with live monitoring of CL/MCL, NQ/MNQ, ES/MES, ZT, ZN, VX futures, VIX spot, OVX, SPX, NDX, HYG/BIL, DX.

---

## Table of Contents

1. [Cross-Asset Correlation Regimes: When Oil Inverts](#1-cross-asset-correlation-regimes)
2. [Vol Surface Cross-Asset Dynamics: OVX vs VIX](#2-vol-surface-cross-asset-dynamics)
3. [Rate-Driven CL/Equity Dynamics](#3-rate-driven-cl-equity-dynamics)
4. [Backwardation as Signal](#4-backwardation-as-signal)
5. [Active Systematic Strategies 2024–2026](#5-active-systematic-strategies-2024-2026)
6. [Pairs and Spread Trades: CL/NQ, CL/DX](#6-pairs-and-spread-trades)
7. [Regime Detection Methods](#7-regime-detection-methods)
8. [Actionable Ideas for combined_monitor.py](#8-actionable-ideas-for-combined_monitorpy)

---

## 1. Cross-Asset Correlation Regimes

### 1.1 The Fundamental Split: Supply vs. Demand Shocks

The single most important finding in the academic literature on oil-equity correlation is that **supply-side shocks and demand-side shocks produce opposite correlation signs**. This is the Kilian (2009) decomposition framework, replicated through March 2024 in arXiv:2409.00769.

**Supply shock (e.g., OPEC cut, Hormuz closure, Venezuelan collapse):**
- Oil price rises
- Equity markets fall
- Correlation: **NEGATIVE** (oil up / stocks down)
- Mechanism: input cost shock → inflation → margin compression → discount-rate repricing

**Aggregate demand shock (e.g., China re-opening, global growth acceleration):**
- Oil price rises
- Equity markets also rise
- Correlation: **POSITIVE** (oil up / stocks up)
- Mechanism: both respond to improved global activity expectations

**Oil-specific precautionary/speculative demand shock:**
- Oil price rises on geopolitical fear premium
- Stock markets fall slightly (risk-off) or mixed
- Correlation: **WEAKLY NEGATIVE** (−0.05 to −0.20 typically)
- The Kilian 3-way decomposition finds this is the dominant driver of "ups and downs" while aggregate demand causes "long swings"

**Practical implication for trading:** When you see CL spike and NQ fall simultaneously, that is consistent with a supply or precautionary shock. When CL and NQ both rise, that is a demand shock. The two regimes require completely different positioning.

Sources:
- Kilian (AER 2009), replicated arXiv:2409.00769 through March 2024
- Risk transmission between oil price shocks and major equity indices: https://www.sciencedirect.com/science/article/pii/S1062940825000993
- "Different strokes for different folks": https://www.sciencedirect.com/science/article/abs/pii/S0140988322000780
- Brookings analysis: https://www.brookings.edu/articles/the-relationship-between-stocks-and-oil-prices/

### 1.2 Historical Correlation Magnitudes

From the wavelet Granger causality study (Frontiers in Physics 2024, full dataset):
- **WTI and DJI overall Pearson:** +0.193 (positive, mild, long-run)
- **OVX and DJI:** effectively zero (0.012) in level terms
- **Bidirectional causality WTI↔DJI at short (2–4 day) frequency:** confirmed (both directions, F-stat > 4)
- **COVID-19 period:** strong positive co-movement over 64–256 day bands
- **Ukraine invasion 2022:** brief negative co-movement on 4–8 day scales

Source: https://www.frontiersin.org/journals/physics/articles/10.3389/fphy.2024.1357366/full

From Brookings (5-year rolling windows):
- **Demand-driven oil price changes correlation with stocks:** ~0.48
- **Supply-driven residual:** ~0.16 (much weaker)
- **With VIX control:** demand component rises to 0.68 correlation; supply residual stays near 0.05
- **Rolling 20-day window:** highly volatile, "swinging between positive and negative"

Key asymmetry in quantile regression: **Negative relationships emerge when WTI is in its upper 80% quantile** across most equity ranges — high oil levels are bearish for equities in a nonlinear way. This is the price-level effect vs. the change effect.

### 1.3 Geopolitical Shock Playbook

From Tickeron analysis and MSCI (March 2026) research:
- In **6 of 8 major geopolitical shocks**, equities traded with a negative correlation to oil
- Gulf War 1990: oil +130%, equities −16%
- Libya 2011: oil +36%, equities −19%
- **10-day rolling correlation hits −0.6** during acute geopolitical supply shocks
- Duration matters: "the longer oil stays high during a conflict, the deeper and stickier the equity damage"

MSCI cross-asset playbook findings:
- US equity-bond correlation: near zero (March 2026), down from −0.19 at Russia-Ukraine onset
- Gold: positive first-day return across all studied conflicts; best diversifier
- Minimum volatility factor: only factor outperforming across all regions during oil shocks
- EM government bond yields spike ~20–25 bps within five trading days

Sources:
- Tickeron geopolitical shock analysis: https://tickeron.com/trading-investing-101/why-oil-is-the-one-chart-you-cant-ignore-right-now/
- MSCI multi-asset playbook: https://www.msci.com/research-and-insights/blog-post/a-multi-asset-playbook-for-geopolitical-shocks-and-oil-supply-disruption

### 1.4 The 2022–2024 "Both Down" Regime

In 2022–2023, oil and equities fell together — a demand-destruction regime where global growth concerns dominated. IMF projected 3.3% global growth (below historical average), and the Fed was aggressively hiking. Both asset classes priced in global recession.

Detection method: **when oil falls and equities also fall**, it is a demand-destruction signal, not supply relief. The traditional "falling oil = good for stocks" logic is wrong in this environment. Systematic algos (especially CTAs) detected this via:
- TSMOM signals going short on both
- Credit spread widening (HYG/LQD) confirming the risk-off
- VIX remaining elevated even as oil fell

This is a critical regime distinction for combined_monitor.py. If OVX is falling AND VX is falling AND HYG/BIL is falling, that is demand destruction, not easing conditions.

---

## 2. Vol Surface Cross-Asset Dynamics

### 2.1 OVX and VIX: The Cointegration Relationship

From the most comprehensive study (PMC7199967, 2009–2018 sample, 2,354 daily observations):

**Cointegration results (ARDL bounds test):**
- F_OVX(OVX|VIX, VKOSPI) = 7.64 vs. upper bound 6.36 at 1% significance → **cointegrated**
- F_VIX(VIX|OVX, VKOSPI) = 25.29 → strong cointegration
- The long-run relationship exists in both sub-periods (pre- and post-2014 shale revolution breakpoint)

**Granger causality (Toda-Yamamoto test):**
- Full sample (2009–2018): **bidirectional** — OVX→VIX (χ²=21.15, p=0.0035) AND VIX→OVX (χ²=20.57, p=0.0044)
- Sub-period 1 (2009–2014): bidirectional confirmed
- Sub-period 2 (2014–2018, post-shale): **OVX→VIX causality disappears** (χ²=2.48, p=0.289), but VIX→OVX remains strong
- **Key finding: VIX leads OVX** in the error correction model. Equity fear transmits to oil vol, not the reverse (post-2014)

**Correlation values:**
- Log-level OVX-VIX correlation: 0.6313
- First-difference (daily change) ΔOVx-ΔVIX: 0.4438
- BEKK-GARCH: VIX impact on OVX variance (a₂₁) = −0.0636 (significant); OVX impact on VKOSPI (a₃₁) = 0.1082

**Structural break:** October 8, 2014 — identified via sequential breakpoint test. Post-shale revolution reduced oil market autonomy; equity vol now dominates.

Source: https://pmc.ncbi.nlm.nih.gov/articles/PMC7199967/

### 2.2 OVX/VIX Divergence as a Signal

From practitioner analysis (Mott Capital Management, 2024):
- **Observed extreme: OVX at ~75, VIX below 17 → ratio of >4x**
- Historical precedent for this extreme divergence: only during COVID-19
- Interpretation: either equity markets are complacent about oil-driven geopolitical risk, or oil vol is overstating near-term risk
- **The divergence historically resolves by VIX rising toward OVX**, not OVX falling to VIX

**VOV (volatility-of-volatility) regime:** From Diversifying Crude Oil Price Risk (2024 paper):
- In high VOV regime (OVX itself is volatile), OVX-equity diversification benefit increases
- In low VOV regime, OVX-equity relationship becomes less negative
- VOV is the **meta-signal**: when OVX is moving around rapidly, cross-asset signals strengthen

**Practical rules from menthorq.com:**
- OVX negatively correlated with WTI price during sharp selloffs (vol up when price crashes)
- OVX positively correlated with VIX during macro shocks (global recession fears)
- OVX high relative to realized vol → oil vol sellers; OVX low relative to realized → buy optionality
- OVX is NOT directly tradable (no listed derivatives on OVX itself); express via USO options or CL options

Sources:
- PMC7199967: https://pmc.ncbi.nlm.nih.gov/articles/PMC7199967/
- Mott Capital divergence analysis: https://mottcapitalmanagement.com/equity-volatility-divergence-oil-fx-signals/
- OVX guide: https://menthorq.com/guide/understanding-ovx-oils-volatility-barometer/

### 2.3 The XTSMOM Strategy: OVX as Equity Return Predictor

**This is the most directly actionable academic finding for combined_monitor.py.**

From Fernandez-Perez, Indriawan, Tse, Xu (Journal of Banking & Finance 2022):

**Cross-Asset Time-Series Momentum (XTSMOM) construction:**
- Signal 1: past 1-month stock market return (positive predictor of next-month return — momentum)
- Signal 2: past 1-month OVX change (negative predictor of next-month return — mean-reversion)
- Combined rule: IF past return > 0 AND past OVX change < 0 → go long equity (strongest bullish signal)
- IF past return < 0 AND past OVX change > 0 → go short equity (strongest bearish signal)
- 4 combinations covering all quadrants

**Performance vs. TSMOM:**
- XTSMOM produces higher mean returns, lower standard deviation, higher Sharpe ratio than single-asset momentum
- Tested on global equity markets, May 2007–August 2021
- One-month lookback for OVX changes; one-month holding period
- Also tested: 12-month lookback (standard momentum)

**Implementation note:** Past OVX change is a monthly signal, not intraday. For a live monitor, you can approximate with a trailing 21-day OVX change (OVX_today vs. OVX_21_days_ago).

Sources:
- SSRN paper: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3850465
- ScienceDirect full text: https://www.sciencedirect.com/science/article/abs/pii/S0378426622002849
- Conference paper PDF: https://acfr.aut.ac.nz/__data/assets/pdf_file/0012/686829/1b-XTSMOM_Fernandez-Perez-et-al.,-2022-DMC.pdf

### 2.4 VIX Term Structure Exploitation

**Quantpedia strategy (VX futures):**
- **Daily roll threshold:** sell VX futures when daily roll > +0.10 points (contango); buy when daily roll < −0.10 points (backwardation)
- **Daily roll formula:** (VX1 futures price − VIX spot) ÷ business days to settlement
- **Holding period:** 5 trading days
- **Minimum contract age:** 10 days remaining to maturity
- **Hedge:** E-mini S&P (ES) futures; hedge ratio from regression of VX changes on % changes in ES
- **Performance (2007–2011):** 19.67% annualized (out-of-sample alpha deteriorated)
- **Context:** VIX futures in contango ~80–85% of trading days

**Critical risk:** 22 backwardation episodes observed 2004–2025; 21 of 22 coincided with S&P drawdown >5% within 30 days. Sustained backwardation (5+ days) historically precedes deeper drawdowns.

Sources:
- Quantpedia VIX term structure: https://quantpedia.com/strategies/exploiting-term-structure-of-vix-futures
- VIX contango/backwardation guide: https://volatilitybox.com/research/vix-contango-backwardation/

---

## 3. Rate-Driven CL/Equity Dynamics

### 3.1 Oil-Treasury Yield Correlation

From Real Investment Advice analysis and Crux Investor:
- **100-day rolling average correlation (since May 2023): 0.60** — statistically robust
- **25-year historical correlation: 0.28** — current regime is significantly elevated
- **Lead-lag direction: oil changes lead Treasury yield changes by ~2 weeks** (short-term trend changes)
- Mechanism: oil price rise → inflation expectations → nominal bond yields rise
- Bond traders should follow crude prices closely as a leading indicator

**Two-regime structure:**
- Inflationary supply shock (2021–2022): oil and yields both rose — positive correlation via inflation expectations
- Growth slowdown (2023): both fell — positive correlation via demand expectations
- The positive correlation persists across both environments; the transmission mechanism differs

From ECB working paper (2024):
- **Pre-2015 US (net importer):** oil-dollar correlation was negative
- **Post-2015 US (net exporter):** correlation unreliable, sometimes positive
- **2021–2022:** dollar AND oil rose simultaneously (Fed tightening + Ukraine supply shock drove both independently)
- 3-year rolling correlation of oil-dollar: −0.29 (as of March 2025); 1-year: −0.11; 3-month: +0.01
- **Conclusion: oil-dollar inverse correlation is structurally weaker and no longer reliable as a systematic signal**

Baker Institute (Fed Watcher's Guide):
- There are documented relationships among interest rate movements, trader behavior, and oil price formation
- Fed rate decisions create observable positioning changes in oil futures

Sources:
- RIA oil-bond yields: https://realinvestmentadvice.com/resources/blog/oil-and-bond-yields-are-tied-at-the-hip/
- ECB oil-dollar link: https://www.ecb.europa.eu/press/economic-bulletin/focus/2024/html/ecb.ebbox202407_02~5ce155d504.en.html
- Baker Institute: https://www.bakerinstitute.org/research/fed-watchers-guide-oil-markets-2024-and-2025

### 3.2 The Rate-Oil-Equity Triangle

Current dominant regime (2023–2026, 10y yield elevated):
- **Rising oil + rising yields = stagflation scenario** → bearish equities. Both ZN falling and CL rising is the most dangerous cross-asset combination for NQ/ES longs
- **Rising oil + falling yields** = demand boom scenario → oil supply tight, rates easing, equity bullish
- **Falling oil + rising yields** = pure discount rate shock (2022) → equities can fall despite "cheaper" oil; equity algos should NOT treat falling oil as relief in this regime
- **Falling oil + falling yields** = demand destruction → bearish risk-on assets, but potential Fed pivot catalyst

**Key threshold from current monitoring in combined_monitor.py:** 10y > 4.5% AND 30y > 5.0% is already an existing rate_alerted condition. This is appropriate as a joint threshold for the stagflation regime.

**Refinement:** Add CL direction. The rate alert is more dangerous when CL is simultaneously above a threshold OR rising. "10y > 4.5% AND CL > $X" is a stronger signal than either alone.

### 3.3 CTA Positioning in Rates + Oil (2024)

From HedgeNordic CTA performance review:
- 2024: Energy was the **worst-performing CTA sector** — crude was range-bound most of the year
- Range-bound oil → CTA trend signals weak or absent on energy
- CTAs generated gains via: equities (AI/soft-landing rally), soft commodities (cocoa, coffee supply shocks)
- Fixed income was mixed: rate-cut uncertainty created trend reversals
- Faster systems (short-term trend following) outperformed during August 2024 VIX spike and carry unwind
- Longer-term CTAs underperformed in choppy, multi-directional markets

**Implication:** CTAs currently have weak or flat CL positions due to range-bound behavior. A breakout from crude's range in either direction will trigger simultaneous CTA entries from many systematic funds — amplifying the move. Watch for CL breaking cleanly above/below 3–6 month range as a CTA flow trigger.

Source: https://hedgenordic.com/2025/02/main-drivers-of-cta-performance-in-2024-2/

---

## 4. Backwardation as Signal

### 4.1 CL Futures Curve: Current Structure (May 2026 data)

From Barchart and CME Group research (2026):
- **CL Jun-26 vs. Jun-27 spread:** $20.65/barrel premium (steep backwardation)
- **CL Jun-26 vs. Jun-28:** $34.47/barrel premium
- **Jun-Jul front spread:** >$4.00/barrel
- **Brent Jul-26 vs. Jul-27:** $29.34/barrel premium
- **Brent Jul-Aug spread:** >$4.60/barrel

This is historically extreme backwardation (comparable to 2022 post-Ukraine levels). The CME Group analysis attributes it to Strait of Hormuz disruptions affecting ~20% of global oil supply.

**Interpretation framework:**
- Steep backwardation = physical scarcity, hedgers paying up for near-term delivery certainty
- Deferred contracts (CL Dec-30: ~$69.85) show market expects eventual supply normalization
- Roll yield for long CL holders: positive and substantial (selling spot premium, buying cheap deferred)
- The curve collapsing from backwardation toward flat/contango would be the warning sign of supply relief

### 4.2 Backwardation as Cross-Asset Signal

**What steep CL backwardation historically signals for other markets:**

From academic research and practitioner analysis:
1. **Tight physical supply** — correlated with geopolitical risk premium, which is **bearish for equities** (supply shock mechanism, Section 1.1)
2. **CTAs go long CL in backwardation** — positive roll yield attracts systematic trend followers, amplifying upward price pressure
3. **HYG/credit correlates 0.85 with front-month CL** during energy-dominated credit stress periods (2009, 2011, 2015) — energy sector is the transmission channel to junk credit
4. **When CL backwardation is supply-shock driven**, expect: equities neutral to negative, yields rising (inflation), VIX potentially rising

**Quantifying the backwardation signal:**
- Aspect Capital (CTA) uses curve dynamics/term structure as a commodity-specific signal layer
- Standard approach: (CL1 price − CL3 price) / CL1 price — positive = backwardation, scaled to % of front price
- Alternative: (CL1 − CL13) as 12-month spread — current: $20.65 ÷ ~$109 ≈ 19% — historically extreme
- **Equity signal application:** 12-month spread > 10% of spot price = tight supply regime; typically associated with negative oil-equity correlation (supply shock), so reduce equity longs and watch for NQ pressure

**Historical backwardation frequency:**
- Since 1985: CL in backwardation ~58% of time, contango ~42% (6-month measure)
- Steep backwardation (>$5/barrel front spread) is more rare — clustered at supply crisis events

### 4.3 CL vs. VX Backwardation Confluence

No systematic academic study on simultaneous CL + VX backwardation was found, but from first principles and available data:

**VX backwardation statistics:**
- VX in backwardation ~20% of trading days (VIX spot > VX1 futures)
- 22 episodes of total backwardation 2004–2025; 21/22 coincided with S&P drawdown >5% within 30 days
- Sustained VX backwardation (5+ days) historically precedes deeper drawdowns

**CL backwardation + VX backwardation simultaneously:**
- Both indicate acute stress: oil = supply stress; VX = equity fear
- Combined signal: increased probability of stagflation-type drawdown scenario
- The combined state should be treated as a high-conviction risk-off regime
- In combined_monitor.py, vx_slope > 0 (VX backwardation) is already tracked. Adding a check for CL backwardation (CL front > CL back) would complete this cross-asset confluence check.

Sources:
- CME Group implications of WTI backwardation: https://www.cmegroup.com/insights/economic-research/2026/implications-of-wti-oil-futures-in-backwardation-amid-the-supply-crunch.html
- EBC Financial oil backwardation warning: https://www.ebc.com/forex/oil-backwardation-not-100-crude-is-the-real-warning
- VIX term structure backwardation: https://www.cboe.com/insights/posts/inside-volatility-trading-is-vix-backwardation-necessarily-a-sign-of-a-future-down-market/

---

## 5. Active Systematic Strategies 2024–2026

### 5.1 CTA Trend Following: The Dominant Systematic Force

CTAs (Commodity Trading Advisors) are the primary systematic participant across futures markets. Their dominant strategy is Time Series Momentum (TSMOM): go long markets with recent positive trend, short markets with negative trend.

**2024 CTA performance by sector:**
- Soft commodities (cocoa, coffee): best performer, supply-driven trends
- Equities (NQ, ES): second best — AI/soft-landing/election-driven trends
- Rates (ZN, ZT): mixed — rate-cut uncertainty caused trend reversals
- Energy (CL, MCL): **worst sector** — range-bound, produced losses

**Market signals CTAs respond to:**
- 3-month, 6-month, and 12-month price momentum (the core TSMOM lookbacks)
- Vol-adjusted position sizing: each market sized to contribute equal vol (typically 10–15% annual target)
- CTA entries amplify existing trends: breaking out of 6-month range in CL is a major CTA trigger

**Key behavioral insight:** When CL breaks out of a range, CTA positioning changes rapidly — these systems are running simultaneously in hundreds of firms. This creates momentum clustering and overshoots.

Sources:
- HedgeNordic CTA 2024 review: https://hedgenordic.com/2025/02/main-drivers-of-cta-performance-in-2024-2/
- Cazadores Investments CTA analysis: https://www.cazadoresinvestments.com/2025/03/27/trend-following-is-not-dead-how-ctas-are-outpacing-traditional-hedge-funds/
- Aspect Capital commodity role: https://hedgenordic.com/2024/10/the-versatile-role-of-commodities-in-cta-portfolios/

### 5.2 Risk Parity and Vol-Targeting Funds

Risk parity (Bridgewater All Weather, AQR Risk Parity) allocates by risk contribution rather than capital:
- Target: each asset class contributes equal volatility to portfolio
- Typical commodity allocation: 15–35% of risk; ranges 10–50% for active managers
- Monthly rebalancing; some daily vol-targeting variants

**2024 risk parity environment:** Elevated rates and correlations between bonds and equities became positive, breaking the core diversification assumption. Risk parity underperformed (CAIA 2024). The equity-bond positive correlation period ran from 2022 through May 2025 (Macrosynergy data).

**Oil in risk parity:**
- Oil has positive correlation with inflation → used as inflation hedge
- When correlation with equities is positive (demand shock regime), oil is not a diversifier
- When correlation is negative (supply shock regime), oil is the ideal hedge

**Systematic implication:** Vol-targeting equity funds mechanically sell equities when VIX spikes and buy when VIX falls. This creates predictable flow patterns. When OVX is elevated AND VIX is low (the divergence described in Section 2.2), it means oil traders see risk that equity vol-targeting funds do not, and the equity selling cascade has not yet started.

Sources:
- Risk parity background: https://tradewiththepros.com/risk-parity-trading-strategies/
- CAIA risk parity 2024: https://caia.org/blog/2024/01/02/risk-parity-not-performing-blame-weather

### 5.3 The HYG/Credit-Oil-Equity Triangle

From ETF.com and SystemTrader.co analysis:

**Credit (HYG) as transmission channel between oil and equity:**
- 30-day correlation between CL front and HYG: **0.85** during energy-dominated credit periods (2009, 2011)
- 30-day correlation between SPY and CL: **0.61** (highest since 2013) during peak co-movement
- Heavy energy sector weighting in high-yield credit makes HYG sensitive to oil prices
- When CL falls, energy junk bonds fall, HYG falls, credit conditions tighten → equities fall as a lagged effect

**HYG/LQD z-score systematic rules (SystemTrader.co thresholds):**
- TIGHT (z ≥ +1σ): Risk On — HY outperforming IG
- NORMAL (−1σ to +1σ): No directional signal
- WIDE (−2σ to −1σ): Risk Off forming — HY underperforming IG
- STRESS (z ≤ −2σ): Severe stress — historically coincides with equity drawdowns

**Cross-confirmation ratios:**
- TLT/HYG ratio: distinguishes rates stress from credit stress (both TLT and HYG falling = rate-driven, not credit stress)
- JNK/AGG: alternative HY-vs-aggregate view
- EMB/LQD: EM credit appetite

**Key divergence rule:** If HYG/LQD (or HYG/BIL) is falling while SPY is making new highs → flag divergence; historically a leading indicator of equity reversal. Regime persistence matters: WIDE for 30+ days is a stronger signal than a single-day reading.

**Important caveat:** Credit-spread widening worked best for credit-driven episodes (2007–08, energy in 2015–16). It was a poor signal when equity drawdowns were driven by discount-rate shocks (2022) or pure leverage events (2020, August 2024). Requires multi-factor confirmation: credit widening + VIX rising + breadth deteriorating.

Sources:
- SystemTrader credit spreads: https://www.systemtrader.co/tools/credit-spreads
- ETF.com oil-credit-equity: https://www.etf.com/sections/news/why-stocks-oil-are-correlated
- HYG analysis: https://seekingalpha.com/article/4814115-hyg-everything-you-need-to-know-about-the-high-yield-bond-etf

### 5.4 Man AHL and Aspect Capital: Cross-Asset Systematic Architecture

From Man Institute and Aspect Capital disclosures:

**Man AHL (founded 1987, now multi-strategy systematic):**
- Started as CTA, evolved to multi-strategy quant with 30+ years of systematic research
- Applies "scientific rigor" across multiple data types and hundreds of global markets
- Recent research topics: trend following deep dives, optimal market mix, regime detection using ML

**Aspect Capital commodity approach:**
- Commodity risk: averages 35% of total portfolio risk; ranges 10–50% dynamically
- July 2024 example: traditional CTA held 45–50% commodity risk; alternatives product at 23%
- Employs commodity-specific signal layers including "curve dynamics and term structure effects"
- Maintains exposure across ~140 commodity markets
- Exposure scales automatically with signal strength; no fixed position when signals are weak

**Quant Winter context:** The "Quant Winter" concern of 2024–2025 was whether factor crowding and signal decay would reduce CTA alpha. The counter-argument (Man Institute, Feb 2026 "Quant Renaissance Part II") suggests that evolved strategies building on ML and alternative data have rebuilt resilience.

Sources:
- Man AHL: https://www.man.com/ahl
- Man Institute systematic research: https://www.man.com/maninstitute/systematic
- Aspect Capital commodities: https://hedgenordic.com/2024/10/the-versatile-role-of-commodities-in-cta-portfolios/

---

## 6. Pairs and Spread Trades

### 6.1 Crude Oil vs. Dollar Index (DX)

**The broken correlation:**
From ECB (July 2024) and AGBI analysis:
- Traditional relationship: **oil and USD move inversely** (oil priced in USD; stronger USD → less demand from other currencies)
- Post-2015 structural shift: US became world's largest oil producer at 13.25 million bpd; net exporter since late 2019
- 3-year correlation: −0.29 (March 2025); 1-year: −0.11; 3-month: +0.01 — trend toward zero or slightly positive
- During June 2021–July 2022: both rose simultaneously (Fed tightening + Ukraine drove both)

**Strategic implication:** The old DX-as-oil-predictor (or oil-as-DX-predictor) signal is **unreliable as a systematic filter**. The US becoming a petrocurrency has structurally reduced the inverse relationship. A trader relying on DX falling to predict CL rising (or vice versa) will get many false signals.

**Residual utility:** Extreme USD strength (DXY > 108–110) still exerts headwinds on commodity demand from non-US buyers. Can be used as a negative filter (extreme DX strength = headwind for CL), not a primary signal.

Sources:
- ECB oil-dollar: https://www.ecb.europa.eu/press/economic-bulletin/focus/2024/html/ecb.ebbox202407_02~5ce155d504.en.html
- AGBI inverse correlation analysis: https://www.agbi.com/opinion/oil-and-gas/2023/12/matein-khalid-will-oils-inverse-correlation-to-the-dollar-return/

### 6.2 Crude Oil vs. NQ (Nasdaq-100)

From Aeromir futures correlation data:
- CL/ES (crude vs. S&P): essentially **uncorrelated** at a naive level (CL/ES ~0.0009 — statistical noise)
- But context-dependent correlation is large and systematic (as described in Section 1)

**Supply-shock regime CL/NQ relationship:**
- NQ is the most rate-sensitive major index (long-duration growth stocks)
- When CL spikes on supply shock → inflation expectations rise → ZN falls (yields rise) → NQ gets doubly hit (input cost + discount rate)
- NQ's sensitivity to yields makes it MOST negatively correlated to oil during supply shocks
- ES is less sensitive to rates than NQ (more value exposure)

**Implication:** When CL spikes on supply/geopolitical signal AND ZN is falling simultaneously, this is the strongest signal to be cautious on NQ longs. The NQ/CL negative correlation in this scenario is driven by both being in the same rate-transmission chain: oil → inflation → yields → NQ discount rate compression.

**Practical pairs trade observation:** CL front−back spread (backwardation depth) as a leading indicator for NQ pressure:
- Deep backwardation (CL1 − CL2 > $3/barrel) = supply shock = potential NQ headwind
- Add yield confirmation: ZN falling (ZN below a rolling moving average) = amplified NQ risk

Source: https://futures.aeromir.com/post/110/understanding-futures-correlation-what-every-trader-should-know

### 6.3 Oil Calendar Spread as Inventory Signal

From quantitative oil trading research:
- **(CL1 − CL3) percentage spread** is the most informative term structure metric (more than raw price)
- Currently (2026): >15% in backwardation — historically extreme
- When curve flips from contango to backwardation: high-signal event for trend followers
- Backwardated curve = positive roll yield for longs; attracts CTA long pressure

**Seasonal pattern (documented 1983–2017):**
- Long Dec CL / Short Apr CL from November to March: average ~1.32% monthly return
- Driven by winter heating oil demand cycle
- Now less reliable due to US shale reducing seasonality

**Mean-reversion calendar spread strategy (academic research):**
- CL and Natural Gas calendar spreads exhibit mean-reverting behavior
- Bollinger Bands (2σ) on front-minus-back spread for entry/exit
- Mean-reversion half-life varies; CL is shorter (days) than natural gas

Source: https://www.alphaexcapital.com/commodities/commodity-derivatives-and-strategies/spread-trading-in-commodities/calendar-spreads-crude-oil

---

## 7. Regime Detection Methods

### 7.1 Rolling Correlation as Primary Signal

**The simplest and most robust approach for a live monitor:**

Rolling Pearson correlation between daily returns of CL and NQ (or CL and ES):
- **20-day window:** high frequency, captures intraday regime shifts quickly but noisy
- **60-day window:** more reliable, identifies sustained regime changes
- **Threshold convention:** correlation > +0.4 = demand-driven co-movement; < −0.4 = supply shock regime

**When correlation is near zero** (±0.2): statistically no relationship — oil and equities are in different worlds. Neither use oil as an equity predictor nor equities as an oil predictor.

**Implementation in Python (pandas):**
```python
# From the available live data in combined_monitor.py, build price history
# and compute rolling correlation
import pandas as pd
import numpy as np

cl_returns = pd.Series(cl_price_history).pct_change()
nq_returns = pd.Series(nq_price_history).pct_change()
rolling_corr_20 = cl_returns.rolling(20).corr(nq_returns)
rolling_corr_60 = cl_returns.rolling(60).corr(nq_returns)
```

combined_monitor.py already maintains `cl_price_hist` and `nq_price_hist` as deques (maxlen=180). A 20-minute rolling correlation is easily computable from these existing structures.

### 7.2 Hidden Markov Models (HMM) for Regime Detection

**Standard implementation (David Borst / QuantStart):**
- **n_components:** 2 (low-vol vs. high-vol) or up to 9 (ranked by annualized return)
- **Features (27-feature full set):** daily returns, multi-horizon MAs, VIX level, VIX 1-day/5-day change, VIX term structure ratios (VIX3M/VIX, VIX6M/VIX), credit spread proxy (log(HYG) − log(LQD)), drawdowns from 6-month peaks
- **PCA compression:** 13 principal components retained at 95.2% variance explained
- **Decoding method:** Filtered probabilities (forward algorithm) for live use — avoids future data bias vs. Viterbi

**Two-state HMM for crude oil specifically:**
- Regime 1: higher returns, higher vol (bull market in oil — note: more volatile even when bullish)
- Regime 2: lower/negative returns, lower vol (bear/sideways market)
- 0/1 strategy: 100% invested in Regime 1; 100% cash in Regime 2

**Python library:** `hmmlearn` (GaussianHMM); `depmixS4` in R

**Practical challenge for real-time trading:** HMM regime assignment is probabilistic and can lag. Use smoothed probability (filtered) not point estimates. A conviction threshold of >0.53 for regime 1 before acting avoids signal noise.

Sources:
- HMM regime detection Medium/David Borst: https://datadave1.medium.com/detecting-market-regimes-hidden-markov-model-2462e819c72e
- QuantStart HMM guide: https://www.quantstart.com/articles/hidden-markov-models-for-regime-detection-using-r/
- QuantInsti regime-adaptive trading Python: https://blog.quantinsti.com/regime-adaptive-trading-python/

### 7.3 Markov Regime-Switching (MRS-VAR) for Oil-Equity

From PMC9944429 (Markov switching approach on oil-equity nexus including COVID):

**Best-fit model:** MSI(2)-VAR(2)
- Two regimes
- Regime 1: TSX index expected monthly return +0.18%; WTI expected monthly return +0.57% → **bull regime** (demand-driven)
- Regime 2: TSX expected monthly return −0.02%; WTI expected monthly return +0.003% → **bear/stagflation regime**

**Key structural finding for HMM feature construction:**
- In bull markets: **negative** oil-stock correlation
- In bear markets: **positive** oil-stock correlation

This inverts the intuition. In bear markets, both fall together (demand destruction). In bull markets, oil and equities move opposite — growth drives stocks while energy sees demand rotation. The sign actually depends on the economic regime more than the supply/demand shock type.

Sources:
- PMC9944429: https://pmc.ncbi.nlm.nih.gov/articles/PMC9944429/
- Markov-switching fear factor: https://www.mdpi.com/1911-8074/16/2/67

### 7.4 Real-Time SVAR Shock Decomposition

For a live systematic trader, full SVAR implementation is complex but a simplified proxy exists:

**Proxy approach for real-time shock classification:**

| Observable signal | Likely shock type | Oil-equity correlation |
|---|---|---|
| CL spike + ZN falling (yields rising) | Supply shock / inflation | NEGATIVE |
| CL spike + ZN rising (yields falling) | Demand shock (risk-on) | POSITIVE |
| CL falling + ZN rising (yields falling) | Demand destruction | NEGATIVE (both fall) |
| CL rising + HYG rising | Demand boom | POSITIVE |
| CL rising + HYG falling | Stagflation / energy stress | NEGATIVE |
| OVX spike without CL move | Precautionary/geopolitical | MIXED to NEGATIVE |

This shock-type proxy is achievable in combined_monitor.py using existing ZN, HYG/BIL, and CL data streams with no additional subscriptions.

---

## 8. Actionable Ideas for combined_monitor.py

Listed in order of implementation priority (highest value first, within practical constraints of the existing architecture). All suggestions use data already subscribed in the monitor.

---

### Idea 1: OVX/VIX Divergence Alert (Highest Priority)
**Rank: 1 | Complexity: Low | Data needed: OVX, VIX — already live**

**Signal:** When OVX / VIX_spot > 2.5x, fire a new dedicated alert.

**Rationale:** This extreme divergence (oil vol >> equity vol) historically precedes a VIX catchup event (equity vol rising toward oil vol). The April 2024 episode where OVX was ~75 and VIX ~17 (ratio >4x) is historically only matched during COVID-19. When equity vol has not yet priced what oil vol is pricing, there is an asymmetric risk: equity vol compression is unlikely; equity vol expansion toward oil vol levels is probable.

**Implementation:**
```python
# In the polling loop, after computing ovx_px and vix_spot_px
_ovx_vix_alerted = False  # add to state variables

if (not math.isnan(ovx_px) and not math.isnan(vix_spot_px)
        and vix_spot_px > 0):
    ovx_vix_ratio = ovx_px / vix_spot_px
    if ovx_vix_ratio > 2.5 and not _ovx_vix_alerted:
        _alert(
            f'⚠️ OVX/VIX divergence: OVX={ovx_px:.1f}  VIX={vix_spot_px:.2f}'
            f'  ratio={ovx_vix_ratio:.2f}x (>2.5x threshold)\n'
            f'Oil vol >> equity vol — VIX historically catches up\n'
            f'CL={cl_now:.2f}  VX={vx_level:.2f}  10y={_sy(tnx_yld)}%',
            critical=True
        )
        _ovx_vix_alerted = True
    elif ovx_vix_ratio < 1.8:
        _ovx_vix_alerted = False
```

**Thresholds:** 2.5x as primary alert; 2.0x as watch level; 1.8x as reset. During RTH when VIX is live.

---

### Idea 2: Shock-Type Classifier Inline Alert
**Rank: 2 | Complexity: Low | Data needed: CL, ZN, HYG — all already live**

**Signal:** On each CL spike alert (already existing cl_spike_rise / cl_spike_fall), append a shock-type classification based on contemporaneous ZN and HYG direction.

**Rationale:** The most important thing to know when CL spikes is *why*. Supply shock (ZN falling = yields rising, HYG falling = credit stress) requires immediately hedging equity longs. Demand shock (ZN and HYG both rising) means the spike is accompanied by risk-on, and equity hedges are less urgent.

**Implementation:**
```python
def classify_oil_shock(cl_move_pct, zn_pct_chg, hyg_pct_chg):
    """Returns shock type string based on concurrent asset movements.
    cl_move_pct: CL % move (positive = price rise)
    zn_pct_chg: ZN % change (positive = bonds rising = yields falling)
    hyg_pct_chg: HYG % change (positive = credit strengthening = risk-on)
    """
    if cl_move_pct > 0:   # oil rising
        if zn_pct_chg < -0.15 and hyg_pct_chg < -0.10:
            return 'SUPPLY SHOCK / STAGFLATION — oil up, bonds down, credit weak → bearish NQ'
        elif zn_pct_chg > 0.10 and hyg_pct_chg > 0.05:
            return 'DEMAND BOOM — oil up, bonds up, credit strong → neutral/bullish NQ'
        elif zn_pct_chg < -0.10:
            return 'INFLATION FEAR — oil up, yields rising → rate headwind for NQ'
        else:
            return 'GEOPOLITICAL/PRECAUTIONARY — oil up, mixed signals → reduce NQ'
    else:   # oil falling
        if zn_pct_chg < -0.10 and hyg_pct_chg < -0.20:
            return 'DEMAND DESTRUCTION — oil down, bonds down, credit wide → bearish NQ'
        elif zn_pct_chg > 0.15:
            return 'SUPPLY RELIEF — oil down, bonds rally → bullish NQ'
        else:
            return 'AMBIGUOUS OIL DECLINE — mixed signals'
```

Append `classify_oil_shock(...)` result to all existing CL spike alert messages. Requires computing session % changes for ZN and HYG, which are already in `_sess_pct()` function in the monitor.

---

### Idea 3: CL Backwardation + VX Backwardation Confluence Alert
**Rank: 3 | Complexity: Low | Data needed: CL front/back (already live), VX slope (already computed)**

**Signal:** When BOTH CL is in backwardation (CL front > CL back) AND VX term structure is inverted (vx_slope > 0), fire a combined confluence alert.

**Rationale:** Simultaneous backwardation in two separate futures markets (oil and equity vol) is the clearest cross-asset stress signal available in the monitor's existing data. VX backwardation alone precedes S&P drawdown >5% in 21/22 historical episodes. CL backwardation alone signals tight supply. Both together = the market simultaneously fears oil supply disruption AND equity market risk.

**Implementation:**
```python
# CL backwardation: front > back (negative calendar spread)
cl_in_backwardation = (
    not math.isnan(cl_front_mid) and not math.isnan(cl_back_mid)
    and cl_front_mid > cl_back_mid
)
cl_backw_depth = (cl_front_mid - cl_back_mid) if cl_in_backwardation else 0.0

# VX backwardation: already computed as vx_slope > 0
# Confluence check:
_cl_vx_backw_alerted = False  # add to state

if cl_in_backwardation and not math.isnan(vx_slope) and vx_slope > 0:
    if not _cl_vx_backw_alerted:
        _alert(
            f'🔴 CL + VX dual backwardation:\n'
            f'CL front={cl_front_mid:.2f}  back={cl_back_mid:.2f}'
            f'  depth=${cl_backw_depth:.2f}/bbl\n'
            f'VIX={vix_spot_px:.2f}  VX1={vx_level:.2f}  VX_slope={vx_slope:+.2f}\n'
            f'Both oil and equity vol markets in stress simultaneously\n'
            f'Consistent with supply shock + equity fear regime\n'
            f'OVX={_sy(ovx_px, dec=2)}  10y={_sy(tnx_yld)}%'
            f'  HYG/BIL={_sy(hyg_bil, dec=4)}',
            critical=True
        )
        _cl_vx_backw_alerted = True
else:
    _cl_vx_backw_alerted = False
```

**Threshold refinement:** Add minimum CL backwardation depth (e.g., >$1.50/barrel for front-month spread) to avoid alerting on trivial inversion noise.

---

### Idea 4: XTSMOM-Style Monthly OVX Signal
**Rank: 4 | Complexity: Medium | Data needed: OVX (already live), price history**

**Signal:** Track the rolling 21-day change in OVX as a negative predictor of NQ direction. When OVX 21-day change is positive (vol rising) AND NQ 21-day return is negative, this is the strongest bearish equity quadrant in the XTSMOM framework.

**Rationale:** Fernandez-Perez et al. (2022) showed that combining past OVX change (negative predictor) with past equity return (positive predictor) outperforms single-asset momentum globally. This is the most academically validated cross-asset signal in this research.

**Implementation:**
```python
# Requires a 21-day price history for OVX and NQ
# OVX is already logged each poll (ovx_px); add to a new deque
_ovx_21d_hist = collections.deque(maxlen=25)  # store (timestamp, ovx_value)

# In polling loop:
if not math.isnan(ovx_px):
    _ovx_21d_hist.append((datetime.now(ET), ovx_px))

# Compute 21-day OVX change and NQ return when enough history
if len(_ovx_21d_hist) >= 21 and len(nq_price_hist) >= 21:
    ovx_21d_change = ovx_px - _ovx_21d_hist[-21][1]  # current minus 21 polls ago
    nq_21d_start = nq_price_hist[-21][1] if len(nq_price_hist) >= 21 else None
    nq_21d_return = ((nq_last - nq_21d_start) / nq_21d_start
                     if nq_21d_start else float('nan'))
    
    # XTSMOM quadrant classification:
    if not math.isnan(nq_21d_return) and not math.isnan(ovx_21d_change):
        if nq_21d_return > 0 and ovx_21d_change < 0:
            xtsmom_signal = 'BULLISH (NQ up + OVX down)'
        elif nq_21d_return < 0 and ovx_21d_change > 0:
            xtsmom_signal = 'BEARISH (NQ down + OVX up)'
        elif nq_21d_return > 0 and ovx_21d_change > 0:
            xtsmom_signal = 'MIXED (NQ up but OVX rising — vol warning)'
        else:
            xtsmom_signal = 'MIXED (NQ down but OVX falling — potential base)'
        log.info('[XTSMOM] 21d signal: %s  NQ_ret=%.2f%%  OVX_chg=%.2f',
                 xtsmom_signal, nq_21d_return*100, ovx_21d_change)
```

**Alert on regime shift:** When the quadrant changes from BULLISH to BEARISH (NQ 21d return flips negative AND OVX 21d change flips positive), alert once.

---

### Idea 5: Yield-Oil Joint Threshold (Stagflation Alert Refinement)
**Rank: 5 | Complexity: Low | Data needed: ZN/TNX (already live), CL (already live)**

**Signal:** Enhance the existing `rate_alerted` condition by requiring CL to be elevated simultaneously.

**Current condition:** 10y > 4.5% AND 30y > 5.0%
**Enhanced condition:** 10y > 4.5% AND 30y > 5.0% AND CL > [configurable threshold, e.g., 85.0]

**Rationale:** High yields alone can be benign (strong economy). High yields + high oil is the stagflation combination that historically damages NQ most severely. The joint condition filters out rate rises that are demand-driven (which are not bearish for equities) vs. supply-shock-driven (which are).

**Implementation:**
```python
# Add to argument parser:
sig.add_argument('--stagflation-cl-min', type=float, default=80.0,
                 help='CL price threshold for stagflation joint alert (default: 80.0)')

# Replace existing rate_triggered condition:
rate_triggered = (
    not math.isnan(tnx_yld) and not math.isnan(tyx_yld)
    and tnx_yld > 4.5 and tyx_yld > 5.0
    and cl_now >= args.stagflation_cl_min
)
# If rate+oil fire:
#   'STAGFLATION RISK: 10y=X%  30y=Y%  CL=Z — joint threshold breached'
# Keep the existing rate-only alert as a separate (lower-priority) signal.
```

**Thresholds:** CL > 80 is a reasonable lower bound (ensures oil is not at depressed levels); adjust to 90–100 for the current elevated backwardation environment.

---

### Idea 6: CL/NQ Rolling Correlation Regime Tracker
**Rank: 6 | Complexity: Medium | Data needed: price history deques already exist**

**Signal:** Compute the rolling 20-poll (20 × POLL_INTERVAL seconds) correlation between CL and NQ returns. Alert when correlation crosses from positive to strongly negative (< −0.4) or vice versa.

**Rationale:** The rolling short-term correlation is the most direct regime detector available. Crossing −0.4 indicates a supply-shock or stagflation regime is taking hold. The correlation tracking also lets you visualize whether oil and NQ are in lockstep or diverging.

**Implementation:**
```python
def _rolling_corr(hist_a, hist_b, n=20):
    """Compute Pearson correlation between last n price returns from two deques."""
    if len(hist_a) < n+1 or len(hist_b) < n+1:
        return float('nan')
    prices_a = [p for _, p in list(hist_a)[-n-1:]]
    prices_b = [p for _, p in list(hist_b)[-n-1:]]
    rets_a = [(prices_a[i] - prices_a[i-1]) / prices_a[i-1]
              for i in range(1, len(prices_a)) if prices_a[i-1] > 0]
    rets_b = [(prices_b[i] - prices_b[i-1]) / prices_b[i-1]
              for i in range(1, len(prices_b)) if prices_b[i-1] > 0]
    if len(rets_a) < n or len(rets_b) < n:
        return float('nan')
    rets_a = rets_a[-n:]; rets_b = rets_b[-n:]
    mean_a = sum(rets_a)/n; mean_b = sum(rets_b)/n
    cov = sum((a - mean_a)*(b - mean_b) for a, b in zip(rets_a, rets_b)) / n
    std_a = (sum((a - mean_a)**2 for a in rets_a)/n)**0.5
    std_b = (sum((b - mean_b)**2 for b in rets_b)/n)**0.5
    if std_a < 1e-10 or std_b < 1e-10:
        return float('nan')
    return cov / (std_a * std_b)

# In polling loop:
cl_nq_corr_20 = _rolling_corr(cl_price_hist, nq_price_hist, n=20)
log.info('[REGIME] CL/NQ 20-poll rolling corr: %.3f', cl_nq_corr_20)

# Alert on regime change (first cross below -0.4):
_cl_nq_neg_regime_alerted = False
if not math.isnan(cl_nq_corr_20) and cl_nq_corr_20 < -0.4:
    if not _cl_nq_neg_regime_alerted:
        _alert(
            f'📊 CL/NQ regime: NEGATIVE correlation={cl_nq_corr_20:.3f}\n'
            f'Oil and NQ moving OPPOSITE — supply shock / stagflation regime\n'
            f'CL={cl_now:.2f}  NQ={nq_last:.0f}  VX={vx_level:.2f}'
            f'  OVX={_sy(ovx_px)}  10y={_sy(tnx_yld)}%'
        )
        _cl_nq_neg_regime_alerted = True
elif not math.isnan(cl_nq_corr_20) and cl_nq_corr_20 > -0.2:
    _cl_nq_neg_regime_alerted = False
```

**Lookback note:** 20 polls at 60-second POLL_INTERVAL = 20 minutes. This is a short-term intraday indicator. For regime persistence, also maintain a 60-poll (60-minute) version. Both are achievable with the existing maxlen=180 deques.

---

### Idea 7: Credit-Oil-Equity Triple Confluence Alert
**Rank: 7 | Complexity: Low | Data needed: HYG/BIL (already live), CL, VX**

**Signal:** Alert when all three risk signals are simultaneously negative: CL falling AND HYG/BIL falling AND VX rising. This is the demand-destruction regime (oil and credit confirming equity risk-off).

**Rationale:** The 2022 and late 2024 episodes demonstrated that falling oil is NOT automatically equity-positive if it reflects demand destruction. The HYG/BIL ratio is already logged. Adding a 5-poll (5-minute) rate-of-change check on HYG/BIL + CL + VX provides a demand-destruction detection filter.

**Implementation:**
```python
# Track short-term direction using existing price history structures
# Add a new HYG price deque (currently ETF prices not stored in history)
_hyg_hist = collections.deque(maxlen=20)  # add alongside cl/nq deques

# In polling loop, append HYG price:
if not math.isnan(hyg_px):
    _hyg_hist.append((datetime.now(ET), hyg_px))

# Check demand destruction triple:
_demand_destruct_alerted = False
if (len(_hyg_hist) >= 5 and len(cl_price_hist) >= 5
        and not math.isnan(vx_level)):
    hyg_5min_chg = (_hyg_hist[-1][1] - _hyg_hist[-5][1]) / _hyg_hist[-5][1]
    cl_5min_chg  = (cl_price_hist[-1][1] - cl_price_hist[-5][1]) / cl_price_hist[-5][1]
    vx_5min_chg  = float('nan')  # VX history not yet stored; use level instead
    
    # Demand destruction: CL falling + HYG falling + VX rising
    if (cl_5min_chg < -0.005   # CL down >0.5% in 5 min
            and hyg_5min_chg < -0.001  # HYG down >0.1% in 5 min
            and vx_level > vx_thr):    # VX already above base threshold
        if not _demand_destruct_alerted:
            _alert(
                f'⚠️ Demand destruction signal:\n'
                f'CL {cl_5min_chg*100:+.2f}%  HYG {hyg_5min_chg*100:+.2f}%'
                f'  VX={vx_level:.2f}\n'
                f'Oil AND credit falling together = growth fear, NOT supply relief\n'
                f'Caution: NQ/ES longs may face continued pressure'
            )
            _demand_destruct_alerted = True
    elif cl_5min_chg > 0 or hyg_5min_chg > 0:
        _demand_destruct_alerted = False
```

---

### Idea 8: VX Daily Roll Signal for NQ Position Sizing
**Rank: 8 | Complexity: Low | Data needed: VIX spot, VX futures (both already live)**

**Signal:** Compute the daily roll (quantpedia VX strategy formula) as a live metric. When roll is strongly positive (backwardation deepening) and VX is above 21, treat as a signal to tighten NQ trade parameters.

**Formula:**
```python
# Daily roll formula from Quantpedia:
# daily_roll = (VX1_futures - VIX_spot) / business_days_to_settlement
# Positive = contango (VX > VIX = normal); Negative = backwardation (VIX > VX = stress)
# Note: vx_slope in combined_monitor.py is (VIX_spot - VX1_futures),
# which is the NEGATIVE of the daily roll (before dividing by days)

# Use existing vx_slope (= VIX_spot - VX1) for the direction signal
# vx_slope > 0 = backwardation (already generating vx_backw_alerted)
# vx_slope > 3.0 = extreme backwardation (new threshold to add)

if (not math.isnan(vx_slope) and vx_slope > 3.0
        and not math.isnan(vx_level) and vx_level > 21.0):
    _alert(
        f'🔥 Extreme VX backwardation: VIX={vix_spot_px:.2f}'
        f'  VX1={vx_level:.2f}  slope={vx_slope:+.2f}\n'
        f'Extreme equity fear premium — near-term vol spike likely\n'
        f'Historical: 21/22 such episodes preceded >5% SPX drawdown in 30d\n'
        f'CL={cl_now:.2f}  OVX={_sy(ovx_px)}  10y={_sy(tnx_yld)}%',
        critical=True
    )
```

Add a separate `vx_extreme_backw_alerted` state variable (triggers at vx_slope > 3.0 vs. the existing vx_backw_alerted which triggers at any positive slope).

---

### Idea 9: CL 12-Month Spread vs. Equity Regime Filter (CTA-Style)
**Rank: 9 | Complexity: Medium | Data needed: requires subscribing CL 12-months-out contract**

**Signal:** Subscribe CL contract 12 months forward and compute (CL_front − CL_12m) / CL_front as a percentage spread. Use this as a regime-conditioning variable for CL IV signals.

**Rationale:** Steep backwardation (>15% of spot for 12-month spread) is historically associated with supply shocks, which are negative for equities. This is the exact signal that CTA commodity specialists (Aspect, Man AHL) use in their term-structure curve models.

**Threshold:** (CL1 − CL13) / CL1 > 10% = supply-shock regime; > 15% = extreme supply stress

**When to suppress the CL IV short signal:** If you are considering shorting front MCL due to elevated IV, but the 12-month backwardation is >15% (physical supply extremely tight), the IV may be justified rather than elevated. The backwardation depth suppresses the "IV is high → mean-revert to short" logic.

**Implementation:** Add `--cl-12m-expiry` argument and subscribe a CL contract 11–13 months forward. Compute the spread each poll and log it alongside the existing CL spread data.

---

### Idea 10: XTSMOM Regime Gate for NQ Execution
**Rank: 10 | Complexity: Medium | Data needed: OVX history (from Idea 4 deque)**

**Signal:** Use the XTSMOM signal from Idea 4 as a gate for NQ/MNQ execution decisions.

**Rule:** Only execute NQ short (front sell) if XTSMOM signal is BEARISH (NQ 21d return < 0 AND OVX 21d change > 0). Do not execute in the BULLISH quadrant even if IV threshold is met.

**Rationale:** The XTSMOM paper shows the highest negative future returns occur precisely when NQ momentum is already negative AND OVX is rising. This is the quadrant where NQ shorts have the best risk/reward. Executing shorts when NQ is in the BULLISH quadrant (NQ up, OVX down) fights a confirmed uptrend with oil vol confirming calm.

**Implementation:** Add XTSMOM state check to `check_nq_trigger()` or as an additional condition in the NQ arm execution block. Can be implemented as a configurable flag `--use-xtsmom-gate` to allow manual override.

---

### Summary Table

| Idea | Signal Type | Complexity | Data Needed | Expected Value |
|------|-------------|------------|-------------|----------------|
| 1. OVX/VIX ratio | New alert | Low | Already subscribed | High — detects equity vol complacency |
| 2. Shock-type classifier | Alert enhancement | Low | Already subscribed | High — contextualizes every CL spike |
| 3. CL+VX dual backwardation | New alert | Low | Already subscribed | High — strongest cross-asset stress signal |
| 4. XTSMOM 21-day OVX | New signal | Medium | Add OVX deque | High — academically validated predictor |
| 5. Stagflation joint threshold | Alert refinement | Low | Already subscribed | Medium — reduces false rate alerts |
| 6. CL/NQ rolling correlation | New regime tracker | Medium | Already subscribed | Medium — direct regime detection |
| 7. Triple confluence (credit+oil+VX) | New alert | Low | Add HYG deque | Medium — demand destruction detection |
| 8. Extreme VX backwardation tier | Alert enhancement | Low | Already subscribed | Medium — tiers existing signal |
| 9. CL 12-month spread | New regime input | Medium | New CL sub needed | Medium — CTA-style curve signal |
| 10. XTSMOM NQ execution gate | Execution gate | Medium | Requires Idea 4 | Medium — improves NQ trade selection |

---

### Key Cross-Asset Rules Summary (for quick reference)

1. **OVX rising + VIX flat/low** → equity vol about to catch up; reduce NQ longs
2. **CL spike + ZN falling + HYG falling** → supply shock; oil-equity negative correlation regime; NQ bearish
3. **CL spike + ZN rising + HYG rising** → demand boom; oil-equity positive; NQ not threatened by oil
4. **CL falling + HYG falling** → demand destruction; falling oil is NOT equity relief; remain cautious
5. **VX backwardation + CL backwardation simultaneously** → dual stress signal; strongest risk-off
6. **OVX 21d change positive + NQ 21d return negative** → XTSMOM bearish quadrant; best risk/reward for NQ shorts
7. **CL calendar spread steep backwardation (>10% of spot)** → physical supply crisis; CTAs long CL; eventually bearish for equities via inflation
8. **HYG/LQD z-score < −2σ for 30+ days** → credit stress; combine with VIX rising + breadth deteriorating for high-conviction equity sell

---

## Sources Referenced

- Kilian (2009) oil shock decomposition, replicated 2024: https://arxiv.org/abs/2409.00769
- Risk transmission oil shocks / equity indices (2025): https://www.sciencedirect.com/science/article/pii/S1062940825000993
- Frontiers in Physics wavelet causality (2024): https://www.frontiersin.org/journals/physics/articles/10.3389/fphy.2024.1357366/full
- Brookings oil-stocks relationship: https://www.brookings.edu/articles/the-relationship-between-stocks-and-oil-prices/
- OVX-VIX-VKOSPI cointegration (PMC7199967): https://pmc.ncbi.nlm.nih.gov/articles/PMC7199967/
- XTSMOM paper (SSRN 3850465): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3850465
- OVX volatility guide (MenthorQ): https://menthorq.com/guide/understanding-ovx-oils-volatility-barometer/
- OVX/VIX divergence (Mott Capital): https://mottcapitalmanagement.com/equity-volatility-divergence-oil-fx-signals/
- VIX term structure / backwardation (Quantpedia): https://quantpedia.com/strategies/exploiting-term-structure-of-vix-futures
- VIX backwardation equity drawdown: https://volatilitybox.com/research/vix-contango-backwardation/
- CBOE VIX backwardation analysis: https://www.cboe.com/insights/posts/inside-volatility-trading-is-vix-backwardation-necessarily-a-sign-of-a-future-down-market/
- MSCI multi-asset geopolitical playbook: https://www.msci.com/research-and-insights/blog-post/a-multi-asset-playbook-for-geopolitical-shocks-and-oil-supply-disruption
- Tickeron oil-equity correlation: https://tickeron.com/trading-investing-101/why-oil-is-the-one-chart-you-cant-ignore-right-now/
- CME Group WTI backwardation implications (2026): https://www.cmegroup.com/insights/economic-research/2026/implications-of-wti-oil-futures-in-backwardation-amid-the-supply-crunch.html
- EBC oil backwardation warning: https://www.ebc.com/forex/oil-backwardation-not-100-crude-is-the-real-warning
- Oil-bond yield correlation (RIA): https://realinvestmentadvice.com/resources/blog/oil-and-bond-yields-are-tied-at-the-hip/
- ECB oil-dollar structural link (2024): https://www.ecb.europa.eu/press/economic-bulletin/focus/2024/html/ecb.ebbox202407_02~5ce155d504.en.html
- AGBI oil-dollar correlation breakdown: https://www.agbi.com/opinion/oil-and-gas/2023/12/matein-khalid-will-oils-inverse-correlation-to-the-dollar-return/
- Baker Institute Fed/oil guide: https://www.bakerinstitute.org/research/fed-watchers-guide-oil-markets-2024-and-2025
- HedgeNordic CTA 2024 performance: https://hedgenordic.com/2025/02/main-drivers-of-cta-performance-in-2024-2/
- Aspect Capital commodities role: https://hedgenordic.com/2024/10/the-versatile-role-of-commodities-in-cta-portfolios/
- Man AHL: https://www.man.com/ahl
- Man Institute systematic: https://www.man.com/maninstitute/systematic
- SystemTrader credit spreads: https://www.systemtrader.co/tools/credit-spreads
- ETF.com oil-credit-equity correlation: https://www.etf.com/sections/news/why-stocks-oil-are-correlated
- HyG ETF analysis: https://seekingalpha.com/article/4814115-hyg-everything-you-need-to-know-about-the-high-yield-bond-etf
- Quantpedia crude oil predicts equity returns: https://quantpedia.com/strategies/crude-oil-predicts-equity-returns
- Quantpedia term structure commodities: https://quantpedia.com/strategies/term-structure-effect-in-commodities
- QuantConnect crude oil equity prediction: https://www.quantconnect.com/learning/articles/investment-strategy-library/can-crude-oil-predict-equity-returns
- HMM regime detection (Medium): https://datadave1.medium.com/detecting-market-regimes-hidden-markov-model-2462e819c72e
- HMM regime detection (Cube Exchange): https://www.cube.exchange/what-is/market-regime-detection-with-hidden-markov-models
- Regime-adaptive trading Python (QuantInsti): https://blog.quantinsti.com/regime-adaptive-trading-python/
- Markov switching oil-equity COVID (PMC9944429): https://pmc.ncbi.nlm.nih.gov/articles/PMC9944429/
- Cazadores CTA post-ZIRP: https://www.cazadoresinvestments.com/2025/03/27/ctas-in-a-post-zirp-world-momentum-strategies-that-still-work/
- Macrosynergy cross-asset rise in correlation: https://research.macrosynergy.com/the-structural-rise-in-cross-asset-correlation/
- Quantpedia commodity portfolio 2026 supply shock: https://quantpedia.com/commodity-portfolio-strategy-for-a-potential-2026-inflationary-and-supply-shock-regime/
- CAIA risk parity 2024: https://caia.org/blog/2024/01/02/risk-parity-not-performing-blame-weather
- VX term structure tracker: https://www.systemtrader.co/tools/vix
- Futures correlation overview (Aeromir): https://futures.aeromir.com/post/110/understanding-futures-correlation-what-every-trader-should-know
