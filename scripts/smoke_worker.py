"""Verify the separately running worker reacts to an incoming event without manual processing."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

config = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines() if "=" in line)
os.environ["DATABASE_URL"] = f"postgresql://fello_app:{config['APP_DB_PASSWORD']}@127.0.0.1:55432/fello"
os.environ["SIMULATION_AS_OF"] = config["SIMULATION_AS_OF"]
from app.db import transaction
from app.processing import clock, ingest_event

now = clock()
eid = "autonomous-smoke-" + uuid4().hex
with transaction("t1") as conn:
    before = conn.execute("SELECT count(*) AS n FROM snapshots WHERE account_id='a00120'").fetchone()["n"]
    ingest_event(
        conn,
        {
            "id": eid,
            "source": "stripe",
            "source_key": "stripe:a00120",
            "occurred_at": now.isoformat(),
            "received_at": now.isoformat(),
            "value": 1,
        },
        now,
    )
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    with transaction("t1") as conn:
        job = conn.execute("SELECT status,attempts FROM jobs WHERE event_id=%s", (eid,)).fetchone()
        after = conn.execute("SELECT count(*) AS n FROM snapshots WHERE account_id='a00120'").fetchone()["n"]
    if job and job["status"] == "done":
        assert after == before + 1
        print(
            json.dumps(
                {
                    "event_id": eid,
                    "job": job,
                    "snapshots_before": before,
                    "snapshots_after": after,
                    "manual_processing": False,
                }
            )
        )
        break
    time.sleep(0.5)
else:
    raise SystemExit("Worker did not complete the event within 60 seconds")
