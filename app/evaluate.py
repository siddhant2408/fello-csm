"""Weekly capacity-constrained historical replay. Final test is never used for fitting.
Only sufficient-quality rows are ranked; insufficient rows form a separate needs-data lane
ordered by renewal date, so imputed values never decide who a CSM calls."""

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from sklearn.metrics import brier_score_loss, roc_auc_score

from app.features import NAMES, compute_features, rules_score, timestamp
from app.model import predict, train_tenant
from app.policy import CAPACITY, COOLDOWN_DAYS, WINDOW_DAYS

MODEL_AS_OF = datetime(2026, 1, 8, tzinfo=UTC)
CAPACITIES = (3, 5, CAPACITY)  # smaller k shows whether ordering matters
RANDOM_SEEDS = 20


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
        first = due - timedelta(days=WINDOW_DAYS[1])
        monday = first + timedelta(days=(-first.weekday()) % 7)
        while (due - monday).days >= WINDOW_DAYS[0]:
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


def capacity_metrics(rows, scores, capacity=CAPACITY):
    """Replay each CSM's weeks in order. A touched account sits out the cooldown, as in the product."""
    by_csm = defaultdict(lambda: defaultdict(list))
    for r, s in zip(rows, scores, strict=True):
        by_csm[r["csm_id"]][r["week"]].append((s, r))
    selected = []
    for weeks in by_csm.values():
        touched = {}
        for week in sorted(weeks):
            when = timestamp(week)
            pool = [
                (s, r)
                for s, r in weeks[week]
                if (when - touched.get(r["account_id"], when - timedelta(days=COOLDOWN_DAYS))).days
                >= COOLDOWN_DAYS
            ]
            for _, r in sorted(pool, key=lambda x: (-x[0], x[1]["account_id"]))[:capacity]:
                selected.append(r)
                touched[r["account_id"]] = when
    positive = {r["renewal_id"] for r in rows if r["label"]}
    leads = defaultdict(int)
    for r in selected:
        if r["label"]:
            leads[r["renewal_id"]] = max(leads[r["renewal_id"]], r["lead_days"])
    return {
        "eligible_churned_renewals": len(positive),
        "caught_renewals": len(leads),
        "recall_at_60_days": len(leads) / len(positive) if positive else None,
        "recall_at_90_days": sum(d >= 90 for d in leads.values()) / len(positive) if positive else None,
        "touches": len(selected),
        "touch_precision": float(np.mean([r["label"] for r in selected])) if selected else None,
        "false_positive_touches": sum(not r["label"] for r in selected),
    }


def mean_metrics(results):
    return {
        k: float(np.mean([r[k] for r in results])) if results[0][k] is not None else None for k in results[0]
    }


def ranking_report(rows, methods):
    """Compare methods at each weekly capacity against the mean of seeded random orderings."""
    report = {}
    randoms = [np.random.default_rng(seed).random(len(rows)) for seed in range(RANDOM_SEEDS)]
    for k in CAPACITIES:
        chance = mean_metrics([capacity_metrics(rows, list(s), k) for s in randoms])
        report[f"k{k}"] = {"random_mean": chance}
        for name, scores in methods.items():
            m = capacity_metrics(rows, scores, k)
            m["precision_lift_over_random"] = (
                m["touch_precision"] / chance["touch_precision"] if chance["touch_precision"] else None
            )
            report[f"k{k}"][name] = m
    return report


def auc(rows, scores):
    return roc_auc_score([r["label"] for r in rows], scores) if len({r["label"] for r in rows}) > 1 else None


def feature_screen(rows):
    """Cohort check before modelling: single-feature AUC on training rows. ~0.5 means no signal."""
    return {n: auc(rows, [r["features"]["values"][n] for r in rows]) for n in NAMES}


def evaluate(data, output="data"):
    rows = examples(data)
    arr = {a["id"]: a["arr"] for a in data["accounts"]}
    models, report = {}, {}
    for tenant in sorted({a["tenant_id"] for a in data["accounts"]}):
        train = [r for r in rows if r["tenant_id"] == tenant and r["year"] == 2024]
        calibration = [r for r in rows if r["tenant_id"] == tenant and r["year"] == 2025]
        test = [r for r in rows if r["tenant_id"] == tenant and r["year"] == 2026]
        model = train_tenant(train, calibration)
        models[tenant] = model
        ranked = [r for r in test if r["features"]["quality"] == "sufficient"]
        needs_data = [r for r in test if r["features"]["quality"] != "sufficient"]
        predictions = [predict(model, r["features"]) for r in ranked]
        methods = {
            "model": [p["priority"] for p in predictions],
            "rules": [rules_score(r["features"]) for r in ranked],
            "arr": [arr[r["account_id"]] for r in ranked],
        }
        sufficient = [
            (r, p) for r, p in zip(ranked, predictions, strict=True) if p["probability"] is not None
        ]
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
        weekly = defaultdict(int)
        for r in ranked:
            weekly[(r["csm_id"], r["week"])] += 1
        ranked_churn = {r["renewal_id"] for r in ranked if r["label"]}
        report[tenant] = {
            "model_kind": model["kind"],
            "version": model["version"],
            "test_predictions": len(test),
            "coverage_sufficient": len(ranked),
            "ranked_per_csm_week": {
                "median": float(np.median(list(weekly.values()))) if weekly else 0,
                "max": max(weekly.values(), default=0),
            },
            "auc": auc(ranked, methods["model"]),
            "auc_by_method": {name: auc(ranked, scores) for name, scores in methods.items()},
            "ranking": ranking_report(ranked, methods),
            "needs_data_lane": {
                "rows": len(needs_data),
                "churn_rate": float(np.mean([r["label"] for r in needs_data])) if needs_data else None,
                "churned_renewals_never_ranked": len(
                    {r["renewal_id"] for r in needs_data if r["label"]} - ranked_churn
                ),
            },
            "brier_sufficient_only": brier_score_loss(
                [r["label"] for r, p in sufficient], [p["probability"] for r, p in sufficient]
            )
            if sufficient
            else None,
            "calibration_bins_sufficient_only": reliability,
            "feature_screen_train_auc": feature_screen(train) if train else None,
        }
    summary = {
        "seed": data["seed"],
        "account_count": len(data["accounts"]),
        "event_count": len(data["events"]),
        "quarantined_events": sum(e["account_id"] is None for e in data["events"]),
        "split": "2024 train; 2025 calibration; 2026 test, scores on/after 2026-01-08",
        "ranking_policy": "Sufficient-quality rows ranked by priority; insufficient rows in a needs-data lane by renewal date",
        "limitations": [
            "Synthetic world only: not real retention uplift",
            "Weekly observations are correlated",
            "Replay assumes every listed account is reviewed and then sits out a 28-day cooldown",
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
