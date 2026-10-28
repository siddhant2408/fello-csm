import hmac
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.types.json import Jsonb
from starlette.middleware.sessions import SessionMiddleware

from app.db import transaction
from app.drafting import evidence_facts, generate_draft
from app.policy import CAPACITY, COOLDOWN_DAYS, WINDOW_DAYS
from app.processing import clock
from app.queries import account_details, owned_account, record_review, worklist


@asynccontextmanager
async def lifespan(app):
    if len(os.environ.get("SESSION_SECRET", "")) < 32:
        raise RuntimeError("Provision SESSION_SECRET before starting")
    yield


app = FastAPI(title="Renewal Risk Worklist", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "not-configured"),
    https_only=os.environ.get("COOKIE_SECURE", "true").lower() == "true",
    same_site="strict",
    max_age=3600,
)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
attempts = defaultdict(list)  # Single web process only. DB independently limits each username.


@app.middleware("http")
async def security_headers(request, call_next):
    if request.method == "POST":
        length = request.headers.get("content-length", "0")
        if not length.isdigit():
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        if int(length) > 16384:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        # All POSTs also carry CSRF tokens. Reject chunked bodies to keep the cap enforceable.
        if request.headers.get("transfer-encoding"):
            return JSONResponse({"detail": "Content-Length required"}, status_code=411)
    response = await call_next(request)
    response.headers.update(
        {
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        }
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def csrf_token(request):
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


def verify_csrf(request, token):
    if not hmac.compare_digest(request.session.get("csrf", ""), token) or not token:
        raise HTTPException(403, "Invalid CSRF token")


def current_user(request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Login required")
    return user


def page(request, name, **context):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "csrf": csrf_token(request),
            "user": request.session.get("user"),
            "simulation": clock().strftime("%Y-%m-%d %H:%M UTC")
            if os.environ.get("SIMULATION_AS_OF")
            else None,
            "cooldown": COOLDOWN_DAYS,
            **context,
        },
    )


@app.get("/health")
def health():
    with transaction(None) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "simulation_as_of": clock().isoformat()}


@app.get("/login")
def login_form(request: Request):
    return page(request, "login.html", error=None)


@app.post("/login")
def login(
    request: Request,
    username: str = Form(max_length=100),
    password: str = Form(max_length=72),
    csrf: str = Form(max_length=100),
):
    verify_csrf(request, csrf)
    # Do not trust forwarded client headers. A production proxy needs an explicit trusted-proxy setup.
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts[ip] = [t for t in attempts[ip] if t > now - 900]
    if len(attempts[ip]) >= 30:
        raise HTTPException(429, "Too many login attempts")
    attempts[ip].append(now)
    with transaction(None) as conn:
        user = conn.execute("SELECT * FROM fello_login(%s,%s,%s)", (username, password, ip)).fetchone()
    if not user:
        response = page(request, "login.html", error="Invalid credentials")
        response.status_code = 401
        return response
    request.session.clear()
    request.session["user"] = user
    csrf_token(request)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form(max_length=100)):
    verify_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/")
def index(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    user = current_user(request)
    with transaction(user["tenant_id"]) as conn:
        lanes = worklist(conn, user)
        jobs = conn.execute("SELECT status,count(*) AS n FROM jobs GROUP BY status").fetchall()
    return page(
        request,
        "worklist.html",
        **lanes,
        jobs=jobs,
        as_of=clock(),
        capacity=CAPACITY,
        window=WINDOW_DAYS,
    )


@app.get("/api/worklist")
def api_worklist(request: Request):
    user = current_user(request)
    with transaction(user["tenant_id"]) as conn:
        lanes = worklist(conn, user)
    return {
        "accounts": lanes["ranked"],
        "needs_data": lanes["needs_data"],
        "capacity": CAPACITY,
        "as_of": clock(),
    }


@app.get("/accounts/{account_id}")
def detail(request: Request, account_id: str):
    user = current_user(request)
    with transaction(user["tenant_id"]) as conn:
        details = account_details(conn, user, account_id)
    if not details:
        raise HTTPException(404, "Account not found")
    return page(request, "account.html", **details)


@app.post("/accounts/{account_id}/draft")
def draft(request: Request, account_id: str, csrf: str = Form(max_length=100)):
    user = current_user(request)
    verify_csrf(request, csrf)
    with transaction(user["tenant_id"]) as conn:
        details = account_details(conn, user, account_id)
    if not details:
        raise HTTPException(404, "Account not found")
    if not details["snapshot"]:
        raise HTTPException(409, "Account has not been scored")
    if not details["draft"]:
        snapshot = details["snapshot"]
        # No arbitrary URLs, raw source text, names, emails or other tenants enter the model context.
        body = generate_draft(evidence_facts(snapshot, details["events"]))
        with transaction(user["tenant_id"]) as conn:
            conn.execute(
                "INSERT INTO drafts(tenant_id,snapshot_id,body) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (user["tenant_id"], snapshot["id"], Jsonb(body)),
            )
    return RedirectResponse(f"/accounts/{account_id}", status_code=303)


@app.post("/accounts/{account_id}/reviewed")
def reviewed(request: Request, account_id: str, csrf: str = Form(max_length=100)):
    user = current_user(request)
    verify_csrf(request, csrf)
    with transaction(user["tenant_id"]) as conn:
        if not owned_account(conn, user, account_id):
            raise HTTPException(404, "Account not found")
        record_review(conn, user, account_id)
    return RedirectResponse("/", status_code=303)


@app.exception_handler(500)
async def server_error(request, exc):
    return JSONResponse({"detail": "Service error; inspect server logs"}, status_code=500)
