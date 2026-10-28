from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.processing import validate_event


def test_non_finite_measurement_is_rejected():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        validate_event(
            {
                "id": "e",
                "source": "stripe",
                "source_key": "stripe:a1",
                "occurred_at": now.isoformat(),
                "received_at": now.isoformat(),
                "value": float("nan"),
            },
            now,
        )


def test_naive_clock_and_future_receipt_are_rejected():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        validate_event(
            {
                "id": "e",
                "source": "stripe",
                "source_key": "stripe:a1",
                "occurred_at": "2026-06-01",
                "received_at": "2026-06-02T00:00:00+00:00",
                "value": 1,
            },
            now,
        )


def test_replay_clock_advances_from_start_and_stops_at_end(monkeypatch):
    from datetime import timedelta

    from app import processing

    start = datetime(2026, 6, 1, 12, tzinfo=UTC)
    monkeypatch.setenv("SIMULATION_AS_OF", start.isoformat())
    monkeypatch.setenv("SIMULATION_END", "2026-09-01T00:00:00+00:00")
    monkeypatch.setenv("SIMULATION_SPEED", "0")
    assert processing.clock() == start
    monkeypatch.setenv("SIMULATION_SPEED", "96")
    monkeypatch.setattr(processing, "replay_anchor", lambda: datetime.now(UTC) - timedelta(minutes=15))
    assert abs(processing.clock() - (start + timedelta(days=1))) < timedelta(minutes=5)
    monkeypatch.setattr(processing, "replay_anchor", lambda: datetime.now(UTC) - timedelta(days=30))
    assert processing.clock() == datetime(2026, 9, 1, tzinfo=UTC)
