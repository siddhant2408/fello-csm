import os

import pytest
from fastapi.testclient import TestClient

from app.web import app

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="Needs seeded Postgres")


def login(client, tenant="t1"):
    return client.post("/login", data={"username": f"demo-{tenant}"})


def test_login_worklist_cross_tenant_and_csm_boundary():
    # Plain HTTP on purpose: the demo must work without a TLS proxy.
    with TestClient(app, base_url="http://testserver") as client:
        assert client.get("/api/worklist").status_code == 401
        assert login(client).status_code == 200
        assert client.get("/api/worklist").status_code == 200
        assert client.get("/accounts/a02001").status_code == 404
        assert client.get("/accounts/a00121").status_code == 404
        assert client.get("/accounts/a00001").status_code == 200
        assert client.get("/").headers["content-security-policy"]


def test_session_cookie_is_same_site_strict():
    # This is what stops cross-site form posts, in place of CSRF tokens.
    with TestClient(app, base_url="http://testserver") as client:
        response = client.post("/login", data={"username": "demo-t1"}, follow_redirects=False)
        assert "samesite=strict" in response.headers["set-cookie"].lower()


def test_reviewed_account_leaves_both_lanes_and_is_csm_scoped():
    with TestClient(app, base_url="http://testserver") as client:
        login(client)
        assert client.post("/accounts/a00121/reviewed").status_code == 404
        assert client.post("/accounts/a00001/reviewed").status_code == 200
        lanes = client.get("/api/worklist").json()
        assert "a00001" not in {r["id"] for r in lanes["accounts"] + lanes["needs_data"]}
        assert len(lanes["accounts"]) <= lanes["capacity"]


def test_switching_user_switches_tenant():
    with TestClient(app, base_url="http://testserver") as client:
        login(client, "t1")
        assert client.get("/accounts/a00001").status_code == 200
        login(client, "t2")
        assert client.get("/accounts/a00001").status_code == 404


def test_unknown_user_is_rejected():
    with TestClient(app, base_url="http://testserver") as client:
        assert client.post("/login", data={"username": "unknown"}).status_code == 404


def test_malformed_content_length_is_a_client_error():
    with TestClient(app, base_url="http://testserver") as client:
        assert client.post("/login", headers={"content-length": "abc"}, content=b"").status_code == 400
