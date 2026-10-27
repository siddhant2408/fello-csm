"""Seeded synthetic world, not a replay of real customer behaviour.
Each account has a hidden monthly health state that drifts and takes occasional shocks.
Every source is a noisy readout of that state; renewal outcomes depend on recent health,
commercial pressure and true urgent support load. Neither labels nor hidden state are
exposed to feature code. Collection problems (disconnects, delays, unresolved aliases)
are applied after the world is simulated.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from app.features import SOURCES

DATA_START = datetime(2023, 1, 1, tzinfo=UTC)  # oldest history the connectors retain
SIM_END = datetime(2027, 1, 1, tzinfo=UTC)
STEP = timedelta(days=30)


def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def anniversary(created: datetime, year: int) -> datetime:
    return created.replace(year=year)


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
            # Staggered starts: about half predate retained history, the rest joined later.
            created = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=int(rng.integers(0, 6 * 365)))
            created = created.replace(day=min(created.day, 28))
            account = {
                "id": aid,
                "tenant_id": f"t{t}",
                "name": f"Example Account {index:05d}",
                "csm_id": f"t{t}-csm-{local // 120:02d}",
                "connected_sources": connected,
                "arr": int(rng.lognormal(9.5, 0.8)),
                "created_at": created.isoformat(),
            }
            accounts.append(account)
            fit, commercial = rng.normal(), rng.normal()
            engagement = rng.lognormal(0, 0.6)  # heavy-tailed: a minority of accounts barely interact
            usage_base = rng.lognormal(4, 0.5)
            health, history, urgent_history = fit, [], []
            start = max(created, DATA_START)
            year = created.year + 1
            while anniversary(created, year) < start:
                year += 1
            due = anniversary(created, year)
            t0, end, n = start, SIM_END, 0
            raw = []
            while t0 < end:
                # Health reverts toward product fit, drifts, and occasionally drops sharply.
                health = 0.85 * health + 0.15 * fit + rng.normal(0, 0.35)
                if rng.random() < 0.02:
                    health -= rng.uniform(1, 2.5)
                history.append(health)
                rates = {
                    "product": 1,  # one aggregate usage sample per month
                    "zendesk": rng.poisson(engagement * 0.25 * np.exp(-0.6 * health)),
                    "gmail": rng.poisson(engagement * 0.35 * np.exp(0.4 * health)),
                    "stripe": rng.poisson(0.04 * np.exp(0.9 * commercial - 0.3 * health)),
                    # CSM-logged activity reflects CSM effort, which today is not driven by account health.
                    "salesforce": rng.poisson(engagement * 0.12),
                }
                urgent = 0
                for source, k in rates.items():
                    for _ in range(k):
                        occurred = t0 + timedelta(seconds=int(rng.integers(0, int(STEP.total_seconds()))))
                        if source == "product":
                            value = int(usage_base * np.exp(0.45 * health) * rng.lognormal(0, 0.25))
                        elif source == "zendesk":
                            value = int(rng.poisson(0.3 + 0.6 * max(0, -health)))
                            urgent += value
                        elif source == "stripe":
                            value = 1
                        elif source == "gmail":
                            value = int(rng.integers(1, 5))
                        else:
                            value = int(rng.integers(1, 4))
                        raw.append((source, occurred, value))
                urgent_history.append(urgent)
                t0 += STEP
                if due < min(t0, SIM_END):
                    # Decision depends on the last four months, not the scoring-time features.
                    recent_health = float(np.mean(history[-4:]))
                    logit = -2.0 - 1.1 * recent_health + 0.5 * commercial + 0.15 * sum(urgent_history[-4:])
                    churned = bool(rng.random() < 0.03 or rng.random() < sigmoid(logit))
                    renewals.append(
                        {
                            "id": f"{aid}-{due.year}",
                            "account_id": aid,
                            "tenant_id": f"t{t}",
                            "due_at": due.isoformat(),
                            "known_at": (due - timedelta(days=365)).isoformat(),
                            "outcome_known_at": (due + timedelta(days=7)).isoformat(),
                            "churned": churned,
                        }
                    )
                    if churned:
                        end = due  # no contract history after non-renewal
                        break
                    year += 1
                    due = anniversary(created, year)
            for source, occurred, value in raw:
                if source not in connected or occurred >= end:
                    continue
                n += 1
                delay = int(rng.integers(2, 35)) if rng.random() < 0.18 else int(rng.integers(0, 2))
                eid = f"e-{aid}-{n:04d}"
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
    rng.shuffle(events)
    return {
        "generator_version": "lifecycle/v3",
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
