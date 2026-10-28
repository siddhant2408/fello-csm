"""Transactional ingestion and leased, retry-safe processing. No broker required."""

import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from functools import cache

from psycopg.types.json import Jsonb

from app.db import transaction
from app.features import compute_features
from app.model import predict

log = logging.getLogger(__name__)


def tenants() -> list[str]:
    with transaction(None) as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM tenants ORDER BY id")]


@cache
def replay_anchor() -> datetime:
    # Wall time the replay began, written once at seed so web, worker and restarts share one clock.
    with transaction(None) as conn:
        return conn.execute("SELECT started_at FROM replay").fetchone()["started_at"]


def clock():
    start = os.environ.get("SIMULATION_AS_OF")
    if not start:
        return datetime.now(UTC)
    start = datetime.fromisoformat(start)
    speed = float(os.environ.get("SIMULATION_SPEED") or 0)
    if not speed:
        return start
    end = datetime.fromisoformat(os.environ.get("SIMULATION_END") or "2026-09-01T00:00:00+00:00")
    return min(end, start + (datetime.now(UTC) - replay_anchor()) * speed)


def validate_event(event: dict, as_of: datetime) -> dict:
    from typing import Literal

    from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

    class SourceEvent(BaseModel):
        model_config = ConfigDict(extra="ignore")
        id: str = Field(min_length=1, max_length=160)
        source: Literal["salesforce", "gmail", "zendesk", "product", "stripe"]
        source_key: str = Field(min_length=1, max_length=200)
        occurred_at: AwareDatetime
        received_at: AwareDatetime
        value: float = Field(ge=0, le=1000000, allow_inf_nan=False)

    parsed = SourceEvent.model_validate(event)
    if as_of.tzinfo is None or parsed.received_at > as_of:
        raise ValueError("Receipt must be available at the aware scoring clock")
    return parsed.model_dump(mode="json")


def ingest_event(conn, event: dict, as_of: datetime) -> bool:
    event = validate_event(event, as_of)
    # Canonical identity is derived from scoped aliases, never caller-provided account_id.
    account = conn.execute(
        "SELECT account_id FROM aliases WHERE source=%s AND source_key=%s",
        (event["source"], event["source_key"]),
    ).fetchone()
    account_id = account["account_id"] if account else None
    row = conn.execute(
        """INSERT INTO events(tenant_id,id,account_id,source,source_key,occurred_at,received_at,value,quarantined)
        VALUES(current_setting('app.tenant_id'),%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
        (
            event["id"],
            account_id,
            event["source"],
            event["source_key"],
            event["occurred_at"],
            event["received_at"],
            event["value"],
            account_id is None,
        ),
    ).fetchone()
    if row and account_id:
        conn.execute(
            """INSERT INTO jobs(tenant_id,event_id,account_id,as_of) VALUES(current_setting('app.tenant_id'),%s,%s,%s)
                     ON CONFLICT DO NOTHING""",
            (event["id"], account_id, as_of),
        )
    return row is not None


def feed(conn, as_of: datetime, limit: int = 200) -> int:
    """Deliver held-back source events whose receipt time the replay clock has reached."""
    # DELETE..RETURNING hands each delivery to one transaction; a concurrent feeder deletes nothing twice.
    rows = conn.execute(
        """DELETE FROM inbox WHERE id IN (SELECT id FROM inbox WHERE received_at<=%s
        ORDER BY received_at,id LIMIT %s) RETURNING *""",
        (as_of, limit),
    ).fetchall()
    for row in rows:
        try:
            with conn.transaction():
                ingest_event(conn, row, as_of)
        except ValueError as exc:  # pydantic ValidationError included; a bad delivery must not block the feed
            log.warning("feed_rejected tenant=%s event=%s error=%s", row["tenant_id"], row["id"], exc)
    return len(rows)


def reconcile(conn, as_of: datetime) -> int:
    rows = conn.execute(
        """INSERT INTO jobs(tenant_id,event_id,account_id,as_of)
        SELECT tenant_id,'reconcile:'||id||':'||%s,id,%s FROM accounts
        ON CONFLICT DO NOTHING RETURNING id""",
        (as_of.isoformat(), as_of),
    ).fetchall()
    return len(rows)


def compute_snapshot(conn, account_id, as_of):
    account = conn.execute("SELECT * FROM accounts WHERE id=%s", (account_id,)).fetchone()
    model = conn.execute(
        "SELECT artifact FROM models WHERE created_at<=%s ORDER BY created_at DESC,version LIMIT 1", (as_of,)
    ).fetchone()
    if not account or not model:
        raise ValueError("Account or model unavailable at scoring date")
    events = conn.execute(
        """SELECT * FROM events WHERE account_id=%s AND occurred_at BETWEEN %s AND %s
                           AND received_at<=%s ORDER BY id""",
        (account_id, as_of - timedelta(days=180), as_of, as_of),
    ).fetchall()
    features = compute_features(account, events, as_of)
    artifact = model["artifact"]
    result = predict(artifact, features)
    renewal = conn.execute(
        """SELECT id FROM renewals WHERE account_id=%s AND due_at>=%s AND known_at<=%s
                           ORDER BY due_at LIMIT 1""",
        (account_id, as_of, as_of),
    ).fetchone()
    digest = hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest()
    sid = hashlib.sha256(
        f"{account['tenant_id']}:{account_id}:{as_of.isoformat()}:{artifact['version']}:{digest}".encode()
    ).hexdigest()[:32]
    conn.execute(
        """INSERT INTO snapshots(tenant_id,id,account_id,renewal_id,as_of,model_version,input_hash,features,result)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (
            account["tenant_id"],
            sid,
            account_id,
            renewal["id"] if renewal else None,
            as_of,
            artifact["version"],
            digest,
            Jsonb(features),
            Jsonb(result),
        ),
    )
    return sid


