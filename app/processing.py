"""Transactional ingestion and leased, retry-safe processing. No broker required."""

import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from app.db import transaction
from app.features import compute_features
from app.model import predict

log = logging.getLogger(__name__)
TENANTS = ("t1", "t2", "t3", "t4")


def clock():
    fixed = os.environ.get("SIMULATION_AS_OF")
    return datetime.fromisoformat(fixed) if fixed else datetime.now(UTC)


def ingest_event(conn, event: dict, as_of: datetime) -> bool:
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
                """UPDATE jobs SET status=%s,available_at=now()+interval '5 seconds',lease_until=NULL,last_error=%s
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
    last_reconcile = None
    while True:
        today = clock().replace(hour=0, minute=0, second=0, microsecond=0)
        if today != last_reconcile:
            for tenant in TENANTS:
                with transaction(tenant) as conn:
                    # A crashed final claim becomes inspectably failed, rather than stuck forever.
                    conn.execute(
                        "UPDATE jobs SET status='failed',last_error='LeaseExpired' WHERE status='running' AND lease_until<now() AND attempts>=5"
                    )
                    reconcile(conn, today)
            last_reconcile = today
        worked = False
        for tenant in TENANTS:
            worked = process_one(tenant) or worked
        if not worked:
            time.sleep(1)


if __name__ == "__main__":
    main()
