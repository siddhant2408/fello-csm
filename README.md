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

Open through an HTTPS reverse proxy or SSH tunnel. The first page lists the demo users (one CSM per tenant, `demo-t1` through `demo-t4`); click one to open their worklist, and "Switch user" to change tenant. There are no passwords: this is a synthetic demo, not identity management. The chosen user is stored in a signed session cookie, and every query takes the tenant from that session, never from the request. Secure cookies default on; for strictly local HTTP testing only, set `COOKIE_SECURE=false` and recreate web. Do not expose that configuration publicly.

The web listener binds to **127.0.0.1:8080**. The normal Compose file publishes no database port. Neither server access nor a public URL is assumed to exist.

## The replay clock moves on its own
The demo replays the synthetic world from `SIMULATION_AS_OF=2026-06-01T12:00:00+00:00` at `SIMULATION_SPEED=20` (one simulated day per 72 real minutes, so the replay lasts about 4.5 days), stopping at `SIMULATION_END=2026-09-01`. The current replay time is shown in the UI banner and `/health`. Web, worker and restarts share one clock, anchored to the wall time the database was first seeded (`replay` table).

Bootstrap loads only events already received at the start. The other ~33k deliveries wait in `inbox`; the worker hands each one to the normal `ingest_event` path once the clock passes its `received_at`, which queues a rescore. Every simulated midnight the worker also rescores all accounts. So with no one touching it, late and new events arrive, snapshots are written and the worklist reorders. Throughput measured locally: ~40 jobs/s, so a day's 5,000-account rescore takes about 2 minutes. Raise the speed for a short live demo (96 = a day per 15 minutes).

`SIMULATION_SPEED=0` freezes the clock at the start (tests use this). Restarting the replay from the beginning means reseeding: `docker compose down --volumes` discards this demo database. For a wall-clock installation set `SIMULATION_AS_OF=` and ingest real-time data; the fixtures do not become current customer data.

## What to try
1. Pick a demo user and open an account from the worklist.
2. Inspect feature values, model contributions, source-event clocks and immutable snapshot identifiers.
3. Prepare a draft. With no API key, the UI explicitly labels it `template_no_key`; it never pretends a model call occurred.
4. Leave it running: the banner clock advances and accounts gain new snapshots as held-back events arrive. To inject one yourself while the worker is running:

```sh
docker compose exec worker python -m app.cli event --tenant t1 --account a00120 --source stripe --value 1
docker compose exec worker python -m app.cli status --tenant t1
```

The event transaction queues the account. The separate worker processes it without a manual scoring command. Refresh the account and inspect its new snapshot. Repeat the exact source event ID through the ingestion function to test deduplication; the CLI intentionally creates a new ID on each invocation.

## Decisions and tradeoffs
- **Worklist, not traffic-light dashboard:** capacity is 10 account reviews per CSM per week, within 60–180 days of renewal; a reviewed account sits out 28 days (`app/policy.py`). Accounts with enough data are ranked by priority; insufficient-data cases sit in a separate needs-data lane by renewal date, visibly labelled unknown rather than green. The 10 is a placeholder: the real number of weekly slots for risk reviews is a question for the business (see evaluation below). No unsupported intervention recommendation is claimed.
- **Canonical Postgres model:** tenant-scoped accounts, source aliases, renewals, immutable source events, models, snapshots, jobs and drafts. Unknown aliases are quarantined rather than fuzzy-matched. Customer organisations live in a `tenants` registry that the worker rereads every minute, so onboarding is administrative data, not a deploy: register the tenant, load its accounts/aliases/renewals, and give it a model (with no outcomes yet, `train_tenant([], [])` yields the rules fallback). Until a model exists its jobs fail visibly rather than scoring with another tenant's model.
- **Two event clocks:** occurred time and received time must both be at/before scoring time. Model training, calibration and final test are chronological; final test scoring begins after the model's release cutoff.
- **Tenant-local logistic regression:** standardized training, plain JSON inference coefficients and separate logistic calibration. Small tenant gets an explicitly uncalibrated rule-based priority. Historical outcome labels are not loaded into the runtime database.
- **Traceability rather than invented explanations:** all displayed computations are stored. Contributions are calibrated log-odds associations, not causal attribution or probabilities that add up.
- **Postgres queue:** atomic event+job insertion, SKIP LOCKED claims, leases, attempt fencing, retry backoff, terminal failures and daily reconciliation. At-least-once execution with idempotent outputs, not a claim of exactly-once execution.
- **Bounded LLM:** OpenRouter can select known fact IDs and one of two openings. Python validates references and renders fixed factual sentences. No raw customer messages, arbitrary SQL, tools, email addresses, account names or cross-tenant data enter the request. This is intentionally not an open-ended agent.
- **Least-privileged runtime:** app role is neither owner, superuser nor BYPASSRLS. No-context reads fail closed. Role grants prevent modification of event/snapshot/model artifacts. Only the initializer gets administrative credentials. Worker does not receive the OpenRouter key or session secret.

## Data realism and honest evaluation
Seed 42 produces **5,000 accounts, 242,083 unique events (1,902 with unresolvable aliases, quarantined) and 11,201 renewal episodes (2,660 non-renewals)**. Four tenants have 2,000 / 1,700 / 1,270 / 30 accounts. Missing source connections, sparse observations, unresolved aliases, delayed arrivals and shuffled input are explicit. Contract history stops after non-renewal. Product events are aggregate usage samples, Zendesk values are urgent-support counts, Stripe values indicate failed payments, and Gmail is metadata only.

