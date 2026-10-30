import os

import pytest
from fastapi.testclient import TestClient

from app.web import app

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="Needs seeded Postgres")


def login(client, tenant="t1"):
    response = client.get("/login")
    import re

    csrf = re.search('name="csrf" value="([^"]+)"', response.text).group(1)
    return client.post("/login", data={"username": f"demo-{tenant}", "csrf": csrf})


def test_login_worklist_cross_tenant_and_csm_boundary():
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/worklist").status_code == 401
        assert login(client).status_code == 200
        assert client.get("/api/worklist").status_code == 200
        assert client.get("/accounts/a02001").status_code == 404
        assert client.get("/accounts/a00121").status_code == 404
        assert client.get("/accounts/a00001").status_code == 200
        assert client.get("/").headers["content-security-policy"]


def test_reviewed_account_leaves_both_lanes_and_is_csm_scoped():
    import re

    with TestClient(app, base_url="https://testserver") as client:
        login(client)
        csrf = re.search('name="csrf" value="([^"]+)"', client.get("/accounts/a00001").text).group(1)
        assert client.post("/accounts/a00121/reviewed", data={"csrf": csrf}).status_code == 404
        assert client.post("/accounts/a00001/reviewed", data={"csrf": "invalid"}).status_code == 403
        assert client.post("/accounts/a00001/reviewed", data={"csrf": csrf}).status_code == 200
        lanes = client.get("/api/worklist").json()
        assert "a00001" not in {r["id"] for r in lanes["accounts"] + lanes["needs_data"]}
        assert len(lanes["accounts"]) <= lanes["capacity"]


def test_state_change_requires_csrf():
    with TestClient(app, base_url="https://testserver") as client:
        login(client)
        response = client.post("/accounts/a00001/draft", data={"csrf": "invalid"})
        assert response.status_code == 403


def test_switching_user_switches_tenant():
    with TestClient(app, base_url="https://testserver") as client:
        login(client, "t1")
        assert client.get("/accounts/a00001").status_code == 200
        login(client, "t2")
        assert client.get("/accounts/a00001").status_code == 404


def test_unknown_user_and_missing_csrf_are_rejected():
    import re

    with TestClient(app, base_url="https://testserver") as client:
        csrf = re.search('name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        assert client.post("/login", data={"username": "unknown", "csrf": csrf}).status_code == 404
        assert client.post("/login", data={"username": "demo-t1", "csrf": "invalid"}).status_code == 403


def test_malformed_content_length_is_a_client_error():
    with TestClient(app, base_url="https://testserver") as client:
        assert client.post("/login", headers={"content-length": "abc"}, content=b"").status_code == 400
