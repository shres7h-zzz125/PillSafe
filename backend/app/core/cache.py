"""SQLite TTL cache for external API responses and storage for confirmed user medicines."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SQLiteCache:
    """Thread-safe SQLite storage for API response caching and confirmed medicines."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_cache (
                    cache_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    dataset_version TEXT,
                    dataset_date TEXT,
                    fetched_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS confirmed_medicines (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    generic_name TEXT,
                    brand_name TEXT,
                    dosage_form TEXT,
                    strength TEXT,
                    active_ingredients_json TEXT NOT NULL,
                    rxcui TEXT,
                    cdci_code TEXT,
                    source_attribution_json TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    user_dosage_note TEXT
                );
                """
            )
            conn.commit()

    def get_cached(self, cache_key: str) -> dict[str, Any] | None:
        """Return non-expired cached entry or None."""
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, dataset_version, dataset_date, fetched_at
                FROM api_cache
                WHERE cache_key = ? AND expires_at > ?
                """,
                (cache_key, now),
            ).fetchone()
            if row is None:
                return None
            return {
                "payload": json.loads(row["payload_json"]),
                "dataset_version": row["dataset_version"],
                "dataset_date": row["dataset_date"],
                "fetched_at": row["fetched_at"],
            }

    def set_cached(
        self,
        cache_key: str,
        *,
        source: str,
        payload: Any,
        ttl_seconds: int,
        dataset_version: str | None = None,
        dataset_date: str | None = None,
    ) -> None:
        """Store or overwrite an API response in SQLite cache."""
        now = time.time()
        expires_at = now + max(1, ttl_seconds)
        serialized = json.dumps(payload)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_cache (
                    cache_key, source, payload_json, dataset_version, dataset_date, fetched_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    source = excluded.source,
                    payload_json = excluded.payload_json,
                    dataset_version = excluded.dataset_version,
                    dataset_date = excluded.dataset_date,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at
                """,
                (cache_key, source, serialized, dataset_version, dataset_date, now, expires_at),
            )
            conn.commit()

    def clear_expired(self) -> int:
        """Delete expired cache entries and return the number deleted."""
        now = time.time()
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM api_cache WHERE expires_at <= ?", (now,))
            conn.commit()
            return cursor.rowcount

    def add_confirmed_medicine(self, record: dict[str, Any]) -> dict[str, Any]:
        """Persist a user-confirmed medicine into the user's medicine list."""
        confirmed_at = record.get("confirmed_at") or datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO confirmed_medicines (
                    id, source, source_id, display_name, generic_name, brand_name,
                    dosage_form, strength, active_ingredients_json, rxcui, cdci_code,
                    source_attribution_json, confirmed_at, user_dosage_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    generic_name = excluded.generic_name,
                    brand_name = excluded.brand_name,
                    dosage_form = excluded.dosage_form,
                    strength = excluded.strength,
                    active_ingredients_json = excluded.active_ingredients_json,
                    rxcui = excluded.rxcui,
                    cdci_code = excluded.cdci_code,
                    source_attribution_json = excluded.source_attribution_json,
                    confirmed_at = excluded.confirmed_at,
                    user_dosage_note = excluded.user_dosage_note
                """,
                (
                    record["id"],
                    record["source"],
                    record["source_id"],
                    record["display_name"],
                    record.get("generic_name"),
                    record.get("brand_name"),
                    record.get("dosage_form"),
                    record.get("strength"),
                    json.dumps(record.get("active_ingredients", [])),
                    record.get("rxcui"),
                    record.get("cdci_code"),
                    json.dumps(record.get("source_attribution", {})),
                    confirmed_at,
                    record.get("user_dosage_note"),
                ),
            )
            conn.commit()
        return {**record, "confirmed_at": confirmed_at}

    def list_confirmed_medicines(self) -> list[dict[str, Any]]:
        """Return all medicines confirmed by the user."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM confirmed_medicines ORDER BY confirmed_at DESC"
            ).fetchall()
            return [self._row_to_confirmed_medicine(row) for row in rows]

    def get_confirmed_medicine(self, entry_id: str) -> dict[str, Any] | None:
        """Fetch a single confirmed medicine by ID."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM confirmed_medicines WHERE id = ?",
                (entry_id,),
            ).fetchone()
            return self._row_to_confirmed_medicine(row) if row else None

    def remove_confirmed_medicine(self, entry_id: str) -> bool:
        """Delete a confirmed medicine from the user's list."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM confirmed_medicines WHERE id = ?",
                (entry_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def stats(self) -> dict[str, Any]:
        """Return cache and medicine list counts."""
        now = time.time()
        with self._lock, self._connect() as conn:
            total_cache = conn.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
            active_cache = conn.execute(
                "SELECT COUNT(*) FROM api_cache WHERE expires_at > ?",
                (now,),
            ).fetchone()[0]
            confirmed_count = conn.execute("SELECT COUNT(*) FROM confirmed_medicines").fetchone()[0]
        return {
            "db_path": str(self.db_path),
            "total_cache_entries": total_cache,
            "active_cache_entries": active_cache,
            "confirmed_medicines_count": confirmed_count,
        }

    @staticmethod
    def _row_to_confirmed_medicine(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "source": row["source"],
            "source_id": row["source_id"],
            "display_name": row["display_name"],
            "generic_name": row["generic_name"],
            "brand_name": row["brand_name"],
            "dosage_form": row["dosage_form"],
            "strength": row["strength"],
            "active_ingredients": json.loads(row["active_ingredients_json"]),
            "rxcui": row["rxcui"],
            "cdci_code": row["cdci_code"],
            "source_attribution": json.loads(row["source_attribution_json"]),
            "confirmed_at": row["confirmed_at"],
            "user_dosage_note": row["user_dosage_note"],
        }
