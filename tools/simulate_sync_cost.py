"""Synthetic API-cost simulator. No credentials, account data, or HTTP requests."""
import argparse
from contextlib import closing
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import types

ROOT=Path(__file__).resolve().parents[1]
LEGACY_COMMIT="78b235817e815b915dc7113248ccaa1c0d10ce80"


class InstrumentedClient:
    configured=True
    config=types.SimpleNamespace(use_testnet=False)

    def __init__(self,markets,trades=None,balances=None,*,latency=.02):
        self.markets=markets
        self.trades=trades or {}
        self.balances=balances or []
        self.latency=latency
        self.events=[]
        self.fail=None

    def record(self,endpoint,weight,**fields):
        self.events.append({"endpoint":endpoint,"weight":weight,"simulated_seconds":self.latency,**fields})

    def get_spot_markets(self):
        self.record("exchangeInfo",20)
        return self.markets

    def get_non_zero_balances(self):
        self.record("account",20)
        return self.balances

    def get_my_trades(self,symbol,*,from_id=0,limit=1000):
        self.record("myTrades",20,symbol=symbol,from_id=from_id)
        if self.fail:
            self.fail(symbol,from_id)
        return [t for t in self.trades.get(symbol,[]) if t["id"]>=from_id][:limit]

    def get_market_price(self,symbol):
        self.record("ticker/price",2,symbol=symbol)
        return "110"

    def reset(self):
        self.events.clear()

    def metrics(self,*,imported=0,duplicates=0,elapsed=0):
        private=[e for e in self.events if e["endpoint"] in {"account","myTrades"}]
        return {"private_requests":len(private),"public_requests":len(self.events)-len(private),
                "estimated_documented_weight":sum(e["weight"] for e in self.events),
                "simulated_wire_seconds":sum(e["simulated_seconds"] for e in self.events),
                "markets_examined":len({e["symbol"] for e in self.events if e["endpoint"]=="myTrades"}),
                "transactions_imported":imported,"duplicates_observed":duplicates,
                "observed_local_seconds":elapsed}


def catalog(count=1666):
    return [{"symbol":f"COIN{i:04}USDC","base_asset":f"COIN{i:04}","quote_asset":"USDC","status":"TRADING"}
            for i in range(count)]


def sample_trade(symbol,identity,buy,quote):
    return {"symbol":symbol,"id":identity,"orderId":identity,"isBuyer":buy,"isMaker":False,
            "qty":"1","price":str(quote),"quoteQty":str(quote),"commission":"0","commissionAsset":"USDC","time":identity}


def _legacy_module(name,relative):
    source=subprocess.check_output(["git","show",LEGACY_COMMIT+":"+relative],cwd=ROOT,text=True)
    module=types.ModuleType(name)
    module.__file__=str(ROOT/relative)
    exec(compile(source,str(ROOT/relative)+"@"+LEGACY_COMMIT,"exec"),module.__dict__)
    return module


def run_comparison(directory,*,market_count=1666,known_count=3):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    os.environ["PYTHON_DOTENV_DISABLED"]="1"
    os.environ.setdefault("CRYPTOAI_DB_PATH",str(directory/"default.sqlite"))
    sys.path.insert(0,str(ROOT))
    from services.binance_sync import sync_binance_positions
    from services.binance_trade_history import connect
    from services.history import init_db
    markets=catalog(market_count)
    trades={row["symbol"]:[sample_trade(row["symbol"],1,True,100),sample_trade(row["symbol"],2,False,130)]
            for row in markets[:known_count]}
    base=directory/"seed.sqlite"
    seed=InstrumentedClient(markets,trades)
    sync_binance_positions(seed,base,mode="bootstrap",full_scan=True)
    paths=[directory/"before.sqlite",directory/"after.sqlite"]
    for path in paths:
        with closing(sqlite3.connect(base)) as source,closing(sqlite3.connect(path)) as target:
            source.backup(target)
    results={}
    for name,path in zip(["before","after"],paths):
        client=InstrumentedClient(markets,trades)
        started=time.perf_counter()
        if name=="before":
            legacy=_legacy_module("_legacy_portfolio","services/binance_portfolio.py")
            result=legacy.synchronize(client,path,get_price=client.get_market_price,
                resolve=lambda asset:{"valid":False},get_alpha=lambda asset:{},threshold=1)
        else:
            result=sync_binance_positions(client,path)
        elapsed=time.perf_counter()-started
        with connect(path) as connection:
            count=connection.execute("SELECT COUNT(*) FROM binance_trades").fetchone()[0]
        results[name]=client.metrics(imported=count-known_count*2,elapsed=elapsed)
        results[name]["sync_status"]=result["sync_status"]
    legacy_history=_legacy_module("_legacy_history","services/history.py")
    reads={}
    for name,initializer,path in [("before",legacy_history.init_db,paths[0]),("after",init_db,paths[1])]:
        started=time.perf_counter()
        for _ in range(100):
            initializer(path)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("SELECT COUNT(*) FROM binance_trades").fetchone()
        reads[name]=time.perf_counter()-started
    results["sqlite_100_reads_observed_seconds"]=reads
    results["scenario"]={"catalog_markets":market_count,"known_markets":known_count,"new_trades":0,
                         "simulated_latency_per_request_seconds":.02,
                         "weight_is_documented_estimate":True,
                         "before_source_commit":LEGACY_COMMIT,
                         "method":"Original lot-3 orchestrator and init_db on synthetic copies; current shared financial plumbing; no real API"}
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets",type=int,default=1666)
    parser.add_argument("--known",type=int,default=3)
    args=parser.parse_args()
    if not 1<=args.markets<=10000 or not 0<=args.known<=args.markets:
        parser.error("Invalid synthetic universe size")
    with tempfile.TemporaryDirectory(prefix="cryptoai-api-simulation-") as directory:
        print(json.dumps(run_comparison(directory,market_count=args.markets,known_count=args.known),indent=2))


if __name__=="__main__":
    main()
