# pysystemtrade versus Globex overlap report

Comparison window: 2010-06-07 through 2024-03-29.
History schema: v5; Carver source commit: `b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8`; Globex fingerprint: `1695035392-1785345649000000000`.

Both providers use contract-consistent arithmetic returns: the current selected-contract price divided by its prior same-contract reference, including the matched forward quote on a Carver roll. Invalid nonpositive references are flagged rather than coerced. Valid returns are compounded into positive signal indices before trend forecasts are computed.

Carver source timestamps are preserved, while Sunday observations are assigned to the following Monday trading session during sidecar import.
Daily volatility is the annualized trailing 20-session standard deviation of normalized returns. The tables report every tested -1/0/+1 calendar-day correlation for both returns and volatility.

Every mapping remains `candidate`. Automated recommendations are triage only, not mapping approvals.

## Summary

| Market | Carver | Globex | Common | Return corr. -1 / 0 / +1 | Vol corr. -1 / 0 / +1 | Trend | Direction | Date alignment | Recommendation |
|---|---|---:|---:|---|---|---:|---:|---|---|
| brl | BRE | 6L | 2298 | -0.019 / 0.462 / 0.231 | 0.363 / 0.379 / 0.386 | 0.960 | 0.949 | none | investigate_known_issue |
| corn | CORN | ZC | 3431 | 0.040 / 0.852 / 0.119 | 0.935 / 0.946 / 0.936 | 0.961 | 0.934 | none | investigate_known_issue |
| crude_wti | CRUDE_W | CL | 3503 | -0.040 / 0.755 / 0.131 | 0.822 / 0.830 / 0.825 | 0.958 | 0.912 | none | investigate_known_issue |
| gold | GOLD | GC | 3484 | -0.057 / 0.864 / 0.124 | 0.943 / 0.963 / 0.962 | 0.977 | 0.944 | none | investigate |
| jpy | JPY | 6J | 3498 | -0.001 / 0.907 / 0.084 | 0.943 / 0.958 / 0.948 | 0.982 | 0.960 | none | proceed_to_manual_review |
| mxn | MXP | 6M | 3491 | -0.023 / 0.845 / 0.137 | 0.932 / 0.945 / 0.937 | 0.986 | 0.963 | none | investigate |
| nasdaq | NASDAQ | NQ | 3509 | -0.066 / 0.837 / 0.089 | 0.961 / 0.971 / 0.968 | 0.988 | 0.980 | none | investigate |
| silver | SILVER | SI | 3555 | 0.159 / 0.813 / 0.024 | 0.909 / 0.918 / 0.905 | 0.987 | 0.964 | previous_globex_session | investigate_known_issue |
| soybean_oil | SOYOIL | ZL | 3482 | 0.050 / 0.535 / 0.489 | 0.927 / 0.935 / 0.930 | 0.988 | 0.974 | none | investigate_known_issue |
| soybeans | SOYBEAN | ZS | 3465 | -0.013 / 0.868 / 0.036 | 0.919 / 0.931 / 0.918 | 0.934 | 0.898 | none | investigate_known_issue |
| sp500 | SP500 | ES | 3517 | -0.031 / 0.734 / 0.212 | 0.950 / 0.960 / 0.960 | 0.987 | 0.976 | none | investigate |
| us_10y | US10 | ZN | 3492 | -0.035 / 0.897 / 0.061 | 0.958 / 0.969 / 0.963 | 0.987 | 0.971 | none | investigate_known_issue |
| us_2y | US2 | ZT | 3505 | -0.081 / 0.897 / 0.013 | 0.982 / 0.987 / 0.986 | 0.989 | 0.973 | none | investigate |
| wheat | WHEAT | ZW | 3477 | -0.004 / 0.781 / 0.208 | 0.918 / 0.930 / 0.922 | 0.940 | 0.915 | none | investigate_known_issue |

## Per-market diagnostics

### brl: BRE / 6L

