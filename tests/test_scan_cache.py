import services.scanner as scanner


def test_cached_scan_reuses_recent_results(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_scan_watchlist_multi(watchlist=None, limit=120, save_history=True):
        calls["count"] += 1
        return [{"symbol": "BTCUSDC", "global_score": 50}]

    monkeypatch.setattr(scanner, "scan_watchlist_multi", fake_scan_watchlist_multi)
    monkeypatch.setattr(scanner, "last_scan_results", None)
    monkeypatch.setattr(scanner, "last_scan_timestamp", None)

    first = scanner.get_cached_scan(watchlist=["BTCUSDC"], force=False)
    second = scanner.get_cached_scan(watchlist=["BTCUSDC"], force=False)

    assert first["results"] == second["results"]
    assert calls["count"] == 1


def test_cached_scan_force_refreshes(monkeypatch) -> None:
    calls = {"count": 0}

    def fake_scan_watchlist_multi(watchlist=None, limit=120, save_history=True):
        calls["count"] += 1
        return [{"symbol": "BTCUSDC", "global_score": calls["count"]}]

    monkeypatch.setattr(scanner, "scan_watchlist_multi", fake_scan_watchlist_multi)
    monkeypatch.setattr(scanner, "last_scan_results", None)
    monkeypatch.setattr(scanner, "last_scan_timestamp", None)

    scanner.get_cached_scan(watchlist=["BTCUSDC"], force=False)
    refreshed = scanner.get_cached_scan(watchlist=["BTCUSDC"], force=True)

    assert refreshed["results"][0]["global_score"] == 2


def test_reset_scan_cache_restores_default_watchlist(monkeypatch) -> None:
    monkeypatch.setattr(scanner, "last_scan_results", [{"symbol": "BTCUSDC"}])
    monkeypatch.setattr(scanner, "last_scan_timestamp", object())
    scanner.WATCHLIST[:] = ["DOGEUSDC"]

    result = scanner.reset_scan_cache(reset_watchlist=False)

    assert result["last_scan_results"] is None
    assert result["last_scan_timestamp"] is None
    assert scanner.WATCHLIST == scanner.DEFAULT_WATCHLIST


def test_reset_scan_cache_can_empty_watchlist() -> None:
    scanner.WATCHLIST[:] = ["DOGEUSDC"]

    result = scanner.reset_scan_cache(reset_watchlist=True)

    assert result["watchlist"] == []
    assert scanner.WATCHLIST == []
    scanner.reset_scan_cache(reset_watchlist=False)
