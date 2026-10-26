# Renewal Risk Worklist — approved scope

A CSM receives a ranked, evidence-backed account worklist. No autonomous customer contact.
Python/FastAPI, Postgres and one worker; Docker Compose deployment on a separate Oracle VM.

## Capability boundaries
1. data-foundation: canonical account/renewal/event schema, source aliases, event and ingestion clocks.
2. risk-evaluation: versioned features; tenant-local logistic regression vs rules; later-period evaluation.
3. processing: ingestion transaction + durable jobs, leased claims/retries; reconciliation safety net.
4. csm-worklist: tenant/user authorization, RLS, score provenance, evidence and history.
5. ai-drafting: bounded OpenRouter request, allowlisted evidence selection, deterministic factual rendering.
Dependencies: data-foundation → risk-evaluation → processing → csm-worklist → ai-drafting.

## Acceptance
- Seed reproducibly: 5,000 accounts, 200,000 unique source events, four isolated tenants.
- Represent late, duplicate, sparse, ambiguous and disconnected-source cases without silently dropping them.
- Feature availability requires both occurred_at and received_at <= as_of.
- Evaluation uses earlier renewal cohorts for training, separate later calibration and final test cohorts.
- Compare rules and fitted model at the same per-CSM weekly capacity; report uncertainty/limitations.
- No pooled cross-tenant training. Insufficient outcomes use an explicitly uncalibrated rules fallback.
- Every displayed score stores model version, feature version, as_of, values, drivers and event IDs.
- Authenticated users cannot change tenant identity; DB application role cannot bypass RLS.
- Worker processing survives retry without duplicate snapshots/drafts; failed jobs remain inspectable.
- No LLM output executes commands or supplies arbitrary factual customer prose.
- Service remains useful with no OpenRouter key. Live LLM verification remains pending until configured.

## Commands and structure
`uv sync --locked`; `uv run pytest`; `uv run ruff check .`; `docker compose up --build -d`.
`app/`: Python modules and SQL; `tests/`: pure and Postgres integration tests;
`data/`: ignored generated fixtures/model reports; `docs/`: engineering handover.
Use typed functions, explicit SQL parameters and small modules. Test behavior before implementation.

## Boundaries
Always: tests, tenant-scoped access, immutable computed snapshots, parameterized queries, escaped HTML.
Ask: changing approved scope or data-sharing policy, deploying to remote server.
Never: real customer data, automatic email sends, committed secrets, fabricated evaluation results.

## Known limits
Synthetic results are not evidence of real retention uplift. Seeded demo auth is not enterprise identity.
Public release requires HTTPS and securely provisioned user credentials; no public database port.
