import sqlite3

from services.history import get_symbol_history, init_db, reset_database, save_scan_result


def test_history_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "history.db"
    init_db(db_path)
    save_scan_result(
        {
            "symbol": "BTCUSDC",
            "global_score": 62,
            "global_signal": "ATTENDRE",
            "current_price": 78000.0,
        },
        db_path=db_path,
    )

    history = get_symbol_history("BTCUSDC", limit=10, db_path=db_path)

    assert len(history) == 1
    assert history[0]["score"] == 62
    assert history[0]["price"] == 78000.0


def test_reset_database_clears_local_tables(tmp_path) -> None:
    db_path = tmp_path / "history.db"
    init_db(db_path)
    save_scan_result(
        {
            "symbol": "BTCUSDC",
            "global_score": 62,
            "global_signal": "ATTENDRE",
            "current_price": 78000.0,
        },
        db_path=db_path,
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO positions (
                symbol, quantity, average_buy_price, invested_amount,
                source, status, created_at, updated_at
            )
            VALUES ('BTCUSDC', 1, 100, 100, 'MANUAL', 'MANUAL', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO watch_candidates (symbol, priority, created_at, updated_at)
            VALUES ('ETHUSDC', 'HIGH', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO trade_journal (symbol, action, created_at)
            VALUES ('SOLUSDC', 'ATTENTE', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO binance_balances (asset, free, locked, total, updated_at)
            VALUES ('DOGE', 1, 0, 1, 'now')
            """
        )
        connection.commit()

    summary = reset_database(db_path=db_path)

    assert summary["positions"]["deleted"] == 1
    assert summary["watch_candidates"]["deleted"] == 1
    assert summary["trade_journal"]["deleted"] == 1
    assert summary["market_history"]["deleted"] == 1
    assert summary["binance_balances"]["deleted"] == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM market_history").fetchone()[0] == 0
