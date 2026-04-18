# AI Trader MVP

Automated funding rate arbitrage system on Hyperliquid.

## Stack
- Python 3.11+, uv for deps
- Hyperliquid Python SDK for exchange connectivity
- SQLite for local storage
- YAML config (config/*.yaml) + .env for secrets

## Project structure
- `src/hl_client/` — HL REST + WebSocket wrapper
- `src/data/` — Market data ingestion and storage
- `src/strategy/` — Trading strategy implementations
- `src/execution/` — Order management, paper + live executors
- `src/risk/` — Position limits, circuit breakers
- `src/monitor/` — PnL tracking, Telegram alerts
- `src/backtest/` — Historical replay engine
- `config/` — YAML configuration files
- `tests/` — pytest test suite
- `scripts/` — CLI utilities

## Commands
- `uv run trader` — Run the trading system
- `uv run pytest` — Run tests
- `uv run ruff check src/` — Lint
- `uv run python scripts/fetch_historical.py` — Fetch historical funding data
- `uv run python scripts/run_backtest.py` — Run backtest

## Conventions
- All amounts in USDC unless noted
- Funding rates stored as decimals (0.0001 = 0.01%)
- Timestamps in UTC, stored as ISO 8601
- Config env override: ENVIRONMENT=testnet|mainnet
