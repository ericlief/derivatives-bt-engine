# pysystemtrade versus Globex overlap report

Comparison window: 2010-06-07 through 2024-03-29.
History schema: v3; Carver source commit: `b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8`; Globex fingerprint: `1695035392-1785345649000000000`.

Carver returns are adjusted-price point differences divided by the same-day positive current-contract price. Globex returns use each selected contract's own prior close, including on roll days. Both are compounded into positive signal indices before trend forecasts are computed.

Carver source timestamps are preserved, while Sunday observations are assigned to the following Monday trading session during sidecar import.
Daily volatility is the annualized trailing 20-session standard deviation of normalized returns. The tables report every tested -1/0/+1 calendar-day correlation for both returns and volatility.

Every mapping remains `candidate`. Automated recommendations are triage only, not mapping approvals.

## Summary

| Market | Carver | Globex | Common | Return corr. -1 / 0 / +1 | Vol corr. -1 / 0 / +1 | Trend | Direction | Date alignment | Recommendation |
|---|---|---:|---:|---|---|---:|---:|---|---|
| brl | BRE | 6L | 2298 | -0.014 / 0.399 / 0.187 | 0.262 / 0.275 / 0.279 | 0.962 | 0.926 | none | investigate_known_issue |
| corn | CORN | ZC | 3431 | 0.040 / 0.851 / 0.119 | 0.934 / 0.945 / 0.935 | 0.959 | 0.929 | none | investigate_known_issue |
| crude_wti | CRUDE_W | CL | 3503 | -0.033 / 0.748 / 0.124 | 0.791 / 0.798 / 0.792 | 0.948 | 0.899 | none | investigate_known_issue |
| gold | GOLD | GC | 3484 | -0.052 / 0.865 / 0.122 | 0.943 / 0.963 / 0.962 | 0.977 | 0.946 | none | investigate |
| jpy | JPY | 6J | 3498 | -0.002 / 0.906 / 0.086 | 0.942 / 0.957 / 0.947 | 0.982 | 0.962 | none | proceed_to_manual_review |
| mxn | MXP | 6M | 3491 | -0.022 / 0.845 / 0.139 | 0.932 / 0.945 / 0.936 | 0.984 | 0.962 | none | investigate |
| nasdaq | NASDAQ | NQ | 3509 | -0.064 / 0.838 / 0.086 | 0.961 / 0.971 / 0.968 | 0.987 | 0.980 | none | investigate |
| silver | SILVER | SI | 3555 | 0.160 / 0.816 / 0.028 | 0.906 / 0.915 / 0.900 | 0.987 | 0.971 | previous_globex_session | investigate_known_issue |
| soybean_oil | SOYOIL | ZL | 3482 | 0.047 / 0.542 / 0.487 | 0.929 / 0.937 / 0.932 | 0.988 | 0.978 | none | investigate_known_issue |
| soybeans | SOYBEAN | ZS | 3465 | -0.012 / 0.869 / 0.035 | 0.920 / 0.933 / 0.920 | 0.933 | 0.884 | none | investigate_known_issue |
| sp500 | SP500 | ES | 3517 | -0.032 / 0.737 / 0.211 | 0.950 / 0.960 / 0.960 | 0.986 | 0.975 | none | investigate |
| us_10y | US10 | ZN | 3492 | -0.034 / 0.899 / 0.062 | 0.958 / 0.969 / 0.963 | 0.988 | 0.966 | none | investigate_known_issue |
| us_2y | US2 | ZT | 3505 | -0.080 / 0.896 / 0.013 | 0.982 / 0.987 / 0.986 | 0.988 | 0.974 | none | investigate |
| wheat | WHEAT | ZW | 3477 | -0.007 / 0.779 / 0.207 | 0.916 / 0.929 / 0.921 | 0.932 | 0.932 | none | investigate_known_issue |

## Per-market diagnostics

### brl: BRE / 6L

- Overlap: 2010-06-14 to 2024-03-28; 2,298 common dates, 1,228 Carver-only and 22 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.014 / 0.399 / 0.187; best 0.399 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.262 / 0.275 / 0.279; best 0.279 at 1 day(s).
- Annualized normalized-return volatility: Carver 0.166; Globex 0.250.
- Trend forecast correlation 0.962; direction agreement 0.926.
- Contract-month agreement 0.062; Carver/Globex rolls 165/105; 77 of 165 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `FGHJKMNQUVXZ`; repository Globex active months `unrestricted/unconfirmed`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Existing Globex 6L sticky-anchor issue remains unresolved.
- Mapping note: CME Brazilian real.

