from __future__ import annotations

import pytest
from aiosqlite import Connection

from app.models import EventCreate, SubscriptionCreate
from app.services import deliveries as delivery_service
from app.services import events as event_service
from app.services import subscriptions as subscription_service


@pytest.mark.anyio
async def test_create_list_delete_subscription(db: Connection) -> None:
    created = await subscription_service.create_subscription(
        db,
        SubscriptionCreate(
            url="https://example.test/a",
            type_filter="order.created",
            source_filter=None,
            payload_conditions=[],
            secret="custom-secret",
        ),
        default_secret="default",
    )
    assert created.type_filter == "order.created"
    assert created.source_filter == "*"
    assert created.active is True

    listed = await subscription_service.list_subscriptions(db)
    assert len(listed) == 1
    assert listed[0].id == created.id

    fetched = await subscription_service.get_subscription(db, created.id)
    assert fetched is not None
    assert fetched.url == "https://example.test/a"

    rows = await subscription_service.list_active_subscription_rows(db)
    assert len(rows) == 1
    assert rows[0]["secret"] == "custom-secret"

    assert await subscription_service.delete_subscription(db, created.id) is True
    assert await subscription_service.get_subscription(db, created.id) is None
    assert await subscription_service.delete_subscription(db, created.id) is False


@pytest.mark.anyio
async def test_create_subscription_uses_default_secret(db: Connection) -> None:
    created = await subscription_service.create_subscription(
        db,
        SubscriptionCreate(url="https://example.test/b"),
        default_secret="fallback-secret",
    )
    rows = await subscription_service.list_active_subscription_rows(db)
    assert rows[0]["id"] == created.id
    assert rows[0]["secret"] == "fallback-secret"


@pytest.mark.anyio
async def test_create_and_get_event(db: Connection) -> None:
    event = await event_service.create_event(
        db,
        EventCreate(type="order.created", source="checkout", payload={"n": 1}),
    )
    assert event.status == "accepted"
    fetched = await event_service.get_event(db, event.id)
    assert fetched is not None
    assert fetched.payload == {"n": 1}
    assert await event_service.get_event(db, "missing") is None

    row = await event_service.get_event_row(db, event.id)
    assert row is not None
    assert row["payload"] == {"n": 1}


@pytest.mark.anyio
async def test_mark_event_status(db: Connection) -> None:
    event = await event_service.create_event(
        db,
        EventCreate(type="t", source="s", payload={}),
    )
    await event_service.mark_event_status(db, event.id, "publish_failed")
    fetched = await event_service.get_event(db, event.id)
    assert fetched is not None
    assert fetched.status == "publish_failed"


@pytest.mark.anyio
async def test_ingest_event_marks_publish_failed_on_mq_error(db: Connection) -> None:
    from unittest.mock import AsyncMock, patch

    channel = AsyncMock()
    with patch(
        "app.services.events.publish_event_id",
        new_callable=AsyncMock,
        side_effect=RuntimeError("mq down"),
    ):
        with pytest.raises(RuntimeError, match="mq down"):
            await event_service.ingest_event(
                db,
                channel,
                EventCreate(type="t", source="s", payload={}),
            )

    listed = await (
        await db.execute("SELECT id, status FROM events")
    ).fetchall()
    assert len(listed) == 1
    assert listed[0]["status"] == "publish_failed"


@pytest.mark.anyio
async def test_delivery_lifecycle(db: Connection) -> None:
    event = await event_service.create_event(
        db,
        EventCreate(type="t", source="s", payload={}),
    )
    sub = await subscription_service.create_subscription(
        db,
        SubscriptionCreate(url="https://example.test/hook"),
        default_secret="secret",
    )

    delivery_id = await delivery_service.ensure_pending_delivery(
        db, event_id=event.id, subscription_id=sub.id
    )
    same_id = await delivery_service.ensure_pending_delivery(
        db, event_id=event.id, subscription_id=sub.id
    )
    assert same_id == delivery_id
    assert await delivery_service.get_delivery_status(db, delivery_id) == "pending"

    await delivery_service.record_attempt(
        db,
        delivery_id=delivery_id,
        attempt_no=1,
        http_status=500,
        error="non_2xx",
        duration_ms=12,
    )
    await delivery_service.record_attempt(
        db,
        delivery_id=delivery_id,
        attempt_no=2,
        http_status=200,
        error=None,
        duration_ms=8,
    )
    await delivery_service.set_delivery_status(db, delivery_id, "delivered")

    delivery = await delivery_service.get_delivery(db, delivery_id)
    assert delivery is not None
    assert delivery.status == "delivered"
    assert len(delivery.attempts) == 2
    assert delivery.attempts[0].attempt_no == 1
    assert delivery.attempts[1].http_status == 200

    by_event = await delivery_service.list_deliveries_for_event(db, event.id)
    assert len(by_event) == 1
    by_sub = await delivery_service.list_deliveries_for_subscription(db, sub.id)
    assert len(by_sub) == 1
    assert await delivery_service.get_delivery(db, "missing") is None
