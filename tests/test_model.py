from app.features import NAMES
from app.model import predict, train_tenant
from app.synthetic import generate


def test_seed_and_event_counts():
    a, b = generate(count=20), generate(count=20)
    assert a == b
    assert len({e["id"] for e in a["events"]}) == len(a["events"]) > 20 * 20
    assert {e["source"] for e in a["events"]} == {"product", "zendesk", "stripe", "gmail", "salesforce"}


def test_non_renewal_ends_the_customer_contract_history():
    dataset = generate(count=100)
    by_account = {}
    for renewal in dataset["renewals"]:
        by_account.setdefault(renewal["account_id"], []).append(renewal)
    for renewals in by_account.values():
        renewals.sort(key=lambda row: row["due_at"])
        assert all(not r["churned"] for r in renewals[:-1])
    churned_at = {rs[-1]["account_id"]: rs[-1]["due_at"] for rs in by_account.values() if rs[-1]["churned"]}
    for e in dataset["events"]:
        assert e["occurred_at"] >= "2023-01-01"
        if e["account_id"] in churned_at:
            assert e["occurred_at"] < churned_at[e["account_id"]]


def test_history_is_continuous_not_renewal_windows():
    dataset = generate(count=100)
    months = {e["occurred_at"][:7] for e in dataset["events"] if e["source"] == "product" and e["account_id"]}
    assert len(months) >= 40  # every month 2023-01..2026-12 has usage samples


def test_sparse_training_falls_back_explicitly():
    result = train_tenant([], [])
    assert result["kind"] == "rules_fallback"
    assert result["calibrated"] is False


def test_serialized_linear_prediction_and_contributions():
    model = {
        "kind": "logistic",
        "calibrated": True,
        "version": "test",
        "intercept": -1,
        "coefficients": dict.fromkeys(NAMES, 1.0),
        "calibration_slope": 1,
        "calibration_intercept": 0,
    }
    features = {"values": dict.fromkeys(NAMES, 0.0), "quality": "sufficient"}
    score = predict(model, features)
    assert 0 < score["probability"] < 0.5
    assert score["kind"] == "logistic"
    assert len(score["drivers"]) == len(NAMES)
