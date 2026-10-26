from app.processing import clock


def owned_account(conn, user, account_id):
    return conn.execute(
        "SELECT * FROM accounts WHERE id=%s AND csm_id=%s", (account_id, user["csm_id"])
    ).fetchone()


def worklist(conn, user):
    now = clock()
    return conn.execute(
        """SELECT a.id,a.name,a.arr,r.due_at,s.id AS snapshot_id,s.as_of,s.features,s.result
        FROM accounts a JOIN renewals r ON r.tenant_id=a.tenant_id AND r.account_id=a.id
        LEFT JOIN LATERAL (SELECT * FROM snapshots WHERE account_id=a.id AND as_of<=%s AND renewal_id=r.id
                           ORDER BY as_of DESC,created_at DESC LIMIT 1) s ON true
        WHERE a.csm_id=%s AND r.known_at<=%s AND r.due_at BETWEEN %s+interval '60 days' AND %s+interval '112 days'
        ORDER BY (s.result->>'priority')::float DESC NULLS LAST,a.id LIMIT 10""",
        (now, user["csm_id"], now, now, now),
    ).fetchall()


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
    return {"account": account, "snapshot": latest, "history": snapshots, "events": events, "draft": draft}
