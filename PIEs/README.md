# PIEs — Pies and Holdings

This directory stores Trading 212 Pie exports (CSV) and pie configuration JSON.

## Example files

- `example_*.csv` — minimal safe examples with 2 fictional holdings and zero values.
  Replace with your own exports locally. Real `PIEs/*.csv` files are **gitignored** and must never be committed.

## How to use your own data

1. Export pies from Trading 212 (CSV).
2. Place your CSVs here, e.g. `MyPie.csv`.
3. Add matching config in `PIEs/config/*.json` (see `config/*.json` examples).
4. Ensure `PIEs/*.csv` stays ignored via `.gitignore`.

## Config

- `PIEs/config/*.json` — pie-level strategy metadata. Example configs are tracked; your private allocations stay local.

> Security: never commit real holdings, quantities, or account-specific values.
