"""FastAPI financial responses keep auth/CSRF and uncertainty semantics."""
from fastapi.testclient import TestClient

import main
from auth_client import AuthenticatedClient
from services import binance_sync
from services.binance_sync_jobs import SyncJobs
from test_financial_reference import PagedClient, trade
from test_financial_sync import balance


def test_financial_sync_and_summary_through_real_protected_routes(tmp_path,monkeypatch):
    path=tmp_path/"db.sqlite"
    fake=PagedClient({"ABCUSDC":[trade()]},[balance()])
    monkeypatch.setattr(binance_sync,"get_current_price",lambda symbol:"110")
    jobs=SyncJobs(path,client_factory=lambda:fake)
    monkeypatch.setattr(main,"sync_jobs",jobs)
    monkeypatch.setattr(main,"get_pnl_summary",lambda:binance_sync.get_pnl_summary(path))
    monkeypatch.setattr(main,"get_binance_positions",lambda:binance_sync.get_binance_positions(path))
    anonymous=TestClient(main.app,base_url="http://localhost:8000")
    assert anonymous.post("/api/binance/sync").status_code==401
    assert anonymous.get("/api/binance/pnl").status_code==401
    with AuthenticatedClient(main.app) as client:
        preview=client.post("/api/binance/sync/preview",json={"symbols":["ABCUSDC"]}).json()
        result=client.post("/api/binance/sync",json={"mode":"bootstrap","preview_id":preview["id"]})
        assert result.status_code==202
        identifier=result.json()["job"]["id"]
        worker=jobs._threads.get(identifier)
        if worker:
            worker.join(5)
        assert jobs.get(identifier)["status"]=="COMPLETED"
        result=client.get("/api/binance/pnl")
        assert result.status_code==200
        assert result.json()["unrealized_pnl"]==10
        assert result.json()["pnl_status"]=="ESTIMATED"
        assert result.json()["funding_history_status"]=="NOT_DOCUMENTED"
        position=client.get("/api/binance/positions").json()[0]
        assert position["invested_amount"]==100
        assert position["decimal_values"]["invested_remaining"]=="100"
        # Remove only CSRF from an authenticated client; no production bypass.
        client.headers.pop("X-CSRF-Token")
        assert client.post("/api/binance/sync").status_code==403


def test_unknown_financials_are_json_null_not_zero(tmp_path,monkeypatch):
    path=tmp_path/"db.sqlite"
    fake=PagedClient({"ABCUSDC":[]},[balance()])
    monkeypatch.setattr(binance_sync,"get_current_price",lambda symbol:None)
    monkeypatch.setattr(binance_sync,"resolve_market_symbol",lambda asset:{"valid":False})
    binance_sync.sync_binance_positions(fake,path)
    monkeypatch.setattr(main,"get_pnl_summary",lambda:binance_sync.get_pnl_summary(path))
    monkeypatch.setattr(main,"get_binance_positions",lambda:binance_sync.get_binance_positions(path))
    with AuthenticatedClient(main.app) as client:
        summary=client.get("/api/binance/pnl")
        assert summary.status_code==200
        assert summary.json()["total_pnl"] is None
        assert summary.json()["portfolio_value_usdc"] is None
        positions=client.get("/api/binance/positions")
        assert positions.status_code==200
        assert positions.json()[0]["current_value"] is None
        assert positions.json()[0]["average_buy_price"] is None
        assert "NaN" not in summary.text
        assert "Infinity" not in summary.text
