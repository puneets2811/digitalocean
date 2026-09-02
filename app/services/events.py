from __future__ import annotations

from typing import Any
from uuid import uuid4

import aiosqlite
from aio_pika.abc import AbstractChannel

from app import db
from app.models import EventCreate, EventOut
from app.mq import publish_event_id
from app.services.subscriptions import utcnow


def _to_out(row: aiosqlite.Row) -> EventOut:
    return EventOut(
        id=row["id"],
        type=row["type"],
        source=row["source"],
        payload=db.loads(row["payload_json"]),
        status=row["status"],
        created_at=row["created_at"],
    )


async def create_event(conn: aiosqlite.Connection, body: EventCreate) -> EventOut:
    event_id = str(uuid4())
    created_at = utcnow()
    await conn.execute(
        """
        INSERT INTO events (id, type, source, payload_json, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event_id, body.type, body.source, db.dumps(body.payload), "accepted", created_at),
    )
    await conn.commit()
    row = await (await conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))).fetchone()
    assert row is not None
    return _to_out(row)


async def mark_event_status(conn: aiosqlite.Connection, event_id: str, status: str) -> None:
    await conn.execute("UPDATE events SET status = ? WHERE id = ?", (status, event_id))
    await conn.commit()


async def get_event(conn: aiosqlite.Connection, event_id: str) -> EventOut | None:
    row = await (await conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))).fetchone()
    return _to_out(row) if row else None


async def get_event_row(conn: aiosqlite.Connection, event_id: str) -> dict[str, Any] | None:
    row = await (await conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["payload"] = db.loads(row["payload_json"])
    return data


async def ingest_event(
    conn: aiosqlite.Connection,
    channel: AbstractChannel,
    body: EventCreate,
) -> EventOut:
    event = await create_event(conn, body)
    try:
        await publish_event_id(
            channel,
            event_id=event.id,
            event_type=event.type,
            event_source=event.source,
        )
    except Exception:
        await mark_event_status(conn, event.id, "publish_failed")
        raise
    return event
