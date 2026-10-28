# Canonical dictionary — account-risk-features/v2

Source of truth: `app/features.py`. No separate prompt definition of risk or health exists.

## Identity and clocks
- `tenant_id`: customer organisation using this system, not the organisation's end customer. Registered in `tenants` (outside RLS, read-only to the app role); `accounts` references it and the worker/CLI iterate it.
- `account_id`: canonical end-customer account within a tenant. Composite foreign keys include tenant.
- `source_key`: source-local identity mapped by `(tenant_id,source,source_key)`; unknown aliases are quarantined.
- `occurred_at`: when the source event happened; `received_at`: when it became available to this system.
- `as_of`: historical scoring cutoff; event clocks must both be at/before this cutoff. At runtime it is the replay clock (`clock()` in `app/processing.py`): `SIMULATION_AS_OF + (now − replay.started_at) × SIMULATION_SPEED`, capped at `SIMULATION_END`.
- `inbox`: source deliveries whose `received_at` is after the replay start. A staging queue, not evidence: the worker deletes each row as it passes it to `ingest_event`, which writes the immutable `events` row and its job. Invalid deliveries are logged and dropped.
- `replay.started_at`: wall time the database was first seeded; the one anchor shared by web, worker and restarts.
- `known_at`: when renewal contract metadata became available; not the outcome date.
- `due_at`: renewal decision date, not an outreach appointment.

## Features
All windows are inclusive at the cutoff. Feature values are deterministic; no LLM assigns them.

- `usage_drop`: one minus mean usage samples in the last 45 days divided by mean samples 45–180 days ago (denominator floored at 1), clipped to [-1,1]. Missing either sample window imputes 0 for ranking but fails quality gating. This is a change in observed samples, not a proven change in all actual usage.
- `usage_level`: natural log of (1 + mean usage samples in the last 45 days). Missing recent samples impute the neutral constant 4.0 (median in 2024 training rows) and fail quality gating; imputing 0 would read as extreme non-use. Absolute level reflects account health better than change alone, but it is not normalised per licensed seat; seats are not simulated and a real connector would need that normalisation.
- `urgent_tickets`: sum of Zendesk urgent support counts over 60 days, capped at 20. These synthetic records are non-overlapping counts; a real connector would need ticket lifecycle deduplication.
- `payment_failures`: sum of Stripe failed-payment indicators over 60 days, capped at 10. Not a currency balance.
- `contact_gap`: days since the latest Gmail metadata event in the 180-day window divided by 180; no contact in 180 days with Gmail connected is 1 (observed silence). Gmail not connected imputes the neutral constant 60/180, the median observed gap in 2024 training rows: a disconnected source is unknown, not silent. This measures observed contact recency, not customer sentiment or response quality.
- `coverage`: fraction of the five configured sources connected, not a guarantee each source delivered complete history. Displayed and used for quality gating; not a model input, because it describes data quality rather than risk.
- Salesforce: loaded, traceable and displayed, but deliberately not a feature. In this world it records CSM-logged activity, which reflects current CSM effort rather than account health; using it would teach the model the existing guesswork.
- `quality`: sufficient only if coverage >=0.6 and both recent and prior usage samples exist. Otherwise insufficient_data. This is a conservative heuristic, not statistically calibrated confidence.

Model inputs (`NAMES` in `app/features.py`): usage_drop, usage_level, urgent_tickets, payment_failures, contact_gap. v1 snapshots remain readable; a v2 model refuses v1 features.

## Model and ranking
- Outcome: full non-renewal at the next renewal; downgrades excluded. Label becomes available seven days after the simulated renewal.
- `probability`: calibrated-logistic estimate only when a fitted tenant model and sufficient coverage exist; otherwise null.
- `priority`: internal ranking value; may be produced from imputed features or fallback rules and must not be displayed as a validated probability when probability is null.
- `drivers`: feature contributions in calibrated log-odds; not causal explanations, not percentages that sum to probability.
- `rules_fallback`: too few independent renewal outcomes to fit/calibrate. Never borrows another tenant's training data.
- Worklist (policy in `app/policy.py`, shared with the evaluation): renewals known and due in 60–180 days for the logged-in CSM. Ranked lane: sufficient-quality snapshots ordered by priority, up to 10. Needs-data lane: everything else, ordered by renewal date; imputed priorities never rank it. Marking an account reviewed removes it from both lanes for 28 days (append-only `reviews` table). The evaluation replays the same cooldown and assumes every listed account is reviewed.

## Evidence and revisions
Source events, model artifacts, score snapshots and drafts cannot be updated/deleted by the runtime role. Corrections append new source events with new IDs; real mutable-source correction semantics remain a connector design task. Snapshots record feature values, observations, evidence IDs, model version, as_of and an input hash. Generated fixture/model/report files have SHA-256 manifests. A new feature definition requires a new feature version and compatible retraining, never a prompt-only reinterpretation.
