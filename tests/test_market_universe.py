from services.market_universe import get_market_universe, get_symbol_sources


def test_market_universe_merges_sources_once_with_badges() -> None:
    universe = get_market_universe(
        positions=[
            {
                "symbol": "BTCUSDC",
                "id": 11,
                "source": "BINANCE",
                "status": "ACTIVE",
                "is_active": 1,
                "current_value": 100,
                "quantity": 0.05,
                "average_buy_price": 78000,
                "profit_loss_pct": 2.4,
                "unrealized_pnl": 12.5,
            }
        ],
        watch_candidates=[
            {
                "id": 7,
                "symbol": "BTCUSDC",
                "priority": "HIGH",
                "target_buy_price": 76000,
                "distance_to_target_buy_pct": 1.8,
                "reason": "Support proche",
            }
        ],
        watchlist=["BTCUSDC", "ETHUSDC"],
        opportunities=[{"symbol": "SUIUSDC", "scan_score": 82, "reason": "Volume en hausse"}],
    )

    symbols = [item["symbol"] for item in universe]
    assert symbols.count("BTCUSDC") == 1

    btc = next(item for item in universe if item["symbol"] == "BTCUSDC")
    assert btc["badges"] == ["Détenue", "Surveillance", "Watchlist", "Spot"]
    assert btc["sources"] == ["POSITION", "BINANCE_SPOT", "WATCH_CANDIDATE", "WATCHLIST"]
    assert btc["position_info"]["unrealized_pnl_pct"] == 2.4
    assert btc["position_info"]["id"] == 11
    assert btc["watch_info"]["id"] == 7
    assert get_symbol_sources("btcusdc", universe) == btc["sources"]

    sui = next(item for item in universe if item["symbol"] == "SUIUSDC")
    assert sui["badges"] == ["Opportunité", "Spot"]
    assert sui["opportunity_info"]["scan_score"] == 82


def test_market_universe_excludes_binance_dust_positions() -> None:
    universe = get_market_universe(
        positions=[
            {
                "symbol": "DOGEUSDC",
                "source": "BINANCE",
                "status": "ACTIVE",
                "is_active": 1,
                "current_value": 0.01,
            }
        ],
        watch_candidates=[],
        watchlist=[],
        opportunities=[],
    )

    assert universe == []


def test_market_universe_prioritizes_positions_then_high_watch_then_opportunities() -> None:
    universe = get_market_universe(
        positions=[
            {"symbol": "SOLUSDC", "source": "MANUAL", "status": "MANUAL", "is_active": 1},
        ],
        watch_candidates=[
            {"id": 1, "symbol": "XRPUSDC", "priority": "HIGH"},
            {"id": 2, "symbol": "ADAUSDC", "priority": "LOW"},
        ],
        watchlist=["ETHUSDC"],
        opportunities=[{"symbol": "SUIUSDC", "scan_score": 80}],
    )

    assert [item["symbol"] for item in universe] == ["SOLUSDC", "XRPUSDC", "ADAUSDC", "SUIUSDC", "ETHUSDC"]
    assert universe[0]["badges"] == ["Détenue", "Manuel"]


def test_market_universe_marks_alpha_watch_candidate() -> None:
    universe = get_market_universe(
        positions=[],
        watch_candidates=[{"id": 1, "symbol": "CKP", "priority": "HIGH", "market_source": "BINANCE_ALPHA"}],
        watchlist=[],
        opportunities=[],
    )

    assert universe[0]["symbol"] == "CKP"
    assert universe[0]["badges"] == ["Surveillance", "Alpha"]
    assert universe[0]["sources"] == ["BINANCE_ALPHA", "WATCH_CANDIDATE"]
    assert universe[0]["watch_info"]["market_source"] == "BINANCE_ALPHA"
