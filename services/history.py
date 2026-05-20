import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).resolve().parent.parent / "database" / "history.db"
RESETTABLE_TABLES = [
    "positions",
    "watch_candidates",
    "trade_journal",
    "alerts",
    "market_history",
    "binance_balances",
    "binance_trades",
    "binance_sync_state",
    "score_snapshots",
    "backtest_results",
    "market_cache",
]


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(db_path: Path | str = DB_PATH) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                global_score INTEGER NOT NULL,
                global_signal TEXT NOT NULL,
                current_price REAL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
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
            )
            """
        )
        for column, definition in [
            ("current_price", "REAL"),
            ("current_value", "REAL"),
            ("source", "TEXT DEFAULT 'MANUAL'"),
            ("binance_asset", "TEXT"),
            ("synced_from_binance", "INTEGER DEFAULT 0"),
            ("is_active", "INTEGER DEFAULT 1"),
            ("status", "TEXT DEFAULT 'MANUAL'"),
            ("last_sync_at", "TEXT"),
            ("realized_pnl", "REAL DEFAULT 0"),
            ("unrealized_pnl", "REAL DEFAULT 0"),
            ("total_pnl", "REAL DEFAULT 0"),
            ("profit_loss_pct", "REAL"),
            ("is_estimated", "INTEGER DEFAULT 0"),
            ("market_source", "TEXT"),
            ("resolved_symbol", "TEXT"),
            ("base_asset", "TEXT"),
        ]:
            _add_column_if_missing(connection, "positions", column, definition)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                target_buy_price REAL,
                invalidation_price REAL,
                priority TEXT,
                reason TEXT,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        for column, definition in [
            ("market_source", "TEXT"),
            ("resolved_symbol", "TEXT"),
            ("base_asset", "TEXT"),
        ]:
            _add_column_if_missing(connection, "watch_candidates", column, definition)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                confidence INTEGER,
                emotion TEXT,
                result TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS binance_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT NOT NULL UNIQUE,
                free REAL NOT NULL,
                locked REAL NOT NULL,
                total REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS binance_trades (
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
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS binance_sync_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_sync TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS score_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                global_score INTEGER,
                context_score INTEGER,
                setup_score INTEGER,
                trigger_score INTEGER,
                risk_score INTEGER,
                market_score INTEGER,
                confidence INTEGER,
                decision TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                decision TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                outcome TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def reset_database(db_path: Path | str = DB_PATH) -> dict[str, Any]:
    init_db(db_path)
    summary: dict[str, Any] = {}

    with sqlite3.connect(db_path) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        for table in RESETTABLE_TABLES:
            if table not in existing_tables:
                summary[table] = {"existed": False, "deleted": 0}
                continue

            count_row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            deleted = int(count_row[0])
            connection.execute(f"DELETE FROM {table}")
            summary[table] = {"existed": True, "deleted": deleted}

        sequence_tables = [
            table
            for table in RESETTABLE_TABLES
            if table in existing_tables and table != "binance_sync_state"
        ]
        if sequence_tables and "sqlite_sequence" in existing_tables:
            placeholders = ", ".join("?" for _ in sequence_tables)
            connection.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                sequence_tables,
            )

        connection.commit()

    return summary


def save_scan_result(
    result: dict[str, Any],
    db_path: Path | str = DB_PATH,
) -> None:
    if "error" in result:
        return

    init_db(db_path)
    timestamp = datetime.now(UTC).isoformat()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO market_history (
                timestamp,
                symbol,
                global_score,
                global_signal,
                current_price
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                result["symbol"],
                int(result["global_score"]),
                result["global_signal"],
                result.get("current_price"),
            ),
        )
        connection.commit()


def get_symbol_history(
    symbol: str,
    limit: int = 50,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    init_db(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT timestamp, global_score, current_price
            FROM market_history
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (symbol.upper(), limit),
        ).fetchall()

    return [
        {
            "timestamp": row["timestamp"],
            "score": row["global_score"],
            "price": row["current_price"],
        }
        for row in rows
    ]
