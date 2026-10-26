import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg.errors import InsufficientPrivilege

from app.db import transaction
from app.processing import compute_snapshot, ingest_event, process_one

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="Needs Postgres")


def test_rls_rejects_cross_tenant_write_on_granted_table():
    with transaction("t1") as conn, pytest.raises(InsufficientPrivilege):
        conn.execute(
            """INSERT INTO events(tenant_id,id,source,source_key,occurred_at,received_at,value)
                          VALUES('t2',%s,'gmail','x',now(),now(),1)""",
            (uuid4().hex,),
        )


def test_failed_job_retries_then_commits_once(monkeypatch):
    from app import processing

    eid = "retry-" + uuid4().hex
    now = datetime(2026, 6, 1, 13, tzinfo=UTC)
    with transaction("t3") as conn:
        alias = conn.execute(
            "SELECT source_key FROM aliases WHERE source='zendesk' ORDER BY account_id LIMIT 1"
        ).fetchone()["source_key"]
        ingest_event(
            conn,
            {
                "id": eid,
                "source": "zendesk",
                "source_key": alias,
                "occurred_at": now.isoformat(),
                "received_at": now.isoformat(),
                "value": 3,
            },
            now,
        )

    def fail(*args):
        raise ValueError("injected test failure")

    monkeypatch.setattr(processing, "compute_snapshot", fail)
    assert process_one("t3")
    with transaction("t3") as conn:
        job = conn.execute("SELECT * FROM jobs WHERE event_id=%s", (eid,)).fetchone()
        assert job["status"] == "pending" and job["attempts"] == 1
        assert job["last_error"] == "ValueError"
        conn.execute("UPDATE jobs SET available_at=now() WHERE event_id=%s", (eid,))
    monkeypatch.setattr(processing, "compute_snapshot", compute_snapshot)
    assert process_one("t3")
    with transaction("t3") as conn:
        job = conn.execute("SELECT * FROM jobs WHERE event_id=%s", (eid,)).fetchone()
        assert job["status"] == "done" and job["attempts"] == 2


def test_incoming_account_identity_cannot_cross_tenant():
    eid = "forged-" + uuid4().hex
    now = datetime(2026, 6, 1, tzinfo=UTC)
    with transaction("t1") as conn:
        ingest_event(
            conn,
            {
                "id": eid,
                "tenant_id": "t2",
                "account_id": "a02001",
                "source": "gmail",
                "source_key": "gmail:a02001",
                "occurred_at": now.isoformat(),
                "received_at": now.isoformat(),
                "value": 1,
            },
            now,
        )
        event = conn.execute(
            "SELECT tenant_id,account_id,quarantined FROM events WHERE id=%s", (eid,)
        ).fetchone()
        assert event == {"tenant_id": "t1", "account_id": None, "quarantined": True}