def process_one(tenant: str) -> bool:
    with transaction(tenant) as conn:
        # Lease expiry must be checked on every poll, not just daily reconciliation.
        conn.execute(
            "UPDATE jobs SET status='failed',last_error='LeaseExpired',lease_until=NULL WHERE status='running' AND lease_until<now() AND attempts>=5"
        )
        job = conn.execute("""UPDATE jobs SET status='running',attempts=attempts+1,lease_until=now()+interval '2 minutes'
            WHERE id=(SELECT id FROM jobs WHERE
              ((status='pending' AND available_at<=now()) OR (status='running' AND lease_until<now())) AND attempts<5
              ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""").fetchone()
    if not job:
        return False
    try:
        with transaction(tenant) as conn:
            # Lock and check fencing token. A stale claimant cannot commit after a lease takeover.
            locked = conn.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (job["id"],)).fetchone()
            if locked["attempts"] != job["attempts"] or locked["status"] != "running":
                return True
            compute_snapshot(conn, job["account_id"], job["as_of"])
            conn.execute(
                "UPDATE jobs SET status='done',lease_until=NULL,last_error=NULL WHERE id=%s", (job["id"],)
            )
    except Exception as exc:  # noqa: BLE001 - worker boundary records and retries all job failures
        log.warning("job_failed tenant=%s job=%s error_type=%s", tenant, job["id"], type(exc).__name__)
        with transaction(tenant) as conn:
            conn.execute(
                """UPDATE jobs SET status=%s,available_at=now()+interval '5 seconds'*power(2,attempts-1),lease_until=NULL,last_error=%s
                          WHERE id=%s AND attempts=%s""",
                (
                    "failed" if job["attempts"] >= 5 else "pending",
                    type(exc).__name__,
                    job["id"],
                    job["attempts"],
                ),
            )
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    reconciled = {}  # tenant -> last reconciled day; a newly onboarded tenant is scored on first sight
    active, refreshed = [], 0.0
    while True:
        if time.monotonic() - refreshed > 60:
            active, refreshed = tenants(), time.monotonic()
        now = clock()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for tenant in active:
            if reconciled.get(tenant) != today:
                with transaction(tenant) as conn:
                    reconcile(conn, today)
                reconciled[tenant] = today
        worked = False
        for tenant in active:
            with transaction(tenant) as conn:
                worked = feed(conn, now) > 0 or worked
            worked = process_one(tenant) or worked
        if not worked:
            time.sleep(1)


if __name__ == "__main__":
    main()
