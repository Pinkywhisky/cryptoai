"""Durable job lifecycle, cancellation, failures, restart and protected API contracts."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import pytest
from fastapi.testclient import TestClient

import main
from auth_client import AuthenticatedClient
from services.binance_sync_jobs import SyncJobs
from services.binance_private_client import BinanceReadError
from services.binance_trade_history import connect, sync_lease
from services.binance_sync import get_stored_trades
from tools.simulate_sync_cost import InstrumentedClient
from test_financial_reference import trade
from test_financial_sync import balance

MARKETS=[{"symbol":s,"base_asset":s[:-4],"quote_asset":"USDC","status":"TRADING"}
         for s in ["ABCUSDC","XYZUSDC"]]


def manager(tmp_path,*,trades=None,balances=None,clock=time.time,markets=None):
    client=InstrumentedClient(markets or MARKETS,trades,balances)
    jobs=SyncJobs(tmp_path/"db.sqlite",client_factory=lambda:client,clock=clock)
    return jobs,client


def bootstrap(jobs,symbols=None,**kwargs):
    item=jobs.preview(symbols=symbols or [],full_scan=not symbols)
    return jobs.start(mode="bootstrap",preview_id=item["id"],confirmed=not symbols,start_thread=False,**kwargs)["job"]["id"]


def test_explicit_job_is_not_executed_during_start_and_runs_once(tmp_path):
    jobs,client=manager(tmp_path)
    result=jobs.start(start_thread=False)
    assert result["accepted"]
    assert client.events==[]
    identifier=result["job"]["id"]
    jobs.run(identifier)
    assert jobs.get(identifier)["status"]=="PARTIAL"
    calls=len(client.events)
    jobs.run(identifier)
    assert len(client.events)==calls
    assert jobs.get(identifier)["result"]["unexplored_markets"]==2


def test_bootstrap_requires_preview_and_full_confirmation(tmp_path):
    jobs,client=manager(tmp_path)
    with pytest.raises(ValueError,match="PREVIEW"):
        jobs.start(mode="bootstrap",start_thread=False)
    item=jobs.preview(full_scan=True)
    assert all(e["endpoint"]=="exchangeInfo" for e in client.events)
    with pytest.raises(ValueError,match="CONFIRMATION"):
        jobs.start(mode="bootstrap",preview_id=item["id"],start_thread=False)
    result=jobs.start(mode="bootstrap",preview_id=item["id"],confirmed=True,start_thread=False)
    jobs.run(result["job"]["id"])
    final=jobs.get(result["job"]["id"])
    assert final["status"]=="COMPLETED"
    assert final["processed"]==2 and final["remaining"]==0


def test_concurrent_double_trigger_has_one_durable_job(tmp_path):
    jobs,client=manager(tmp_path)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(lambda _:jobs.start(start_thread=False),range(5)))
    assert sum(r["accepted"] for r in results)==1
    assert len({r["job"]["id"] for r in results})==1
    assert not client.events


def test_start_returns_while_private_work_is_blocked_and_shutdown_cancels(tmp_path):
    jobs,client=manager(tmp_path)
    entered=threading.Event();release=threading.Event()
    def balances():
        entered.set()
        assert release.wait(5)
        return []
    client.get_non_zero_balances=balances
    started=time.perf_counter()
    result=jobs.start()
    assert time.perf_counter()-started<1
    assert entered.wait(2)
    assert jobs.get(result["job"]["id"])["status"]=="RUNNING"
    release.set()
    assert jobs.shutdown()
    assert jobs.get(result["job"]["id"])["status"]=="CANCELLED"


def test_cancel_queued_job_never_calls_binance(tmp_path):
    jobs,client=manager(tmp_path)
    identifier=jobs.start(start_thread=False)["job"]["id"]
    assert jobs.cancel(identifier)["status"]=="CANCELLED"
    jobs.run(identifier)
    assert not client.events


def test_cancellation_mid_page_writes_neither_page_nor_cursor(tmp_path):
    jobs,client=manager(tmp_path,trades={"ABCUSDC":[trade()]},balances=[balance()])
    identifier=bootstrap(jobs,["ABCUSDC"])
    def cancel(symbol,cursor):
        jobs.cancel(identifier)
    client.fail=cancel
    jobs.run(identifier)
    assert jobs.get(identifier)["status"]=="CANCELLED"
    assert get_stored_trades("ABCUSDC",jobs.path)==[]
    with connect(jobs.path) as c:
        assert c.execute("SELECT next_id FROM binance_trade_sync WHERE symbol='ABCUSDC'").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM financial_sync_lock").fetchone()[0]==0


def test_completed_page_survives_timeout_and_worker_restart(tmp_path):
    items=[trade(identity=i,time=i) for i in range(1,5)]
    jobs,client=manager(tmp_path,trades={"ABCUSDC":items})
    original=client.get_my_trades
    fail_enabled=[True]
    def paged(symbol,*,from_id=0,limit=1000):
        if from_id>=3 and fail_enabled[0]:
            raise BinanceReadError("TRANSPORT")
        return original(symbol,from_id=from_id,limit=2)
    client.get_my_trades=paged
    identifier=bootstrap(jobs,["ABCUSDC"])
    jobs.run(identifier)
    assert jobs.get(identifier)["status"]=="ERROR"
    assert len(get_stored_trades("ABCUSDC",jobs.path))==2
    assert jobs.get(identifier)["inserted"]==2
    fail_enabled[0]=False
    restarted=SyncJobs(jobs.path,client_factory=lambda:client)
    result=restarted.start(resume_id=identifier,start_thread=False)
    assert result["accepted"]
    restarted.run(identifier)
    assert len(get_stored_trades("ABCUSDC",jobs.path))==4
    final=restarted.get(identifier)
    assert final["inserted"]==4
    assert final["attempt"]==2


def test_interruption_between_markets_resumes_without_requerying_done_market(tmp_path):
    jobs,client=manager(tmp_path,trades={"ABCUSDC":[trade()],"XYZUSDC":[trade(symbol="XYZUSDC")]})
    identifier=bootstrap(jobs)
    def fail(symbol,cursor):
        if symbol=="XYZUSDC":
            raise BinanceReadError("TRANSPORT")
    client.fail=fail
    jobs.run(identifier)
    assert jobs.get(identifier)["processed"]==2
    client.fail=None;client.reset()
    jobs.start(resume_id=identifier,start_thread=False)
    jobs.run(identifier)
    assert {e["symbol"] for e in client.events if e["endpoint"]=="myTrades"}=={"XYZUSDC"}
    assert len(get_stored_trades("ABCUSDC",jobs.path))==1
    assert len(get_stored_trades("XYZUSDC",jobs.path))==1


@pytest.mark.parametrize("code",["RATE_LIMIT","HTTP_418","LOCAL_QUOTA","RETRY_LATER"])
def test_quota_pause_is_durable_and_cannot_be_resumed_early(tmp_path,code):
    now=[100.]
    jobs,client=manager(tmp_path,clock=lambda:now[0])
    identifier=bootstrap(jobs)
    def quota(symbol,cursor):
        raise BinanceReadError(code,retry_after=120)
    client.fail=quota
    jobs.run(identifier)
    assert jobs.get(identifier)["status"]=="PAUSED_QUOTA"
    assert jobs.get(identifier)["retry_at"]==220
    with pytest.raises(ValueError,match="SUSPENSION"):
        jobs.start(resume_id=identifier,start_thread=False)
    now[0]=221;client.fail=None
    jobs.start(resume_id=identifier,start_thread=False)
    jobs.run(identifier)
    assert jobs.get(identifier)["status"]=="COMPLETED"


def test_restart_marks_orphan_interrupted_without_automatic_private_calls(tmp_path):
    now=[100.]
    jobs,client=manager(tmp_path,clock=lambda:now[0])
    identifier=jobs.start(start_thread=False)["job"]["id"]
    with connect(jobs.path) as c:
        c.execute("UPDATE binance_sync_jobs SET status='RUNNING',owner='old' WHERE id=?",(identifier,))
        c.execute("INSERT INTO financial_sync_lock VALUES('BINANCE_SPOT','old',150)")
    now[0]=200
    restarted=SyncJobs(jobs.path,client_factory=lambda:client,clock=lambda:now[0])
    assert restarted.get(identifier)["status"]=="INTERRUPTED"
    assert not client.events
    assert restarted.start(resume_id=identifier,start_thread=False)["accepted"]


def test_stale_worker_cannot_replace_new_owner_result(tmp_path):
    jobs,client=manager(tmp_path)
    identifier=jobs.start(start_thread=False)["job"]["id"]
    with connect(jobs.path) as c:
        c.execute("UPDATE binance_sync_jobs SET status='RUNNING',owner='new' WHERE id=?",(identifier,))
    jobs._finish(identifier,"old","COMPLETED",result={"synced":True})
    assert jobs.get(identifier)["status"]=="RUNNING"


def test_direct_sync_lease_and_job_cannot_publish_concurrently(tmp_path):
    jobs,client=manager(tmp_path)
    identifier=jobs.start(start_thread=False)["job"]["id"]
    with sync_lease(jobs.path):
        jobs.run(identifier)
        assert jobs.get(identifier)["status"]=="INTERRUPTED"
        assert not client.events


def test_worker_errors_are_sanitized_and_release_resources(tmp_path):
    jobs,client=manager(tmp_path)
    def fail():
        raise RuntimeError("SYNTHETIC_PRIVATE_SECRET")
    client.get_non_zero_balances=fail
    identifier=jobs.start(start_thread=False)["job"]["id"]
    jobs.run(identifier)
    final=jobs.get(identifier)
    assert final["status"]=="ERROR"
    assert "SYNTHETIC_PRIVATE_SECRET" not in json.dumps(final)
    with connect(jobs.path) as c:
        assert not c.execute("SELECT * FROM financial_sync_lock").fetchall()
    with pytest.raises(Exception):
        c.execute("SELECT 1")


def test_new_trade_after_first_market_is_recovered_next_incremental(tmp_path):
    jobs,client=manager(tmp_path,trades={"ABCUSDC":[trade()]},balances=[balance()])
    added=[False]
    def arriving(symbol,cursor):
        if symbol=="XYZUSDC" and not added[0]:
            client.trades["ABCUSDC"].append(trade(identity=2,time=2))
            client.balances=[balance(quantity="2")]
            added[0]=True
    client.fail=arriving
    identifier=bootstrap(jobs)
    jobs.run(identifier)
    assert len(get_stored_trades("ABCUSDC",jobs.path))==1
    next_id=jobs.start(start_thread=False)["job"]["id"]
    jobs.run(next_id)
    assert len(get_stored_trades("ABCUSDC",jobs.path))==2
    assert jobs.get(next_id)["inserted"]==1


def test_async_routes_are_authenticated_csrf_protected_and_nonblocking(tmp_path,monkeypatch):
    jobs,client=manager(tmp_path,trades={"ABCUSDC":[trade()]},balances=[balance()])
    monkeypatch.setattr(main,"sync_jobs",jobs)
    anonymous=TestClient(main.app,base_url="http://localhost:8000")
    assert anonymous.post("/api/binance/sync").status_code==401
    assert anonymous.post("/api/binance/sync/preview",json={}).status_code==401
    with AuthenticatedClient(main.app) as http:
        preview=http.post("/api/binance/sync/preview",json={"symbols":["ABCUSDC"]})
        assert preview.status_code==200
        result=http.post("/api/binance/sync",json={"mode":"bootstrap","preview_id":preview.json()["id"]})
        assert result.status_code==202
        identifier=result.json()["job"]["id"]
        assert http.get("/api/binance/sync/jobs/"+identifier).status_code==200
        token=http.headers.pop("X-CSRF-Token")
        assert http.post("/api/binance/sync/jobs/"+identifier+"/cancel").status_code==403
        assert http.post("/api/binance/sync/preview",json={}).status_code==403
        http.headers["X-CSRF-Token"]=token
        assert http.post("/api/binance/sync/jobs/"+identifier+"/cancel").status_code==200


@pytest.mark.parametrize("body",[
    {"mode":"all"},{"full_scan":True},{"mode":"bootstrap"},
    {"preview_id":"secret-in-url"},{"confirmed":True},{"resume_id":"x"*32},
    {"mode":"bootstrap","preview_id":"a"*32,"confirmed":"true"},
])
def test_malformed_sync_requests_are_rejected_before_any_private_call(tmp_path,monkeypatch,body):
    jobs,client=manager(tmp_path)
    monkeypatch.setattr(main,"sync_jobs",jobs)
    with AuthenticatedClient(main.app) as http:
        assert http.post("/api/binance/sync",json=body).status_code==422
    assert not client.events


def test_job_progress_counts_overlap_duplicates_without_reimport(tmp_path):
    jobs,client=manager(tmp_path,trades={"ABCUSDC":[trade()]},balances=[balance(quantity="2")])
    from services.binance_trade_history import sync_market_history
    sync_market_history("ABCUSDC",client,jobs.path)
    second=trade(identity=2,time=2)
    def overlapping(symbol,*,from_id=0,limit=1000):
        client.record("myTrades",20,symbol=symbol,from_id=from_id)
        return [trade(),second] if from_id==2 else []
    client.get_my_trades=overlapping
    identifier=jobs.start(start_thread=False)["job"]["id"]
    jobs.run(identifier)
    final=jobs.get(identifier)
    assert final["inserted"]==1
    assert final["duplicates"]==1
    assert len(get_stored_trades("ABCUSDC",jobs.path))==2


def test_later_invalid_page_keeps_valid_previous_page_and_cursor(tmp_path):
    jobs,client=manager(tmp_path,balances=[balance()])
    identifier=bootstrap(jobs,["ABCUSDC"])
    def partial(symbol,*,from_id=0,limit=1000):
        return [trade()] if from_id==0 else [trade(identity=2,time=2),{"id":3}]
    client.get_my_trades=partial
    jobs.run(identifier)
    assert jobs.get(identifier)["status"]=="PARTIAL"
    assert jobs.get(identifier)["errors"][0]["error_code"]=="INVALID_INTEGER"
    assert jobs.get(identifier)["inserted"]==1
    assert len(get_stored_trades("ABCUSDC",jobs.path))==1
    with connect(jobs.path) as c:
        assert c.execute("SELECT next_id FROM binance_trade_sync").fetchone()[0]==2

def test_global_worker_serializes_real_and_testnet_jobs(tmp_path):
    from types import SimpleNamespace
    jobs,real=manager(tmp_path)
    first=jobs.start(start_thread=False)
    demo=InstrumentedClient(MARKETS)
    demo.config=SimpleNamespace(use_testnet=True)
    jobs.client_factory=lambda:demo
    second=jobs.start(start_thread=False)
    assert second["accepted"] is False
    assert second["job"]["id"]==first["job"]["id"]
    assert second["sync_status"]=="BUSY"

def test_error_count_remains_exact_when_error_details_are_bounded(tmp_path):
    jobs,client=manager(tmp_path)
    identifier=jobs.start(start_thread=False)["job"]["id"]
    with connect(jobs.path) as c:
        c.executemany("INSERT INTO binance_job_markets(job_id,symbol,status,error_code) VALUES(?,?,'ERROR','UNKNOWN_SYMBOL')",
                      [(identifier,f"SYMBOL{i:04}USDC") for i in range(105)])
    result=jobs.get(identifier)
    assert result["error_count"]==105
    assert len(result["errors"])==100
    assert result["errors_truncated"] is True
