import os

import pytest
from fastapi.testclient import TestClient

from app.web import app

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="Needs seeded Postgres")


def login(client, tenant="t1"):
    response = client.get("/login")
    import re

    csrf = re.search('name="csrf" value="([^"]+)"', response.text).group(1)
    return client.post(
        "/login", data={"username": f"demo-{tenant}", "password": os.environ["DEMO_PASSWORD"], "csrf": csrf}
    )


def test_login_worklist_cross_tenant_and_csm_boundary():
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/worklist").status_code == 401
        assert login(client).status_code == 200
        assert client.get("/api/worklist").status_code == 200
        assert client.get("/accounts/a02001").status_code == 404
        assert client.get("/accounts/a00121").status_code == 404
        assert client.get("/accounts/a00001").status_code == 200
        assert client.get("/").headers["content-security-policy"]


def test_state_change_requires_csrf():
    with TestClient(app, base_url="https://testserver") as client:
        login(client)
        response = client.post("/accounts/a00001/draft", data={"csrf": "invalid"})
        assert response.status_code == 403


def test_invalid_login_is_generic():
    with TestClient(app, base_url="https://testserver") as client:
        import re

        csrf = re.search('name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
        response = client.post("/login", data={"username": "unknown", "password": "incorrect", "csrf": csrf})
        assert response.status_code == 401
        assert "Invalid credentials" in response.text
