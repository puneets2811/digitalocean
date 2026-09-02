from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from app.config import Settings
from consumer.deliver import post_webhook, sign_body


def test_sign_body_is_hex_hmac_sha256() -> None:
    body = b'{"id":"evt-1"}'
    secret = "s3cret"
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sign_body(secret, body) == expected


@pytest.mark.anyio
async def test_post_webhook_success_returns_status_and_no_error(settings: Settings) -> None:
    event = {
        "id": "evt-1",
        "type": "order.created",
        "source": "checkout",
        "payload": {"status": "paid"},
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        status, error, duration_ms = await post_webhook(
            client=client,
            url="https://example.test/hook",
            secret="hook-secret",
            event=event,
            settings=settings,
        )

    assert status == 204
    assert error is None
    assert duration_ms >= 0
    assert seen["url"] == "https://example.test/hook"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["x-event-id"] == "evt-1"
    assert headers["x-webhook-signature"] == sign_body("hook-secret", seen["body"])  # type: ignore[arg-type]
    assert json.loads(seen["body"]) == {  # type: ignore[arg-type]
        "id": "evt-1",
        "type": "order.created",
        "source": "checkout",
        "payload": {"status": "paid"},
        "created_at": "2024-01-01T00:00:00+00:00",
    }


@pytest.mark.anyio
async def test_post_webhook_non_2xx_returns_error(settings: Settings) -> None:
    event = {
        "id": "evt-2",
        "type": "t",
        "source": "s",
        "payload": {},
        "created_at": "2024-01-01T00:00:00+00:00",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        status, error, duration_ms = await post_webhook(
            client=client,
            url="https://example.test/hook",
            secret="secret",
            event=event,
            settings=settings,
        )

    assert status == 500
    assert error is not None and "non_2xx" in error
    assert duration_ms >= 0


@pytest.mark.anyio
async def test_post_webhook_transport_error(settings: Settings) -> None:
    event = {
        "id": "evt-3",
        "type": "t",
        "source": "s",
        "payload": {},
        "created_at": "2024-01-01T00:00:00+00:00",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        status, error, duration_ms = await post_webhook(
            client=client,
            url="https://example.test/hook",
            secret="secret",
            event=event,
            settings=settings,
        )

    assert status is None
    assert error is not None and "refused" in error
    assert duration_ms >= 0
