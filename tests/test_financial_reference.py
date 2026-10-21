"""Independent synthetic accounting references: expected amounts are literal arithmetic."""
from decimal import Decimal
import sqlite3

import pytest

from services.pnl import calculate_fifo_pnl, decimal_value
from services import binance_sync
from services.binance_trade_history import sync_market_history
from services.history import init_db


def trade(side="BUY",qty="1",quote="100",*,symbol="ABCUSDC",identity=1,time=1,
          fee="0",fee_asset="USDC"):
    return {"symbol":symbol,"id":identity,"orderId":identity,"isBuyer":side=="BUY",
            "isMaker":False,"side":side,"qty":qty,
            "price":str(Decimal(quote)/Decimal(qty)),"quoteQty":quote,
            "commission":fee,"commissionAsset":fee_asset,"time":time}


def assert_amount(result,key,expected):
    actual=result["decimal_values"][key]
    if expected is None:
        assert actual is None
        assert result[key] is None
    else:
        assert Decimal(actual)==Decimal(expected), (key,actual,expected)


@pytest.mark.parametrize("trades,balance,price,realized,invested,latent",[
    ([trade(),trade("SELL",quote="130",identity=2,time=2)],"0",None,"30","0","0"),
    ([trade(qty="2",quote="20"),trade(qty="3",quote="60",identity=2,time=2),
      trade("SELL",qty="3",quote="90",identity=3,time=3)],"2","25","50","40","10"),
    ([trade(qty="10",quote="100",fee="1",fee_asset="ABC")],"9","20","0","100","80"),
    ([trade(qty="10",quote="100",fee="1")],"10","20","0","101","99"),
    ([trade(qty="10",quote="100"),trade("SELL",qty="4",quote="60",fee="1",identity=2,time=2)],
     "6","20","19","60","60"),
    ([trade(qty="10",quote="100"),trade("SELL",qty="4",quote="60",fee="1",fee_asset="ABC",identity=2,time=2)],
     "5","20","10","50","50"),
    ([trade(qty="0.000000000001",quote="0.0000000001")],"0.000000000001","200","0","0.0000000001","0.0000000001"),
])
def test_independent_fifo_references(trades,balance,price,realized,invested,latent):
    result=calculate_fifo_pnl(trades,"ABCUSDC",price,balance)
    for key,expected in {"realized_pnl":realized,"invested_remaining":invested,
                         "unrealized_pnl":latent,"remaining_quantity":balance}.items():
        assert_amount(result,key,expected)
    assert result["pnl_status"]=="EXACT"


def test_missing_cost_is_not_profit():
    result=calculate_fifo_pnl([],"ABCUSDC","10","2")
    assert_amount(result,"current_value","20")
    for key in ("invested_remaining","average_remaining_cost","unrealized_pnl","total_pnl"):
        assert_amount(result,key,None)
    assert result["cost_basis_status"]=="UNKNOWN"
    assert "BALANCE_MISMATCH" in result["issues"]


def test_missing_price_is_not_total_loss():
    result=calculate_fifo_pnl([trade()],"ABCUSDC",None,"1")
    assert_amount(result,"invested_remaining","100")
    assert_amount(result,"current_value",None)
    assert_amount(result,"unrealized_pnl",None)
    assert result["price_status"]=="UNAVAILABLE"


def test_uncovered_sale_cannot_be_exact_realized():
    result=calculate_fifo_pnl([trade("SELL",quote="120")],"ABCUSDC","120","0")
    assert_amount(result,"realized_pnl",None)
    assert "SALE_WITH_UNKNOWN_COST" in result["issues"]


def test_unknown_commission_conversion_is_not_zero():
    result=calculate_fifo_pnl([trade(fee="0.01",fee_asset="BNB")],"ABCUSDC","100","1")
    assert_amount(result,"invested_remaining",None)
    assert "MISSING_COMMISSION_CONVERSION" in result["issues"]


def test_documented_historical_fee_conversion():
    # 100 USDC purchase + .01 BNB at 300 USDC/BNB = 103 cost.
    result=calculate_fifo_pnl([trade(fee="0.01",fee_asset="BNB")],"ABCUSDC","110","1",
        historical_rates={("BNB","USDC",1):"300"})
    assert_amount(result,"invested_remaining","103")
    assert_amount(result,"unrealized_pnl","7")


def test_current_fee_conversion_cannot_replace_historical():
    result=calculate_fifo_pnl([trade(fee="0.01",fee_asset="BNB")],"ABCUSDC","110","1",
        historical_rates={("BNB","USDC",2):"300"})
    assert_amount(result,"invested_remaining",None)


