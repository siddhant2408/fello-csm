# Operator runbook

## Scope and credentials
This Compose project is `fello-worklist`. Do not operate on other Docker projects. `.env` is ignored and permission 0600. Never run `docker compose config` into a shared log: interpolation includes credentials. Normal deployment exposes neither Postgres nor the web service publicly; configure TLS at the reverse proxy first. Provision fresh credentials on the separate Oracle server, not a copy of local credentials.

## Health and failures
- `docker compose ps -a`: init must exit 0; web, worker and DB must run.
- `curl --fail http://127.0.0.1:8080/health`: database connectivity, not worker progress.
- `docker compose exec worker python -m app.cli status --tenant t1`: inspect backlog and terminal failures per tenant.
- `docker compose logs --since 10m worker`: logs contain IDs/error types, not source bodies or secrets.
- Failed jobs retain attempts and last_error. Investigate cause first; an operator can reset a specific scoped failed job to pending after remediation. Never blanket-retry poison records or bypass tenant RLS.
- Claims expire after two minutes. A replacement claimant increments attempts; the old claimant checks the attempt token under lock before committing. A worker killed mid-computation rolls back its transaction; reconciliation and lease recovery remain safe.

## Live OpenRouter verification
Configure the key securely in server `.env`, select a structured-output capable model, and recreate web. Open a newly computed snapshot (old cached template drafts are intentionally immutable). Prepare a draft and verify `mode=openrouter_validated_selection`, model usage metadata and citations. Test provider refusal/timeout to labelled template fallback. An API-key placeholder or a successful mock is not a live verification.

## Deployment checklist
- Confirm free disk, architecture, Docker version and remote access on the intended server.
- Clone the private repository after token access is fixed; provision fresh `.env`.
- `docker compose build web && docker compose up -d`.
- Check init exit status and health; verify worker reacts to a new source event.
- Put an HTTPS reverse proxy in front of the loopback web port; leave secure cookies enabled.
- Do not expose the test Postgres override or publish database/admin credentials.
- Share demo credentials privately and rotate after evaluation.
- Confirm restore: logical Postgres backup plus persisted generated dataset/model artifacts; preserve the deployed Git revision.

## Rollback
Check out the previously tested Git commit, rebuild the shared image and recreate web/worker. Current schema is additive; no general migration rollback framework is claimed. Back up the database before future schema changes. Do not use volume deletion as deployment rollback.

## Outstanding access
GitHub PAT cannot create repositories (HTTP 403). Owner must create the private repository and grant token access. Oracle SSH endpoint/user and approved key access are not yet provided. OpenRouter key is not configured. Do not represent these steps as completed.
