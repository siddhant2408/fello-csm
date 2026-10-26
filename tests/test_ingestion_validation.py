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
