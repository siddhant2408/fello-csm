# Renewal Risk Worklist

A deliberately narrow Fello take-home: help a CSM decide which accounts to contact, with inspectable evidence. **Synthetic demonstration, not a validated production churn predictor. No email is sent.**

## Start
Requires Docker with Compose and Python 3.11+; allow several GB of free disk for image construction.

```sh
python3 scripts/provision.py        # creates ignored .env, mode 0600; refuses overwriting
# Optionally configure OPENROUTER_API_KEY and OPENROUTER_MODEL in .env securely.
docker compose build web           # one shared image for init, web and worker
docker compose up -d
curl --fail http://127.0.0.1:8080/health
```

The initializer generates data, fits/evaluates the models, and seeds Postgres. It exits successfully before web/worker start. On a repeated start the existing seed is retained. Do not use `down --volumes` unless intentionally discarding this demo's generated database.

Open through an HTTPS reverse proxy or SSH tunnel. Users are `demo-t1` through `demo-t4`; retrieve `DEMO_PASSWORD` privately from `.env`. It is never printed by provisioning and is hashed with bcrypt in Postgres. The same password is a demo convenience, not production identity management. Secure cookies default on; for strictly local HTTP testing only, set `COOKIE_SECURE=false` and recreate web. Do not expose that configuration publicly.

The web listener binds to **127.0.0.1:8080**. The normal Compose file publishes no database port. Neither server access nor a public URL is assumed to exist.

## The replay clock is explicit
`SIMULATION_AS_OF=2026-06-01T12:00:00+00:00` is a fixed historical demo clock, prominently displayed in the UI. Seed data spans earlier history and future records relative to this clock. Historical availability filters both timestamps; storing future fixture rows does not make them visible in computed state. To run a wall-clock installation, explicitly set `SIMULATION_AS_OF=` and ingest appropriate real-time data; the fixtures do not magically become current customer data.

## What to try
1. Sign in and open an account from the worklist.
2. Inspect feature values, model contributions, source-event clocks and immutable snapshot identifiers.
3. Prepare a draft. With no API key, the UI explicitly labels it `template_no_key`; it never pretends a model call occurred.
4. Ingest a synthetic source event while the worker is running:

```sh
docker compose exec worker python -m app.cli event --tenant t1 --account a00120 --source stripe --value 1
docker compose exec worker python -m app.cli status --tenant t1
```

The event transaction queues the account. The separate worker processes it without a manual scoring command. Refresh the account and inspect its new snapshot. Repeat the exact source event ID through the ingestion function to test deduplication; the CLI intentionally creates a new ID on each invocation.

## Decisions and tradeoffs
- **Worklist, not traffic-light dashboard:** capacity is 10 account reviews per CSM per week, within 60–112 days of renewal. Rankings include insufficient-data cases, visibly labelled unknown rather than green. No unsupported intervention recommendation is claimed.
- **Canonical Postgres model:** tenant-scoped accounts, source aliases, renewals, immutable source events, models, snapshots, jobs and drafts. Unknown aliases are quarantined rather than fuzzy-matched.
- **Two event clocks:** occurred time and received time must both be at/before scoring time. Model training, calibration and final test are chronological; final test scoring begins after the model's release cutoff.
- **Tenant-local logistic regression:** standardized training, plain JSON inference coefficients and separate logistic calibration. Small tenant gets an explicitly uncalibrated rule-based priority. Historical outcome labels are not loaded into the runtime database.
- **Traceability rather than invented explanations:** all displayed computations are stored. Contributions are calibrated log-odds associations, not causal attribution or probabilities that add up.
- **Postgres queue:** atomic event+job insertion, SKIP LOCKED claims, leases, attempt fencing, retry backoff, terminal failures and daily reconciliation. At-least-once execution with idempotent outputs, not a claim of exactly-once execution.
- **Bounded LLM:** OpenRouter can select known fact IDs and one of two openings. Python validates references and renders fixed factual sentences. No raw customer messages, arbitrary SQL, tools, email addresses, account names or cross-tenant data enter the request. This is intentionally not an open-ended agent.
- **Least-privileged runtime:** app role is neither owner, superuser nor BYPASSRLS. No-context reads fail closed. Role grants prevent modification of event/snapshot/model artifacts. Only the initializer gets administrative credentials. Worker does not receive the OpenRouter key or session secret.

