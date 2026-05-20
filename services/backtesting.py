from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from services.history import DB_PATH, init_db


def init_backtesting(db_path: Path | str = DB_PATH) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                decision TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                outcome TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def simulate_signal(
    df: pd.DataFrame,
    *,
    symbol: str,
    decision: str,
    tp_pct: float = 3.0,
    sl_pct: float = 2.0,
    horizon: int = 24,
) -> dict[str, Any]:
    if df.empty or "close" not in df.columns:
        raise ValueError("DataFrame OHLC requis pour le backtest")
    entry_price = float(df.iloc[0]["close"])
    future = df.iloc[1 : horizon + 1] if len(df) > 1 else df.iloc[:0]
    exit_price = float(df.iloc[-1]["close"]) if future.empty else float(future.iloc[-1]["close"])
    outcome = "TIME_EXIT"

    for _, row in future.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        if decision in {"BUY_READY", "BUY_WATCH"}:
            if low <= entry_price * (1 - sl_pct / 100):
                exit_price = entry_price * (1 - sl_pct / 100)
                outcome = "SL"
                break
            if high >= entry_price * (1 + tp_pct / 100):
                exit_price = entry_price * (1 + tp_pct / 100)
                outcome = "TP"
                break
        elif decision in {"TAKE_PROFIT", "CUT_LOSS"}:
            exit_price = float(row["close"])
            outcome = decision
            break

    pnl_pct = (exit_price - entry_price) / entry_price * 100 if entry_price else 0.0
    return {
        "symbol": symbol.upper(),
        "decision": decision,
        "entry_price": round(entry_price, 8),
        "exit_price": round(exit_price, 8),
        "pnl_pct": round(pnl_pct, 4),
        "outcome": outcome,
    }


def calculate_backtest_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {
            "total": 0,
            "winrate": 0,
            "average_gain": 0,
            "average_loss": 0,
            "profit_factor": 0,
            "expectancy": 0,
            "max_drawdown": 0,
        }

    pnls = [float(item.get("pnl_pct", 0)) for item in results]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)

    return {
        "total": len(results),
        "winrate": round(len(wins) / len(results) * 100, 2),
        "average_gain": round(sum(wins) / len(wins), 4) if wins else 0,
        "average_loss": round(sum(losses) / len(losses), 4) if losses else 0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else round(gross_profit, 4),
        "expectancy": round(sum(pnls) / len(pnls), 4),
        "max_drawdown": round(max_drawdown, 4),
    }


def save_backtest_result(
    result: dict[str, Any],
    *,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    init_backtesting(db_path)
    created_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO backtest_results (
                symbol, decision, entry_price, exit_price, pnl_pct, outcome, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["symbol"],
                result["decision"],
                result["entry_price"],
                result["exit_price"],
                result["pnl_pct"],
                result["outcome"],
                created_at,
            ),
        )
        connection.commit()
    return {"id": cursor.lastrowid, **result, "created_at": created_at}


def list_backtest_results(
    *,
    limit: int = 100,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    init_backtesting(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT *
            FROM backtest_results
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    results = [dict(row) for row in rows]
    return {"results": results, "metrics": calculate_backtest_metrics(results)}