def test_multiquote_fifo_has_one_inventory():
    # 1 ABC for 100 USDC + 1 ABC for 90 EUR at 1.2 USDC/EUR.
    # Sell 1.5 ABC for .006 BTC at 30000 => 180 proceeds.
    # FIFO sold cost =100+.5*108=154; realized=26; remaining=.5 at54.
    items=[trade(),trade(symbol="ABCEUR",quote="90",identity=1,time=2),
           trade("SELL",symbol="ABCBTC",qty="1.5",quote="0.006",identity=1,time=3)]
    result=calculate_fifo_pnl(items,"ABCUSDC","120",".5",
        historical_rates={("EUR","USDC",2):"1.2",("BTC","USDC",3):"30000"})
    assert_amount(result,"realized_pnl","26")
    assert_amount(result,"invested_remaining","54")
    assert_amount(result,"unrealized_pnl","6")


def test_stablecoins_are_not_assumed_at_parity():
    result=calculate_fifo_pnl([trade(symbol="ABCUSDT")],"ABCUSDC","110","1")
    assert_amount(result,"invested_remaining",None)
    assert "MISSING_TRADE_CONVERSION" in result["issues"]


def test_spending_crypto_quote_consumes_its_fifo():
    items=[trade(symbol="BTCUSDC",qty="1",quote="100"),
           trade(symbol="ABCBTC",qty="1",quote=".25",identity=2,time=2)]
    result=calculate_fifo_pnl(items,"BTCUSDC","200",".75",
        historical_rates={("BTC","USDC",2):"200"})
    assert_amount(result,"realized_pnl","25")
    assert_amount(result,"invested_remaining","75")
    assert_amount(result,"unrealized_pnl","75")


def test_third_asset_fee_disposal_consumes_inventory():
    items=[trade(symbol="BNBUSDC",quote="100"),
           trade(symbol="ABCUSDC",quote="10",fee=".01",fee_asset="BNB",identity=2,time=2)]
    result=calculate_fifo_pnl(items,"BNBUSDC","200",".99",
        historical_rates={("BNB","USDC",2):"200"})
    assert_amount(result,"realized_pnl","1")
    assert_amount(result,"invested_remaining","99")


@pytest.mark.parametrize("balance",["2",".5","0"])
def test_balance_reconciliation_never_invents_cost(balance):
    result=calculate_fifo_pnl([trade()],"ABCUSDC","100",balance)
    assert_amount(result,"invested_remaining",None)
    assert_amount(result,"unrealized_pnl",None)
    assert result["is_estimated"]


def test_identical_duplicates_are_idempotent():
    item=trade()
    result=calculate_fifo_pnl([item,dict(item)],"ABCUSDC","100","1")
    assert_amount(result,"remaining_quantity","1")
    assert_amount(result,"invested_remaining","100")


def test_conflicting_duplicate_is_explicit():
    with pytest.raises(ValueError,match="Conflicting duplicate"):
        calculate_fifo_pnl([trade(),trade(quote="999")],"ABCUSDC","100","1")


class PagedClient:
    configured=True

    def __init__(self,trades=None,balances=None,markets=None):
        self.trades=trades or {}
        self.balances=balances or []
        self.markets=markets or [{"symbol":symbol,"base_asset":symbol[:-4],"quote_asset":"USDC"}
                                 for symbol in self.trades]
        self.calls=[]
        self.fail=None

    def get_my_trades(self,symbol,*,from_id=0,limit=1000):
        self.calls.append((symbol,from_id,limit))
        if self.fail and self.fail(symbol,from_id):
            raise BinanceReadError("SIMULATED_INTERRUPTION")
        return [row for row in self.trades.get(symbol,[]) if row["id"]>=from_id][:limit]

    def get_non_zero_balances(self):
        return self.balances

    def get_spot_markets(self):
        return self.markets


from services.binance_private_client import BinanceReadError


