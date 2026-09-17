# pysystemtrade versus Globex overlap report

Comparison window: 2010-06-07 through 2024-03-29.
History schema: v1; Carver source commit: `b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8`; Globex fingerprint: `1695035392-1785345649000000000`.

Carver returns are adjusted-price point differences divided by the same-day positive current-contract price. Globex returns use each selected contract's own prior close, including on roll days. Both are compounded into positive signal indices before trend forecasts are computed.

Every mapping remains `candidate`. Automated recommendations are triage only, not mapping approvals.

## Summary

| Market | Carver | Globex | Common days | Return corr. | Best shift | Trend corr. | Direction | Contract month | Recommendation |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| brl | BRE | 6L | 2298 | 0.407 | 0 | 0.941 | 0.909 | 0.062 | investigate_known_issue |
| corn | CORN | ZC | 3431 | 0.859 | 0 | 0.953 | 0.923 | 0.357 | investigate_known_issue |
| crude_wti | CRUDE_W | CL | 3503 | 0.728 | 0 | 0.932 | 0.893 | 0.000 | investigate_known_issue |
| gold | GOLD | GC | 3484 | 0.864 | 0 | 0.969 | 0.943 | 0.780 | investigate |
| jpy | JPY | 6J | 3498 | 0.908 | 0 | 0.973 | 0.957 | 0.969 | proceed_to_manual_review |
| mxn | MXP | 6M | 3491 | 0.846 | 0 | 0.983 | 0.957 | 0.970 | investigate |
| nasdaq | NASDAQ | NQ | 3509 | 0.851 | 0 | 0.975 | 0.961 | 0.965 | investigate |
| silver | SILVER | SI | 3556 | 0.654 | 1 | 0.909 | 0.910 | 0.811 | investigate_known_issue |
| soybean_oil | SOYOIL | ZL | 3482 | 0.546 | 0 | 0.981 | 0.974 | 0.342 | investigate_known_issue |
| soybeans | SOYBEAN | ZS | 3465 | 0.869 | 0 | 0.933 | 0.884 | 0.222 | investigate_known_issue |
| sp500 | SP500 | ES | 3517 | 0.741 | 0 | 0.966 | 0.961 | 0.968 | investigate |
| us_10y | US10 | ZN | 3492 | 0.903 | 0 | 0.983 | 0.966 | 0.965 | investigate_known_issue |
| us_2y | US2 | ZT | 3505 | 0.895 | 0 | 0.982 | 0.969 | 0.977 | investigate |
| wheat | WHEAT | ZW | 3477 | 0.783 | 0 | 0.923 | 0.924 | 0.078 | investigate_known_issue |

## Per-market diagnostics

### brl: BRE / 6L

- Overlap: 2010-06-14 to 2024-03-28; 2,298 common dates, 1,359 Carver-only and 22 Globex-only dates.
- Non-roll return correlation: same-day 0.407; best of -1/0/+1 calendar-day Globex shifts 0.407 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.161; Globex 0.250.
- Trend forecast correlation 0.941; direction agreement 0.909.
- Contract-month agreement 0.062; Carver/Globex rolls 165/105; 77 of 165 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `FGHJKMNQUVXZ`; repository Globex active months `unrestricted/unconfirmed`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Existing Globex 6L sticky-anchor issue remains unresolved.
- Mapping note: CME Brazilian real.

### corn: CORN / ZC

- Overlap: 2010-06-07 to 2024-03-28; 3,431 common dates, 115 Carver-only and 51 Globex-only dates.
- Non-roll return correlation: same-day 0.859; best of -1/0/+1 calendar-day Globex shifts 0.859 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.229; Globex 0.251.
- Trend forecast correlation 0.953; direction agreement 0.923.
- Contract-month agreement 0.357; Carver/Globex rolls 14/54; 0 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `Z`; repository Globex active months `HKNZ`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver CORN holds only the annual December contract; Globex ZC selects the liquid front contract.
- Mapping note: CBOT corn underlying.

### crude_wti: CRUDE_W / CL

