"""Integration tests require a real Postgres and the actual non-owner application role."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg.errors import InsufficientPrivilege

from app.db import transaction
from app.processing import ingest_event, process_one, reconcile

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="Postgres URL not configured")
TENANT = "t1"
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def test_actual_role_cannot_bypass_rls():
    with transaction(TENANT) as conn:
        role = conn.execute(
            "select rolsuper, rolbypassrls from pg_roles where rolname=current_user"
        ).fetchone()
        assert not role["rolsuper"] and not role["rolbypassrls"]
        assert conn.execute("select count(*) as n from accounts where tenant_id='t2'").fetchone()["n"] == 0
        with pytest.raises(InsufficientPrivilege):
            conn.execute(
                "insert into accounts(id,tenant_id,name,csm_id,connected_sources,arr,created_at) values('bad','t2','bad','bad','[]',1,now())"
            )


def test_no_tenant_context_fails_closed():
    with transaction(None) as conn:
        assert conn.execute("select count(*) as n from accounts").fetchone()["n"] == 0


def test_duplicate_event_and_job_are_idempotent():
    unique = uuid4().hex
    now = NOW.replace(microsecond=int(unique[:5], 16) % 1000000)
    event_id = "integration-" + unique
    with transaction(TENANT) as conn:
        event = {
            "id": event_id,
            "tenant_id": TENANT,
            "source_key": "zendesk:a00001",
            "source": "zendesk",
            "occurred_at": now.isoformat(),
            "received_at": now.isoformat(),
            "value": 7,
        }
        assert ingest_event(conn, event, now) is True
        assert ingest_event(conn, event, now) is False
    assert process_one(TENANT)
    with transaction(TENANT) as conn:
        assert (
            conn.execute(
                "select count(*) as n from snapshots where account_id='a00001' and as_of=%s", (now,)
            ).fetchone()["n"]
            == 1
        )
        conn.execute("update jobs set status='pending' where event_id=%s", (event_id,))
    assert process_one(TENANT)
    with transaction(TENANT) as conn:
        assert (
            conn.execute(
                "select count(*) as n from snapshots where account_id='a00001' and as_of=%s", (now,)
            ).fetchone()["n"]
            == 1
        )


def test_unresolvable_alias_is_quarantined():
    with transaction(TENANT) as conn:
        ingest_event(
            conn,
            {
                "id": "integration-unknown",
                "tenant_id": TENANT,
                "source_key": "missing",
                "source": "gmail",
                "occurred_at": NOW.isoformat(),
                "received_at": NOW.isoformat(),
                "value": 1,
            },
            NOW,
        )
        row = conn.execute(
            "select account_id,quarantined from events where id='integration-unknown'"
        ).fetchone()
        assert row == {"account_id": None, "quarantined": True}


def test_reconciliation_is_idempotent():
    with transaction("t4") as conn:
        now = NOW.replace(microsecond=int(uuid4().hex[:5], 16) % 1000000)
        first = reconcile(conn, now)
        assert first > 0
        assert reconcile(conn, now) == 0
