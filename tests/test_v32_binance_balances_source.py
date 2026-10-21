import sqlite3

import services.binance_sync as binance_sync
from services.history import init_db


class BalanceClient:
    configured = True

    def __init__(self, balances, trades=None):
        self._balances = balances
        self._trades = trades or []

    def get_non_zero_balances(self):
        return self._balances

    def get_my_trades(self, symbol, *, from_id=0, limit=1000):
        target = (self._balances[0]["asset"] if self._balances else "DOGE") + "USDC"
        return [trade for trade in self._trades if symbol == target and trade["id"] >= from_id][:limit]

    def get_spot_markets(self):
        assets = {"DOGE"} | {row["asset"] for row in self._balances if row["asset"] != "MORPHO"}
        return [{"symbol":asset+"USDC","base_asset":asset,"quote_asset":"USDC"} for asset in sorted(assets)]


def _insert_trade(db_path, symbol="DOGEUSDC", trade_id=1):
    init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO binance_trades (
                symbol, order_id, trade_id, side, price, qty, quote_qty,
                commission, commission_asset, time, is_buyer, is_maker
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, 1, trade_id, "BUY", 0.1, 100, 10, 0, "USDC", 1, 1, 0),
        )
        connection.commit()


def test_trade_history_without_balance_does_not_create_active_position(tmp_path, monkeypatch):
    db_path = tmp_path / "history.db"
    _insert_trade(db_path)
    monkeypatch.setattr(binance_sync, "symbol_exists", lambda symbol: True)
    monkeypatch.setattr(binance_sync, "get_current_price", lambda symbol: 0.11)

    result = binance_sync.sync_binance_positions(
        client=BalanceClient(balances=[]),
        db_path=db_path,
    )

    assert result["positions"] == []
    assert binance_sync.get_binance_positions(db_path=db_path) == []
    # An unsold historical buy followed by zero balance is a possible withdrawal,
    # not evidence of a full sale.
    assert binance_sync.get_closed_trade_symbols(db_path=db_path) == []
    assert binance_sync.get_inactive_positions(db_path=db_path)[0]["status"] == "INACTIVE"


def test_positive_balance_creates_active_position(tmp_path, monkeypatch):
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(binance_sync, "symbol_exists", lambda symbol: True)
    monkeypatch.setattr(binance_sync, "get_current_price", lambda symbol: 0.11)
    client = BalanceClient(
        balances=[{"asset": "DOGE", "free": 25, "locked": 0, "total": 25}],
        trades=[
            {
                "id": 1,
                "orderId": 1,
                "price": "0.109",
                "qty": "25",
                "quoteQty": "2.725",
                "commission": "0",
                "commissionAsset": "USDC",
                "time": 1,
                "isBuyer": True,
                "isMaker": False,
            }
        ],
    )

    result = binance_sync.sync_binance_positions(client=client, db_path=db_path)
    active = binance_sync.get_binance_positions(db_path=db_path)

    assert result["positions"][0]["symbol"] == "DOGEUSDC"
    assert active[0]["is_active"] == 1
    assert active[0]["status"] == "ACTIVE"
    assert active[0]["quantity"] == 25


def test_alpha_balance_creates_active_position_without_spot_symbol(tmp_path, monkeypatch):
    monkeypatch.setattr(binance_sync, "get_current_price", lambda symbol: None)
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(binance_sync, "symbol_exists", lambda symbol: False)
    monkeypatch.setattr(
        binance_sync,
        "resolve_market_symbol",
        lambda asset: {
            "valid": True,
            "symbol": "MORPHO",
            "market_source": "BINANCE_ALPHA",
        },
    )
    monkeypatch.setattr(
        binance_sync,
        "fetch_alpha_market_data",
        lambda asset: {
            "valid": True,
            "symbol": "MORPHO",
            "current_price": 1.01,
            "quote_asset": "USDC",  # Explicit synthetic financial quote.
            "market_source": "BINANCE_ALPHA",
            "price_change_pct": -4.5,
            "quote_volume": 433000,
        },
    )
    client = BalanceClient(
        balances=[{"asset": "MORPHO", "free": 11.55, "locked": 0, "total": 11.55}],
        trades=[],
    )

    result = binance_sync.sync_binance_positions(client=client, db_path=db_path)
    active = binance_sync.get_binance_positions(db_path=db_path)

    assert result["positions"][0]["symbol"] == "MORPHO"
    assert result["positions"][0]["market_source"] == "BINANCE_ALPHA"
    assert active[0]["market_source"] == "BINANCE_ALPHA"
    assert active[0]["current_price"] == 1.01
    assert round(active[0]["current_value"], 8) == round(11.55 * 1.01, 8)


