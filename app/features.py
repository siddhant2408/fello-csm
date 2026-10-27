"""Canonical feature definition v2. Both event clocks enforce historical availability."""

import math
from datetime import datetime

VERSION = "account-risk-features/v2"
# Coverage is a data-quality gate, not a risk input. Salesforce activity reflects CSM effort,
# not account health, so it is deliberately not a model input.
NAMES = ["usage_drop", "usage_level", "urgent_tickets", "payment_failures", "contact_gap"]
SOURCES = ["salesforce", "gmail", "zendesk", "product", "stripe"]
# Median observed gap (about 60 days) in 2024 training rows with Gmail connected.
# A disconnected source is unknown, not silent, so it must not impute maximum risk.
NEUTRAL_CONTACT_GAP = 60 / 180
# Median usage_level in 2024 training rows with recent samples. Imputing 0 would read as
# extreme non-use and flatten the fitted usage relationship.
NEUTRAL_USAGE_LEVEL = 4.0


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
    gap = min(contacts, default=180) / 180 if "gmail" in account["connected_sources"] else NEUTRAL_CONTACT_GAP
    values = {
        "usage_drop": round(drop, 5),
        "usage_level": round(math.log1p(current), 5) if current is not None else NEUTRAL_USAGE_LEVEL,
        "urgent_tickets": min(sum(tickets), 20),
        "payment_failures": min(sum(payments), 10),
        "contact_gap": gap,
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
