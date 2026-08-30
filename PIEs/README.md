# PIEs — Example Exports

This directory stores Trading 212 Pie exports (CSV).

- Real Pie CSVs are **never** committed — they contain private financial data and are ignored via `.gitignore` (`PIEs/*.csv`).
- Only `example_*.csv` files are tracked. They contain a minimal header and two fictitious demo rows.

## Header

The parser expects at least:

```
Slice,Name,Invested value,Value,Result,Owned quantity,Dividends gained,Dividends cash,Dividends reinvested
```

Additional columns are ignored. Demo values are fictitious (0 or 1 share) and must not be used for real calculations.

## Example files

- `example_daily_dividend.csv` — demo dividend pie
- `example_renewable.csv` — demo renewable theme
- `example_savings.csv` — demo low-risk savings pie
- `example_technology.csv` — demo technology pie

Copy and rename an example file for local testing if needed. Never commit real exports.
