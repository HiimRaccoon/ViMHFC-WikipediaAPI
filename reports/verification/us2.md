# US2 — Resume, metrics and gate fixture verification

Date: 2026-10-25. Scope: T039–T048; real pilots T049–T053 remain blocked.

Verified with offline controlled providers/HTTP transport and temporary SQLite stores:

- Result and completed state commit together; rollback preserves the prior run. Compatible results reuse dependency fingerprints; changed inputs rerun. Same-snapshot compatible discovery cohorts/cursors carry to a new rule run; changed snapshots do not.
- Raw objects survive interruption before their database index write and are recovered by revision address after hash/identity validation without network access.
- Claims are checked/written in one writer transaction, and ownership tokens prevent stale workers from overwriting a newer result. Expired leases can resume; transient failures and pending metadata retain explicit attempts/error/retry state.
- Retry only requeues eligible work, without acquisition. Extend cannot escape a pilot or initiate production. Config changes produce exit 3 with new-run guidance and preserve the old configuration.
- Pilot sizes are only 100, 1,000 and 10,000, in order. A controlled 100-candidate orchestration stops at 100 and warm replay invokes no provider processing; neither action records production authorization.
- Production requires deliberately supplied archived final report identity, matching run/config, staged reports and no known critical defects. Missing/stale/mismatched reports fail before source client construction. Freeze never treats a small pilot as a smaller valid corpus.
- Metrics distinguish invocation-specific physical requests, retries/errors and denominators, raw/derived cache, new/reused candidate work, wall/active/backoff time, unexecuted stages, resource samples and cold/warm assumptions. Rolling progress starts with unknown ETA. Cold production extrapolation has an explicit sensitivity range and unmeasured full-size costs; warm replay never becomes a cold ETA.
- Template attempted/success/unresolved denominators retain completed extraction even if a later stage fails. Affected articles are distinct; supported argument-only formatters, excluded non-prose and unsupported factual forms remain separate. Unresolved lead/sentence/link outputs stay provisional and outside eligible selection.

Evidence: `test_resume.py`, `test_pilot_controls.py`, `test_review_regressions.py`, `test_pilot_report.py`, `test_progress.py`, `test_cli.py` and the shared source/storage suites. Final aggregate result is in `pilot-readiness.md`; independent review and regressions are in `review.md`.

All cohort processing in these tests uses synthetic providers and temporary stores. No real `pipeline pilot`, production command or real corpus freeze was invoked. The user must explicitly release the Manual Pilot Start Gate before T049; T097 remains a separate later decision.
