from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_unauthenticated(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_ok(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"]["ok"] is True
    assert body["rabbitmq"]["ok"] is True


def test_ready_not_ready_when_mq_down(client: TestClient) -> None:
    client.check_rabbitmq.return_value = {"ok": False, "error": "down"}  # type: ignore[attr-defined]
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "not_ready"


def test_auth_required(client: TestClient) -> None:
    response = client.get("/subscriptions")
    assert response.status_code == 401

    response = client.get(
        "/subscriptions",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 401


def test_subscription_crud(client: TestClient, auth_headers: dict[str, str]) -> None:
    create = client.post(
        "/subscriptions",
        headers=auth_headers,
        json={
            "url": "https://example.test/hook",
            "type_filter": "order.created",
            "source_filter": "checkout",
            "payload_conditions": [{"path": "status", "eq": "paid"}],
        },
    )
    assert create.status_code == 201
    sub = create.json()
    assert sub["type_filter"] == "order.created"
    assert sub["payload_conditions"] == [{"path": "status", "eq": "paid"}]

    listed = client.get("/subscriptions", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    deleted = client.delete(f"/subscriptions/{sub['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    missing = client.delete(f"/subscriptions/{sub['id']}", headers=auth_headers)
    assert missing.status_code == 404


def test_create_and_get_event(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/events",
        headers=auth_headers,
        json={"type": "order.created", "source": "checkout", "payload": {"status": "paid"}},
    )
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "accepted"
    assert "id" in body
    client.publish_event_id.assert_awaited()  # type: ignore[attr-defined]

    fetched = client.get(f"/events/{body['id']}", headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["payload"] == {"status": "paid"}

    missing = client.get("/events/does-not-exist", headers=auth_headers)
    assert missing.status_code == 404


def test_create_event_publish_failure(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.publish_event_id.side_effect = RuntimeError("broker unavailable")  # type: ignore[attr-defined]
    response = client.post(
        "/events",
        headers=auth_headers,
        json={"type": "order.created", "source": "checkout", "payload": {}},
    )
    assert response.status_code == 503
    assert "Failed to publish" in response.json()["detail"]


def test_delivery_query_endpoints(client: TestClient, auth_headers: dict[str, str]) -> None:
    sub = client.post(
        "/subscriptions",
        headers=auth_headers,
        json={"url": "https://example.test/hook"},
    ).json()
    event = client.post(
        "/events",
        headers=auth_headers,
        json={"type": "t", "source": "s", "payload": {}},
    ).json()

    # Seed a delivery row via service layer using the same DB path
    import asyncio

    from app.config import get_settings
    from app.db import connect
    from app.services import deliveries as delivery_service

    async def seed() -> str:
        async with connect(get_settings().database_path) as conn:
            return await delivery_service.ensure_pending_delivery(
                conn, event_id=event["id"], subscription_id=sub["id"]
            )

    delivery_id = asyncio.run(seed())

    by_event = client.get(f"/events/{event['id']}/deliveries", headers=auth_headers)
    assert by_event.status_code == 200
    assert len(by_event.json()) == 1
    assert by_event.json()[0]["id"] == delivery_id

    by_sub = client.get(f"/subscriptions/{sub['id']}/deliveries", headers=auth_headers)
    assert by_sub.status_code == 200
    assert by_sub.json()[0]["event_id"] == event["id"]

    one = client.get(f"/deliveries/{delivery_id}", headers=auth_headers)
    assert one.status_code == 200
    assert one.json()["status"] == "pending"

    assert client.get("/events/missing/deliveries", headers=auth_headers).status_code == 404
    assert (
        client.get("/subscriptions/missing/deliveries", headers=auth_headers).status_code == 404
    )
    assert client.get("/deliveries/missing", headers=auth_headers).status_code == 404


def test_get_channel_unavailable() -> None:
    from app import main as main_mod

    main_mod.state.channel = None
    with pytest.raises(Exception) as exc_info:
        main_mod.get_channel()
    assert getattr(exc_info.value, "status_code", None) == 503