Outcomes depend probabilistically on latent product fit, commercial pressure and shocks; observed features are noisy proxies, not the outcome formula. Events are sampled from eligible histories without replacement. This is still an invented world with simplified business dynamics.

```sh
uv sync --locked
uv run python -m app.synthetic
OPENBLAS_NUM_THREADS=1 uv run python -m app.evaluate
```

`data/evaluation.json` is measured output: 2024 outcomes train, 2025 calibrate, 2026 tests. Test predictions occur on/after 2026-01-08. The replay walks each CSM's weeks in order with the product's cooldown and compares the model, the rules baseline, ARR order and 20 random orders at 3, 5 and 10 reviews a week. Touch precision counts every weekly review; catch rate counts distinct renewal episodes.

| Tenants t1 / t2 / t3 | Model | Rules | ARR |
|---|---|---|---|
| AUC | 0.69 / 0.70 / 0.72 | 0.61 / 0.62 / 0.62 | 0.51 / 0.48 / 0.57 |
| Touch-precision lift over random, 3/week | 1.29 / 1.30 / 1.33 | 1.17 / 1.18 / 1.14 | 1.03 / 0.98 / 1.11 |
| Same, 10/week | 1.01 / 1.01 / 1.00 | 1.00 / 1.01 / 1.00 | 1.01 / 1.00 / 1.01 |

**Capacity 10 does not bind in this world.** A CSM has a median of 16–18 accounts in the ranked lane and each sits out four weeks after review, so about 4–5 reviews a week cover everyone; with 10 slots every ordering reaches every eligible account within a month and the methods converge. Ranking only matters below that: at 3 reviews a week roughly 30% more of the model's calls land on accounts that really churn. That is precision per call, not more saves: at 3/week the model reaches about as many distinct churned renewals as random or rules (t1 161 vs 167 rules of 183), just with fewer wasted calls. Whether ranking is worth having therefore depends on how many weekly slots CSMs actually spend on risk reviews, which this data cannot answer.

Rows in the needs-data lane churn at the base rate, and some churned renewals never reach the ranked lane. The small tenant (t4) uses the rules fallback and has 2 churned renewals in the test year, too few for any metric to mean anything. There is no claimed real retention uplift, causal benefit or statistically significant win.

## Tests
Integration tests write reviews, drafts and job claims, and the claim/retry tests need no competing worker. So run them in their own Compose project (`-p fello-test`), never against the demo database. The test override binds that Postgres only to loopback (port 55432). The demo stack can keep running.

```sh
alias ftest='docker compose -p fello-test -f compose.yml -f compose.test.yml'
ftest up -d --wait db
uv run python scripts/local.py bootstrap
uv run python scripts/local.py test -q
uv run ruff check app tests scripts
ftest up -d --no-deps worker       # smoke test needs a worker on the test database
uv run python -m scripts.smoke_worker
ftest down --volumes               # discards only the test database
uv run pip-audit
```

Tests cover both clocks, deterministic generation, terminal churn, model fallback, tenant read/write isolation using the actual app role, missing tenant context, forged identity, duplicate processing, job retry, user switching, CSRF, per-CSM authorization and constrained draft validation. The smoke test verifies the **running separate worker**, not a mocked function call. It requires this demo's seeded account/source alias.

## Deliberately skipped
Real Salesforce/Gmail/Zendesk/Stripe connectors; enterprise SSO and user provisioning; customer email sending; free-text LLM prose; embedding retrieval; sophisticated identity resolution; automatic retraining/promotion; causal intervention evaluation; action/cooldown tracking; multi-region HA; cold-tier export and production retention enforcement. Source adapters are represented by canonical synthetic records, not purported working SaaS integrations.

## What breaks first at 10×
1. **Bootstrap/training memory:** JSON generation and replay run in memory. Stream typed records to partitioned Parquet/object storage and process batches; do not increase web memory.
2. **Connections and reconciliation:** each claimed job uses short-lived connections; daily reconciliation touches all accounts. Add bounded connection pooling, batched claims, incremental dirty-account reconciliation and backlog/age alarms before adding a broker.
3. **Retention and audit cost:** event and snapshot ledgers grow indefinitely in this demo. Partition by tenant/time, retain a hot operational window, export historical evidence to low-cost object storage with queryable manifests and tested restore. Never discard evidence still referenced by displayed scores without a retrieval path.
4. **Serving paths:** tenant/account/time indexes are present, but full-portfolio snapshot lookup needs measured EXPLAIN plans and potentially a current-state projection. Preserve immutable history behind that projection.
5. **Identity/security/availability:** a password-free demo user picker is deliberately not authentication; anyone with the URL can open any demo tenant. Isolation is still enforced below it (session-carried tenant, RLS), so real login only has to replace how the session is set: SSO, revocable server-side sessions, rate limits, per-app roles and managed secrets; separate DB, backups and HTTPS edge.
6. **LLM concurrency/cost:** requests are bounded, cached per snapshot, and timeout to labelled templates. Concurrent uncached requests can still duplicate provider calls; add a durable draft job/claim and tenant budgets before increasing concurrency. No token-saving claim is made without measurement.

## Release status
Local Docker image, web, worker and Postgres have run successfully. Desktop/mobile browser flows and autonomous ingestion were exercised. Remote Oracle deployment and live OpenRouter verification remain access-gated. The private source repository is https://github.com/siddhant2408/fello-csm. The final Loom must be recorded by the owner after code review. Read `docs/RUNBOOK.md` and `docs/WALKTHROUGH.md` before presenting.
