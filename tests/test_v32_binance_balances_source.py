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

    def get_my_trades(self, symbol):
        return self._trades


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
    assert binance_sync.get_closed_trade_symbols(db_path=db_path) == [{"symbol": "DOGEUSDC"}]


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


def test_cleanup_marks_old_binance_position_inactive(tmp_path):
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

    inactive = binance_sync.get_inactive_positions(db_path=db_path)
    assert inactive[0]["symbol"] == "DOGEUSDC"
    assert inactive[0]["is_active"] == 0
    assert inactive[0]["status"] == "CLOSED"


def test_pnl_summary_excludes_inactive_positions(tmp_path):
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
            VALUES ('ETHUSDC', 1, 100, 100, 0, 50, 0, 50, 'BINANCE', 1, 0, 'CLOSED', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO positions (
                symbol, quantity, average_buy_price, invested_amount,
                current_value, realized_pnl, unrealized_pnl, total_pnl,
                source, synced_from_binance, is_active, status, created_at, updated_at
            )
            VALUES ('SOLUSDC', 0.001, 100, 0.5, 0.5, 99, 0, 99, 'BINANCE', 1, 0, 'DUST', 'now', 'now')
            """
        )
        connection.execute(
            """
            INSERT INTO binance_balances (asset, free, locked, total, updated_at)
            VALUES ('USDC', 40, 0, 40, 'now')
            """
        )
        connection.commit()

    summary = binance_sync.get_pnl_summary(db_path=db_path)

    assert summary["current_value"] == 3
    assert summary["usdc_available"] == 40
    assert summary["portfolio_value_usdc"] == 43
    assert summary["realized_pnl"] == 1
    assert summary["total_pnl"] == 1.5
    assert summary["inactive_symbols_count"] == 1
    assert summary["dust_count"] == 1
    assert summary["dust_value_usdc"] == 0.5
