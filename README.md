# Renewal Risk Worklist

A Monday worklist for Customer Success Managers: the accounts most likely not to renew, ranked within each CSM's weekly capacity, with the evidence behind every score. It drafts outreach for a human to edit and never emails a customer.

All data is synthetic. This is a demonstration, not a validated churn predictor.

## Run it

Needs Docker with Compose and Python 3.11+.

```sh
python3 scripts/provision.py     # writes .env with fresh secrets
docker compose build web
docker compose up -d             # generates data, trains models, seeds Postgres, starts web + worker
```

Open http://127.0.0.1:8080 and pick a demo user (one CSM per tenant). Optional: set `OPENROUTER_API_KEY` in `.env` for LLM-assisted drafts; without it, drafts use a clearly labelled template.

The system runs on its own. A replay clock starts at 1 June 2026 and moves one simulated day every 72 minutes: about 33k late deliveries arrive as the clock reaches them, and every account is rescored each simulated midnight. To push an event in by hand:

```sh
docker compose exec worker python -m app.cli event --tenant t1 --account a00120 --source stripe --value 1
```

## How it works

1. **Source ledger.** Events from five sources (Salesforce, Gmail metadata, Zendesk, product usage, Stripe) are mapped to one account and stored with two timestamps: when it happened and when we received it.
2. **Durable jobs.** Each event and the job to rescore its account are written in one transaction. A worker claims jobs with leases and retries failures with backoff.
3. **Immutable snapshots.** Each score is stored with its inputs, model version and the exact source events it used.
4. **Worklist.** The web app only reads stored snapshots. It never computes a score.

## What I decided, and why

- **A worklist, not a red/yellow/green dashboard.** The problem is who a CSM calls this week, not what they look at. Up to 10 accounts per CSM, renewing in 60–180 days; a reviewed account leaves the list for 28 days.
- **The system drafts; the CSM sends.** An automated email to an unhappy customer can speed up churn, and false positives annoy healthy ones. There is no email path at all.
- **Unknown is not green.** Accounts with too little data go to a separate needs-data lane, ordered by renewal date, never ranked by an imputed score. In the backtest they churn as often as ranked accounts.
- **A fitted model per tenant, measured honestly.** Calibrated logistic regression, trained on 2024, calibrated on 2025, tested on 2026, and compared with a rules baseline, ARR order and random order. A tenant with too few outcomes gets the rules baseline, labelled as such. No cross-tenant training.
- **Two clocks.** A score only uses events that had both happened and arrived by scoring time, so late data can't leak backwards.
- **Postgres as the queue.** `SKIP LOCKED` claims, leases, fencing and capped retries. Execution is at-least-once; snapshots are content-hashed, so retries and duplicates don't create extra scores. No message broker needed at this size.
- **Isolation in the database.** Every table is tenant-scoped with row-level security, and the app connects as a role that can't bypass it. The tenant comes only from the server-set session, never from the request.
- **A bounded LLM.** The model may only pick from known facts and fixed openings; code writes the sentences and validates citations. No raw messages, names, emails or other tenants' data go into the request.
- **Everything shown is stored.** Any number on screen traces to a snapshot, its model version and its source events. Model contributions are shown as associations, not causes.
- **Demo login on purpose.** The first page is a user picker with no passwords. Isolation sits below it, so real login only changes how the session is set.

## The data

Seed 42 generates 5,000 accounts across four tenants, 242,083 events and 11,201 renewals (2,660 not renewed). A hidden health state drives noisy signals in each source, and outcomes are drawn from it, so the model has something real but imperfect to find. The mess is deliberate: disconnected sources, sparse histories, late and out-of-order arrivals, duplicates, and 1,902 events with unknown aliases, which are quarantined rather than guessed. Field definitions are in `docs/DATA_DICTIONARY.md`.

## Results

Replaying each CSM's weeks in 2026 with the real cooldown (`data/evaluation.json`):

| Tenants 1 / 2 / 3 | Model | Rules | ARR order |
|---|---|---|---|
| AUC | 0.69 / 0.70 / 0.72 | 0.61 / 0.62 / 0.62 | 0.51 / 0.48 / 0.57 |
| Useful-call lift over random, 3 reviews a week | 1.29 / 1.30 / 1.33 | 1.17 / 1.18 / 1.14 | 1.03 / 0.98 / 1.11 |
| Same, 10 reviews a week | 1.01 / 1.01 / 1.00 | 1.00 / 1.01 / 1.00 | 1.01 / 1.00 / 1.01 |

- **The model helps when calls are scarce.** At 3 reviews a week about 30% more of its calls land on accounts that really churn.
- **That is precision, not more saves.** It reaches about as many churned renewals as the rules (tenant 1: 161 vs 167 of 183).
- **At 10 a week, every ordering converges.** A CSM has 16–18 eligible accounts and each sits out four weeks, so 10 slots cover everyone regardless of order.
- **So the deciding question is for the business:** how many calls a week do CSMs really spend on risk reviews?

The smallest tenant has too few outcomes to measure. None of this is evidence of real retention uplift.

## Deliberately skipped

- Real Salesforce, Gmail, Zendesk and Stripe connectors (sources are canonical synthetic records)
- SSO, user provisioning and real authentication
- Sending any customer email
- Free-text LLM writing and embedding retrieval
- Fuzzy identity matching across sources
- Automatic retraining and model promotion
- Measuring whether outreach actually changes outcomes
- High availability, backups and data retention policy

## What breaks first at 10×

1. **Data generation and training run in memory.** Stream records to partitioned storage and train in batches.
2. **Connections and the daily rescore.** Every job opens its own connections, and the daily pass touches every account. Add connection pooling, batched claims and rescoring only accounts that changed.
3. **The ledgers only grow.** Partition events and snapshots by tenant and time, and move old evidence to cheap storage while keeping it retrievable for any score still on screen.
4. **Portfolio reads.** Finding the latest snapshot per account will need a current-state table in front of the immutable history.
5. **Identity.** Replace the demo picker with SSO and revocable sessions.
6. **LLM calls.** Two uncached clicks at once can both call the provider. Add a draft job with a claim, and per-tenant budgets.

## Tests

36 tests. The database ones run against real Postgres as the app's own role, not as an owner. They cover both clocks, cross-tenant reads and writes, missing tenant context, forged identity, duplicates, retries, per-CSM access and draft validation. A smoke test checks the running worker picks up a new event on its own.

```sh
alias ftest='docker compose -p fello-test -f compose.yml -f compose.test.yml'
ftest up -d --wait db
uv run python scripts/local.py bootstrap
uv run python scripts/local.py test -q
ftest up -d --no-deps worker && uv run python -m scripts.smoke_worker
ftest down --volumes
```

Tests run in their own Compose project, so they never touch the demo database. Operations, HTTPS deployment and recovery are in `docs/RUNBOOK.md`.
