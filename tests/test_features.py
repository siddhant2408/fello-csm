from datetime import UTC, datetime, timedelta

from app.features import compute_features

NOW = datetime(2026, 6, 1, tzinfo=UTC)
ACCOUNT = {"connected_sources": ["product", "zendesk", "stripe", "gmail", "salesforce"]}


def event(source="product", age=10, delay=0, value=10, id="e1"):
    return {
        "id": id,
        "source": source,
        "occurred_at": (NOW - timedelta(days=age)).isoformat(),
        "received_at": (NOW - timedelta(days=age - delay)).isoformat(),
        "value": value,
    }


def test_future_and_not_yet_received_events_are_excluded():
    expected = compute_features(ACCOUNT, [event()], NOW)
    actual = compute_features(
        ACCOUNT, [event(), event(age=-1, id="future"), event(age=5, delay=10, id="late")], NOW
    )
    assert actual == expected


def test_disconnected_usage_is_not_zero_usage():
    result = compute_features({"connected_sources": ["gmail"]}, [], NOW)
    assert result["values"]["coverage"] == 0.2
    assert result["quality"] == "insufficient_data"
    assert result["evidence_ids"] == []


def test_evidence_only_includes_used_window():
    result = compute_features(ACCOUNT, [event(age=400, id="old"), event(id="used")], NOW)
    assert result["evidence_ids"] == ["used"]
