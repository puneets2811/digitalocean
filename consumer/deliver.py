"""Webhook delivery helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def sign_body(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def post_webhook(
    *,
    client: httpx.AsyncClient,
    url: str,
    secret: str,
    event: dict[str, Any],
    settings: Settings,
) -> tuple[int | None, str | None, int]:
    payload = {
        "id": event["id"],
        "type": event["type"],
        "source": event["source"],
        "payload": event["payload"],
        "created_at": event["created_at"],
    }
    body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Event-Id": event["id"],
        "X-Webhook-Signature": sign_body(secret, body),
    }
    started = time.perf_counter()
    try:
        response = await client.post(
            url,
            content=body,
            headers=headers,
            timeout=settings.webhook_timeout_seconds,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        if 200 <= response.status_code < 300:
            return response.status_code, None, duration_ms
        error = f"non_2xx status={response.status_code} body={response.text[:500]}"
        return response.status_code, error, duration_ms
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.perf_counter() - started) * 1000)
        return None, str(exc), duration_ms
