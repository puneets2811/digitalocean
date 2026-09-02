from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from aiosqlite import Connection

from app.config import Settings
from app.models import EventCreate, SubscriptionCreate
from app.services import deliveries as delivery_service
from app.services import events as event_service
from app.services import subscriptions as subscription_service
from consumer.worker import deliver_with_retries, process_event


@pytest.mark.anyio
async def test_process_event_fans_out_only_matching(db: Connection, settings: Settings) -> None:
    event = await event_service.create_event(
        db,
        EventCreate(
            type="order.created",
            source="checkout",
            payload={"status": "paid"},
        ),
    )
    matching = await subscription_service.create_subscription(
        db,
        SubscriptionCreate(
            url="https://example.test/match",
            type_filter="order.created",
            source_filter="checkout",
            payload_conditions=[{"path": "status", "eq": "paid"}],
        ),
        default_secret="secret",
    )
    await subscription_service.create_subscription(
        db,
        SubscriptionCreate(
            url="https://example.test/skip",
            type_filter="order.created",
            source_filter="billing",
        ),
        default_secret="secret",
    )

    posted: list[str] = []

    async def fake_post_webhook(*, client, url, secret, event, settings):
        posted.append(url)
        return 200, None, 5

    with patch("consumer.worker.post_webhook", side_effect=fake_post_webhook):
        await process_event(event.id)

    assert posted == ["https://example.test/match"]
    deliveries = await delivery_service.list_deliveries_for_event(db, event.id)
    assert len(deliveries) == 1
    assert deliveries[0].subscription_id == matching.id
    assert deliveries[0].status == "delivered"


@pytest.mark.anyio
async def test_process_event_skips_completed_deliveries(
    db: Connection, settings: Settings
) -> None:
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
    await delivery_service.set_delivery_status(db, delivery_id, "delivered")

    with patch("consumer.worker.post_webhook", new_callable=AsyncMock) as post:
        await process_event(event.id)
        post.assert_not_called()


@pytest.mark.anyio
async def test_process_event_missing_event_is_noop(db: Connection, settings: Settings) -> None:
    with patch("consumer.worker.post_webhook", new_callable=AsyncMock) as post:
        await process_event("missing-event-id")
        post.assert_not_called()


@pytest.mark.anyio
async def test_deliver_with_retries_succeeds_after_failure(
    db: Connection, settings: Settings
) -> None:
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
    event_row = await event_service.get_event_row(db, event.id)
    assert event_row is not None
    sub_rows = await subscription_service.list_active_subscription_rows(db)

    outcomes = [
        (500, "non_2xx", 1),
        (200, None, 2),
    ]

    async def fake_post_webhook(**kwargs):
        return outcomes.pop(0)

    async with httpx.AsyncClient() as client:
        with patch("consumer.worker.post_webhook", side_effect=fake_post_webhook):
            await deliver_with_retries(
                client=client,
                delivery_id=delivery_id,
                subscription=sub_rows[0],
                event=event_row,
            )

    delivery = await delivery_service.get_delivery(db, delivery_id)
    assert delivery is not None
    assert delivery.status == "delivered"
    assert len(delivery.attempts) == 2


@pytest.mark.anyio
async def test_deliver_with_retries_marks_failed(
    db: Connection, settings: Settings
) -> None:
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
    event_row = await event_service.get_event_row(db, event.id)
    assert event_row is not None
    sub_rows = await subscription_service.list_active_subscription_rows(db)

    async def always_fail(**kwargs):
        return 500, "non_2xx", 1

    async with httpx.AsyncClient() as client:
        with patch("consumer.worker.post_webhook", side_effect=always_fail):
            await deliver_with_retries(
                client=client,
                delivery_id=delivery_id,
                subscription=sub_rows[0],
                event=event_row,
            )

    delivery = await delivery_service.get_delivery(db, delivery_id)
    assert delivery is not None
    assert delivery.status == "failed"
    assert len(delivery.attempts) == settings.webhook_max_attempts
