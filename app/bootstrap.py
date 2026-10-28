"""Administrative, one-shot bootstrap. The web/worker never receive this DSN."""

import json
import os
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.evaluate import evaluate
from app.synthetic import generate, save


def main():
    path = Path("data/dataset.json")
    if not path.exists():
        dataset = generate()
        save(dataset)
    else:
        dataset = json.loads(path.read_text())
    models_path = Path("data/models.json")
    if not models_path.exists():
        evaluate(dataset)
    models = json.loads(models_path.read_text())
    password = os.environ["APP_DB_PASSWORD"]
    demo_password = os.environ["DEMO_PASSWORD"]
    if len(password) < 20 or len(demo_password) < 16:
        raise ValueError("Provision strong random database/demo credentials")
    with psycopg.connect(os.environ["ADMIN_DATABASE_URL"]) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='fello_app'").fetchone():
            conn.execute(
                sql.SQL("CREATE ROLE fello_app LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                    sql.Literal(password)
                )
            )
        conn.execute(Path("app/schema.sql").read_text())
        tables = [
            "accounts",
            "aliases",
            "renewals",
            "events",
            "inbox",
            "models",
            "jobs",
            "snapshots",
            "drafts",
            "reviews",
        ]
        for table in tables:
            ident = sql.Identifier(table)
            conn.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(ident))
            conn.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(ident))
            conn.execute(sql.SQL("DROP POLICY IF EXISTS tenant_isolation ON {}").format(ident))
            conn.execute(
                sql.SQL(
                    "CREATE POLICY tenant_isolation ON {} USING (tenant_id=current_setting('app.tenant_id',true)) WITH CHECK (tenant_id=current_setting('app.tenant_id',true))"
                ).format(ident)
            )
            conn.execute(sql.SQL("GRANT SELECT ON {} TO fello_app").format(ident))
        conn.execute("GRANT INSERT ON events,jobs,snapshots,drafts,reviews TO fello_app")
        conn.execute("GRANT UPDATE ON jobs TO fello_app")
        conn.execute("GRANT INSERT,DELETE ON inbox TO fello_app")
        conn.execute("GRANT SELECT ON replay TO fello_app")
        conn.execute("INSERT INTO replay DEFAULT VALUES ON CONFLICT DO NOTHING")
        conn.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO fello_app")
        conn.execute("GRANT EXECUTE ON FUNCTION fello_login(text,text) TO fello_app")
        if conn.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0:
            with conn.cursor().copy(
                "COPY accounts(tenant_id,id,name,csm_id,connected_sources,arr,created_at) FROM STDIN"
            ) as copy:
                for a in dataset["accounts"]:
                    copy.write_row(
                        (
                            a["tenant_id"],
                            a["id"],
                            a["name"],
                            a["csm_id"],
                            Jsonb(a["connected_sources"]),
                            a["arr"],
                            a["created_at"],
                        )
                    )
            with conn.cursor().copy(
                "COPY aliases(tenant_id,source,source_key,account_id) FROM STDIN"
            ) as copy:
                for a in dataset["accounts"]:
                    for source in a["connected_sources"]:
                        copy.write_row((a["tenant_id"], source, f"{source}:{a['id']}", a["id"]))
            with conn.cursor().copy(
                "COPY renewals(tenant_id,id,account_id,due_at,known_at) FROM STDIN"
            ) as copy:
                for r in dataset["renewals"]:
                    copy.write_row((r["tenant_id"], r["id"], r["account_id"], r["due_at"], r["known_at"]))
            # Deliveries after the replay start are held back for the worker to ingest as the clock reaches them.
            start = os.environ.get("SIMULATION_AS_OF")
            start = datetime.fromisoformat(start) if start else None
            held = [
                e for e in dataset["events"] if start and datetime.fromisoformat(e["received_at"]) > start
            ]
            with conn.cursor().copy(
                "COPY inbox(tenant_id,id,source,source_key,occurred_at,received_at,value) FROM STDIN"
            ) as copy:
                for e in held:
                    copy.write_row(
                        (
                            e["tenant_id"],
                            e["id"],
                            e["source"],
                            e["source_key"],
                            e["occurred_at"],
                            e["received_at"],
                            e["value"],
                        )
                    )
            held_ids = {e["id"] for e in held}
            with conn.cursor().copy(
                "COPY events(tenant_id,id,account_id,source,source_key,occurred_at,received_at,value,quarantined) FROM STDIN"
            ) as copy:
                for e in dataset["events"]:
                    if e["id"] in held_ids:
                        continue
                    copy.write_row(
                        (
                            e["tenant_id"],
                            e["id"],
                            e["account_id"],
                            e["source"],
                            e["source_key"],
                            e["occurred_at"],
                            e["received_at"],
                            e["value"],
                            e["account_id"] is None,
                        )
                    )
        for tenant, artifact in models.items():
            conn.execute(
                "INSERT INTO models(tenant_id,version,artifact,created_at) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    tenant,
                    artifact["version"],
                    Jsonb(artifact),
                    datetime.fromisoformat(artifact["trained_before"]),
                ),
            )
            csm = conn.execute(
                "SELECT csm_id FROM accounts WHERE tenant_id=%s ORDER BY id LIMIT 1", (tenant,)
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO users(username,id,tenant_id,csm_id,password_hash)
                VALUES(%s,%s,%s,%s,crypt(%s,gen_salt('bf',12))) ON CONFLICT DO NOTHING""",
                (f"demo-{tenant}", f"user-{tenant}", tenant, csm, demo_password),
            )
        # Counts are verified before committing the seed transaction.
        counts = {
            t: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
            for t in ["accounts", "events", "inbox", "renewals"]
        }
        if (
            counts["accounts"] != 5000
            or counts["renewals"] != len(dataset["renewals"])
            or counts["events"] + counts["inbox"] < 200000
        ):
            raise ValueError("Unexpected existing dataset; refusing destructive reset")
        print(json.dumps({"seeded": counts, "role": "fello_app", "rls_tables": len(tables)}))


if __name__ == "__main__":
    main()
