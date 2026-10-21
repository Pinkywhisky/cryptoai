"""Selection must save API calls without claiming unexplored history is complete."""
from decimal import Decimal
import json
import pytest
from services import binance_sync
from services import binance_sync_planner as planner
from services.binance_private_client import BinanceReadError
from services.binance_trade_history import connect, sync_market_history
from services.history import init_db
from tools.simulate_sync_cost import InstrumentedClient, catalog, run_comparison
from test_financial_reference import trade
from test_financial_sync import balance


def test_first_incremental_with_1666_markets_does_not_scan_them(tmp_path):
    path=tmp_path/"db.sqlite"
    client=InstrumentedClient(catalog(),balances=[balance("COIN0000")])
    result=binance_sync.sync_binance_positions(client,path)
    assert result["markets_examined"]==1
    assert result["unexplored_markets"]==1665
    assert result["discovery_status"]=="INCOMPLETE"
    assert result["sync_status"]=="PARTIAL"
    assert client.metrics()["private_requests"]==3
    assert result["positions"][0]["invested_amount"] is None


def test_no_holdings_cannot_discover_previously_sold_markets(tmp_path):
    client=InstrumentedClient(catalog(),trades={"COIN0000USDC":[trade(symbol="COIN0000USDC")]})
    result=binance_sync.sync_binance_positions(client,tmp_path/"db.sqlite")
    assert result["markets_examined"]==0
    assert result["unexplored_markets"]==1666
    assert not result["synced"]
    assert client.metrics()["private_requests"]==2


def test_known_markets_incremental_avoids_full_catalog_and_reuses_cache(tmp_path):
    path=tmp_path/"db.sqlite";markets=catalog()
    client=InstrumentedClient(markets,{"COIN0000USDC":[trade(symbol="COIN0000USDC")]})
    sync_market_history("COIN0000USDC",client,path)
    planner.load_catalog(client,path,"BINANCE_SPOT")
    client.reset()
    result=binance_sync.sync_binance_positions(client,path)
    assert result["markets_selected"]==1
    assert client.metrics()["private_requests"]==3
    assert not any(e["endpoint"]=="exchangeInfo" for e in client.events)
    assert client.events[1]["from_id"]==2


def test_full_bootstrap_reaches_sold_out_asset_and_preserves_realized(tmp_path):
    path=tmp_path/"db.sqlite"
    symbol="COIN0000USDC"
    client=InstrumentedClient(catalog(4),{symbol:[trade(symbol=symbol),trade("SELL",symbol=symbol,identity=2,time=2,quote="130")]})
    result=binance_sync.sync_binance_positions(client,path,mode="bootstrap",full_scan=True)
    assert result["synced"]
    assert result["closed"][0]["realized_pnl"]==30
    assert result["unexplored_markets"]==0
    client.reset()
    again=binance_sync.sync_binance_positions(client,path)
    assert again["synced"]
    assert client.metrics()["markets_examined"]==1
    summary=binance_sync.get_pnl_summary(path)
    assert summary["realized_pnl"] is None
    assert summary["known_realized_pnl"]==30
    assert summary["markets_unrefreshed"]==3
    assert summary["historical_coverage_as_of"]


def test_targeted_bootstrap_only_queries_previewed_markets(tmp_path):
    client=InstrumentedClient(catalog(10),balances=[balance("COIN0009")])
    result=binance_sync.sync_binance_positions(client,tmp_path/"db.sqlite",mode="bootstrap",symbols=("COIN0000USDC",))
    symbols={e["symbol"] for e in client.events if e["endpoint"]=="myTrades"}
    assert symbols=={"COIN0000USDC"}
    assert not result["synced"]
    assert result["unexplored_markets"]==9


def test_preview_uses_no_private_calls_and_full_scope_needs_confirmation(tmp_path):
    path=tmp_path/"db.sqlite"
    client=InstrumentedClient(catalog())
    item=planner.preview(client,path,"BINANCE_SPOT",full_scan=True,clock=lambda:100)
    assert len(item["symbols"])==1666
    assert item["cost"]["private_requests_min"]==1668
    assert item["cost"]["estimated_weight"]==33380  # 1666*20 + 2*20 + catalog*20.
    assert item["requires_confirmation"]
    assert all(e["endpoint"]=="exchangeInfo" for e in client.events)
    with connect(path) as connection:
        with pytest.raises(ValueError,match="CONFIRMATION"):
            planner.consume_preview(connection,item["id"],"BINANCE_SPOT",clock=lambda:100)
        assert planner.consume_preview(connection,item["id"],"BINANCE_SPOT",confirmed=True,clock=lambda:100)["scope"]=="CATALOG"


