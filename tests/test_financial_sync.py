"""Financial integration tests: synthetic inputs, literal reference amounts, no private I/O."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import sqlite3
import threading

import pytest

from services import binance_sync
from services.binance_private_client import BinanceDataError, BinanceReadError
from services.binance_trade_history import (
    LeaseLost, SyncBusy, connect, fence, sync_lease, sync_market_history)
from services.history import init_db
from test_financial_reference import PagedClient, trade


def balance(asset="ABC",quantity="1"):
    return {"asset":asset,"free":quantity,"locked":"0","total":quantity}


@pytest.fixture
def prices(monkeypatch):
    values={"ABCUSDC":"110","XYZUSDC":"120","BTCUSDC":"30000"}
    monkeypatch.setattr(binance_sync,"get_current_price",lambda symbol:values.get(symbol))
    monkeypatch.setattr(binance_sync,"resolve_market_symbol",lambda asset:{"valid":False})
    return values


def rows(path,table="positions"):
    with sqlite3.connect(path) as connection:
        connection.row_factory=sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM "+table)]


def test_sold_out_asset_is_discovered_without_current_holdings(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade(),trade("SELL",quote="130",identity=2,time=2)]})
    result=binance_sync.sync_binance_positions(client,path)
    assert result["synced"]
    assert result["positions"]==[]
    assert result["closed"][0]["quantity"]==0
    assert result["closed"][0]["realized_pnl"]==30
    summary=binance_sync.get_pnl_summary(path)
    assert summary["realized_pnl"]==30
    assert summary["unrealized_pnl"]==0
    assert summary["total_pnl"]==30
    assert summary["portfolio_value_usdc"]==0
    assert summary["closed_positions_count"]==1


def test_price_failure_preserves_position_and_recovers(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade()]},[balance()])
    first=binance_sync.sync_binance_positions(client,path)
    assert first["positions"][0]["unrealized_pnl"]==10
    prices.clear()
    second=binance_sync.sync_binance_positions(client,path)
    position=second["positions"][0]
    assert position["status"]=="ACTIVE"
    assert position["quantity"]==1
    assert position["current_value"] is None
    assert position["unrealized_pnl"] is None
    assert position["last_known_current_value"]==110
    assert rows(path)[0]["current_value"]==110
    assert binance_sync.get_pnl_summary(path)["portfolio_value_usdc"] is None
    prices["ABCUSDC"]="120"
    recovered=binance_sync.sync_binance_positions(client,path)
    assert recovered["positions"][0]["unrealized_pnl"]==20
    assert recovered["positions"][0]["pnl_status"]=="ESTIMATED"


def test_account_failure_cannot_close_or_clear_wallet(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade()]},[balance()])
    binance_sync.sync_binance_positions(client,path)
    before=rows(path)
    last_sync=binance_sync.get_last_sync(path)
    def unavailable():
        raise BinanceReadError("TRANSPORT")
    client.get_non_zero_balances=unavailable
    with pytest.raises(BinanceReadError):
        binance_sync.sync_binance_positions(client,path)
    after=rows(path)
    assert after[0]["quantity"]==before[0]["quantity"]
    assert after[0]["status"]=="ACTIVE"
    assert after[0]["data_status"]=="TEMPORARILY_UNAVAILABLE"
    assert binance_sync.get_binance_positions(path)[0]["current_value"] is None
    assert binance_sync.get_pnl_summary(path)["total_pnl"] is None
    assert binance_sync.get_last_sync(path)==last_sync
    assert len(rows(path,"binance_balances"))==1


@pytest.mark.parametrize("bad",[
    None,{},[{"asset":"ABC","free":"1"}],
    [balance(),balance()],
    [balance(quantity="-1")],
    [{**balance(),"total":"2"}],
    [balance(quantity="NaN")],
])
def test_malformed_snapshot_does_not_replace_valid_data(tmp_path,prices,bad):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade()]},[balance()])
    binance_sync.sync_binance_positions(client,path)
    before=rows(path,"binance_balances")
    client.balances=bad
    with pytest.raises(BinanceDataError):
        binance_sync.sync_binance_positions(client,path)
    assert rows(path,"binance_balances")==before
    assert rows(path)[0]["status"]=="ACTIVE"


def test_manual_position_same_market_is_preserved_byte_for_field(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute("""INSERT INTO positions
            (symbol,quantity,average_buy_price,invested_amount,source,status,
             note,take_profit_1,created_at,updated_at)
            VALUES('ABCUSDC',7,20,140,'MANUAL','MANUAL','private synthetic note',999,'before','before')""")
    before=rows(path)[0]
    client=PagedClient({"ABCUSDC":[trade()]},[balance()])
    binance_sync.sync_binance_positions(client,path)
    after=rows(path)
    assert after[0]==before
    assert len(after)==2
    assert after[1]["source"]=="BINANCE"
    assert after[1]["quantity"]==1
    assert binance_sync.get_pnl_summary(path)["portfolio_value_usdc"]==110


def test_partial_market_failure_preserves_reliable_subset(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade()],"XYZUSDC":[trade(symbol="XYZUSDC")]},
                       [balance(),balance("XYZ")])
    client.fail=lambda symbol,cursor:symbol=="XYZUSDC"
    result=binance_sync.sync_binance_positions(client,path)
    assert not result["synced"]
    assert result["sync_status"]=="PARTIAL"
    by_symbol={row["symbol"]:row for row in result["positions"]}
    assert by_symbol["ABCUSDC"]["pnl_status"]=="ESTIMATED"
    assert by_symbol["ABCUSDC"]["unrealized_pnl"]==10
    assert by_symbol["XYZUSDC"]["pnl_status"]=="INCOMPLETE"
    assert by_symbol["XYZUSDC"]["invested_amount"] is None
    summary=binance_sync.get_pnl_summary(path)
    assert summary["unrealized_pnl"] is None
    assert summary["known_unrealized_pnl"]==10
    assert summary["portfolio_value_usdc"]==230
    assert summary["incomplete_positions_count"]==1
    client.fail=None
    recovered=binance_sync.sync_binance_positions(client,path)
    assert recovered["synced"]
    assert binance_sync.get_pnl_summary(path)["unrealized_pnl"]==30


def test_multiquote_sync_has_one_position_per_asset(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.executemany("INSERT INTO historical_fx_rates(asset,quote_asset,time,rate,source) VALUES(?,?,?,?,?)",
            [("EUR","USDC",2,"1.2","SYNTHETIC_REFERENCE"),("BTC","USDC",3,"30000","SYNTHETIC_REFERENCE")])
    items={"ABCUSDC":[trade()],
           "ABCEUR":[trade(symbol="ABCEUR",quote="90",time=2)],
           "ABCBTC":[trade("SELL",symbol="ABCBTC",qty="1.5",quote=".006",time=3)]}
    markets=[{"symbol":symbol,"base_asset":"ABC","quote_asset":quote} for symbol,quote in
             [("ABCUSDC","USDC"),("ABCEUR","EUR"),("ABCBTC","BTC")]]
    client=PagedClient(items,[balance(quantity=".5"),balance("BTC",".006")],markets)
    prices["ABCUSDC"]="120"
    result=binance_sync.sync_binance_positions(client,path)
    assert result["synced"]
    assert len(rows(path))==2
    abc=next(row for row in result["positions"] if row["base_asset"]=="ABC")
    assert abc["invested_amount"]==54
    assert abc["realized_pnl"]==26
    assert abc["unrealized_pnl"]==6
    summary=binance_sync.get_pnl_summary(path)
    assert summary["portfolio_value_usdc"]==240
    assert summary["total_pnl"]==32


def test_same_sync_is_idempotent_for_financial_values(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade()]},[balance()])
    first=binance_sync.sync_binance_positions(client,path)
    second=binance_sync.sync_binance_positions(client,path)
    assert len(rows(path,"binance_trades"))==1
    assert len(rows(path))==1
    for key in ("quantity","invested_amount","realized_pnl","unrealized_pnl","total_pnl"):
        assert first["positions"][0][key]==second["positions"][0][key]
    assert client.calls==[("ABCUSDC",0,1000),("ABCUSDC",2,1000),("ABCUSDC",2,1000)]


def test_malformed_page_is_atomic_and_retryable(tmp_path):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade(identity=1),trade(identity=2),trade(identity=3)]})
    original=client.get_my_trades
    def malformed(symbol,*,from_id=0,limit=1000):
        if from_id==3:
            return [trade(identity=3),{**trade(identity=4),"qty":"NaN"}]
        return original(symbol,from_id=from_id,limit=limit)
    client.get_my_trades=malformed
    with pytest.raises(BinanceDataError):
        sync_market_history("ABCUSDC",client,path,page_size=2)
    assert len(rows(path,"binance_trades"))==2
    assert rows(path,"binance_trade_sync")[0]["next_id"]==3
    client.get_my_trades=original
    assert sync_market_history("ABCUSDC",client,path,page_size=2)["complete"]
    assert len(rows(path,"binance_trades"))==3


def test_simultaneous_syncs_are_serialized_across_connections(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade()]},[balance()])
    entered=threading.Event()
    release=threading.Event()
    original=client.get_non_zero_balances
    def blocked():
        entered.set()
        assert release.wait(3),"test failed to release first synchronization"
        return original()
    client.get_non_zero_balances=blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(binance_sync.sync_binance_positions,client,path)
        try:
            assert entered.wait(3)
            with pytest.raises(SyncBusy):
                binance_sync.sync_binance_positions(client,path)
        finally:
            release.set()
        assert first.result(timeout=5)["synced"]
    assert len(rows(path,"binance_trades"))==1
    assert rows(path,"financial_sync_lock")==[]


def test_expired_lease_owner_cannot_write_or_delete_new_lease(tmp_path):
    path=tmp_path/"db.sqlite"
    first=sync_lease(path,clock=lambda:10)
    second=sync_lease(path,clock=lambda:200)
    old_owner=first.__enter__()
    new_owner=second.__enter__()
    try:
        with connect(path) as connection:
            with pytest.raises(LeaseLost):
                fence(connection,"BINANCE_SPOT",old_owner,clock=lambda:201)
        first.__exit__(None,None,None)
        with connect(path) as connection:
            fence(connection,"BINANCE_SPOT",new_owner,clock=lambda:201)
    finally:
        second.__exit__(None,None,None)


def test_never_synced_is_not_a_zero_performance(tmp_path):
    summary=binance_sync.get_pnl_summary(tmp_path/"db.sqlite")
    assert summary["total_pnl"] is None
    assert summary["portfolio_value_usdc"] is None
    assert summary["sync_status"]=="NEVER"


def test_no_invented_eur_exchange_rate(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade()]},[balance()])
    binance_sync.sync_binance_positions(client,path)
    summary=binance_sync.get_pnl_summary(path)
    assert summary["portfolio_value_eur"] is None
    assert summary["usdc_to_eur_rate_estimate"] is None


def test_failed_market_without_balance_still_makes_global_realized_incomplete(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade(),trade("SELL",quote="130",identity=2,time=2)],
                       "XYZUSDC":[]})
    client.fail=lambda symbol,cursor:symbol=="XYZUSDC"
    result=binance_sync.sync_binance_positions(client,path)
    assert result["sync_status"]=="PARTIAL"
    summary=binance_sync.get_pnl_summary(path)
    assert summary["realized_pnl"] is None
    assert summary["known_realized_pnl"]==30
    assert summary["total_pnl"] is None
    assert summary["portfolio_value_usdc"]==0


@pytest.mark.parametrize("code",["RATE_LIMIT","RETRY_LATER","SERVER","TRANSPORT","HTTP_401","HTTP_418"])
def test_global_outage_stops_market_loop_without_hammering_api(tmp_path,prices,code):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[],"XYZUSDC":[]},[balance()])
    calls=[]
    def fail(symbol,**kwargs):
        calls.append(symbol)
        raise BinanceReadError(code,retry_after=120)
    client.get_my_trades=fail
    with pytest.raises(BinanceReadError):
        binance_sync.sync_binance_positions(client,path)
    assert calls==["ABCUSDC"]
    assert rows(path,"binance_balances")==[]
    assert binance_sync.get_binance_status(path)["sync_status"]=="ERROR"


def test_invalid_fx_does_not_poison_unrelated_usdc_position(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute("""INSERT INTO historical_fx_rates(asset,quote_asset,time,rate,source)
            VALUES('EUR','USDC',2,'NaN','SYNTHETIC_INVALID')""")
    client=PagedClient({"ABCUSDC":[trade()],
        "XYZEUR":[trade(symbol="XYZEUR",quote="100",time=2)]},
        [balance(),balance("XYZ")],
        [{"symbol":"ABCUSDC","base_asset":"ABC","quote_asset":"USDC"},
         {"symbol":"XYZEUR","base_asset":"XYZ","quote_asset":"EUR"}])
    prices["XYZEUR"]="100"
    prices["EURUSDC"]="1.2"
    result=binance_sync.sync_binance_positions(client,path)
    assert result["invalid_historical_fx_records"]==1
    positions={row["base_asset"]:row for row in result["positions"]}
    assert positions["ABC"]["unrealized_pnl"]==10
    assert positions["ABC"]["pnl_status"]=="ESTIMATED"
    assert positions["XYZ"]["invested_amount"] is None
    assert positions["XYZ"]["unrealized_pnl"] is None


def test_foreign_cash_is_not_double_counted_or_assumed_at_parity(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[]},[balance("USDT","2")])
    prices["USDTUSDC"]=".99"
    result=binance_sync.sync_binance_positions(client,path)
    assert result["positions"]==[]
    summary=binance_sync.get_pnl_summary(path)
    assert summary["portfolio_value_usdc"]==1.98
    assert summary["cash_fx_cost_unknown_count"]==1
    assert summary["unrealized_pnl"] is None
    assert summary["total_pnl"] is None


def test_missing_cash_conversion_is_not_a_zero_value(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[]},[balance("EUR","2")])
    binance_sync.sync_binance_positions(client,path)
    summary=binance_sync.get_pnl_summary(path)
    assert summary["portfolio_value_usdc"] is None
    assert summary["known_portfolio_value_usdc"]==0
    assert summary["valuation_status"]=="INCOMPLETE"


def test_testnet_and_real_account_never_share_trade_identity_or_wallet(tmp_path,prices,monkeypatch):
    path=tmp_path/"db.sqlite"
    real=PagedClient({"ABCUSDC":[trade()]},[balance()])
    demo=PagedClient({"ABCUSDC":[trade(quote="5")]},[balance()])
    demo.config=type("Config",(),{"use_testnet":True})()
    binance_sync.sync_binance_positions(real,path)
    binance_sync.sync_binance_positions(demo,path)
    assert len(rows(path,"binance_trades"))==2
    assert len(rows(path))==2
    assert binance_sync.get_stored_trades("ABCUSDC",path)[0]["quote_qty"]=="100"
    assert binance_sync.get_stored_trades("ABCUSDC",path,exchange="BINANCE_SPOT_TESTNET")[0]["quote_qty"]=="5"
    assert binance_sync.get_pnl_summary(path)["total_pnl"]==10
    monkeypatch.setattr(binance_sync,"get_private_client_status",lambda:{"configured":True,"use_testnet":True})
    assert binance_sync.get_pnl_summary(path)["total_pnl"]==105
    assert binance_sync.get_pnl_summary(path)["scope"]=="BINANCE_SPOT_TESTNET"
    from services.personal_data import list_positions
    assert len(list_positions(path,enrich=False))==1


def test_alpha_usdt_price_requires_observed_conversion_and_no_invented_spot_pair(tmp_path,prices,monkeypatch):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[]},[balance("MORPHO","1")])
    monkeypatch.setattr(binance_sync,"resolve_market_symbol",
        lambda asset:{"valid":True,"market_source":"BINANCE_ALPHA","symbol":asset})
    monkeypatch.setattr(binance_sync,"fetch_alpha_market_data",
        lambda asset:{"symbol":asset,"current_price":2,"alpha_trading_symbol":"ALPHA_1USDT",
                      "ticker":{"lastPrice":"2"}})
    prices["USDTUSDC"]=".99"
    first=binance_sync.sync_binance_positions(client,path)
    assert first["positions"][0]["current_value"]==1.98
    assert first["positions"][0]["average_buy_price"] is None
    assert first["positions"][0]["price_status"]=="ESTIMATED"
    assert all(symbol!="MORPHOUSDC" for symbol,_,_ in client.calls)
    second=binance_sync.sync_binance_positions(client,path)
    assert second["synced"]
    assert second["market_errors"]==[]
    prices.pop("USDTUSDC")
    unavailable=binance_sync.sync_binance_positions(client,path)
    assert unavailable["positions"][0]["current_value"] is None
    assert unavailable["positions"][0]["status"]=="ACTIVE"


def test_alpha_unlabelled_token_fallback_price_is_not_a_usdc_valuation(tmp_path,prices,monkeypatch):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[]},[balance("MORPHO","1")])
    monkeypatch.setattr(binance_sync,"resolve_market_symbol",
        lambda asset:{"valid":True,"market_source":"BINANCE_ALPHA","symbol":asset})
    monkeypatch.setattr(binance_sync,"fetch_alpha_market_data",
        lambda asset:{"symbol":asset,"current_price":2,"alpha_trading_symbol":"ALPHA_1USDT"})
    prices["USDTUSDC"]=".99"
    result=binance_sync.sync_binance_positions(client,path)
    assert result["positions"][0]["current_value"] is None


def test_old_unknown_quote_trade_is_preserved_and_explicitly_incomplete(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute("""INSERT INTO binance_trades(symbol,order_id,trade_id,side,
            price,qty,quote_qty,commission,commission_asset,time,is_buyer,is_maker)
            VALUES('ABCBUSD',1,1,'BUY','100','1','100','0','BUSD',1,1,0)""")
    client=PagedClient({"ABCUSDC":[]})
    result=binance_sync.sync_binance_positions(client,path)
    assert result["sync_status"]=="PARTIAL"
    assert result["market_errors"]==[{"symbol":"ABCBUSD","code":"UNRESOLVED_OR_UNSUPPORTED_QUOTE"}]
    assert len(rows(path,"binance_trades"))==1
    assert binance_sync.get_pnl_summary(path)["total_pnl"] is None


def test_price_outage_keeps_business_source_in_market_universe():
    from services.market_universe import merge_symbols_from_sources
    position={"id":1,"symbol":"ABCUSDC","source":"BINANCE","is_active":1,"status":"ACTIVE",
              "quantity":1,"current_value":None,"average_buy_price":None,"data_status":"TEMPORARILY_UNAVAILABLE"}
    merged=merge_symbols_from_sources(positions=[position])
    assert len(merged)==1
    assert "POSITION" in merged[0]["sources"]
    assert merged[0]["position_info"]["current_value"] is None
    assert merged[0]["position_info"]["data_status"]=="TEMPORARILY_UNAVAILABLE"


def test_invalid_snapshot_does_not_fall_back_to_unverified_profit(tmp_path,prices):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade()]},[balance()])
    binance_sync.sync_binance_positions(client,path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE positions SET accounting_json='{broken'")
    position=binance_sync.get_binance_positions(path)[0]
    assert position["unrealized_pnl"] is None
    assert position["average_buy_price"] is None
    assert position["pnl_status"]=="INCOMPLETE"
    assert position["issues"]==["ACCOUNTING_SNAPSHOT_INVALID"]


def test_pagination_budget_is_explicit_and_resumable(tmp_path):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade(identity=i,time=i) for i in range(1,5)]})
    with pytest.raises(BinanceReadError) as info:
        sync_market_history("ABCUSDC",client,path,page_size=2,max_pages=1)
    assert info.value.code=="PAGINATION_BUDGET_EXCEEDED"
    assert len(rows(path,"binance_trades"))==2
    result=sync_market_history("ABCUSDC",client,path,page_size=2)
    assert result["complete"]
    assert len(rows(path,"binance_trades"))==4
