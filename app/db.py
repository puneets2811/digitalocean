from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from app.config import get_settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    type_filter TEXT NOT NULL,
    source_filter TEXT NOT NULL,
    payload_conditions_json TEXT NOT NULL,
    secret TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(event_id, subscription_id),
    FOREIGN KEY(event_id) REFERENCES events(id),
    FOREIGN KEY(subscription_id) REFERENCES subscriptions(id)
);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    at TEXT NOT NULL,
    http_status INTEGER,
    error TEXT,
    duration_ms INTEGER,
    FOREIGN KEY(delivery_id) REFERENCES deliveries(id)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_event ON deliveries(event_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_subscription ON deliveries(subscription_id);
CREATE INDEX IF NOT EXISTS idx_attempts_delivery ON delivery_attempts(delivery_id);
"""


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def connect(database_path: str | None = None) -> AsyncIterator[aiosqlite.Connection]:
    path = database_path or get_settings().database_path
    _ensure_parent(path)
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA journal_mode = WAL")
        yield db
    finally:
        await db.close()


async def init_db(database_path: str | None = None) -> None:
    async with connect(database_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()
    logger.info("database_initialized path=%s", database_path or get_settings().database_path)


def row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


def loads(raw: str) -> Any:
    return json.loads(raw)
