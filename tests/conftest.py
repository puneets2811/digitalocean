from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import init_db


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Iterator[Settings]:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "webhooks.db"))
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("WEBHOOK_DEFAULT_SECRET", "test-webhook-secret")
    monkeypatch.setenv("WEBHOOK_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("WEBHOOK_BACKOFF_BASE_SECONDS", "0")
    monkeypatch.setenv("WEBHOOK_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
async def db(settings: Settings):
    await init_db(settings.database_path)
    from app.db import connect

    async with connect(settings.database_path) as conn:
        yield conn


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-api-key"}


@pytest.fixture
def client(settings: Settings, auth_headers: dict[str, str]) -> Iterator[TestClient]:
    """HTTP client with temp DB and mocked RabbitMQ (no broker required)."""
    mock_connection = AsyncMock()
    mock_connection.close = AsyncMock()
    mock_channel = AsyncMock()

    @asynccontextmanager
    async def test_lifespan(app: FastAPI) -> AsyncIterator[None]:
        from app import main as main_mod

        await init_db(settings.database_path)
        main_mod.state.connection = mock_connection
        main_mod.state.channel = mock_channel
        yield
        main_mod.state.connection = None
        main_mod.state.channel = None

    with (
        patch("app.services.events.publish_event_id", new_callable=AsyncMock) as publish_mock,
        patch("app.main.check_rabbitmq", new_callable=AsyncMock) as ready_mq,
    ):
        publish_mock.return_value = None
        ready_mq.return_value = {"ok": True}

        from app.main import app

        original_lifespan = app.router.lifespan_context
        app.router.lifespan_context = test_lifespan
        try:
            with TestClient(app) as test_client:
                test_client.publish_event_id = publish_mock  # type: ignore[attr-defined]
                test_client.check_rabbitmq = ready_mq  # type: ignore[attr-defined]
                test_client.auth_headers = auth_headers  # type: ignore[attr-defined]
                yield test_client
        finally:
            app.router.lifespan_context = original_lifespan
