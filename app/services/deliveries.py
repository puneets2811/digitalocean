from __future__ import annotations

from uuid import uuid4

import aiosqlite

from app.models import DeliveryAttemptOut, DeliveryOut
from app.services.subscriptions import utcnow


def _delivery_out(row: aiosqlite.Row, attempts: list[DeliveryAttemptOut]) -> DeliveryOut:
    return DeliveryOut(
        id=row["id"],
        event_id=row["event_id"],
        subscription_id=row["subscription_id"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        attempts=attempts,
    )


async def _attempts_for(conn: aiosqlite.Connection, delivery_id: str) -> list[DeliveryAttemptOut]:
    cursor = await conn.execute(
        """
        SELECT * FROM delivery_attempts
        WHERE delivery_id = ?
        ORDER BY attempt_no ASC
        """,
        (delivery_id,),
    )
    rows = await cursor.fetchall()
    return [
        DeliveryAttemptOut(
            id=row["id"],
            attempt_no=row["attempt_no"],
            at=row["at"],
            http_status=row["http_status"],
            error=row["error"],
            duration_ms=row["duration_ms"],
        )
        for row in rows
    ]


async def ensure_pending_delivery(
    conn: aiosqlite.Connection,
    *,
    event_id: str,
    subscription_id: str,
) -> str:
    existing = await (
        await conn.execute(
            """
            SELECT id FROM deliveries
            WHERE event_id = ? AND subscription_id = ?
            """,
            (event_id, subscription_id),
        )
    ).fetchone()
    if existing:
        return existing["id"]

    delivery_id = str(uuid4())
    now = utcnow()
    await conn.execute(
        """
        INSERT INTO deliveries (id, event_id, subscription_id, status, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?)
        """,
        (delivery_id, event_id, subscription_id, now, now),
    )
    await conn.commit()
    return delivery_id


async def get_delivery(conn: aiosqlite.Connection, delivery_id: str) -> DeliveryOut | None:
    row = await (await conn.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,))).fetchone()
    if row is None:
        return None
    return _delivery_out(row, await _attempts_for(conn, delivery_id))


async def list_deliveries_for_event(conn: aiosqlite.Connection, event_id: str) -> list[DeliveryOut]:
    cursor = await conn.execute(
        "SELECT * FROM deliveries WHERE event_id = ? ORDER BY created_at ASC",
        (event_id,),
    )
    rows = await cursor.fetchall()
    return [_delivery_out(row, await _attempts_for(conn, row["id"])) for row in rows]


async def list_deliveries_for_subscription(
    conn: aiosqlite.Connection, subscription_id: str
) -> list[DeliveryOut]:
    cursor = await conn.execute(
        "SELECT * FROM deliveries WHERE subscription_id = ? ORDER BY created_at DESC",
        (subscription_id,),
    )
    rows = await cursor.fetchall()
    return [_delivery_out(row, await _attempts_for(conn, row["id"])) for row in rows]


async def record_attempt(
    conn: aiosqlite.Connection,
    *,
    delivery_id: str,
    attempt_no: int,
    http_status: int | None,
    error: str | None,
    duration_ms: int | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO delivery_attempts (
            delivery_id, attempt_no, at, http_status, error, duration_ms
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (delivery_id, attempt_no, utcnow(), http_status, error, duration_ms),
    )
    await conn.commit()


async def set_delivery_status(conn: aiosqlite.Connection, delivery_id: str, status: str) -> None:
    await conn.execute(
        "UPDATE deliveries SET status = ?, updated_at = ? WHERE id = ?",
        (status, utcnow(), delivery_id),
    )
    await conn.commit()


async def get_delivery_status(conn: aiosqlite.Connection, delivery_id: str) -> str | None:
    row = await (
        await conn.execute("SELECT status FROM deliveries WHERE id = ?", (delivery_id,))
    ).fetchone()
    return row["status"] if row else None