## Data realism and honest evaluation
Seed 42 produces **5,000 accounts, 200,000 unique events and 11,454 renewal episodes**. Four tenants have 2,000 / 1,700 / 1,270 / 30 accounts. Missing source connections, sparse observations, unresolved aliases, delayed arrivals and shuffled input are explicit. Contract history stops after non-renewal. Product events are aggregate usage samples, Zendesk values are urgent-support counts, Stripe values indicate failed payments, and Gmail is metadata only.

Outcomes depend probabilistically on latent product fit, commercial pressure and shocks; observed features are noisy proxies, not the outcome formula. Events are sampled from eligible histories without replacement. This is still an invented world with simplified business dynamics.

```sh
uv sync --locked
uv run python -m app.synthetic
OPENBLAS_NUM_THREADS=1 uv run python -m app.evaluate
```

`data/evaluation.json` is measured output: 2024 outcomes train, 2025 calibrate, 2026 tests. Test predictions occur on/after 2026-01-08. Capacity counts repeated weekly flags; catch rate counts distinct renewal episodes. High recall at capacity 10 is near saturation and **not evidence the model adds value**. Current fitted models do not consistently outperform the rules baseline; AUC is weak and many observations fail the coverage gate. The small tenant has no positive held-out outcomes in this seed, so recall/AUC are undefined. There is no claimed real retention uplift, causal benefit or statistically significant win.

## Tests
Full integration tests require an isolated demo database and no competing worker while deterministic claim/retry tests run. The test override binds Postgres only to loopback.

```sh
docker compose -f compose.yml -f compose.test.yml up -d db
uv run python scripts/local.py bootstrap
docker compose stop worker
uv run python scripts/local.py test -q
uv run ruff check app tests scripts
docker compose up -d worker
uv run python -m scripts.smoke_worker
uv run pip-audit
```

Tests cover both clocks, deterministic generation, terminal churn, model fallback, tenant read/write isolation using the actual app role, missing tenant context, forged identity, duplicate processing, job retry, login, CSRF, per-CSM authorization and constrained draft validation. The smoke test verifies the **running separate worker**, not a mocked function call. It requires this demo's seeded account/source alias.

## Deliberately skipped
Real Salesforce/Gmail/Zendesk/Stripe connectors; enterprise SSO and user provisioning; customer email sending; free-text LLM prose; embedding retrieval; sophisticated identity resolution; automatic retraining/promotion; causal intervention evaluation; action/cooldown tracking; multi-region HA; cold-tier export and production retention enforcement. Source adapters are represented by canonical synthetic records, not purported working SaaS integrations.

## What breaks first at 10×
1. **Bootstrap/training memory:** JSON generation and replay run in memory. Stream typed records to partitioned Parquet/object storage and process batches; do not increase web memory.
2. **Connections and reconciliation:** each claimed job uses short-lived connections; daily reconciliation touches all accounts. Add bounded connection pooling, batched claims, incremental dirty-account reconciliation and backlog/age alarms before adding a broker.
3. **Retention and audit cost:** event and snapshot ledgers grow indefinitely in this demo. Partition by tenant/time, retain a hot operational window, export historical evidence to low-cost object storage with queryable manifests and tested restore. Never discard evidence still referenced by displayed scores without a retrieval path.
4. **Serving paths:** tenant/account/time indexes are present, but full-portfolio snapshot lookup needs measured EXPLAIN plans and potentially a current-state projection. Preserve immutable history behind that projection.
5. **Identity/security/availability:** seeded users, per-username DB limits and a single-process IP limiter are not enterprise IAM. Replace with SSO, revocable server-side sessions, shared rate limits, per-app roles and managed secrets; separate DB, backups and HTTPS edge.
6. **LLM concurrency/cost:** requests are bounded, cached per snapshot, and timeout to labelled templates. Concurrent uncached requests can still duplicate provider calls; add a durable draft job/claim and tenant budgets before increasing concurrency. No token-saving claim is made without measurement.

## Release status
Local Docker image, web, worker and Postgres have run successfully. Desktop/mobile browser flows and autonomous ingestion were exercised. Remote Oracle deployment and live OpenRouter verification remain access-gated. The private source repository is https://github.com/siddhant2408/fello-csm. The final Loom must be recorded by the owner after code review. Read `docs/RUNBOOK.md` and `docs/WALKTHROUGH.md` before presenting.
