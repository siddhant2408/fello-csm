"""Fit once, store plain JSON coefficients; inference does not unpickle artifacts."""

import hashlib
import json
import math
from collections import Counter

from app.features import NAMES, VERSION, rules_score, vector


def sigmoid(x):
    return 1 / (1 + math.exp(-max(-40, min(40, x))))


def train_tenant(train: list[dict], calibration: list[dict]) -> dict:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    # Count independent renewal outcomes, not repeated weekly snapshots.
    distinct = {r["renewal_id"]: r["label"] for r in train}
    counts = Counter(distinct.values())
    distinct_cal = {r["renewal_id"]: r["label"] for r in calibration}
    cc = Counter(distinct_cal.values())
    base = {
        "feature_version": VERSION,
        "trained_before": "2026-01-08T00:00:00+00:00",
        "training_renewals": len(distinct),
        "calibration_renewals": len(distinct_cal),
    }
    if min(counts.get(0, 0), counts.get(1, 0)) < 30 or min(cc.get(0, 0), cc.get(1, 0)) < 20:
        model = {
            **base,
            "kind": "rules_fallback",
            "calibrated": False,
            "reason": "Insufficient independent outcomes per class",
        }
    else:
        x = np.array([vector(r["features"]) for r in train])
        y = np.array([r["label"] for r in train])
        pipeline = make_pipeline(StandardScaler(), LogisticRegression(C=1, max_iter=500, random_state=42))
        pipeline.fit(x, y)
        scaler, classifier = pipeline.steps[0][1], pipeline.steps[1][1]
        coefficients = classifier.coef_[0] / scaler.scale_
        intercept = float(classifier.intercept_[0] - np.dot(coefficients, scaler.mean_))
        cx = np.array([vector(r["features"]) for r in calibration])
        logits = pipeline.decision_function(cx).reshape(-1, 1)
        calibrator = LogisticRegression(C=100, max_iter=500).fit(logits, [r["label"] for r in calibration])
        slope = float(calibrator.coef_[0][0])
        model = {
            **base,
            "kind": "logistic",
            "calibrated": True,
            "intercept": intercept,
            "coefficients": dict(zip(NAMES, map(float, coefficients), strict=True)),
            "calibration_slope": slope,
            "calibration_intercept": float(calibrator.intercept_[0]),
        }
    model["version"] = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()[:16]
    return model


def predict(model: dict, features: dict) -> dict:
    if "feature_version" in model and model["feature_version"] != features["version"]:
        raise ValueError("Feature/model version mismatch")
    if model["kind"] == "rules_fallback":
        return {
            "probability": None,
            "priority": rules_score(features),
            "kind": model["kind"],
            "calibrated": False,
            "drivers": [],
        }
    contributions = {
        name: model["coefficients"][name] * features["values"][name] * model["calibration_slope"]
        for name in NAMES
    }
    logit = model["intercept"] * model["calibration_slope"] + model["calibration_intercept"]
    probability = sigmoid(logit + sum(contributions.values()))
    return {
        "probability": probability if features["quality"] == "sufficient" else None,
        "priority": probability,
        "kind": model["kind"],
        "calibrated": features["quality"] == "sufficient",
        "drivers": [
            {"feature": k, "contribution_log_odds": v}
            for k, v in sorted(contributions.items(), key=lambda item: -abs(item[1]))
        ],
    }
