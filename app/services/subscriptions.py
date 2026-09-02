from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import aiosqlite

from app import db
from app.models import PayloadCondition, SubscriptionCreate, SubscriptionOut


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_out(row: aiosqlite.Row) -> SubscriptionOut:
    conditions = [
        PayloadCondition.model_validate(item) for item in db.loads(row["payload_conditions_json"])
    ]
    return SubscriptionOut(
        id=row["id"],
        url=row["url"],
        type_filter=row["type_filter"],
        source_filter=row["source_filter"],
        payload_conditions=conditions,
        active=bool(row["active"]),
        created_at=row["created_at"],
    )


async def create_subscription(
    conn: aiosqlite.Connection,
    body: SubscriptionCreate,
    default_secret: str,
) -> SubscriptionOut:
    subscription_id = str(uuid4())
    conditions = [c.model_dump(mode="json") for c in body.payload_conditions]
    secret = body.secret or default_secret
    created_at = utcnow()
    await conn.execute(
        """
        INSERT INTO subscriptions (
            id, url, type_filter, source_filter, payload_conditions_json, secret, active, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            subscription_id,
            str(body.url),
            body.type_filter or "*",
            body.source_filter or "*",
            db.dumps(conditions),
            secret,
            created_at,
        ),
    )
    await conn.commit()
    row = await (await conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))).fetchone()
    assert row is not None
    return _to_out(row)


async def list_subscriptions(conn: aiosqlite.Connection) -> list[SubscriptionOut]:
    cursor = await conn.execute(
        "SELECT * FROM subscriptions WHERE active = 1 ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    return [_to_out(row) for row in rows]


async def delete_subscription(conn: aiosqlite.Connection, subscription_id: str) -> bool:
    """Soft-delete: deactivate so fanout stops but delivery audits remain."""
    cursor = await conn.execute(
        "UPDATE subscriptions SET active = 0 WHERE id = ? AND active = 1",
        (subscription_id,),
    )
    await conn.commit()
    return cursor.rowcount > 0


async def get_subscription(conn: aiosqlite.Connection, subscription_id: str) -> SubscriptionOut | None:
    row = await (
        await conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))
    ).fetchone()
    return _to_out(row) if row else None


async def list_active_subscription_rows(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
    cursor = await conn.execute("SELECT * FROM subscriptions WHERE active = 1")
    rows = await cursor.fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload_conditions"] = db.loads(row["payload_conditions_json"])
        result.append(item)
    return result
