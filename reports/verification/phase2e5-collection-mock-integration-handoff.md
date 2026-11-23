# Phase 2E.5 — Offline collection mock integration

Baseline: `609b546b18f75e5cc04f0b31827b7dec19660817`, verified in a separate checkout. Branch: `codex/phase-2e5-collection-mock-integration`. Operator authorized local implementation, verification, commit and branch push. No SSH, UIT, Slurm, dependency installation, source-network acquisition or production execution occurred.

## Integration

`collect_batch(..., synthetic_budget=SyntheticCollection(launched_gate))` explicitly exercises the real historical collection workflow offline. It requires `VIMFC_OFFLINE=1`, acquisition mode, `allow_network=False`, no client factory, an exact `SyntheticBudgetTransport` and an exact HTTPX `MockTransport`. There is no CLI/configuration selector for this fixture mode. The collection binding ID, run ID, bundle ID, workspace limit and invocation limit must match the independently launched gate.

The existing production request guard is unchanged. In the fixture path it runs at the transport boundary, so omitting HTTPX request hooks or calling `client.send()` still passes through that guard and durable admission. A missing binding/guard or substituted transport fails closed. Each transport-bound request receives a fresh lifetime ID and final-request pin; the existing adapter commits its unit and acknowledges the witness before the mock handler executes. Its unresolved-outcome, stale-owner, lifetime-reservation, pacing and request-mutation checks remain intact.

Before admission, the collection checkpoint counter is reconciled upward to durable spending. A counter above the ledger requires explicit cutover review. Lower/restored counters cannot replenish allowance; durable completion/charge timestamps still control pacing. The original guard retains its conservative checkpoint precharge: a later durable denial can leave a higher local counter without a physical call. This never creates allowance, and no refund or automatic repair is supplied. Collection receipt attempt counts retain the original guard semantics; the authority ledger is the physical-attempt accounting authority.

HistoricalProvider, RevisionSource, MediaWikiClient, stage checkpoint/lease behavior, snapshot/content validation and source retry/cooldown policies are reused without edits. Cached source/checkpoint results require no new HTTP charge. The collection client still uses one attempt per query; the existing client retry loop is additionally exercised through the fixture boundary with explicit mock responses. Move-log continuation pages, revision lookup and content fetches each cross admission independently.

## Final verification

Both final runs used the same source/test versions, `VIMFC_OFFLINE=1`, the existing autouse socket-denial guard, Python `-B`, pytest `-q --tb=short -p no:cacheprovider`, fresh basetemps and JUnit output. Process-crash children explicitly install the socket guard and import this isolated checkout.

- Focused: **244 passed**, exit 0, 136.36 seconds. Includes 30 new collection integration cases, existing collection/transport tests, transactional control, activation, selection epoch/recovery and durable publication.
- Full safe offline regression: **1229 passed, 3 skipped**, exit 0, 539.07 seconds. All skips are existing Windows symlink privilege cases in backup recovery/local snapshot.
- Environment: Windows, Python 3.12.10, SQLite 3.49.1, HTTPX 0.28.1, pytest 9.1.1.
- Four real subprocess exits (`os._exit(71)`) at charge acknowledgment, pre-dispatch, post-transport and pre-outcome. Fresh authority objects retain spending; existing stage leases block immediate restart, and admission still denies replay after a synthetic lease expiry. A separate fresh-owner test verifies that unresolved charges block a newly launched epoch.
- Other cases cover independently charged lookup/content and move-log continuation, cache/checkpoint reuse, fresh-owner continuation after counter restoration, timeout/maxlag/429/503 cooldown, retry IDs, duplicate IDs, stale epochs, stopped owners, reservation/workspace/invocation exhaustion, high local counters, removed request hooks, explicit HTTPX send, hook mutation and non-mock rejection.

Local evidence: `.tools/e5-evidence/` contains `focused.xml`, `full.xml`, separate stdout/stderr and exit files, environment details, before/after code hashes and preservation receipts. Development runs in `.phase2e5-evidence/` are superseded by these final runs and are not acceptance evidence.

The three final code/test SHA-256 values matched before/after both final suites. Seven targeted protected hashes matched: budget adapter, transactional authority, MediaWiki client, revision source, historical identities, provider and annotation-builder test. `git diff --check` passed. The original checkout's annotation fixture remains SHA-256 `35f3a77ed26f8039fa97124a223c6d93e6448b0b7cfaf65dc201bbf054472cae`; no fixture patch was applied to this isolated checkout or included in the commit. No classification, selection or annotation policy changed. This is targeted evidence, not a claim that every untracked artifact or the old venv was exhaustively hashed.

## Scope and status

The boundary is a cooperating offline fixture, not a sandbox against malicious Python code or malicious mock handlers. It does not implement production egress, protected trust anchors, accounting cutover, physical fencing, automatic crash disposition or coordinated rollback detection. Native Linux validation of this integration was not performed; the earlier Phase 2E.4 results cover its earlier adapter baseline only.

```text
PHASE_2E_5 = PASS
COLLECTION_MOCK_INTEGRATION = PASS
UNIFIED_REQUEST_ACCOUNTING = PASS
RESTORE_AND_RETRY = PASS
ZERO_REAL_NETWORK = PASS
OFFLINE_REGRESSION = PASS
PRODUCTION_INTEGRATION = NOT_AUTHORIZED
PILOT_10000 = NO_GO
```

PASS applies only to the tested local offline synthetic scope.
