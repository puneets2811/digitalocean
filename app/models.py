from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class PayloadCondition(BaseModel):
    path: str = Field(..., min_length=1, description="Dotted path into the event payload")
    eq: Any = Field(..., description="Expected value at path (equality)")


class EventCreate(BaseModel):
    type: str = Field(..., min_length=1, max_length=256)
    source: str = Field(..., min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)


class EventOut(BaseModel):
    id: str
    type: str
    source: str
    payload: dict[str, Any]
    status: str
    created_at: str


class SubscriptionCreate(BaseModel):
    url: HttpUrl
    type_filter: str | None = Field(default="*", max_length=256)
    source_filter: str | None = Field(default="*", max_length=256)
    payload_conditions: list[PayloadCondition] = Field(default_factory=list)
    secret: str | None = Field(default=None, max_length=512)


class SubscriptionOut(BaseModel):
    id: str
    url: str
    type_filter: str
    source_filter: str
    payload_conditions: list[PayloadCondition]
    active: bool
    created_at: str


class DeliveryAttemptOut(BaseModel):
    id: int
    attempt_no: int
    at: str
    http_status: int | None
    error: str | None
    duration_ms: int | None


DeliveryStatus = Literal["pending", "delivered", "failed"]


class DeliveryOut(BaseModel):
    id: str
    event_id: str
    subscription_id: str
    status: DeliveryStatus
    created_at: str
    updated_at: str
    attempts: list[DeliveryAttemptOut] = Field(default_factory=list)


class EventAccepted(BaseModel):
    id: str
    status: str = "accepted"