### corn: CORN / ZC

- Overlap: 2010-06-07 to 2024-03-28; 3,431 common dates, 28 Carver-only and 51 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: 0.040 / 0.851 / 0.119; best 0.851 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.934 / 0.945 / 0.935; best 0.945 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.231; Globex 0.251.
- Trend forecast correlation 0.959; direction agreement 0.929.
- Contract-month agreement 0.357; Carver/Globex rolls 14/54; 0 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `Z`; repository Globex active months `HKNZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver CORN holds only the annual December contract; Globex ZC selects the liquid front contract.
- Mapping note: CBOT corn underlying.

### crude_wti: CRUDE_W / CL

- Overlap: 2010-06-07 to 2024-03-28; 3,503 common dates, 5 Carver-only and 77 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.033 / 0.748 / 0.124; best 0.748 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.791 / 0.798 / 0.792; best 0.798 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.301; Globex 0.421.
- Trend forecast correlation 0.948; direction agreement 0.899.
- Contract-month agreement 0.000; Carver/Globex rolls 14/166; 10 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `Z`; repository Globex active months `FGHJKMNQUVXZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver CRUDE_W holds the annual December winter contract while Globex CL selects the liquid monthly front contract.
- Mapping note: WTI crude underlying.

### gold: GOLD / GC

- Overlap: 2010-06-07 to 2024-03-28; 3,484 common dates, 7 Carver-only and 95 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.052 / 0.865 / 0.122; best 0.865 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.943 / 0.963 / 0.962; best 0.963 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.159; Globex 0.155.
- Trend forecast correlation 0.977; direction agreement 0.946.
- Contract-month agreement 0.780; Carver/Globex rolls 83/69; 52 of 83 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `GJMQVZ`; repository Globex active months `GJMQZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: COMEX gold.

### jpy: JPY / 6J

- Overlap: 2010-06-07 to 2024-03-28; 3,498 common dates, 6 Carver-only and 79 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.002 / 0.906 / 0.086; best 0.906 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.942 / 0.957 / 0.947; best 0.957 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.089; Globex 0.090.
- Trend forecast correlation 0.982; direction agreement 0.962.
- Contract-month agreement 0.969; Carver/Globex rolls 56/56; 49 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `proceed_to_manual_review`.
- Mapping note: CME Japanese yen.

### mxn: MXP / 6M

- Overlap: 2010-06-07 to 2024-03-28; 3,491 common dates, 8 Carver-only and 84 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.022 / 0.845 / 0.139; best 0.845 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.932 / 0.945 / 0.936; best 0.945 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.124; Globex 0.121.
- Trend forecast correlation 0.984; direction agreement 0.962.
- Contract-month agreement 0.970; Carver/Globex rolls 56/56; 52 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: CME Mexican peso.

### nasdaq: NASDAQ / NQ

- Overlap: 2010-06-07 to 2024-03-28; 3,509 common dates, 6 Carver-only and 68 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.064 / 0.838 / 0.086; best 0.838 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.961 / 0.971 / 0.968; best 0.971 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.197; Globex 0.202.
- Trend forecast correlation 0.987; direction agreement 0.980.
- Contract-month agreement 0.965; Carver/Globex rolls 56/56; 49 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: Nasdaq-100 full-size signal history.

### silver: SILVER / SI

- Overlap: 2010-06-07 to 2024-03-28; 3,555 common dates, 0 Carver-only and 24 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: 0.160 / 0.816 / 0.028; best 0.816 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.906 / 0.915 / 0.900; best 0.915 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.305; Globex 0.293.
- Trend forecast correlation 0.987; direction agreement 0.971.
- Contract-month agreement 0.807; Carver/Globex rolls 69/69; 23 of 69 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HKNUZ`; repository Globex active months `HKNUZ`.
- Date alignment: `previous_globex_session` through 2021-06-30 inclusive; 7 duplicate mapped rows collapsed.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Point-scale reconciliation remains required.
- Alignment audit: before the configured correction, same-date return correlation was 0.358 and the best naive calendar shift produced 0.654; after actual-session alignment, the same-date result is 0.816.
- Mapping note: COMEX silver; legacy observations are aligned to the preceding actual SI session through June 2021.

