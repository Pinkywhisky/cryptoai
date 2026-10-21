from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.history import DB_PATH, init_db


MARKET_CACHE_ID = 1
# A cache built before qualified opportunities and business sources is incompatible.
MARKET_CACHE_VERSION = 2


def _connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    init_db(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def save_market_payload(payload: dict[str, Any], db_path: Path | str = DB_PATH) -> dict[str, Any]:
    updated_at = datetime.now(UTC).isoformat()
    payload_to_store = {
        **payload,
        "cache_version": MARKET_CACHE_VERSION,
        "updated_at": updated_at,
        "source": "fresh",
        "is_stale": False,
    }
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO market_cache (id, payload_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload_json = excluded.payload_json,
              updated_at = excluded.updated_at
            """,
            (MARKET_CACHE_ID, json.dumps(payload_to_store), updated_at),
        )
        connection.commit()
    return payload_to_store


def get_market_payload_cache(db_path: Path | str = DB_PATH) -> dict[str, Any] | None:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT payload_json, updated_at FROM market_cache WHERE id = ?",
            (MARKET_CACHE_ID,),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("cache_version") != MARKET_CACHE_VERSION:
        return None
    if any(
        "WATCHLIST" in item.get("sources", [])
        for key in ("results", "universe")
        for item in payload.get(key, [])
    ):
        return None
    age_seconds = (
        datetime.now(UTC) - datetime.fromisoformat(row["updated_at"])
    ).total_seconds()
    return {
        **payload,
        "updated_at": row["updated_at"],
        "source": "cache",
        "is_stale": age_seconds > 15 * 60,
    }


def invalidate_market_cache(connection: sqlite3.Connection) -> None:
    """Invalidate in the caller's transaction, so mutations and cache stay atomic."""
    connection.execute("DELETE FROM market_cache WHERE id = ?", (MARKET_CACHE_ID,))


def clear_market_payload_cache(db_path: Path | str = DB_PATH) -> None:
    with _connect(db_path) as connection:
        invalidate_market_cache(connection)
        connection.commit()
