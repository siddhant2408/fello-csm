# Canonical dictionary — account-risk-features/v1

Source of truth: `app/features.py`. No separate prompt definition of risk or health exists.

## Identity and clocks
- `tenant_id`: customer organisation using this system, not the organisation's end customer.
- `account_id`: canonical end-customer account within a tenant. Composite foreign keys include tenant.
- `source_key`: source-local identity mapped by `(tenant_id,source,source_key)`; unknown aliases are quarantined.
- `occurred_at`: when the source event happened; `received_at`: when it became available to this system.
- `as_of`: historical scoring cutoff; event clocks must both be at/before this cutoff.
- `known_at`: when renewal contract metadata became available; not the outcome date.
- `due_at`: renewal decision date, not an outreach appointment.

## Features
All windows are inclusive at the cutoff. Feature values are deterministic; no LLM assigns them.

- `usage_drop`: one minus mean usage samples in the last 45 days divided by mean samples 45–180 days ago (denominator floored at 1), clipped to [-1,1]. Missing either sample window imputes 0 for ranking but fails quality gating. This is a change in observed samples, not a proven change in all actual usage.
- `urgent_tickets`: sum of Zendesk urgent support counts over 60 days, capped at 20. These synthetic records are non-overlapping counts; a real connector would need ticket lifecycle deduplication.
- `payment_failures`: sum of Stripe failed-payment indicators over 60 days, capped at 10. Not a currency balance.
- `contact_gap`: days since the latest Gmail metadata event in the 180-day window divided by 180; missing contact imputes 1. This measures observed contact recency, not customer sentiment or response quality.
- `coverage`: fraction of the five configured sources connected, not a guarantee each source delivered complete history.
- `quality`: sufficient only if coverage >=0.6 and both recent and prior usage samples exist. Otherwise insufficient_data. This is a conservative heuristic, not statistically calibrated confidence.

## Model and ranking
- Outcome: full non-renewal at the next renewal; downgrades excluded. Label becomes available seven days after the simulated renewal.
- `probability`: calibrated-logistic estimate only when a fitted tenant model and sufficient coverage exist; otherwise null.
- `priority`: internal ranking value; may be produced from imputed features or fallback rules and must not be displayed as a validated probability when probability is null.
- `drivers`: feature contributions in calibrated log-odds; not causal explanations, not percentages that sum to probability.
- `rules_fallback`: too few independent renewal outcomes to fit/calibrate. Never borrows another tenant's training data.
- Worklist: up to 10 accounts assigned to the logged-in CSM, with known renewals in 60–112 days. Repeated weekly appearances consume weekly capacity; action/cooldown tracking is not implemented.

## Evidence and revisions
Source events, model artifacts, score snapshots and drafts cannot be updated/deleted by the runtime role. Corrections append new source events with new IDs; real mutable-source correction semantics remain a connector design task. Snapshots record feature values, observations, evidence IDs, model version, as_of and an input hash. Generated fixture/model/report files have SHA-256 manifests. A new feature definition requires a new feature version and compatible retraining, never a prompt-only reinterpretation.
