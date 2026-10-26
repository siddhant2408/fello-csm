"""Weekly capacity-constrained historical replay. Final test is never used for fitting."""

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from sklearn.metrics import brier_score_loss, roc_auc_score

from app.features import compute_features, rules_score, timestamp
from app.model import predict, train_tenant

MODEL_AS_OF = datetime(2026, 1, 8, tzinfo=UTC)


def examples(data):
    accounts = {a["id"]: a for a in data["accounts"]}
    by_account = defaultdict(list)
    for event in data["events"]:
        if event["account_id"]:
            by_account[event["account_id"]].append(event)
    rows = []
    for renewal in data["renewals"]:
        account = accounts[renewal["account_id"]]
        due = timestamp(renewal["due_at"])
        first = due - timedelta(days=112)
        monday = first + timedelta(days=(-first.weekday()) % 7)
        while (due - monday).days >= 60:
            # Model release date must precede every final-test prediction.
            if due.year != 2026 or monday >= MODEL_AS_OF:
                rows.append(
                    {
                        "tenant_id": account["tenant_id"],
                        "csm_id": account["csm_id"],
                        "account_id": account["id"],
                        "renewal_id": renewal["id"],
                        "year": due.year,
                        "week": monday.isoformat(),
                        "lead_days": (due - monday).days,
                        "label": int(renewal["churned"]),
                        "features": compute_features(account, by_account[account["id"]], monday),
                    }
                )
            monday += timedelta(days=7)
    return rows


def capacity_metrics(rows, scores, capacity=10):
    groups = defaultdict(list)
    for r, s in zip(rows, scores, strict=True):
        groups[(r["csm_id"], r["week"])].append((s, r))
    selected = []
    for group in groups.values():
        selected.extend(r for _, r in sorted(group, key=lambda x: (-x[0], x[1]["account_id"]))[:capacity])
    positive = {r["renewal_id"] for r in rows if r["label"]}
    caught = {r["renewal_id"] for r in selected if r["label"]}
    leads = defaultdict(int)
    for r in selected:
        if r["label"]:
            leads[r["renewal_id"]] = max(leads[r["renewal_id"]], r["lead_days"])
    return {
        "capacity_per_csm_week": capacity,
        "eligible_churned_renewals": len(positive),
        "caught_renewals": len(caught),
        "recall_at_60_days": len(caught) / len(positive) if positive else None,
        "touches": len(selected),
        "touch_precision": float(np.mean([r["label"] for r in selected])) if selected else None,
        "false_positive_touches": sum(not r["label"] for r in selected),
        "median_first_warning_days": float(np.median(list(leads.values()))) if leads else None,
        "repeat_policy": "May reappear weekly; capacity counts each appearance. No claimed intervention uplift.",
    }


def evaluate(data, output="data"):
    rows = examples(data)
    models, report = {}, {}
    for tenant in sorted({a["tenant_id"] for a in data["accounts"]}):
        train = [r for r in rows if r["tenant_id"] == tenant and r["year"] == 2024]
        calibration = [r for r in rows if r["tenant_id"] == tenant and r["year"] == 2025]
        test = [r for r in rows if r["tenant_id"] == tenant and r["year"] == 2026]
        model = train_tenant(train, calibration)
        models[tenant] = model
        predictions = [predict(model, r["features"]) for r in test]
        scores = [p["priority"] for p in predictions]
        baseline = [rules_score(r["features"]) for r in test]
        sufficient = [(r, p) for r, p in zip(test, predictions, strict=True) if p["probability"] is not None]
        reliability = []
        for low in np.arange(0, 1, 0.2):
            bucket = [(r, p) for r, p in sufficient if low <= p["probability"] < low + 0.2]
            if bucket:
                reliability.append(
                    {
                        "lower": float(low),
                        "n": len(bucket),
                        "predicted": float(np.mean([p["probability"] for r, p in bucket])),
                        "observed": float(np.mean([r["label"] for r, p in bucket])),
                    }
                )
        report[tenant] = {
            "model_kind": model["kind"],
            "version": model["version"],
            "test_predictions": len(test),
            "coverage_sufficient": len(sufficient),
            "model": capacity_metrics(test, scores),
            "rules": capacity_metrics(test, baseline),
            "brier_sufficient_only": brier_score_loss(
                [r["label"] for r, p in sufficient], [p["probability"] for r, p in sufficient]
            )
            if sufficient
            else None,
            "auc": roc_auc_score([r["label"] for r in test], scores)
            if len({r["label"] for r in test}) > 1
            else None,
            "calibration_bins_sufficient_only": reliability,
        }
    summary = {
        "seed": data["seed"],
        "account_count": len(data["accounts"]),
        "event_count": len(data["events"]),
        "quarantined_events": sum(e["account_id"] is None for e in data["events"]),
        "split": "2024 train; 2025 calibration; 2026 test, scores on/after 2026-01-08",
        "limitations": [
            "Synthetic world only: not real retention uplift",
            "Weekly observations are correlated",
            "Repeated flags consume weekly capacity",
            "Insufficient coverage scores are priorities, not calibrated probabilities",
            "No real integrations; no external causal validation",
        ],
        "tenants": report,
    }
    Path(output).mkdir(parents=True, exist_ok=True)
    Path(output, "models.json").write_text(json.dumps(models, indent=2))
    Path(output, "evaluation.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return models, summary


if __name__ == "__main__":
    evaluate(json.loads(Path("data/dataset.json").read_text()))
