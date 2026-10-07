# derivatives-bt-engine

## Python

- Use the project virtual environment at `.venv`.
- Run Python using `.venv/bin/python`.
- Use Polars rather than pandas for project data processing.
- Use the project's existing data-format conventions.
- Do not install packages globally.

## Testing and execution

- Project CLI executables under `.venv/bin/` may be used for testing.
- Backtests may be computationally expensive; use narrow symbol/year ranges for validation before running large tests.
- Preserve existing backtest results unless explicitly asked to regenerate or overwrite them.

## Code documentation

- Give every module a docstring explaining its responsibility and its place in
  the surrounding pipeline.
- Give functions and classes docstrings that explain their behavior, important
  parameters, return values, and any non-obvious units or data shapes.
- Add concise inline comments where dataframe identity, ownership, joins,
  normalization, or financial conventions would otherwise be difficult to
  infer from the code. Comments should explain why the step exists, not merely
  restate the syntax.

## Data

- Financial data may exist outside the repository under `/home/dev/data/fin`.
- Database resources may exist under `/home/dev/fin/db`.
- Do not modify source market data unless explicitly requested.

## Public report schemas

- Keep internal dataframe and calculation names descriptive. Compact names only
  at the public CSV/report boundary.
- A futures cost-report row represents one executable contract. Primary
  monetary values are USD, so use `notional`, `daily_dvol`, and `ann_dvol`.
  Retain suffixes such as `_native` only when they distinguish parallel values
  or units. Cost SR calculations use `ann_dvol`; do not add a duplicate
  cost-specific dollar-volatility field.
- Do not prefix the primary calculation path with `selected_`. Use `snap_` for
  point-in-time quote alternatives and `ref_` for reference inputs or
  fallbacks.
- Use short, unambiguous report tokens consistently: `con`, `instr`, `exec`,
  `elig`, `ann`, `dvol`, `sr`, `pct`, `avg`, `min`, `max`, `n`, `px`, `pt`,
  `pts`, `ccy`, `mult`, `src`, `hist`, `ref`, `snap`, `cap`, `wt`, and `cls`.
  In public column names, abbreviate `contract` as `con`.
- Add new abbreviations to the centralized public-schema rename/token map, not
  as one-off aliases. Update report ordering, ranking/notebook helpers,
  documentation, and schema tests together.
- Pass every public Polars CSV/report dataframe through
  `data.report_formatting.round_public_report` at the output boundary. Keep
  calculation dataframes at full precision, and register new monetary or
  reproduction-sensitive rate columns in that shared formatter rather than
  adding report-local rounding logic.

## Interactive Brokers

- Local IB Gateway/TWS ports may include 7496, 7497, 4001, and 4002.
- `nc` may be used to test whether these local ports are reachable.

## Repository

- Treat this repository as private.
- Treat `.env` files and credentials as sensitive.

## Logging

- Use the shared `derivatives_bt_engine` file logger; do not add module-local or stdout handlers.
- Use `DEBUG` for iterative candidate evaluation, rejection reasons, and intermediate calculations; use `INFO` for phase starts, selected decisions, and final outcomes.
- For allocation decisions, log the current and candidate contract books, dollar-vol/risk and distance before and after, applicable limits, and the exact reason a candidate is rejected.
- Keep log messages structured and unit-explicit (`contracts`, `notional`, `dvol`, `risk`) so saved run logs are auditable without source inspection.

## Workflow

- After each completed task, commit the task changes and push the commit to the configured remote.
- Include 1–3 sentences of context in each commit message describing what changed and why.
- Keep unrelated user changes out of task commits.
