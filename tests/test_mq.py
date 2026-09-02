from __future__ import annotations

from app.mq import _safe_token


def test_safe_token_preserves_safe_chars() -> None:
    assert _safe_token("order.created") == "order.created"
    assert _safe_token("checkout-v2") == "checkout-v2"
    assert _safe_token("a_b") == "a_b"


def test_safe_token_replaces_unsafe_chars() -> None:
    assert _safe_token("order created!") == "order_created_"
    assert _safe_token("a/b") == "a_b"
