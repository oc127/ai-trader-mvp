SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS funding_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    rate REAL NOT NULL,
    premium REAL NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL,
    UNIQUE(coin, timestamp)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    price REAL NOT NULL,
    order_id TEXT,
    is_spot INTEGER NOT NULL DEFAULT 0,
    fee REAL NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    spot_size REAL NOT NULL DEFAULT 0,
    perp_size REAL NOT NULL DEFAULT 0,
    spot_entry_price REAL NOT NULL DEFAULT 0,
    perp_entry_price REAL NOT NULL DEFAULT 0,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL NOT NULL DEFAULT 0,
    funding_earned REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS pnl_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equity REAL NOT NULL,
    available_balance REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    funding_earned REAL NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    data TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_funding_coin_ts ON funding_rates(coin, timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_coin_ts ON trades(coin, timestamp);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_pnl_ts ON pnl_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, timestamp);
"""
