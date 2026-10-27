from datetime import UTC, datetime, timedelta

from app.evaluate import capacity_metrics


def row(account, week, label=0):
    monday = datetime(2026, 2, 2, tzinfo=UTC) + timedelta(weeks=week)
    return {
        "csm_id": "c",
        "account_id": account,
        "renewal_id": account,
        "week": monday.isoformat(),
        "lead_days": 150 - 7 * week,
        "label": label,
    }


def test_cooldown_frees_capacity_for_the_next_account():
    # "hot" always outranks "cold"; with capacity 1, cooldown must let "cold" in during week 1.
    rows = [row("hot", w) for w in range(5)] + [row("cold", w, label=1) for w in range(5)]
    scores = [0.9] * 5 + [0.1] * 5
    m = capacity_metrics(rows, scores, capacity=1)
    assert m["caught_renewals"] == 1
    assert m["touches"] == 3  # hot (week 0), cold (week 1), hot again after 28 days (week 4)
    assert m["recall_at_90_days"] == 1.0
