"""Canonical feature definition v1. Both event clocks enforce historical availability."""

import math
from datetime import datetime

VERSION = "account-risk-features/v1"
NAMES = ["usage_drop", "urgent_tickets", "payment_failures", "contact_gap", "coverage"]
SOURCES = ["salesforce", "gmail", "zendesk", "product", "stripe"]


def timestamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def compute_features(account: dict, events: list[dict], as_of: datetime) -> dict:
    usable = [
        e
        for e in events
        if e.get("account_id", "valid") is not None
        and timestamp(e["received_at"]) <= as_of
        and 0 <= (as_of - timestamp(e["occurred_at"])).total_seconds() / 86400 <= 180
    ]
    recent, prior, tickets, payments, contacts = [], [], [], [], []
    for e in usable:
        age = (as_of - timestamp(e["occurred_at"])).total_seconds() / 86400
        if e["source"] == "product":
            (recent if age <= 45 else prior).append(float(e["value"]))
        elif e["source"] == "zendesk" and age <= 60:
            tickets.append(float(e["value"]))
        elif e["source"] == "stripe" and age <= 60:
            payments.append(float(e["value"]))
        elif e["source"] == "gmail":
            contacts.append(age)
    baseline = sum(prior) / len(prior) if prior else None
    current = sum(recent) / len(recent) if recent else None
    # Missing usage is not an observed zero: quality gating is independent of imputation.
    drop = (
        max(-1.0, min(1.0, 1 - current / max(baseline, 1)))
        if baseline is not None and current is not None
        else 0
    )
    coverage = len(account["connected_sources"]) / len(SOURCES)
    quality = "sufficient" if coverage >= 0.6 and recent and prior else "insufficient_data"
    values = {
        "usage_drop": round(drop, 5),
        "urgent_tickets": min(sum(tickets), 20),
        "payment_failures": min(sum(payments), 10),
        "contact_gap": min(contacts, default=180) / 180,
        "coverage": coverage,
    }
    return {
        "version": VERSION,
        "values": values,
        "quality": quality,
        "evidence_ids": sorted(e["id"] for e in usable),
        "observations": {
            "usage_recent_mean": current,
            "usage_prior_mean": baseline,
            "product_observations": len(recent) + len(prior),
        },
    }


def vector(features: dict) -> list[float]:
    return [features["values"][name] for name in NAMES]


def rules_score(features: dict) -> float:
    v = features["values"]
    logit = (
        -2 + 2 * v["usage_drop"] + 0.25 * v["urgent_tickets"] + 0.4 * v["payment_failures"] + v["contact_gap"]
    )
    return 1 / (1 + math.exp(-logit))