### soybean_oil: SOYOIL / ZL

- Overlap: 2010-06-07 to 2024-03-28; 3,482 common dates, 3 Carver-only and 0 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: 0.047 / 0.542 / 0.487; best 0.542 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.929 / 0.937 / 0.932; best 0.937 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.227; Globex 0.231.
- Trend forecast correlation 0.988; direction agreement 0.978.
- Contract-month agreement 0.342; Carver/Globex rolls 111/69; 3 of 111 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `FHKNQUVZ`; repository Globex active months `FHKNZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver SOYOIL and Globex ZL use materially different roll cycles.
- Mapping note: CBOT soybean-oil underlying.

### soybeans: SOYBEAN / ZS

- Overlap: 2010-06-07 to 2024-03-28; 3,465 common dates, 13 Carver-only and 17 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.012 / 0.869 / 0.035; best 0.869 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.920 / 0.933 / 0.920; best 0.933 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.180; Globex 0.203.
- Trend forecast correlation 0.933; direction agreement 0.884.
- Contract-month agreement 0.222; Carver/Globex rolls 14/69; 0 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `X`; repository Globex active months `FHKNX`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver SOYBEAN holds only the annual November contract; Globex ZS selects the liquid front contract.
- Mapping note: CBOT soybean underlying.

### sp500: SP500 / ES

- Overlap: 2010-06-07 to 2024-03-28; 3,517 common dates, 6 Carver-only and 60 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.032 / 0.737 / 0.211; best 0.737 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.950 / 0.960 / 0.960; best 0.960 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.165; Globex 0.170.
- Trend forecast correlation 0.986; direction agreement 0.975.
- Contract-month agreement 0.968; Carver/Globex rolls 56/56; 52 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: S&P 500 full-size signal history.

### us_10y: US10 / ZN

- Overlap: 2010-06-07 to 2024-03-28; 3,492 common dates, 2 Carver-only and 89 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.034 / 0.899 / 0.062; best 0.899 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.958 / 0.969 / 0.963; best 0.969 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.054; Globex 0.053.
- Trend forecast correlation 0.988; direction agreement 0.966.
- Contract-month agreement 0.965; Carver/Globex rolls 55/55; 48 of 55 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Treasury fractional-price scaling requires explicit review.
- Mapping note: 10-year Treasury note.

### us_2y: US2 / ZT

- Overlap: 2010-06-07 to 2024-03-28; 3,505 common dates, 4 Carver-only and 76 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.080 / 0.896 / 0.013; best 0.896 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.982 / 0.987 / 0.986; best 0.987 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.013; Globex 0.013.
- Trend forecast correlation 0.988; direction agreement 0.974.
- Contract-month agreement 0.977; Carver/Globex rolls 55/55; 48 of 55 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: 2-year Treasury note.

### wheat: WHEAT / ZW

- Overlap: 2010-06-07 to 2024-03-28; 3,477 common dates, 9 Carver-only and 5 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.007 / 0.779 / 0.207; best 0.779 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.916 / 0.929 / 0.921; best 0.929 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.254; Globex 0.304.
- Trend forecast correlation 0.932; direction agreement 0.932.
- Contract-month agreement 0.078; Carver/Globex rolls 14/69; 0 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `Z`; repository Globex active months `HKNUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver WHEAT holds only the annual December contract; Globex ZW selects the liquid front contract.
- Mapping note: CBOT wheat underlying.

## Interpretation safeguards

- Silver alone has an explicit date adjustment: legacy observations through 2021-06-30 map to the preceding actual SI session. The other 13 mappings use their normalized dates without a cross-provider shift.
- Contract-month disagreement can reflect intentionally different roll rules, not a bad price series.
- `proceed_to_manual_review` requires at least 1,000 common dates, return correlation >= 0.90, trend correlation >= 0.80, direction agreement >= 0.80, and no registered known issue.
- The report does not validate multipliers, currency conversion, or executable slippage and therefore does not make any series live-trading eligible.
- Detailed common-date rows can be regenerated with `--write-details`; source databases remain read-only.
- `missing_dates.csv` identifies each non-common date and which source lacks it.