- Overlap: 2010-06-07 to 2024-03-28; 3,503 common dates, 188 Carver-only and 77 Globex-only dates.
- Non-roll return correlation: same-day 0.728; best of -1/0/+1 calendar-day Globex shifts 0.728 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.283; Globex 0.421.
- Trend forecast correlation 0.932; direction agreement 0.893.
- Contract-month agreement 0.000; Carver/Globex rolls 14/166; 10 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `Z`; repository Globex active months `FGHJKMNQUVXZ`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver CRUDE_W holds the annual December winter contract while Globex CL selects the liquid monthly front contract.
- Mapping note: WTI crude underlying.

### gold: GOLD / GC

- Overlap: 2010-06-07 to 2024-03-28; 3,484 common dates, 93 Carver-only and 95 Globex-only dates.
- Non-roll return correlation: same-day 0.864; best of -1/0/+1 calendar-day Globex shifts 0.864 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.158; Globex 0.155.
- Trend forecast correlation 0.969; direction agreement 0.943.
- Contract-month agreement 0.780; Carver/Globex rolls 83/69; 52 of 83 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `GJMQVZ`; repository Globex active months `GJMQZ`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: COMEX gold.

### jpy: JPY / 6J

- Overlap: 2010-06-07 to 2024-03-28; 3,498 common dates, 82 Carver-only and 79 Globex-only dates.
- Non-roll return correlation: same-day 0.908; best of -1/0/+1 calendar-day Globex shifts 0.908 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.089; Globex 0.090.
- Trend forecast correlation 0.973; direction agreement 0.957.
- Contract-month agreement 0.969; Carver/Globex rolls 56/56; 49 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Status: candidate; automated recommendation: `proceed_to_manual_review`.
- Mapping note: CME Japanese yen.

### mxn: MXP / 6M

- Overlap: 2010-06-07 to 2024-03-28; 3,491 common dates, 84 Carver-only and 84 Globex-only dates.
- Non-roll return correlation: same-day 0.846; best of -1/0/+1 calendar-day Globex shifts 0.846 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.121; Globex 0.121.
- Trend forecast correlation 0.983; direction agreement 0.957.
- Contract-month agreement 0.970; Carver/Globex rolls 56/56; 52 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: CME Mexican peso.

### nasdaq: NASDAQ / NQ

- Overlap: 2010-06-07 to 2024-03-28; 3,509 common dates, 108 Carver-only and 68 Globex-only dates.
- Non-roll return correlation: same-day 0.851; best of -1/0/+1 calendar-day Globex shifts 0.851 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.192; Globex 0.202.
- Trend forecast correlation 0.975; direction agreement 0.961.
- Contract-month agreement 0.965; Carver/Globex rolls 56/56; 49 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: Nasdaq-100 full-size signal history.

### silver: SILVER / SI