def test_preview_targeting_and_expiry_do_not_expand_scope(tmp_path):
    path=tmp_path/"db.sqlite"
    client=InstrumentedClient(catalog(5))
    item=planner.preview(client,path,"BINANCE_SPOT",symbols=("COIN0001USDC",),clock=lambda:100)
    assert item["symbols"]==["COIN0001USDC"]
    with connect(path) as connection:
        with pytest.raises(ValueError,match="EXPIRED"):
            planner.consume_preview(connection,item["id"],"BINANCE_SPOT",clock=lambda:1001)
        with pytest.raises(ValueError):
            planner.consume_preview(connection,item["id"],"BINANCE_SPOT_TESTNET",clock=lambda:100)


def test_break_market_history_is_kept_and_reactivation_resets_review(tmp_path):
    path=tmp_path/"db.sqlite"
    rows=catalog(1);rows[0]["status"]="BREAK"
    client=InstrumentedClient(rows,{"COIN0000USDC":[trade(symbol="COIN0000USDC")]})
    planner.load_catalog(client,path,"BINANCE_SPOT",clock=lambda:100)
    sync_market_history("COIN0000USDC",client,path)
    planner.record_market(path,"BINANCE_SPOT","COIN0000USDC",success=True,inserted=1,clock=lambda:100)
    markets={r["symbol"]:r for r in rows}
    plan=planner.select_markets(path,"BINANCE_SPOT",markets,[],clock=lambda:101)
    assert plan["deferred"]==["COIN0000USDC"]
    assert len(binance_sync.get_stored_trades("COIN0000USDC",path))==1
    client.markets=[dict(rows[0],status="TRADING")]
    new,_,_=planner.load_catalog(client,path,"BINANCE_SPOT",force=True,clock=lambda:102)
    plan=planner.select_markets(path,"BINANCE_SPOT",{r["symbol"]:r for r in new},[],clock=lambda:103)
    assert list(plan["selected"])==["COIN0000USDC"]


def test_unrecognized_market_is_not_deleted_and_can_be_retried_explicitly(tmp_path):
    path=tmp_path/"db.sqlite";client=InstrumentedClient(catalog(2))
    planner.load_catalog(client,path,"BINANCE_SPOT",clock=lambda:100)
    planner.record_market(path,"BINANCE_SPOT","COIN0000USDC",success=False,error="UNKNOWN_SYMBOL",clock=lambda:100)
    with connect(path) as c:
        c.execute("UPDATE binance_market_catalog SET has_trades=1 WHERE symbol='COIN0000USDC'")
    sync_market_history("COIN0000USDC",client,path)
    markets={r["symbol"]:r for r in client.markets}
    plan=planner.select_markets(path,"BINANCE_SPOT",markets,[],symbols=("COIN0000USDC",),clock=lambda:101)
    assert "COIN0000USDC" in plan["selected"]


def test_stale_catalog_is_reported_but_rate_limit_is_not_hidden(tmp_path):
    path=tmp_path/"db.sqlite";client=InstrumentedClient(catalog(2))
    planner.load_catalog(client,path,"BINANCE_SPOT",clock=lambda:100)
    def fail():
        raise BinanceReadError("INVALID_MARKET_CATALOG")
    client.get_spot_markets=fail
    _,fresh,_=planner.load_catalog(client,path,"BINANCE_SPOT",clock=lambda:30000)
    assert not fresh
    def limited():
        raise BinanceReadError("RATE_LIMIT",retry_after=120)
    client.get_spot_markets=limited
    with pytest.raises(BinanceReadError):
        planner.load_catalog(client,path,"BINANCE_SPOT",clock=lambda:30000)