def test_dust_balance_is_not_active_position(tmp_path, monkeypatch):
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(binance_sync, "symbol_exists", lambda symbol: True)
    monkeypatch.setattr(binance_sync, "get_current_price", lambda symbol: 0.11)
    client = BalanceClient(
        balances=[{"asset": "DOGE", "free": 0.08, "locked": 0, "total": 0.08}],
        trades=[],
    )

    result = binance_sync.sync_binance_positions(client=client, db_path=db_path)
    dust = binance_sync.get_dust_balances(db_path=db_path)

    assert result["positions"] == []
    assert dust[0]["symbol"] == "DOGEUSDC"
    assert dust[0]["current_value_usdc"] == 0.0088
    assert dust[0]["reason"] == "Valeur inférieure au seuil actif de 1 USDC"
    assert binance_sync.get_active_positions(db_path=db_path) == []
    inactive = binance_sync.get_inactive_positions(db_path=db_path)
    assert inactive[0]["status"] == "DUST"


def test_value_099_usdc_is_dust(tmp_path, monkeypatch):
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(binance_sync, "symbol_exists", lambda symbol: True)
    monkeypatch.setattr(binance_sync, "get_current_price", lambda symbol: 0.99)
    client = BalanceClient(
        balances=[{"asset": "DOGE", "free": 1, "locked": 0, "total": 1}],
        trades=[],
    )

    result = binance_sync.sync_binance_positions(client=client, db_path=db_path)

    assert result["positions"] == []
    assert result["dust"][0]["current_value_usdc"] == 0.99
    assert binance_sync.get_active_positions(db_path=db_path) == []


def test_value_100_usdc_is_active(tmp_path, monkeypatch):
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(binance_sync, "symbol_exists", lambda symbol: True)
    monkeypatch.setattr(binance_sync, "get_current_price", lambda symbol: 1.0)
    client = BalanceClient(
        balances=[{"asset": "DOGE", "free": 1, "locked": 0, "total": 1}],
        trades=[
            {
                "id": 1,
                "orderId": 1,
                "price": "1",
                "qty": "1",
                "quoteQty": "1",
                "commission": "0",
                "commissionAsset": "USDC",
                "time": 1,
                "isBuyer": True,
                "isMaker": False,
            }
        ],
    )

    result = binance_sync.sync_binance_positions(client=client, db_path=db_path)
    active = binance_sync.get_active_positions(db_path=db_path)

    assert result["positions"][0]["symbol"] == "DOGEUSDC"
    assert active[0]["status"] == "ACTIVE"
    assert active[0]["current_value"] == 1


