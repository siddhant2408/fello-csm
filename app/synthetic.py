"""Seeded synthetic world, not a replay of real customer behaviour.
Hidden commercial pressure and product fit drive outcomes. Observed events are noisy,
incomplete proxies; neither labels nor hidden variables are exposed to feature code.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from app.features import SOURCES


def generate(seed: int = 42, count: int = 5000) -> dict:
    rng = np.random.default_rng(seed)
    accounts, renewals, events = [], [], []
    sizes = [2000, 1700, 1270, 30] if count == 5000 else [count // 2, count - count // 2]
    index = 0
    for t, size in enumerate(sizes, 1):
        for local in range(size):
            index += 1
            aid = f"a{index:05d}"
            connected = [s for s in SOURCES if rng.random() > 0.12]
            if not connected:
                connected = ["salesforce"]
            account = {
                "id": aid,
                "tenant_id": f"t{t}",
                "name": f"Example Account {index:05d}",
                "csm_id": f"t{t}-csm-{local // 120:02d}",
                "connected_sources": connected,
                "arr": int(rng.lognormal(9.5, 0.8)),
                "created_at": "2023-01-01T00:00:00+00:00",
            }
            accounts.append(account)
            fit, commercial = rng.normal(), rng.normal()
            month, day = int(rng.integers(1, 13)), int(rng.integers(1, 29))
            for cycle, year in enumerate([2024, 2025, 2026]):
                renewal = datetime(year, month, day, tzinfo=UTC)
                shock = rng.normal()
                distress = -0.7 * fit + 0.8 * shock + 0.5 * commercial
                outcome_probability = 1 / (1 + np.exp(-(-1.35 + distress + rng.normal(0, 0.7))))
                renewals.append(
                    {
                        "id": f"{aid}-{year}",
                        "account_id": aid,
                        "tenant_id": f"t{t}",
                        "due_at": renewal.isoformat(),
                        "known_at": "2023-01-01T00:00:00+00:00",
                        "outcome_known_at": (renewal + timedelta(days=7)).isoformat(),
                        "churned": bool(rng.random() < outcome_probability),
                    }
                )
                # Generate a history reservoir, then sample without replacement globally.
                # Stop contract history at non-renewal; do not resurrect churned accounts.
                n = 40
                for j in range(n):
                    source = (
                        connected[j % len(connected)] if j < len(connected) else str(rng.choice(connected))
                    )
                    age = int(rng.integers(1, 181))
                    occurred = renewal - timedelta(days=age)
                    progress = (181 - age) / 180
                    if source == "product":
                        value = max(0, int(rng.lognormal(4 + 0.3 * fit - 0.9 * distress * progress, 0.45)))
                    elif source == "zendesk":
                        value = int(rng.poisson(np.exp(min(2, -0.5 + 0.65 * distress * progress))))
                    elif source == "stripe":
                        value = int(rng.random() < 1 / (1 + np.exp(2 - 0.8 * distress)))
                    elif source == "gmail":
                        value = int(rng.integers(1, 5))
                    else:
                        value = int(rng.integers(1, 4))
                    delay = int(rng.integers(2, 35)) if rng.random() < 0.18 else int(rng.integers(0, 2))
                    eid = f"e-{aid}-{cycle}-{j:02d}"
                    # An unresolvable source alias is retained for quarantine, never guessed.
                    unresolved = rng.random() < 0.008
                    events.append(
                        {
                            "id": eid,
                            "tenant_id": f"t{t}",
                            "account_id": None if unresolved else aid,
                            "source": source,
                            "source_key": f"{source}:{aid}" if not unresolved else "unknown:" + eid,
                            "occurred_at": occurred.isoformat(),
                            "received_at": (occurred + timedelta(days=delay)).isoformat(),
                            "value": value,
                        }
                    )
                if renewals[-1]["churned"]:
                    break
    selected = rng.choice(len(events), size=count * 40, replace=False)
    events = [events[int(i)] for i in selected]
    rng.shuffle(events)
    return {
        "generator_version": "survival/v2",
        "seed": seed,
        "accounts": accounts,
        "renewals": renewals,
        "events": events,
    }


def save(dataset: dict, path="data/dataset.json"):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dataset))


if __name__ == "__main__":
    data = generate()
    save(data)
    print(
        json.dumps(
            {
                "accounts": len(data["accounts"]),
                "events": len(data["events"]),
                "renewals": len(data["renewals"]),
                "seed": data["seed"],
            }
        )
    )
