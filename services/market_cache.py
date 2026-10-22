from __future__ import annotations

import json
import math
import time
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


def get_market_payload_cache(db_path: Path | str = DB_PATH, *, allow_alpha_refresh=False) -> dict[str, Any] | None:
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
    # A whole-cockpit snapshot cannot extend an Alpha quote's own lifetime.
    # Retry deadlines also allow recovery from an outage without a forced scan.
    alpha_refresh = False
    for key in ("results", "universe"):
        for item in payload.get(key, []):
            infos = [item, item.get("position_info") or {}, item.get("watch_info") or {}]
            for info in infos:
                is_alpha = (info.get("market_source") == "BINANCE_ALPHA"
                            or info is item and "BINANCE_ALPHA" in item.get("sources", []))
                if not is_alpha:
                    continue
                state = info.get("market_data") or next(
                    (nested.get("market_data") for nested in infos[1:]
                     if nested.get("market_source") == "BINANCE_ALPHA"), None) or {}
                deadline = state.get("refresh_after")
                from services.binance_alpha import alpha_catalog_versions
                known = state.get("catalog_versions") or {}
                newer_catalog = bool(known) and any(version and version != known.get(name, 0)
                    for name, version in alpha_catalog_versions().items())
                if (type(deadline) not in {int, float} or not math.isfinite(deadline)
                        or time.time() >= deadline or newer_catalog):
                    if not allow_alpha_refresh:
                        return None
                    alpha_refresh = True
    age_seconds = (
        datetime.now(UTC) - datetime.fromisoformat(row["updated_at"])
    ).total_seconds()
    return {
        **payload,
        "updated_at": row["updated_at"],
        "source": "cache",
        "is_stale": age_seconds > 15 * 60,
        **({"alpha_refresh_required": True} if alpha_refresh else {}),
    }


def get_market_payload_for_refresh(db_path: Path | str = DB_PATH):
    return get_market_payload_cache(db_path, allow_alpha_refresh=True)


def save_alpha_refresh(payload, db_path: Path | str = DB_PATH):
    """Replace only a still-present snapshot; preserve the Spot observation date.

    A concurrent business mutation may have invalidated the snapshot while HTTP
    was in flight. Never recreate that deleted cache or overwrite a newer scan.
    """
    stored = {k: v for k, v in payload.items() if k != "alpha_refresh_required"}
    with _connect(db_path) as connection:
        current = connection.execute("SELECT payload_json FROM market_cache WHERE id=? AND updated_at=?",
                                     (MARKET_CACHE_ID, stored["updated_at"])).fetchone()
        if current is None:
            return None
        try:
            previous = json.loads(current["payload_json"])
            # Reader-only envelope fields do not justify rewriting the snapshot.
            normalize = lambda value: {k: v for k, v in value.items()
                if k not in {"source", "is_stale", "alpha_refresh_required"}}
            if normalize(previous) == normalize(stored):
                return stored
        except (ValueError, TypeError):
            pass
        cursor = connection.execute(
            "UPDATE market_cache SET payload_json=? WHERE id=? AND updated_at=?",
            (json.dumps(stored, allow_nan=False), MARKET_CACHE_ID, stored["updated_at"]))
    return stored if cursor.rowcount else None


def invalidate_market_cache(connection: sqlite3.Connection) -> None:
    """Invalidate in the caller's transaction, so mutations and cache stay atomic."""
    connection.execute("DELETE FROM market_cache WHERE id = ?", (MARKET_CACHE_ID,))


def clear_market_payload_cache(db_path: Path | str = DB_PATH) -> None:
    with _connect(db_path) as connection:
        invalidate_market_cache(connection)
        connection.commit()
