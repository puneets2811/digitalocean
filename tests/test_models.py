from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import EventCreate, PayloadCondition, SubscriptionCreate


def test_event_create_requires_type_and_source() -> None:
    with pytest.raises(ValidationError):
        EventCreate(type="", source="checkout", payload={})
    with pytest.raises(ValidationError):
        EventCreate(type="order.created", source="", payload={})


def test_event_create_defaults_payload() -> None:
    event = EventCreate(type="order.created", source="checkout")
    assert event.payload == {}


def test_subscription_create_defaults_and_url() -> None:
    sub = SubscriptionCreate(url="https://example.test/hook")
    assert sub.type_filter == "*"
    assert sub.source_filter == "*"
    assert sub.payload_conditions == []
    assert sub.secret is None


def test_subscription_create_rejects_invalid_url() -> None:
    with pytest.raises(ValidationError):
        SubscriptionCreate(url="not-a-url")


def test_payload_condition_requires_path() -> None:
    with pytest.raises(ValidationError):
        PayloadCondition(path="", eq=1)
    condition = PayloadCondition(path="order.status", eq="paid")
    assert condition.path == "order.status"
    assert condition.eq == "paid"