- Overlap: 2010-06-14 to 2024-03-28; 2,298 common dates, 1,228 Carver-only and 22 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.019 / 0.462 / 0.231; best 0.462 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.363 / 0.379 / 0.386; best 0.386 at 1 day(s).
- Annualized normalized-return volatility: Carver 0.166; Globex 0.213.
- Trend forecast correlation 0.960; direction agreement 0.949.
- Contract-month agreement 0.062; Carver/Globex rolls 165/105; 77 of 165 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `FGHJKMNQUVXZ`; repository Globex active months `unrestricted/unconfirmed`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Existing Globex 6L sticky-anchor issue remains unresolved.
- Mapping note: CME Brazilian real.

### corn: CORN / ZC

- Overlap: 2010-06-07 to 2024-03-28; 3,431 common dates, 28 Carver-only and 51 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: 0.040 / 0.852 / 0.119; best 0.852 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.935 / 0.946 / 0.936; best 0.946 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.232; Globex 0.251.
- Trend forecast correlation 0.961; direction agreement 0.934.
- Contract-month agreement 0.357; Carver/Globex rolls 14/54; 0 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `Z`; repository Globex active months `HKNZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver CORN holds only the annual December contract; Globex ZC selects the liquid front contract.
- Mapping note: CBOT corn underlying.

### crude_wti: CRUDE_W / CL

- Overlap: 2010-06-07 to 2024-03-28; 3,503 common dates, 5 Carver-only and 77 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.040 / 0.755 / 0.131; best 0.755 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.822 / 0.830 / 0.825; best 0.830 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.294; Globex 0.399.
- Trend forecast correlation 0.958; direction agreement 0.912.
- Contract-month agreement 0.000; Carver/Globex rolls 14/166; 10 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `Z`; repository Globex active months `FGHJKMNQUVXZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver CRUDE_W holds the annual December winter contract while Globex CL selects the liquid monthly front contract.
- Mapping note: WTI crude underlying.

### gold: GOLD / GC

- Overlap: 2010-06-07 to 2024-03-28; 3,484 common dates, 7 Carver-only and 95 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.057 / 0.864 / 0.124; best 0.864 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.943 / 0.963 / 0.962; best 0.963 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.159; Globex 0.155.
- Trend forecast correlation 0.977; direction agreement 0.944.
- Contract-month agreement 0.780; Carver/Globex rolls 83/69; 52 of 83 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `GJMQVZ`; repository Globex active months `GJMQZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: COMEX gold.

### jpy: JPY / 6J

- Overlap: 2010-06-07 to 2024-03-28; 3,498 common dates, 6 Carver-only and 79 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.001 / 0.907 / 0.084; best 0.907 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.943 / 0.958 / 0.948; best 0.958 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.089; Globex 0.090.
- Trend forecast correlation 0.982; direction agreement 0.960.
- Contract-month agreement 0.969; Carver/Globex rolls 56/56; 49 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `proceed_to_manual_review`.
- Mapping note: CME Japanese yen.

### mxn: MXP / 6M

- Overlap: 2010-06-07 to 2024-03-28; 3,491 common dates, 8 Carver-only and 84 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.023 / 0.845 / 0.137; best 0.845 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.932 / 0.945 / 0.937; best 0.945 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.124; Globex 0.121.
- Trend forecast correlation 0.986; direction agreement 0.963.
- Contract-month agreement 0.970; Carver/Globex rolls 56/56; 52 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: CME Mexican peso.

### nasdaq: NASDAQ / NQ

- Overlap: 2010-06-07 to 2024-03-28; 3,509 common dates, 6 Carver-only and 68 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.066 / 0.837 / 0.089; best 0.837 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.961 / 0.971 / 0.968; best 0.971 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.197; Globex 0.202.
- Trend forecast correlation 0.988; direction agreement 0.980.
- Contract-month agreement 0.965; Carver/Globex rolls 56/56; 49 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: Nasdaq-100 full-size signal history.

### silver: SILVER / SI

- Overlap: 2010-06-07 to 2024-03-28; 3,555 common dates, 0 Carver-only and 24 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: 0.159 / 0.813 / 0.024; best 0.813 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.909 / 0.918 / 0.905; best 0.918 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.299; Globex 0.290.
- Trend forecast correlation 0.987; direction agreement 0.964.
- Contract-month agreement 0.807; Carver/Globex rolls 69/69; 23 of 69 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HKNUZ`; repository Globex active months `HKNUZ`.
- Date alignment: `previous_globex_session` through 2021-06-30 inclusive; 7 duplicate mapped rows collapsed.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Point-scale reconciliation remains required.
- Alignment audit: before the configured correction, same-date return correlation was 0.361 and the best naive calendar shift produced 0.646; after actual-session alignment, the same-date result is 0.813.
- Mapping note: COMEX silver; legacy observations are aligned to the preceding actual SI session through June 2021.

