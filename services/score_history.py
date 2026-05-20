from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.history import DB_PATH, init_db


def init_score_history(db_path: Path | str = DB_PATH) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS score_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                global_score INTEGER,
                context_score INTEGER,
                setup_score INTEGER,
                trigger_score INTEGER,
                risk_score INTEGER,
                market_score INTEGER,
                confidence INTEGER,
                decision TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def save_score_snapshot(
    symbol: str,
    decision_payload: dict[str, Any],
    *,
    global_score: int | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    init_score_history(db_path)
    created_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO score_snapshots (
                symbol, global_score, context_score, setup_score, trigger_score,
                risk_score, market_score, confidence, decision, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                global_score,
                decision_payload.get("context_score"),
                decision_payload.get("setup_score"),
                decision_payload.get("trigger_score"),
                decision_payload.get("risk_score"),
                decision_payload.get("market_score"),
                decision_payload.get("confidence"),
                decision_payload.get("decision"),
                created_at,
            ),
        )
        connection.commit()
        snapshot_id = cursor.lastrowid
    return {"id": snapshot_id, "symbol": symbol.upper(), "created_at": created_at}


def get_score_evolution(
    symbol: str,
    *,
    limit: int = 50,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    init_score_history(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT *
            FROM score_snapshots
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (symbol.upper(), limit),
        ).fetchall()

    ordered = list(reversed(rows))
    history = [row["global_score"] for row in ordered if row["global_score"] is not None]
    acceleration = calculate_score_acceleration(symbol, values=history)
    return {
        "symbol": symbol.upper(),
        "history": history,
        "trend": acceleration["trend"],
        "acceleration": acceleration["acceleration"],
        "snapshots": [dict(row) for row in ordered],
    }


def calculate_score_acceleration(
    symbol: str,
    *,
    values: list[int] | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    if values is None:
        init_score_history(db_path)
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                """
                SELECT global_score
                FROM score_snapshots
                WHERE symbol = ? AND global_score IS NOT NULL
                ORDER BY id DESC
                LIMIT 6
                """,
                (symbol.upper(),),
            ).fetchall()
        values = [int(row[0]) for row in reversed(rows)]

    if len(values) < 2:
        return {"symbol": symbol.upper(), "trend": "FLAT", "acceleration": "NONE", "delta": 0}

    delta = values[-1] - values[0]
    recent_delta = values[-1] - values[-2]
    if delta >= 12 and recent_delta >= 5:
        acceleration = "STRONG"
    elif delta >= 5:
        acceleration = "MODERATE"
    elif delta <= -8:
        acceleration = "NEGATIVE"
    else:
        acceleration = "WEAK"

    if delta > 3:
        trend = "IMPROVING"
    elif delta < -3:
        trend = "DEGRADING"
    else:
        trend = "FLAT"

    return {
        "symbol": symbol.upper(),
        "trend": trend,
        "acceleration": acceleration,
        "delta": delta,
        "recent_delta": recent_delta,
    }
