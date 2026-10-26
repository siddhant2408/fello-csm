# Owner walkthrough — explain before recording

## Narrative
1. Business problem: 120 accounts, limited attention, missed signals. Why a worklist and why no automatic email?
2. Open the UI: explicit replay clock and synthetic-data banner. Explain why unknown is not green.
3. Follow one account from canonical source aliases through event/ingestion times, features, model and immutable snapshot.
4. Ingest a Stripe event using the CLI while the worker runs. Refresh and explain the new snapshot and preserved old one. The ingest command does not invoke scoring.
5. Show duplicate/retry tests and real Postgres RLS tests. Explain why an app-owner connection would invalidate the isolation test.
6. Show evaluation: train/calibration/test cutoffs, weekly capacity, repeated touches and false positives. State plainly that the fitted model did not consistently beat rules. High recall is partly capacity saturation, not proof of predictive skill.
7. Generate a draft. Explain allowlisted fact selection, deterministic sentences, citations, cache and human review. With no key it is explicitly a template, not an LLM demo.
8. Finish with what was skipped and what fails first at 10×.

## ML in plain language
A training example is one renewal at one historical Monday. Its inputs contain only source records that had happened AND arrived by that Monday. The label is whether that renewal eventually failed. This label is available for offline training/evaluation, never supplied to runtime prediction.

Logistic regression learns a weight per feature plus an intercept. It adds weighted feature values into a log-odds score; a sigmoid maps that to a number between zero and one. Standardization is fitted on the training cohort only, then algebraically folded into the stored coefficients. Calibration learns a correction using a separate later cohort. It is an attempt to align probability with observed frequency, not a guarantee of accurate confidence.

Contributions are weighted feature terms in the calibrated log-odds sum. They describe model associations, not why a customer actually leaves. Sparse coverage suppresses the displayed probability even though a ranking value is computed. Too few independent positive/negative renewal outcomes means no fitted tenant model, not permission to borrow another tenant's data.

## Questions to be able to answer
- Why does event time alone leak information when a ticket arrives late?
- What commits atomically with event ingestion? What happens if a worker dies after claiming?
- How do source IDs, tenant IDs and canonical account IDs differ?
- Why does the DB policy not replace per-CSM application authorization?
- Why keep the model when its improvement is unproven? It demonstrates the required fitted-model path; this demo is not approval to deploy it to real accounts.
- What does the LLM decide, and what can it never do?
- How would you add retention/cold storage without breaking evidence links?

## Submission gates
Private remote repository accessible to reviewer; live HTTPS deployment; actual OpenRouter call verified or dependency explicitly disclosed; tested startup instructions; final short PPT; owner-recorded Loom. Do not claim the release is complete until these gates are satisfied.