### soybean_oil: SOYOIL / ZL

- Overlap: 2010-06-07 to 2024-03-28; 3,482 common dates, 3 Carver-only and 0 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: 0.050 / 0.535 / 0.489; best 0.535 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.927 / 0.935 / 0.930; best 0.935 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.228; Globex 0.231.
- Trend forecast correlation 0.988; direction agreement 0.974.
- Contract-month agreement 0.342; Carver/Globex rolls 111/69; 3 of 111 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `FHKNQUVZ`; repository Globex active months `FHKNZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver SOYOIL and Globex ZL use materially different roll cycles.
- Mapping note: CBOT soybean-oil underlying.

### soybeans: SOYBEAN / ZS

- Overlap: 2010-06-07 to 2024-03-28; 3,465 common dates, 13 Carver-only and 17 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.013 / 0.868 / 0.036; best 0.868 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.919 / 0.931 / 0.918; best 0.931 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.180; Globex 0.203.
- Trend forecast correlation 0.934; direction agreement 0.898.
- Contract-month agreement 0.222; Carver/Globex rolls 14/69; 0 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `X`; repository Globex active months `FHKNX`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver SOYBEAN holds only the annual November contract; Globex ZS selects the liquid front contract.
- Mapping note: CBOT soybean underlying.

### sp500: SP500 / ES

- Overlap: 2010-06-07 to 2024-03-28; 3,517 common dates, 6 Carver-only and 60 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.031 / 0.734 / 0.212; best 0.734 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.950 / 0.960 / 0.960; best 0.960 at 1 day(s).
- Annualized normalized-return volatility: Carver 0.164; Globex 0.169.
- Trend forecast correlation 0.987; direction agreement 0.976.
- Contract-month agreement 0.968; Carver/Globex rolls 56/56; 52 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: S&P 500 full-size signal history.

### us_10y: US10 / ZN

- Overlap: 2010-06-07 to 2024-03-28; 3,492 common dates, 2 Carver-only and 89 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.035 / 0.897 / 0.061; best 0.897 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.958 / 0.969 / 0.963; best 0.969 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.054; Globex 0.053.
- Trend forecast correlation 0.987; direction agreement 0.971.
- Contract-month agreement 0.965; Carver/Globex rolls 55/55; 48 of 55 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Treasury fractional-price scaling requires explicit review.
- Mapping note: 10-year Treasury note.

### us_2y: US2 / ZT

- Overlap: 2010-06-07 to 2024-03-28; 3,505 common dates, 4 Carver-only and 76 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.081 / 0.897 / 0.013; best 0.897 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.982 / 0.987 / 0.986; best 0.987 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.013; Globex 0.013.
- Trend forecast correlation 0.989; direction agreement 0.973.
- Contract-month agreement 0.977; Carver/Globex rolls 55/55; 48 of 55 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Date alignment: `none`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: 2-year Treasury note.

### wheat: WHEAT / ZW

- Overlap: 2010-06-07 to 2024-03-28; 3,477 common dates, 9 Carver-only and 5 Globex-only dates.
- Non-roll return correlation at -1/0/+1 calendar-day Globex shifts: -0.004 / 0.781 / 0.208; best 0.781 at 0 day(s).
- Trailing 20-session annualized-volatility correlation at -1/0/+1 shifts: 0.918 / 0.930 / 0.922; best 0.930 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.255; Globex 0.304.
- Trend forecast correlation 0.940; direction agreement 0.915.
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