def test_market_identity_collisions_and_replay(tmp_path):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade()],"XYZUSDC":[trade(symbol="XYZUSDC")]})
    for symbol in client.trades:
        sync_market_history(symbol,client,path)
        sync_market_history(symbol,client,path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM binance_trades").fetchone()[0]==2


def test_interruption_resume_and_process_restart(tmp_path):
    path=tmp_path/"db.sqlite"
    client=PagedClient({"ABCUSDC":[trade(identity=i,time=i) for i in range(1,6)]})
    client.fail=lambda symbol,cursor:cursor>=3
    with pytest.raises(BinanceReadError):
        sync_market_history("ABCUSDC",client,path,page_size=2)
    assert len(binance_sync.get_stored_trades("ABCUSDC",path))==2
    restarted=PagedClient(client.trades)
    result=sync_market_history("ABCUSDC",restarted,path,page_size=2)
    assert restarted.calls[0][1]==3
    assert result["complete"]
    assert len(binance_sync.get_stored_trades("ABCUSDC",path))==5


def test_pagination_repeating_page_fails_without_loop(tmp_path):
    client=PagedClient({"ABCUSDC":[trade()]})
    client.get_my_trades=lambda *args,**kwargs:[trade()]
    with pytest.raises(BinanceReadError) as error:
        sync_market_history("ABCUSDC",client,tmp_path/"db.sqlite",max_pages=5)
    assert error.value.code=="PAGINATION_NO_PROGRESS"


def test_same_millisecond_cross_market_fifo_order_is_not_claimed_exact():
    items=[trade(qty="1",quote="100",symbol="ABCUSDC",time=1),
           trade(qty="1",quote="200",symbol="ABCUSDT",time=1)]
    result=calculate_fifo_pnl(items,"ABCUSDC","200","2",
        historical_rates={("USDT","USDC",1):"1"})
    assert "AMBIGUOUS_EVENT_ORDER" in result["issues"]
    assert result["pnl_status"]=="INCOMPLETE"
    assert_amount(result,"invested_remaining",None)


def test_missing_base_market_still_validates_currency_metadata():
    result=calculate_fifo_pnl([trade(symbol="ABCUSDT")],"ABCUSDC","100","1")
    assert result["quote_asset"]=="USDC"
    assert "MISSING_TRADE_CONVERSION" in result["issues"]


def test_unknown_cost_deposit_never_becomes_free_profit():
    result=calculate_fifo_pnl([],"ABCUSDC","100","2",movements=[
        {"id":"deposit-1","source":"SYNTHETIC","type":"TRANSFER_IN",
         "asset":"ABC","quantity":"2","time":0,"cost_basis":None}])
    assert_amount(result,"current_value","200")
    assert_amount(result,"invested_remaining",None)
    assert_amount(result,"unrealized_pnl",None)
    assert result["pnl_status"]=="INCOMPLETE"


def test_documented_transfer_in_cost_and_partial_sale():
    # 2 units acquired elsewhere for a documented 100 USDC; one sold for 70.
    result=calculate_fifo_pnl([trade("SELL",quote="70",time=2)],"ABCUSDC","80","1",movements=[
        {"id":"deposit-1","type":"TRANSFER_IN","asset":"ABC","quantity":"2",
         "time":1,"cost_basis":"100","cost_basis_quote":"USDC"}])
    assert_amount(result,"realized_pnl","20")
    assert_amount(result,"invested_remaining","50")
    assert_amount(result,"unrealized_pnl","30")


def test_withdrawal_moves_cost_without_realizing_a_sale():
    result=calculate_fifo_pnl([trade(qty="2",quote="100")],"ABCUSDC","80","1",movements=[
        {"id":"withdraw-1","type":"TRANSFER_OUT","asset":"ABC","quantity":"1","time":2}])
    assert_amount(result,"realized_pnl","0")
    assert_amount(result,"invested_remaining","50")
    assert_amount(result,"transferred_cost_basis","50")
    assert_amount(result,"unrealized_pnl","30")


def test_sale_of_unknown_transfer_cost_does_not_create_profit():
    result=calculate_fifo_pnl([trade("SELL",quote="100",time=2)],"ABCUSDC",None,"0",movements=[
        {"id":"deposit-1","type":"TRANSFER_IN","asset":"ABC","quantity":"1","time":1}])
    assert_amount(result,"realized_pnl",None)
    assert_amount(result,"total_pnl",None)


def test_transfer_cost_currency_is_explicit_and_conversion_is_never_invented():
    movement={"id":"deposit-1","type":"TRANSFER_IN","asset":"ABC","quantity":"1",
              "time":0,"cost_basis":"100","cost_basis_quote":"EUR"}
    missing=calculate_fifo_pnl([],"ABCUSDC","150","1",movements=[movement])
    assert_amount(missing,"invested_remaining",None)
    assert "MISSING_MOVEMENT_CONVERSION" in missing["issues"]
    converted=calculate_fifo_pnl([],"ABCUSDC","150","1",movements=[movement],
        historical_rates={("EUR","USDC",0):"1.2"})
    assert_amount(converted,"invested_remaining","120")
    assert_amount(converted,"unrealized_pnl","30")


def test_spot_only_history_is_an_estimate_even_when_quantities_match():
    result=calculate_fifo_pnl([trade()],"ABCUSDC","110","1",funding_history_complete=False)
    assert_amount(result,"unrealized_pnl","10")
    assert result["pnl_status"]=="ESTIMATED"
    assert result["funding_history_status"]=="NOT_DOCUMENTED"


def test_unmatched_withdrawal_requires_history_uncertainty():
    result=calculate_fifo_pnl([],"ABCUSDC",None,"0",movements=[
        {"id":"withdraw-1","type":"TRANSFER_OUT","asset":"ABC","quantity":"1","time":1}])
    assert result["pnl_status"]=="INCOMPLETE"
    assert "TRANSFER_WITH_UNKNOWN_COST" in result["issues"]


def test_even_a_tiny_unexplained_deposit_does_not_get_an_invented_cost():
    result=calculate_fifo_pnl([trade()],"ABCUSDC","100","1.0000000000000001")
    assert "BALANCE_MISMATCH" in result["issues"]
    assert_amount(result,"invested_remaining",None)