- Overlap: 2010-06-07 to 2024-03-28; 3,556 common dates, 574 Carver-only and 23 Globex-only dates.
- Non-roll return correlation: same-day 0.358; best of -1/0/+1 calendar-day Globex shifts 0.654 at 1 day(s).
- Annualized normalized-return volatility: Carver 0.270; Globex 0.292.
- Trend forecast correlation 0.909; direction agreement 0.910.
- Contract-month agreement 0.811; Carver/Globex rolls 69/69; 21 of 69 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HKNUZ`; repository Globex active months `HKNUZ`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Settlement/date and point-scale reconciliation required.
- Mapping note: COMEX silver.

### soybean_oil: SOYOIL / ZL

- Overlap: 2010-06-07 to 2024-03-28; 3,482 common dates, 102 Carver-only and 0 Globex-only dates.
- Non-roll return correlation: same-day 0.546; best of -1/0/+1 calendar-day Globex shifts 0.546 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.225; Globex 0.231.
- Trend forecast correlation 0.981; direction agreement 0.974.
- Contract-month agreement 0.342; Carver/Globex rolls 111/69; 3 of 111 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `FHKNQUVZ`; repository Globex active months `FHKNZ`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver SOYOIL and Globex ZL use materially different roll cycles.
- Mapping note: CBOT soybean-oil underlying.

### soybeans: SOYBEAN / ZS

- Overlap: 2010-06-07 to 2024-03-28; 3,465 common dates, 13 Carver-only and 17 Globex-only dates.
- Non-roll return correlation: same-day 0.869; best of -1/0/+1 calendar-day Globex shifts 0.869 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.180; Globex 0.203.
- Trend forecast correlation 0.933; direction agreement 0.884.
- Contract-month agreement 0.222; Carver/Globex rolls 14/69; 0 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `X`; repository Globex active months `FHKNX`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver SOYBEAN holds only the annual November contract; Globex ZS selects the liquid front contract.
- Mapping note: CBOT soybean underlying.

### sp500: SP500 / ES

- Overlap: 2010-06-07 to 2024-03-28; 3,517 common dates, 160 Carver-only and 60 Globex-only dates.
- Non-roll return correlation: same-day 0.741; best of -1/0/+1 calendar-day Globex shifts 0.741 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.160; Globex 0.170.
- Trend forecast correlation 0.966; direction agreement 0.961.
- Contract-month agreement 0.968; Carver/Globex rolls 56/56; 52 of 56 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: S&P 500 full-size signal history.

### us_10y: US10 / ZN

- Overlap: 2010-06-07 to 2024-03-28; 3,492 common dates, 93 Carver-only and 89 Globex-only dates.
- Non-roll return correlation: same-day 0.903; best of -1/0/+1 calendar-day Globex shifts 0.903 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.053; Globex 0.053.
- Trend forecast correlation 0.983; direction agreement 0.966.
- Contract-month agreement 0.965; Carver/Globex rolls 55/55; 48 of 55 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Treasury fractional-price scaling requires explicit review.
- Mapping note: 10-year Treasury note.

### us_2y: US2 / ZT

- Overlap: 2010-06-07 to 2024-03-28; 3,505 common dates, 134 Carver-only and 76 Globex-only dates.
- Non-roll return correlation: same-day 0.895; best of -1/0/+1 calendar-day Globex shifts 0.895 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.013; Globex 0.013.
- Trend forecast correlation 0.982; direction agreement 0.969.
- Contract-month agreement 0.977; Carver/Globex rolls 55/55; 48 of 55 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `HMUZ`; repository Globex active months `HMUZ`.
- Status: candidate; automated recommendation: `investigate`.
- Mapping note: 2-year Treasury note.

### wheat: WHEAT / ZW

- Overlap: 2010-06-07 to 2024-03-28; 3,477 common dates, 89 Carver-only and 5 Globex-only dates.
- Non-roll return correlation: same-day 0.783; best of -1/0/+1 calendar-day Globex shifts 0.783 at 0 day(s).
- Annualized normalized-return volatility: Carver 0.253; Globex 0.304.
- Trend forecast correlation 0.923; direction agreement 0.924.
- Contract-month agreement 0.078; Carver/Globex rolls 14/69; 0 of 14 Carver rolls have a nearest Globex roll within five calendar days.
- Configured contract cycles: Carver hold cycle `Z`; repository Globex active months `HKNUZ`.
- Status: candidate; automated recommendation: `investigate_known_issue`.
- Known issue: Carver WHEAT holds only the annual December contract; Globex ZW selects the liquid front contract.
- Mapping note: CBOT wheat underlying.

## Interpretation safeguards

- A one-day lead/lag result is diagnostic evidence of settlement or session-date alignment; it is not permission to shift data automatically.
- Contract-month disagreement can reflect intentionally different roll rules, not a bad price series.
- `proceed_to_manual_review` requires at least 1,000 common dates, return correlation >= 0.90, trend correlation >= 0.80, direction agreement >= 0.80, and no registered known issue.
- The report does not validate multipliers, currency conversion, or executable slippage and therefore does not make any series live-trading eligible.
- Detailed common-date rows can be regenerated with `--write-details`; source databases remain read-only.
- `missing_dates.csv` identifies each non-common date and which source lacks it.
