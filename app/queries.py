from app.policy import CAPACITY, COOLDOWN_DAYS, WINDOW_DAYS
from app.processing import clock


def owned_account(conn, user, account_id):
    return conn.execute(
        "SELECT * FROM accounts WHERE id=%s AND csm_id=%s", (account_id, user["csm_id"])
    ).fetchone()


def worklist(conn, user):
    """Ranked lane: sufficient evidence, ordered by stored priority, capped at weekly capacity.
    Needs-data lane: everything else, ordered by renewal date. Imputed priorities never rank it."""
    now = clock()
    rows = conn.execute(
        """SELECT a.id,a.name,a.arr,r.due_at,s.id AS snapshot_id,s.as_of,s.features,s.result
        FROM accounts a JOIN renewals r ON r.tenant_id=a.tenant_id AND r.account_id=a.id
        LEFT JOIN LATERAL (SELECT * FROM snapshots WHERE account_id=a.id AND as_of<=%(now)s AND renewal_id=r.id
                           ORDER BY as_of DESC,created_at DESC LIMIT 1) s ON true
        WHERE a.csm_id=%(csm)s AND r.known_at<=%(now)s
          AND r.due_at BETWEEN %(now)s+make_interval(days=>%(lo)s) AND %(now)s+make_interval(days=>%(hi)s)
          AND NOT EXISTS (SELECT 1 FROM reviews v WHERE v.account_id=a.id
                          AND v.reviewed_at>%(now)s-make_interval(days=>%(cooldown)s) AND v.reviewed_at<=%(now)s)
        ORDER BY (s.result->>'priority')::float DESC NULLS LAST,a.id""",
        {
            "now": now,
            "csm": user["csm_id"],
            "lo": WINDOW_DAYS[0],
            "hi": WINDOW_DAYS[1],
            "cooldown": COOLDOWN_DAYS,
        },
    ).fetchall()
    sufficient = [r for r in rows if r["features"] and r["features"]["quality"] == "sufficient"]
    needs_data = [r for r in rows if r not in sufficient]
    return {
        "ranked": sufficient[:CAPACITY],
        "needs_data": sorted(needs_data, key=lambda r: (r["due_at"], r["id"])),
    }


def record_review(conn, user, account_id):
    conn.execute(
        """INSERT INTO reviews(tenant_id,account_id,csm_id,reviewed_at)
        VALUES(current_setting('app.tenant_id'),%s,%s,%s)""",
        (account_id, user["csm_id"], clock()),
    )


def account_details(conn, user, account_id):
    account = owned_account(conn, user, account_id)
    if not account:
        return None
    snapshots = conn.execute(
        "SELECT * FROM snapshots WHERE account_id=%s AND as_of<=%s ORDER BY as_of DESC,created_at DESC LIMIT 10",
        (account_id, clock()),
    ).fetchall()
    latest = snapshots[0] if snapshots else None
    events, draft = [], None
    if latest:
        events = conn.execute(
            "SELECT * FROM events WHERE account_id=%s AND id=ANY(%s) ORDER BY occurred_at DESC",
            (account_id, latest["features"]["evidence_ids"]),
        ).fetchall()
        row = conn.execute("SELECT body FROM drafts WHERE snapshot_id=%s", (latest["id"],)).fetchone()
        draft = row["body"] if row else None
    review = conn.execute(
        "SELECT max(reviewed_at) AS at FROM reviews WHERE account_id=%s AND reviewed_at<=%s",
        (account_id, clock()),
    ).fetchone()
    return {
        "account": account,
        "snapshot": latest,
        "history": snapshots,
        "events": events,
        "draft": draft,
        "last_review": review["at"],
    }
