from services import binance_sync


class FakeClient:
    configured = True

    def get_non_zero_balances(self):
        return [{"asset": "BTC", "free": 0.1, "locked": 0.0, "total": 0.1}]

    def get_my_trades(self, symbol):
        return [
            {
                "id": 1,
                "orderId": 11,
                "price": "100",
                "qty": "1",
                "quoteQty": "100",
                "commission": "0",
                "commissionAsset": "USDC",
                "time": 1,
                "isBuyer": True,
                "isMaker": False,
            },
            {
                "id": 2,
                "orderId": 12,
                "price": "110",
                "qty": "0.4",
                "quoteQty": "44",
                "commission": "0",
                "commissionAsset": "USDC",
                "time": 2,
                "isBuyer": False,
                "isMaker": False,
            },
        ]


def test_sync_balances(tmp_path) -> None:
    db_path = tmp_path / "history.db"

    balances = binance_sync.sync_balances(client=FakeClient(), db_path=db_path)
    stored = binance_sync.get_balances(db_path=db_path)

    assert balances[0]["asset"] == "BTC"
    assert stored[0]["total"] == 0.1


def test_sync_trades_and_average_buy_price(tmp_path) -> None:
    db_path = tmp_path / "history.db"

    binance_sync.sync_trades("BTCUSDC", client=FakeClient(), db_path=db_path)
    estimate = binance_sync.estimate_average_buy_price("BTCUSDC", db_path=db_path)

    assert estimate["quantity"] == 0.6
    assert estimate["average_buy_price"] == 100
    assert estimate["realized_pnl"] == 4


def test_calculate_unrealized_and_realized_pnl(tmp_path) -> None:
    db_path = tmp_path / "history.db"

    binance_sync.sync_trades("BTCUSDC", client=FakeClient(), db_path=db_path)
    pnl = binance_sync.calculate_unrealized_pnl("BTCUSDC", current_price=120, db_path=db_path)

    assert pnl["realized_pnl"] == 4
    assert pnl["unrealized_pnl"] == 12
    assert pnl["total_pnl"] == 16


def test_fifo_handles_partial_sell_and_remaining_position() -> None:
    trades = [
        {
            "symbol": "DOGEUSDC",
            "side": "BUY",
            "price": 0.1,
            "qty": 100,
            "quote_qty": 10,
            "commission": 0,
            "commission_asset": "USDC",
        },
        {
            "symbol": "DOGEUSDC",
            "side": "SELL",
            "price": 0.12,
            "qty": 75,
            "quote_qty": 9,
            "commission": 0,
            "commission_asset": "USDC",
        },
    ]

    pnl = binance_sync.rebuild_fifo_position(trades, "DOGEUSDC", current_price=0.105)

    assert pnl["remaining_qty"] == 25
    assert pnl["average_remaining_cost"] == 0.1
    assert pnl["realized_pnl"] == 1.5
    assert pnl["unrealized_pnl"] == 0.125
    assert pnl["total_pnl"] == 1.625


def test_fifo_handles_multiple_scalps_and_new_remaining_cost() -> None:
    trades = [
        {
            "symbol": "DOGEUSDC",
            "side": "BUY",
            "price": 0.1,
            "qty": 100,
            "quote_qty": 10,
            "commission": 0,
            "commission_asset": "USDC",
        },
        {
            "symbol": "DOGEUSDC",
            "side": "SELL",
            "price": 0.12,
            "qty": 100,
            "quote_qty": 12,
            "commission": 0,
            "commission_asset": "USDC",
        },
        {
            "symbol": "DOGEUSDC",
            "side": "BUY",
            "price": 0.109,
            "qty": 25,
            "quote_qty": 2.725,
            "commission": 0,
            "commission_asset": "USDC",
        },
    ]

    pnl = binance_sync.rebuild_fifo_position(trades, "DOGEUSDC", current_price=0.1058)

    assert pnl["remaining_qty"] == 25
    assert pnl["average_remaining_cost"] == 0.109
    assert pnl["realized_pnl"] == 2
    assert pnl["unrealized_pnl"] == -0.08
    assert pnl["total_pnl"] == 1.92


def test_fifo_avoids_absurd_percentage_on_dust() -> None:
    trades = [
        {
            "symbol": "DOGEUSDC",
            "side": "BUY",
            "price": 0.001,
            "qty": 10,
            "quote_qty": 0.01,
            "commission": 0,
            "commission_asset": "USDC",
        }
    ]

    pnl = binance_sync.rebuild_fifo_position(trades, "DOGEUSDC", current_price=0.002)

    assert pnl["unrealized_pnl"] == 0.01
    assert pnl["unrealized_pnl_pct"] is None


def test_fifo_marks_estimated_when_balance_differs_from_trades() -> None:
    trades = [
        {
            "symbol": "DOGEUSDC",
            "side": "BUY",
            "price": 0.1,
            "qty": 100,
            "quote_qty": 10,
            "commission": 0,
            "commission_asset": "USDC",
        }
    ]

    pnl = binance_sync.rebuild_fifo_position(
        trades,
        "DOGEUSDC",
        current_price=0.12,
        current_balance=25,
    )

    assert pnl["quantity"] == 25
    assert pnl["is_estimated"] is True


def test_sync_without_key_returns_status(monkeypatch, tmp_path) -> None:
    class NoKeyClient:
        configured = False

    monkeypatch.setattr(
        binance_sync,
        "get_private_client_status",
        lambda: {"configured": False, "read_only_mode": True, "use_testnet": False},
    )

    result = binance_sync.sync_binance_account(client=NoKeyClient(), db_path=tmp_path / "history.db")

    assert result["synced"] is False
    assert "non configurée" in result["message"]
