from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Response, status
from aio_pika.abc import AbstractChannel, AbstractRobustConnection

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.db import connect, init_db
from app.models import (
    DeliveryOut,
    EventAccepted,
    EventCreate,
    EventOut,
    SubscriptionCreate,
    SubscriptionOut,
)
from app.mq import check_rabbitmq, connect_rabbitmq, declare_topology, open_channel
from app.services import deliveries as delivery_service
from app.services import events as event_service
from app.services import subscriptions as subscription_service

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


class AppState:
    connection: AbstractRobustConnection | None = None
    channel: AbstractChannel | None = None


state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await init_db(settings.database_path)
    state.connection = await connect_rabbitmq(settings.rabbitmq_url)
    state.channel = await open_channel(state.connection)
    await declare_topology(state.channel, settings)
    logger.info("api_started")
    try:
        yield
    finally:
        if state.connection is not None:
            await state.connection.close()
        logger.info("api_stopped")


app = FastAPI(
    title="Event Fanout Service",
    version="1.0.0",
    lifespan=lifespan,
)


def get_channel() -> AbstractChannel:
    if state.channel is None:
        raise HTTPException(status_code=503, detail="RabbitMQ channel unavailable")
    return state.channel


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    db_ok = False
    db_error: str | None = None
    try:
        async with connect(settings.database_path) as conn:
            await conn.execute("SELECT 1")
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        db_error = str(exc)

    mq = await check_rabbitmq(settings.rabbitmq_url)
    ready_ok = db_ok and bool(mq.get("ok"))
    payload = {
        "status": "ready" if ready_ok else "not_ready",
        "database": {"ok": db_ok, "error": db_error},
        "rabbitmq": mq,
    }
    if not ready_ok:
        raise HTTPException(status_code=503, detail=payload)
    return payload


@app.post(
    "/events",
    response_model=EventAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
async def create_event(
    body: EventCreate,
    settings: Settings = Depends(get_settings),
    channel: AbstractChannel = Depends(get_channel),
) -> EventAccepted:
    async with connect(settings.database_path) as conn:
        try:
            event = await event_service.ingest_event(conn, channel, body)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ingest_failed error=%s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to publish event to RabbitMQ",
            ) from exc
    logger.info("event_accepted event_id=%s type=%s source=%s", event.id, event.type, event.source)
    return EventAccepted(id=event.id, status=event.status)


@app.get(
    "/events/{event_id}",
    response_model=EventOut,
    dependencies=[Depends(require_api_key)],
)
async def get_event(event_id: str, settings: Settings = Depends(get_settings)) -> EventOut:
    async with connect(settings.database_path) as conn:
        event = await event_service.get_event(conn, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.post(
    "/subscriptions",
    response_model=SubscriptionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
async def create_subscription(
    body: SubscriptionCreate,
    settings: Settings = Depends(get_settings),
) -> SubscriptionOut:
    async with connect(settings.database_path) as conn:
        return await subscription_service.create_subscription(
            conn, body, settings.webhook_default_secret
        )


@app.get(
    "/subscriptions",
    response_model=list[SubscriptionOut],
    dependencies=[Depends(require_api_key)],
)
async def list_subscriptions(settings: Settings = Depends(get_settings)) -> list[SubscriptionOut]:
    async with connect(settings.database_path) as conn:
        return await subscription_service.list_subscriptions(conn)


@app.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_api_key)],
)
async def delete_subscription(
    subscription_id: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    async with connect(settings.database_path) as conn:
        deleted = await subscription_service.delete_subscription(conn, subscription_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get(
    "/events/{event_id}/deliveries",
    response_model=list[DeliveryOut],
    dependencies=[Depends(require_api_key)],
)
async def list_event_deliveries(
    event_id: str,
    settings: Settings = Depends(get_settings),
) -> list[DeliveryOut]:
    async with connect(settings.database_path) as conn:
        event = await event_service.get_event(conn, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        return await delivery_service.list_deliveries_for_event(conn, event_id)


@app.get(
    "/subscriptions/{subscription_id}/deliveries",
    response_model=list[DeliveryOut],
    dependencies=[Depends(require_api_key)],
)
async def list_subscription_deliveries(
    subscription_id: str,
    settings: Settings = Depends(get_settings),
) -> list[DeliveryOut]:
    async with connect(settings.database_path) as conn:
        sub = await subscription_service.get_subscription(conn, subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return await delivery_service.list_deliveries_for_subscription(conn, subscription_id)


@app.get(
    "/deliveries/{delivery_id}",
    response_model=DeliveryOut,
    dependencies=[Depends(require_api_key)],
)
async def get_delivery(delivery_id: str, settings: Settings = Depends(get_settings)) -> DeliveryOut:
    async with connect(settings.database_path) as conn:
        delivery = await delivery_service.get_delivery(conn, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return delivery
