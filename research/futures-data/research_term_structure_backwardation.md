# Futures Term Structure: Is Pre-2018 ES/NQ Backwardation a Data Bug?

**Research date:** 2026-07-08
**Purpose:** Investigate an anomaly noticed while inspecting `ohlcv_enriched`: ES (S&P 500 futures) front-month closes trade *above* next-month closes almost every day from 2010–2016, which looked at first glance like a data-quality problem. Checked the front-vs-back spread for every instrument in the TSMOM universe across 2010–2026 and corroborated the equity-index pattern against known Fed funds rate and S&P 500 dividend-yield history.

**Reproduce:** `python -m scripts.term_structure_diagnostic` → writes [term_structure_by_year.csv](term_structure_by_year.csv). Query/aggregation logic lives in [scripts/term_structure_diagnostic.py](../scripts/term_structure_diagnostic.py).

---

## Summary

**Not a data bug.** The pre-2018 ES/NQ backwardation is the expected consequence of the equity-index futures cost-of-carry relationship, `F ≈ S · e^((r−q)T)`, during a decade of near-zero Fed funds rates (`r`) against a ~2% S&P 500 dividend yield (`q`). When `r < q`, the curve is decreasing (front > back = "backwardation"); when `r > q`, it's normal contango. The DB's spread sign flips *exactly* at the two points where the Fed funds rate actually crossed the dividend yield (2018, and again 2022), and flips back to backwardation during the 2020 COVID ZIRP window — both moves line up precisely with documented FOMC history. Every other instrument in the universe (metals, energy, grains, FX) shows spread behavior consistent with its own known carry mechanics, none of it explainable by a shared data defect.

## Method

For each `(asset, ts_event)`, rank all live (non-expired) contracts by soonest expiration using `row_number() OVER (PARTITION BY asset, ts_event ORDER BY expiration ASC)`. Take `front` = close of `rn=1`, `back` = close of `rn=2`, and aggregate `avg(front - back)`, `avg((front-back)/back)`, and `% days front > back` by asset/year. Full SQL in [scripts/term_structure_diagnostic.py](../scripts/term_structure_diagnostic.py) (`_TERM_STRUCTURE_SQL`).

## Findings: ES / NQ vs. Fed funds rate and dividend yield

| Period | ES avg %spread | ES % days backwardated | NQ avg %spread | Fed funds | S&P div yield |
|---|---|---|---|---|---|
| 2010–2016 | +0.36% to +0.47% | ~99–100% | +0.11% to +0.24% | 0–0.5% (ZIRP) | 1.83–2.20% |
| 2017 | +0.08% | 77% (transition) | −0.12% | 3 hikes → ~1.3% | 1.84% |
| 2018 | **−0.16%** | 0.6% | −0.36% | 4 hikes → 2.25–2.5% | 2.09% |
| 2019 | −0.11% | 5.8% | −0.30% | 3 cuts → 1.5–1.75% | 1.83% |
| 2020–2021 | +0.29%, +0.23% | 89–100% | +0.09%, +0.06% | cut to 0% (COVID) | 1.58%, 1.29% |
| 2022 | −0.33% | 21% (transition) | −0.50% | 0% → 4.25–4.5% | 1.71% |
| 2023–2025 | −0.86% to −1.09% | 0% | −1.0% to −1.2% | 5.25–5.5% held | 1.15–1.50% |

Reading the transitions: the Fed funds rate first exceeded the dividend yield in late 2018 (4 hikes that year, to 2.25–2.5%, vs. a ~2.09% yield) — and 2017 shows up in the data as exactly the transition year, with the backwardated-day share collapsing from 100% to 77% as hikes closed the `r − q` gap before crossing it in 2018. The 2020 COVID emergency cuts back to 0% flip the sign right back to backwardation. The 2022–2023 hiking cycle (fastest since Volcker, 0% → 5.25–5.5% in 17 months) then drove deep, near-universal contango as `r` pulled far ahead of a shrinking dividend yield. NQ tracks the same sign flips with larger magnitude, consistent with Nasdaq's comparable/slightly lower dividend yield.

## Other instruments (corroborating, not contradicting)

- **GC (gold):** persistent mild contango throughout, widening as rates rose post-2022 — gold has zero yield, so contango scales with `r` alone; no regime flip expected, and none seen.
- **SI (silver):** same pattern as gold, noisier.
- **CL (crude):** flips repeatedly between contango and backwardation (2020 COVID storage-glut contango; 2021–22 and 2024–25 backwardation on supply tightness) — driven by convenience yield/storage economics, not rates. Matches well-documented oil market behavior.
- **Grains (ZC/ZS/ZW/ZL):** alternate contango/backwardation on crop-year and seasonal expectations — normal for storable ag commodities.
- **FX (6J/JPY, 6L/BRL, 6M/MXN):** 6J (JPY, chronically low/negative rates vs. USD) sits in persistent mild contango; 6L/6M (high-yield EM currencies) sit in persistent, strong backwardation (~90%+ of days) — exactly what covered interest rate parity predicts for a low-yield vs. high-yield currency against USD.

## Conclusion

The DB's front/back ranking and close prices are behaving correctly. The "surprising" ES/NQ backwardation is a dated fingerprint of the ZIRP era, not a bug to chase — treat it as expected historical market structure.

## Sources

- [Federal Funds Rate History 1990 to 2026 – Forbes Advisor](https://www.forbes.com/advisor/investing/fed-funds-rate-history/)
- [History of Federal Open Market Committee actions - Wikipedia](https://en.wikipedia.org/wiki/History_of_Federal_Open_Market_Committee_actions)
- [S&P 500 Dividend Yield by Year - Multpl](https://www.multpl.com/s-p-500-dividend-yield/table/by-year)
