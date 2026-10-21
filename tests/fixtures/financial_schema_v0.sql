-- Financial DDL only, from commit bb784f8; contains no user data.

CREATE TABLE positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                average_buy_price REAL NOT NULL,
                invested_amount REAL NOT NULL,
                strategy_type TEXT,
                take_profit_1 REAL,
                take_profit_2 REAL,
                stop_loss REAL,
                max_loss_accepted_pct REAL,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            , current_price REAL, current_value REAL, source TEXT DEFAULT 'MANUAL', binance_asset TEXT, synced_from_binance INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, status TEXT DEFAULT 'MANUAL', last_sync_at TEXT, realized_pnl REAL DEFAULT 0, unrealized_pnl REAL DEFAULT 0, total_pnl REAL DEFAULT 0, profit_loss_pct REAL, is_estimated INTEGER DEFAULT 0, market_source TEXT, resolved_symbol TEXT, base_asset TEXT);

CREATE TABLE binance_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT NOT NULL UNIQUE,
                free REAL NOT NULL,
                locked REAL NOT NULL,
                total REAL NOT NULL,
                updated_at TEXT NOT NULL
            );

CREATE TABLE binance_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                order_id INTEGER,
                trade_id INTEGER NOT NULL UNIQUE,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                quote_qty REAL NOT NULL,
                commission REAL,
                commission_asset TEXT,
                time INTEGER,
                is_buyer INTEGER NOT NULL,
                is_maker INTEGER NOT NULL
            );

CREATE TABLE binance_sync_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_sync TEXT
            );