def test_cleanup_cannot_close_from_list_absence(tmp_path):
    db_path = tmp_path / "history.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO positions (
                symbol, quantity, average_buy_price, invested_amount,
                source, synced_from_binance, is_active, status, created_at, updated_at
            )
            VALUES ('DOGEUSDC', 25, 0.1, 2.5, 'BINANCE', 1, 1, 'ACTIVE', 'now', 'now')
            """
        )
        connection.commit()

    binance_sync.cleanup_inactive_binance_positions([], db_path=db_path)

    assert binance_sync.get_inactive_positions(db_path=db_path) == []
    active = binance_sync.get_binance_positions(db_path=db_path)
    assert active[0]["symbol"] == "DOGEUSDC"
    assert active[0]["is_active"] == 1
    assert active[0]["status"] == "ACTIVE"
    assert active[0]["data_status"] == "TEMPORARILY_UNAVAILABLE"




def _verified_synthetic_snapshots(db_path):
    """Give aggregate-only fixtures explicit independently specified provenance."""
    import json
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT * FROM positions").fetchall():
            values = {
                "remaining_quantity":row["quantity"],
                "average_remaining_cost":row["average_buy_price"],
                "invested_remaining":row["invested_amount"],
                "current_price":row["current_price"],
                "current_value":row["current_value"],
                "realized_pnl":row["realized_pnl"],
                "known_realized_pnl":row["realized_pnl"],
                "unrealized_pnl":row["unrealized_pnl"],
                "total_pnl":row["total_pnl"],
                "unrealized_pnl_pct":row["profit_loss_pct"],
            }
            payload={**values,"decimal_values":{key:str(value) if value is not None else None for key,value in values.items()},"issues":[]}
            connection.execute("""UPDATE positions SET data_status='VALID',
                history_status='COMPLETE',cost_basis_status='EXACT',price_status='EXACT',
                pnl_status='EXACT',realized_pnl_status='EXACT',accounting_json=? WHERE id=?""",
                (json.dumps(payload),row["id"]))
        connection.execute("""INSERT INTO exchange_sync_state(exchange,last_sync,last_attempt,sync_status,discovery_status)
            VALUES('BINANCE_SPOT','synthetic','synthetic','COMPLETE','COMPLETE')""")


def test_pnl_summary_preserves_closed_and_dust_realized(tmp_path):
    db_path = tmp_path / "history.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO positions (
                symbol, quantity, average_buy_price, invested_amount,
                current_value, realized_pnl, unrealized_pnl, total_pnl,
                source, synced_from_binance, is_active, status, created_at, updated_at
            )
            VALUES ('DOGEUSDC', 25, 0.1, 2.5, 3, 1, 0.5, 1.5, 'BINANCE', 1, 1, 'ACTIVE', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO positions (
                symbol, quantity, average_buy_price, invested_amount,
                current_value, realized_pnl, unrealized_pnl, total_pnl,
                source, synced_from_binance, is_active, status, created_at, updated_at
            )
            VALUES ('ETHUSDC', 0, 0, 0, 0, 50, 0, 50, 'BINANCE', 1, 0, 'CLOSED', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO positions (
                symbol, quantity, average_buy_price, invested_amount,
                current_value, realized_pnl, unrealized_pnl, total_pnl,
                source, synced_from_binance, is_active, status, created_at, updated_at
            )
            VALUES ('SOLUSDC', 0.005, 100, 0.5, 0.5, 99, 0, 99, 'BINANCE', 1, 0, 'DUST', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO binance_balances (asset, free, locked, total, updated_at)
            VALUES ('USDC', 40, 0, 40, 'now')
            """
        )
        connection.commit()

    _verified_synthetic_snapshots(db_path)
    summary = binance_sync.get_pnl_summary(db_path=db_path)

    assert summary["current_value"] == 3
    assert summary["usdc_available"] == 40
    assert summary["portfolio_value_usdc"] == 43.5
    assert summary["realized_pnl"] == 150
    assert summary["total_pnl"] == 150.5
    assert summary["inactive_symbols_count"] == 1
    assert summary["dust_count"] == 1
    assert summary["dust_value_usdc"] == 0.5


def test_pnl_summary_includes_spot_and_alpha_positions(tmp_path):
    db_path = tmp_path / "history.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO positions (
                symbol, quantity, average_buy_price, invested_amount,
                current_value, realized_pnl, unrealized_pnl, total_pnl,
                source, synced_from_binance, is_active, status, market_source,
                created_at, updated_at
            )
            VALUES ('BTCUSDC', 0.001, 80000, 80, 82, 0, 2, 2,
                    'BINANCE', 1, 1, 'ACTIVE', 'BINANCE_SPOT', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO positions (
                symbol, quantity, average_buy_price, invested_amount,
                current_value, realized_pnl, unrealized_pnl, total_pnl,
                source, synced_from_binance, is_active, status, market_source,
                created_at, updated_at
            )
            VALUES ('CKP', 10, 1.5, 15, 12, 0, -3, -3,
                    'BINANCE', 1, 1, 'ACTIVE', 'BINANCE_ALPHA', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO binance_balances (asset, free, locked, total, updated_at)
            VALUES ('USDC', 6, 0, 6, 'now')
            """
        )
        connection.commit()

    _verified_synthetic_snapshots(db_path)
    summary = binance_sync.get_pnl_summary(db_path=db_path)

    assert summary["current_value"] == 94
    assert summary["portfolio_value_usdc"] == 100
    assert summary["unrealized_pnl"] == -1
    assert summary["total_pnl"] == -1
