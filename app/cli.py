import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from app.db import transaction
from app.processing import TENANTS, clock, ingest_event, process_one, reconcile


def drain_tenant(tenant):
    total = 0
    while process_one(tenant):
        total += 1
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["drain", "event", "status"])
    parser.add_argument("--tenant", choices=TENANTS, default="t1")
    parser.add_argument("--account", default="a00001")
    parser.add_argument(
        "--source", choices=["product", "zendesk", "gmail", "stripe", "salesforce"], default="zendesk"
    )
    parser.add_argument("--value", type=float, default=5)
    args = parser.parse_args()
    now = clock()
    if args.command == "drain":
        for tenant in TENANTS:
            with transaction(tenant) as conn:
                reconcile(conn, now)
        with ThreadPoolExecutor(max_workers=4) as pool:
            totals = list(pool.map(drain_tenant, TENANTS))
        print(json.dumps({"processed": dict(zip(TENANTS, totals, strict=True)), "as_of": now.isoformat()}))
    elif args.command == "event":
        event = {
            "id": "incoming-" + uuid4().hex,
            "source": args.source,
            "source_key": f"{args.source}:{args.account}",
            "occurred_at": (now - timedelta(days=4)).isoformat(),
            "received_at": now.isoformat(),
            "value": args.value,
        }
        with transaction(args.tenant) as conn:
            ingest_event(conn, event, now)
        print(json.dumps({"ingested": event["id"], "tenant": args.tenant, "worker_triggered": "durable_job"}))
    else:
        with transaction(args.tenant) as conn:
            print(
                json.dumps(conn.execute("SELECT status,count(*) AS n FROM jobs GROUP BY status").fetchall())
            )


if __name__ == "__main__":
    main()
