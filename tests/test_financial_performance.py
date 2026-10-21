"""Measure a deterministic synthetic workload and assert an API-call budget."""
import time
from decimal import Decimal

from services.binance_trade_history import sync_market_history
from services.binance_sync import get_stored_trades
from services.pnl import calculate_fifo_pnl
from test_financial_reference import PagedClient, trade


def test_reference_workload_and_page_call_budget(tmp_path,record_property):
    # 1200 purchases for .00000001 USDC then 1200 sales for .00000002:
    # exact total realized = 1200 * .00000001 = .000012 USDC.
    items=[trade(qty=".00000001",quote=".00000001",identity=i,time=i)
           for i in range(1,1201)]
    items += [trade("SELL",qty=".00000001",quote=".00000002",identity=i,time=i)
              for i in range(1201,2401)]
    client=PagedClient({"ABCUSDC":items})
    started=time.perf_counter()
    result=sync_market_history("ABCUSDC",client,tmp_path/"benchmark.sqlite",page_size=100)
    elapsed=time.perf_counter()-started
    assert result["complete"]
    assert len(client.calls)==25  # 24 nonempty pages and one explicit terminal page.
    stored=get_stored_trades("ABCUSDC",tmp_path/"benchmark.sqlite")
    started_fifo=time.perf_counter()
    calculated=calculate_fifo_pnl(stored,"ABCUSDC",None,"0")
    fifo_elapsed=time.perf_counter()-started_fifo
    assert Decimal(calculated["decimal_values"]["realized_pnl"])==Decimal(".000012")
    assert calculated["remaining_quantity"]==0
    for key,value in {"trades":2400,"api_calls":len(client.calls),
                      "sync_seconds":elapsed,"fifo_seconds":fifo_elapsed}.items():
        record_property(key,value)
