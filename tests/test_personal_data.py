from services import personal_data
from services.history import init_db


def _snapshot(price: float = 110.0, score: int = 62, signal: str = "ATTENDRE"):
    return {
        "symbol": "BTCUSDC",
        "global_score": score,
        "global_signal": signal,
        "current_price": price,
        "timeframes": {
            "4h": {"score": score},
            "1d": {"score": score},
        },
    }


def test_positions_crud_and_gain_loss(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(personal_data, "get_market_snapshot", lambda symbol: _snapshot())

    position = personal_data.create_position(
        {
            "symbol": "btcusdc",
            "quantity": 2,
            "average_buy_price": 100,
            "invested_amount": 200,
            "strategy_type": "swing",
            "take_profit_1": 112,
            "take_profit_2": 120,
            "stop_loss": 95,
            "max_loss_accepted_pct": 5,
        },
        db_path=db_path,
    )
    updated = personal_data.update_position(position["id"], {"note": "test"}, db_path=db_path)
    positions = personal_data.list_positions(db_path=db_path)

    assert updated["note"] == "test"
    assert positions[0]["symbol"] == "BTCUSDC"
    assert positions[0]["current_value"] == 220
    assert positions[0]["gain_loss_amount"] == 20
    assert positions[0]["gain_loss_pct"] == 10
    assert positions[0]["distance_to_tp1_pct"] > 0
    assert personal_data.delete_position(position["id"], db_path=db_path) is True


def test_watch_candidates_crud(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(personal_data, "get_market_snapshot", lambda symbol: _snapshot(price=100))

    candidate = personal_data.create_watch_candidate(
        {
            "symbol": "ethusdc",
            "target_buy_price": 90,
            "invalidation_price": 80,
            "priority": "HIGH",
            "reason": "support",
        },
        db_path=db_path,
    )
    personal_data.update_watch_candidate(candidate["id"], {"priority": "LOW"}, db_path=db_path)
    candidates = personal_data.list_watch_candidates(db_path=db_path)

    assert candidates[0]["symbol"] == "ETHUSDC"
    assert candidates[0]["priority"] == "LOW"
    assert candidates[0]["distance_to_target_buy_pct"] == 10
    assert personal_data.delete_watch_candidate(candidate["id"], db_path=db_path) is True


def test_watch_candidate_rejects_duplicate(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(personal_data, "get_market_snapshot", lambda symbol: _snapshot(price=100))

    personal_data.create_watch_candidate({"symbol": "ETHUSDC"}, db_path=db_path)

    try:
        personal_data.create_watch_candidate({"symbol": "ethusdc"}, db_path=db_path)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("Duplicate watch candidate should be rejected")


def test_watch_candidate_rejects_invalid_symbol(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(
        personal_data,
        "resolve_market_symbol",
        lambda symbol: {"valid": False, "market_source": "UNKNOWN", "error": "Invalid symbol"},
    )

    try:
        personal_data.create_watch_candidate({"symbol": "CKP"}, db_path=db_path)
    except ValueError as exc:
        assert "Symbole introuvable" in str(exc)
    else:
        raise AssertionError("Invalid symbol should be rejected")


def test_watch_candidate_converts_base_symbol(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(
        personal_data,
        "resolve_market_symbol",
        lambda symbol: {
            "valid": True,
            "symbol": "BTCUSDC",
            "resolved_symbol": "BTCUSDC",
            "base_asset": "BTC",
            "quote_asset": "USDC",
            "market_source": "BINANCE_SPOT",
        },
    )
    monkeypatch.setattr(personal_data, "get_market_snapshot", lambda symbol: _snapshot(price=100))

    candidate = personal_data.create_watch_candidate({"symbol": "BTC"}, db_path=db_path)

    assert candidate["symbol"] == "BTCUSDC"


def test_watch_candidate_allows_alpha_symbol(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(
        personal_data,
        "resolve_market_symbol",
        lambda symbol: {
            "valid": True,
            "symbol": "CKP",
            "resolved_symbol": "CKP",
            "base_asset": "CKP",
            "quote_asset": None,
            "market_source": "BINANCE_ALPHA",
        },
    )
    monkeypatch.setattr(
        personal_data,
        "get_market_snapshot",
        lambda symbol: {
            "valid": True,
            "symbol": "CKP",
            "current_price": 2,
            "market_source": "BINANCE_ALPHA",
            "analysis_available": False,
            "price_available": True,
            "market_error": "Analyse technique limitée pour les tokens Alpha.",
        },
    )

    candidate = personal_data.create_watch_candidate({"symbol": "CKP"}, db_path=db_path)
    listed = personal_data.list_watch_candidates(db_path=db_path)[0]

    assert candidate["symbol"] == "CKP"
    assert candidate["market_source"] == "BINANCE_ALPHA"
    assert listed["current_price"] == 2
    assert listed["analysis_available"] is False


def test_manual_position_allows_non_binance_symbol(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(
        personal_data,
        "resolve_market_symbol",
        lambda symbol: {"valid": False, "market_source": "UNKNOWN", "error": "Invalid symbol"},
    )

    position = personal_data.create_position(
        {
            "symbol": "CKP",
            "quantity": 1,
            "average_buy_price": 1,
            "invested_amount": 1,
            "source": "MANUAL",
        },
        db_path=db_path,
    )

    assert position["symbol"] == "CKP"
    assert position["source"] == "MANUAL"


def test_alpha_manual_position_gets_limited_market_price(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(
        personal_data,
        "resolve_market_symbol",
        lambda symbol: {
            "valid": True,
            "symbol": "CKP",
            "resolved_symbol": "CKP",
            "base_asset": "CKP",
            "quote_asset": None,
            "market_source": "BINANCE_ALPHA",
        },
    )
    monkeypatch.setattr(
        personal_data,
        "get_alpha_market_data",
        lambda symbol: {
            "valid": True,
            "symbol": "CKP",
            "current_price": 2,
            "market_source": "BINANCE_ALPHA",
            "analysis_available": False,
            "market_error": "Analyse technique limitée pour les tokens Alpha.",
        },
    )

    personal_data.create_position(
        {
            "symbol": "CKP",
            "quantity": 3,
            "average_buy_price": 1,
            "invested_amount": 3,
            "source": "MANUAL",
        },
        db_path=db_path,
    )

    position = personal_data.list_positions(db_path=db_path)[0]

    assert position["market_source"] == "BINANCE_ALPHA"
    assert position["current_price"] == 2
    assert position["current_value"] == 6
    assert position["gain_loss_amount"] == 3
    assert position["gain_loss_pct"] == 100


def test_alpha_snapshot_never_calls_spot_scanner(monkeypatch) -> None:
    monkeypatch.setattr(
        personal_data,
        "resolve_market_symbol",
        lambda symbol: {
            "valid": True,
            "symbol": "MORPHO",
            "resolved_symbol": "MORPHO",
            "base_asset": "MORPHO",
            "quote_asset": None,
            "market_source": "BINANCE_ALPHA",
        },
    )
    monkeypatch.setattr(
        personal_data,
        "get_alpha_market_data",
        lambda symbol: {
            "valid": True,
            "symbol": "MORPHO",
            "current_price": 1.01,
            "market_source": "BINANCE_ALPHA",
            "analysis_available": True,
            "limited_analysis": True,
            "global_trend": "BEARISH",
            "global_score": 41,
        },
    )

    snapshot = personal_data.get_market_snapshot("MORPHOUSDC")

    assert snapshot["symbol"] == "MORPHO"
    assert snapshot["market_source"] == "BINANCE_ALPHA"
    assert snapshot["current_price"] == 1.01
    assert snapshot["global_trend"] == "BEARISH"


def test_invalid_symbols_do_not_break_listing_and_cleanup(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    init_db(db_path)
    monkeypatch.setattr(
        personal_data,
        "resolve_market_symbol",
        lambda symbol: {"valid": True, "symbol": "BTCUSDC", "market_source": "BINANCE_SPOT"} if symbol == "BTCUSDC" else {"valid": False, "market_source": "UNKNOWN", "error": "Invalid symbol"},
    )

    with personal_data._connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO watch_candidates (symbol, priority, created_at, updated_at)
            VALUES ('CKP', 'MEDIUM', '2026-01-01', '2026-01-01')
            """
        )
        connection.execute(
            """
            INSERT INTO positions (
                symbol, quantity, average_buy_price, invested_amount,
                status, is_active, created_at, updated_at
            )
            VALUES ('CKP', 1, 1, 1, 'ACTIVE', 1, '2026-01-01', '2026-01-01')
            """
        )
        connection.commit()

    watch = personal_data.list_watch_candidates(db_path=db_path)
    positions = personal_data.list_positions(db_path=db_path)
    cleanup = personal_data.cleanup_invalid_symbols(db_path=db_path)

    assert watch[0]["analysis_available"] is False
    assert watch[0]["is_valid_symbol"] is False
    assert positions[0]["analysis_available"] is False
    assert cleanup["removed"] == 1
    assert cleanup["watch_removed"] == 1
    assert cleanup["positions_marked_invalid"] == 0
    assert cleanup["invalid_symbols"] == ["CKP"]


def test_journal_crud(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(
        personal_data,
        "_build_journal_ai_snapshot",
        lambda symbol: {
            "resolution": {"symbol": "SOLUSDC", "market_source": "BINANCE_SPOT"},
            "market": {
                "symbol": "SOLUSDC",
                "current_price": 100,
                "global_score": 58,
                "global_trend": "NEUTRAL",
                "setup_quality": "MOYEN",
                "triggers": {"label": "Absent"},
            },
            "decision": {"confidence": 62, "market_score": 50, "trigger_label": "Absent"},
            "market_regime": {"regime": "NEUTRAL"},
        },
    )
    entry = personal_data.create_journal_entry(
        {
            "symbol": "solusdc",
            "decision_type": "WAIT",
            "trade_type": "SWING",
            "emotion": "CALM",
            "confidence": 70,
            "reason": "setup propre",
        },
        db_path=db_path,
    )

    entries = personal_data.list_journal(db_path=db_path)

    assert entries[0]["symbol"] == "SOLUSDC"
    assert entries[0]["confidence"] == 70
    assert entries[0]["decision_type"] == "WAIT"
    assert entries[0]["ai_score"] == 58
    assert entries[0]["ai_snapshot"]["market"]["current_price"] == 100
    updated = personal_data.update_journal_entry(
        entry["id"],
        {"result_status": "WIN", "pnl_percent": 3.2, "pnl_usdc": 1.4},
        db_path=db_path,
    )
    assert updated["result_status"] == "WIN"
    assert updated["pnl_percent"] == 3.2
    assert personal_data.delete_journal_entry(entry["id"], db_path=db_path) is True


def test_position_alerts_are_sorted() -> None:
    alerts = personal_data.generate_position_alerts(
        [
            {
                "symbol": "BTCUSDC",
                "distance_to_tp1_pct": 0.5,
                "distance_to_tp2_pct": 4,
                "distance_to_stop_loss_pct": 0.8,
                "gain_loss_pct": -6,
                "max_loss_accepted_pct": 5,
                "signal": "ÉVITER / VENTE POSSIBLE",
                "trend_score": "DOWN STRONG",
            }
        ]
    )

    assert alerts[0]["severity"] == "HIGH"
    assert any(alert["title"] == "Proche stop-loss" for alert in alerts)
    assert any(alert["title"] == "Trend score DOWN STRONG" for alert in alerts)
