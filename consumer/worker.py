from __future__ import annotations

import asyncio
import json
import logging

import httpx
from aio_pika import IncomingMessage

from app.config import get_settings
from app.db import connect, init_db
from app.filters import subscription_matches
from app.mq import connect_rabbitmq, declare_topology, open_channel
from app.services import deliveries as delivery_service
from app.services import events as event_service
from app.services import subscriptions as subscription_service
from consumer.deliver import post_webhook

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def process_event(event_id: str) -> None:
    settings = get_settings()
    async with connect(settings.database_path) as conn:
        event = await event_service.get_event_row(conn, event_id)
        if event is None:
            logger.error("event_missing event_id=%s", event_id)
            return

        subscriptions = await subscription_service.list_active_subscription_rows(conn)
        matched = [
            sub
            for sub in subscriptions
            if subscription_matches(
                type_filter=sub["type_filter"],
                source_filter=sub["source_filter"],
                payload_conditions=sub["payload_conditions"],
                event_type=event["type"],
                event_source=event["source"],
                payload=event["payload"],
            )
        ]
        logger.info(
            "fanout_start event_id=%s matches=%s subscriptions=%s",
            event_id,
            len(matched),
            len(subscriptions),
        )

        delivery_jobs: list[tuple[str, dict]] = []
        for sub in matched:
            delivery_id = await delivery_service.ensure_pending_delivery(
                conn,
                event_id=event_id,
                subscription_id=sub["id"],
            )
            status = await delivery_service.get_delivery_status(conn, delivery_id)
            if status in {"delivered", "failed"}:
                logger.info(
                    "delivery_skip delivery_id=%s status=%s",
                    delivery_id,
                    status,
                )
                continue
            delivery_jobs.append((delivery_id, sub))

    async with httpx.AsyncClient() as client:
        for delivery_id, sub in delivery_jobs:
            await deliver_with_retries(
                client=client,
                delivery_id=delivery_id,
                subscription=sub,
                event=event,
            )


async def deliver_with_retries(
    *,
    client: httpx.AsyncClient,
    delivery_id: str,
    subscription: dict,
    event: dict,
) -> None:
    settings = get_settings()
    async with connect(settings.database_path) as conn:
        current = await delivery_service.get_delivery_status(conn, delivery_id)
        if current in {"delivered", "failed"}:
            return

    for attempt_no in range(1, settings.webhook_max_attempts + 1):
        http_status, error, duration_ms = await post_webhook(
            client=client,
            url=subscription["url"],
            secret=subscription["secret"],
            event=event,
            settings=settings,
        )
        async with connect(settings.database_path) as conn:
            await delivery_service.record_attempt(
                conn,
                delivery_id=delivery_id,
                attempt_no=attempt_no,
                http_status=http_status,
                error=error,
                duration_ms=duration_ms,
            )

        logger.info(
            "delivery_attempt delivery_id=%s subscription_id=%s attempt=%s http_status=%s error=%s",
            delivery_id,
            subscription["id"],
            attempt_no,
            http_status,
            error,
        )

        if error is None and http_status is not None and 200 <= http_status < 300:
            async with connect(settings.database_path) as conn:
                await delivery_service.set_delivery_status(conn, delivery_id, "delivered")
            logger.info("delivery_delivered delivery_id=%s", delivery_id)
            return

        if attempt_no < settings.webhook_max_attempts:
            delay = settings.webhook_backoff_base_seconds * (2 ** (attempt_no - 1))
            await asyncio.sleep(delay)

    async with connect(settings.database_path) as conn:
        await delivery_service.set_delivery_status(conn, delivery_id, "failed")
    logger.error("delivery_failed delivery_id=%s", delivery_id)


async def on_message(message: IncomingMessage) -> None:
    async with message.process(requeue=False):
        try:
            payload = json.loads(message.body.decode("utf-8"))
            event_id = payload.get("event_id") or message.message_id
            if not event_id:
                logger.error("message_missing_event_id body=%s", message.body[:200])
                return
            await process_event(event_id)
        except Exception:
            logger.exception("message_processing_failed")
            raise


async def run() -> None:
    settings = get_settings()
    await init_db(settings.database_path)
    connection = await connect_rabbitmq(settings.rabbitmq_url)
    channel = await open_channel(connection)
    await channel.set_qos(prefetch_count=1)
    await declare_topology(channel, settings)
    queue = await channel.get_queue(settings.events_queue)
    logger.info("consumer_started queue=%s", settings.events_queue)
    await queue.consume(on_message)
    try:
        await asyncio.Future()
    finally:
        await connection.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
