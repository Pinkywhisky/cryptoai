"""Declared manual holdings retain precision and explicit currency/unknown states."""
from decimal import Decimal
import sqlite3

import pytest

from services import personal_data, binance_sync
from services.history import init_db


def position(**changes):
    return {"id":1,"symbol":"ABCUSDC","source":"MANUAL","status":"MANUAL",
            "quantity":1,"average_buy_price":100,"invested_amount":100,**changes}


def test_manual_tiny_value_and_pnl_are_not_rounded_to_zero(monkeypatch):
    monkeypatch.setattr(personal_data,"get_market_snapshot",lambda symbol:{
        "analysis_available":False,"is_valid_symbol":True,
        "market_source":"BINANCE_SPOT","current_price":"200"})
    result=personal_data.enrich_position(position(quantity=1e-12,invested_amount=1e-10))
    assert result["current_value"]==2e-10
    assert result["gain_loss_amount"]==1e-10
    assert Decimal(result["decimal_values"]["current_value"])==Decimal("0.0000000002")


def test_manual_unknown_basis_is_not_free_profit(monkeypatch):
    monkeypatch.setattr(personal_data,"get_market_snapshot",lambda symbol:{
        "analysis_available":False,"is_valid_symbol":True,
        "market_source":"BINANCE_SPOT","current_price":"100"})
    result=personal_data.enrich_position(position(average_buy_price=None,invested_amount=None))
    assert result["current_value"]==100
    assert result["gain_loss_amount"] is None
    assert result["gain_loss_pct"] is None
    assert result["cost_basis_status"]=="UNKNOWN"


@pytest.mark.parametrize("rate,expected",[(Decimal(".99"),5.94),(None,None)])
def test_manual_alpha_requires_real_quote_conversion(monkeypatch,rate,expected):
    monkeypatch.setattr(personal_data,"get_market_snapshot",lambda symbol:{
        "analysis_available":False,"is_valid_symbol":True,"market_source":"BINANCE_ALPHA",
        "alpha_trading_symbol":"ALPHA_1USDT","ticker":{"lastPrice":"3"},"current_price":3})
    monkeypatch.setattr(personal_data,"get_manual_fx_rate",lambda asset:rate)
    result=personal_data.enrich_position(position(symbol="MORPHO",quantity=2,
                                                 average_buy_price=2,invested_amount=4))
    assert result["current_value"]==expected
    assert result["gain_loss_amount"]==(1.94 if rate is not None else None)
    assert result["pnl_status"]==("ESTIMATED" if rate is not None else "INCOMPLETE")


def test_manual_fx_is_fetched_once_per_currency_and_read(tmp_path,monkeypatch):
    path=tmp_path/"db.sqlite"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.executemany("""INSERT INTO positions(symbol,quantity,average_buy_price,
            invested_amount,source,status,created_at,updated_at)
            VALUES(?,1,2,2,'MANUAL','MANUAL','synthetic','synthetic')""",[("MORPHO",),("CKP",)])
    calls=[]
    monkeypatch.setattr(personal_data,"get_market_snapshot",lambda symbol:{
        "analysis_available":False,"is_valid_symbol":True,"market_source":"BINANCE_ALPHA",
        "alpha_trading_symbol":"ALPHA_1USDT","ticker":{"lastPrice":"3"}})
    monkeypatch.setattr(personal_data,"get_manual_fx_rate",lambda asset:calls.append(asset) or Decimal(".99"))
    result=personal_data.list_positions(path)
    assert len(result)==2
    assert calls==["USDT"]
    assert all(row["current_value"]==2.97 for row in result)


def test_manual_closed_recorded_result_is_not_lost_or_called_verified(tmp_path):
    path=tmp_path/"db.sqlite"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute("""INSERT INTO positions(symbol,quantity,average_buy_price,
            invested_amount,source,status,realized_pnl,created_at,updated_at)
            VALUES('ABCUSDC',0,0,0,'MANUAL','CLOSED',9,'synthetic','synthetic')""")
    result=binance_sync.get_pnl_summary(path)
    assert result["manual_closed_positions_count"]==1
    assert result["manual_realized_pnl_recorded"]==9
    assert result["manual_realized_pnl_status"]=="ESTIMATED"
    assert result["realized_pnl"] is None  # Binance is never initialized here.
    with sqlite3.connect(path) as connection:
        connection.execute("""INSERT INTO positions(symbol,quantity,average_buy_price,
            invested_amount,source,status,created_at,updated_at)
            VALUES('XYZUSDC',0,0,0,'MANUAL','CLOSED','synthetic','synthetic')""")
    partial=binance_sync.get_pnl_summary(path)
    assert partial["manual_realized_pnl_recorded"] is None
    assert partial["known_manual_realized_pnl_recorded"]==9
    assert partial["manual_recorded_results_count"]==1


def test_default_manual_closed_zero_is_unknown_not_a_verified_breakeven(tmp_path):
    path=tmp_path/"db.sqlite"
    init_db(path)
    with sqlite3.connect(path) as connection:
        connection.execute("""INSERT INTO positions(symbol,quantity,average_buy_price,
            invested_amount,source,status,created_at,updated_at)
            VALUES('ABCUSDC',0,0,0,'MANUAL','CLOSED','synthetic','synthetic')""")
    result=binance_sync.get_pnl_summary(path)
    assert result["manual_closed_positions_count"]==1
    assert result["manual_realized_pnl_recorded"] is None
    assert result["manual_realized_pnl_status"]=="INCOMPLETE"
