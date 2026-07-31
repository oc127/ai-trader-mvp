CREATE TABLE IF NOT EXISTS bond_info (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    conversion_price REAL NOT NULL,
    maturity_date TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    par_value REAL NOT NULL DEFAULT 100.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_bars (
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(date);

CREATE TABLE IF NOT EXISTS bond_snapshots (
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    stock_code TEXT NOT NULL,
    stock_price REAL NOT NULL,
    conversion_price REAL NOT NULL,
    conversion_value REAL NOT NULL,
    premium_rate REAL NOT NULL,
    volume_cny REAL NOT NULL,
    ytm REAL NOT NULL,
    remaining_years REAL NOT NULL,
    timestamp TEXT NOT NULL,
    PRIMARY KEY (code, timestamp)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_run_id INTEGER,
    code TEXT NOT NULL,
    side TEXT NOT NULL,
    shares INTEGER NOT NULL,
    price REAL NOT NULL,
    cost REAL NOT NULL,
    timestamp TEXT NOT NULL,
    signal_composite REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY (backtest_run_id) REFERENCES backtest_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_trades_code ON trades(code);
CREATE INDEX IF NOT EXISTS idx_trades_run ON trades(backtest_run_id);

CREATE TABLE IF NOT EXISTS daily_pnl (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backtest_run_id INTEGER,
    date TEXT NOT NULL,
    pnl REAL NOT NULL,
    cumulative_pnl REAL NOT NULL,
    capital REAL NOT NULL,
    trade_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (backtest_run_id) REFERENCES backtest_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_daily_pnl_run ON daily_pnl(backtest_run_id);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    config_json TEXT NOT NULL,
    total_return REAL,
    annualized_return REAL,
    max_drawdown REAL,
    sharpe_ratio REAL,
    win_rate REAL,
    total_trades INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