def test_new_catalog_market_invalidates_global_coverage_without_losing_old_trades(tmp_path):
    path=tmp_path/"db.sqlite";client=InstrumentedClient(catalog(1))
    assert binance_sync.sync_binance_positions(client,path,mode="bootstrap",full_scan=True)["synced"]
    client.markets=catalog(2)
    planner.load_catalog(client,path,"BINANCE_SPOT",force=True)
    result=binance_sync.sync_binance_positions(client,path)
    assert result["unexplored_markets"]==1
    assert not result["synced"]


@pytest.mark.parametrize("count,page_size,pages",[(0,1000,1),(1,1000,2),(1000,1000,2),(1001,1000,3),(2400,100,25)])
def test_cost_simulator_counts_terminal_page_independently(count,page_size,pages):
    result=planner.estimate_cost(["ABCUSDC"],trade_counts={"ABCUSDC":count},page_size=page_size)
    assert result["my_trades_pages"]==pages
    assert result["estimated_weight"]==pages*20+40


@pytest.mark.parametrize("bad",[
    [],[{"symbol":"BADUSDC","base_asset":"OTHER","quote_asset":"USDC"}],
    [{"symbol":"ABCUSDC","base_asset":"ABC","quote_asset":"USDC"}]*2,
    [{"symbol":"ABCUSDC","base_asset":False,"quote_asset":"USDC"}],
])
def test_malformed_catalog_never_makes_discovery_complete(bad):
    with pytest.raises(BinanceReadError):
        planner.validate_catalog(bad)


def test_comparison_observes_reduction_and_sqlite_read_timings(tmp_path,record_property):
    results=run_comparison(tmp_path/"comparison",market_count=1666,known_count=3)
    assert results["before"]["private_requests"]==1668
    assert results["after"]["private_requests"]==5
    assert results["before"]["estimated_documented_weight"]==33380
    assert results["after"]["estimated_documented_weight"]==100
    assert results["before"]["markets_examined"]==1666
    assert results["after"]["markets_examined"]==3
    assert results["before"]["transactions_imported"]==results["after"]["transactions_imported"]==0
    assert results["before"]["sync_status"]==results["after"]["sync_status"]=="COMPLETE"
    record_property("sync_cost_comparison",json.dumps(results))

def test_fully_sold_new_market_between_incrementals_is_not_hidden_as_complete(tmp_path):
    path=tmp_path/"db.sqlite"
    markets=[{"symbol":s,"base_asset":s[:-4],"quote_asset":"USDC","status":"TRADING"}
             for s in ["ABCUSDC","XYZUSDC"]]
    client=InstrumentedClient(markets,{"ABCUSDC":[trade(),trade("SELL",quote="130",identity=2,time=2)]})
    binance_sync.sync_binance_positions(client,path,mode="bootstrap",full_scan=True)
    assert binance_sync.get_pnl_summary(path)["realized_pnl"]==30
    client.trades["XYZUSDC"]=[trade(symbol="XYZUSDC"),trade("SELL",symbol="XYZUSDC",quote="150",identity=2,time=2)]
    incremental=binance_sync.sync_binance_positions(client,path)
    assert incremental["markets_unrefreshed"]==1
    summary=binance_sync.get_pnl_summary(path)
    assert summary["realized_pnl"] is None
    assert summary["known_realized_pnl"]==30
    binance_sync.sync_binance_positions(client,path,mode="bootstrap",full_scan=True)
    assert binance_sync.get_pnl_summary(path)["realized_pnl"]==80

def test_legacy_position_with_null_market_source_is_still_followed(tmp_path):
    path=tmp_path/"db.sqlite"
    client=InstrumentedClient(catalog(1),balances=[balance("COIN0000")])
    binance_sync.sync_binance_positions(client,path)
    with connect(path) as c:
        c.execute("UPDATE positions SET market_source=NULL")
    client.balances=[]
    client.reset()
    binance_sync.sync_binance_positions(client,path)
    assert client.metrics()["markets_examined"]==1


def test_explicit_legacy_service_symbols_keep_case_normalization(tmp_path):
    client=InstrumentedClient(catalog(1))
    result=binance_sync.sync_binance_positions(client,tmp_path/"db.sqlite",symbols=("coin0000usdc",))
    assert result["markets_selected"]==1
    assert client.metrics()["markets_examined"]==1
