# CB Trader

Chinese convertible bond (可转债) T+0 intraday trading system.

## Stack
- Python 3.11+, uv for deps
- akshare for market data
- SQLite for local storage
- YAML config (config/*.yaml) + .env for secrets

## Project structure
- `src/data/` — Data fetching (akshare) and SQLite storage
- `src/strategy/` — Alpha signals and composite strategy
- `src/risk/` — Position limits, daily loss control
- `src/backtest/` — Historical replay engine
- `src/execution/` — Order execution (paper now, QMT later)
- `config/` — YAML configuration files
- `tests/` — pytest test suite
- `scripts/` — CLI utilities

## Commands
- `uv run cb-trader` — Run the trading system
- `uv run pytest` — Run tests
- `uv run ruff check src/` — Lint
- `uv run python scripts/fetch_data.py` — Fetch historical data
- `uv run python scripts/run_backtest.py` — Run backtest

## Conventions
- All amounts in CNY unless noted
- Convertible bond prices in yuan per unit (face value 100)
- Premium rates stored as decimals (0.01 = 1%)
- Timestamps in Asia/Shanghai timezone, stored as ISO 8601
- Bond quantities in 张 (units), minimum trading unit 10张
