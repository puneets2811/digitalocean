from app.filters import field_matches, payload_conditions_match, subscription_matches


def test_type_and_source_filters() -> None:
    assert subscription_matches(
        type_filter="order.created",
        source_filter="checkout",
        payload_conditions=[],
        event_type="order.created",
        event_source="checkout",
        payload={},
    )
    assert not subscription_matches(
        type_filter="order.created",
        source_filter="billing",
        payload_conditions=[],
        event_type="order.created",
        event_source="checkout",
        payload={},
    )


def test_wildcard_and_payload_conditions() -> None:
    assert subscription_matches(
        type_filter="*",
        source_filter="*",
        payload_conditions=[{"path": "status", "eq": "paid"}],
        event_type="anything",
        event_source="anywhere",
        payload={"status": "paid"},
    )
    assert not subscription_matches(
        type_filter="*",
        source_filter="*",
        payload_conditions=[{"path": "order.status", "eq": "paid"}],
        event_type="anything",
        event_source="anywhere",
        payload={"order": {"status": "pending"}},
    )
    assert subscription_matches(
        type_filter="*",
        source_filter="*",
        payload_conditions=[{"path": "order.status", "eq": "paid"}],
        event_type="anything",
        event_source="anywhere",
        payload={"order": {"status": "paid"}},
    )


def test_field_matches_wildcards_and_empty() -> None:
    assert field_matches(None, "x")
    assert field_matches("", "x")
    assert field_matches("*", "x")
    assert field_matches("order.created", "order.created")
    assert not field_matches("order.created", "order.updated")


def test_payload_conditions_empty_and_missing_path() -> None:
    assert payload_conditions_match([], {"a": 1})
    assert not payload_conditions_match([{"eq": "paid"}], {"status": "paid"})
    assert not payload_conditions_match(
        [{"path": "missing", "eq": 1}],
        {"other": 1},
    )


def test_payload_conditions_and_semantics() -> None:
    payload = {"a": 1, "b": {"c": 2}}
    assert payload_conditions_match(
        [{"path": "a", "eq": 1}, {"path": "b.c", "eq": 2}],
        payload,
    )
    assert not payload_conditions_match(
        [{"path": "a", "eq": 1}, {"path": "b.c", "eq": 99}],
        payload,
    )


def test_payload_path_through_non_dict() -> None:
    assert not payload_conditions_match(
        [{"path": "items.0", "eq": "x"}],
        {"items": ["x"]},
    )


def test_subscription_matches_requires_all_filters() -> None:
    assert not subscription_matches(
        type_filter="order.created",
        source_filter="*",
        payload_conditions=[{"path": "status", "eq": "paid"}],
        event_type="order.updated",
        event_source="checkout",
        payload={"status": "paid"},
    )
