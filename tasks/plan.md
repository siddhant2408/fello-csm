# Implementation plan
Scope approved in Discord; implementation begins with reproducible data and evaluation.

1. Data/evaluation proof: test time boundaries, seed dataset, fit tenant models, persist measured report.
2. Postgres path: schema/RLS, idempotent ingestion and leased worker, snapshots and evidence.
3. Authenticated CSM interface: worklist/detail, source trace, history, controlled drafting.
4. Verification/release: integration/abuse tests, Docker smoke, dependency audit, walkthrough/PPT.

Checkpoint after each slice: tests + actual command output + commit. Remote deployment and live
OpenRouter call require access/secret; do not claim either from local execution.

Task tracker: no duplicate task store is created; these are implementation milestones, not personal tasks.
