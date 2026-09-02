from __future__ import annotations

import json
import logging
from typing import Any

import aio_pika
from aio_pika import ExchangeType, Message, DeliveryMode
from aio_pika.abc import AbstractChannel, AbstractRobustConnection

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def connect_rabbitmq(url: str | None = None) -> AbstractRobustConnection:
    settings = get_settings()
    return await aio_pika.connect_robust(url or settings.rabbitmq_url)


async def open_channel(connection: AbstractRobustConnection) -> AbstractChannel:
    """Open a channel with publisher confirms enabled for durable ingest."""
    return await connection.channel(publisher_confirms=True)


async def declare_topology(channel: AbstractChannel, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    exchange = await channel.declare_exchange(
        settings.events_exchange,
        ExchangeType.TOPIC,
        durable=True,
    )
    dlq = await channel.declare_queue(settings.events_dlq, durable=True)
    queue = await channel.declare_queue(
        settings.events_queue,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": settings.events_dlq,
        },
    )
    await queue.bind(exchange, routing_key="#")
    logger.info(
        "mq_topology_ready exchange=%s queue=%s dlq=%s",
        settings.events_exchange,
        settings.events_queue,
        dlq.name,
    )


async def publish_event_id(
    channel: AbstractChannel,
    *,
    event_id: str,
    event_type: str,
    event_source: str,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    exchange = await channel.get_exchange(settings.events_exchange)
    body = json.dumps({"event_id": event_id}).encode("utf-8")
    routing_key = f"{_safe_token(event_type)}.{_safe_token(event_source)}"
    message = Message(
        body=body,
        content_type="application/json",
        delivery_mode=DeliveryMode.PERSISTENT,
        message_id=event_id,
        headers={"event_id": event_id, "type": event_type, "source": event_source},
    )
    await exchange.publish(message, routing_key=routing_key, mandatory=False)
    logger.info("event_published event_id=%s routing_key=%s", event_id, routing_key)


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


async def check_rabbitmq(url: str | None = None) -> dict[str, Any]:
    connection = await connect_rabbitmq(url)
    try:
        async with connection:
            channel = await connection.channel()
            await declare_topology(channel)
            return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
