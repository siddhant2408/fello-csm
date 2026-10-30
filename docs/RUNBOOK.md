# Operator runbook

## Scope and credentials
This Compose project is `fello-worklist`. Do not operate on other Docker projects. `.env` is ignored and permission 0600. Never run `docker compose config` into a shared log: interpolation includes credentials. Postgres is never exposed. The web service is public only through Caddy (`compose.https.yml`), which terminates TLS. Provision fresh credentials on the separate Oracle server, not a copy of local credentials.

## Health and failures
- `docker compose ps -a`: init must exit 0; web, worker and DB must run.
- `curl --fail http://127.0.0.1:8080/health`: database connectivity and current replay time, not worker progress.
- `docker compose exec worker python -m app.cli status --tenant t1`: inspect backlog and terminal failures per tenant.
- `docker compose logs --since 10m worker`: logs contain IDs/error types, not source bodies or secrets.
- Failed jobs retain attempts and last_error. Investigate cause first; an operator can reset a specific scoped failed job to pending after remediation. Never blanket-retry poison records or bypass tenant RLS.
- Retries back off 5s, 10s, 20s, 40s; the fifth failure is terminal.
- Claims expire after two minutes. A replacement claimant increments attempts; the old claimant checks the attempt token under lock before committing. A worker killed mid-computation rolls back its transaction; reconciliation and lease recovery remain safe.

## Replay clock
- Settings (`.env` or Compose defaults): `SIMULATION_AS_OF` start, `SIMULATION_SPEED` (20 ≈ 4.5 days to the end; 96 = one simulated day per 15 minutes; 0 freezes), `SIMULATION_END` (2026-09-01). Speed/end changes take effect on recreating web and worker; the start anchor does not move.
- Progress: the replay time in `/health` advances; `SELECT count(*) FROM inbox` falls; `max(received_at)` in `events` follows the clock. A daily rescore queues every account at each simulated midnight (~2 minutes of work locally).
- After `SIMULATION_END` the clock stops and the worker idles. Restarting from the start requires reseeding (`down --volumes`, which discards this demo database), so do it just before an evaluation window, not during one.

## Onboarding a tenant
Administrative only; the app role cannot write these tables. As the admin role: insert into `tenants`, then load its accounts, aliases and renewals, and a model (a tenant with no outcomes gets `train_tenant([], [])`, the rules fallback) plus its users. The worker picks the tenant up within a minute and queues its accounts. Without a model its jobs fail visibly with `ValueError`; they are not scored with another tenant's model.

## Live OpenRouter verification
Configure the key securely in server `.env`, select a structured-output capable model, and recreate web. Open a newly computed snapshot (old cached template drafts are intentionally immutable). Prepare a draft and verify `mode=openrouter_validated_selection`, model usage metadata and citations. Test provider refusal/timeout to labelled template fallback. An API-key placeholder or a successful mock is not a live verification.

## Deployment checklist
- Confirm free disk, architecture, Docker version and remote access on the intended server.
- Clone the private repository after token access is fixed; provision fresh `.env`.
- `docker compose build web && docker compose up -d`.
- Check init exit status and health; verify worker reacts to a new source event.
- For public access: set `SITE_ADDRESS` in `.env`, open TCP 80/443 in the cloud security list and the host firewall (persist the host rules), then `docker compose -f compose.yml -f compose.https.yml up -d`. Check `docker logs fello-worklist-caddy-1` for "certificate obtained successfully".
- Do not expose the test Postgres override or publish database/admin credentials.
- The demo user picker has no password: anyone who can reach the URL can open any demo tenant. Only synthetic data belongs behind it.
- Confirm restore: logical Postgres backup plus persisted generated dataset/model artifacts; preserve the deployed Git revision.

## Rollback
Check out the previously tested Git commit, rebuild the shared image and recreate web/worker. Schema changes are additive except the removal of password login (`fello_login`, `login_attempts`, `users.password_hash`); rolling the web back past that change needs them recreated and the demo users reseeded. No general migration rollback framework is claimed. Back up the database before future schema changes. Do not use volume deletion as deployment rollback.

## Outstanding access
The owner-created private repository is https://github.com/siddhant2408/fello-csm; repository visibility and token push access were verified. Deployed on the Oracle VM (`ubuntu@140.245.17.41`, checkout `~/fello-csm`) at https://140-245-17-41.sslip.io. To update: `git pull`, `docker compose build web`, then the HTTPS `up -d` above. OpenRouter key is not configured. Do not represent live-provider verification as completed.
