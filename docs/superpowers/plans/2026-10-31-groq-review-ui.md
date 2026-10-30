# Groq Human Review UI Implementation Plan

> **For agentic workers:** Execute inline with executing-plans and TDD; use one fresh final code reviewer.

**Goal:** Deliver a ready-to-label local review UI, historical audit and three-RQ offline evaluation for existing Groq responses.

**Architecture:** A new versioned packet and review workspace extend existing immutable experiment utilities. FastAPI serves static assets and a revisioned SQLite review store. All source access is read-only.

**Tech Stack:** Python 3.12, existing FastAPI/uvicorn, SQLite, HTML/CSS/JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-groq-review-ui-design.md`

## Global Constraints

- No Groq requests, pending resume, source/policy/prediction changes or corpus promotion.
- Snapshot `2026-10-25T03:00:00Z`; archived `qid-5 / lead-4 / quality-2.1 / rank-1`.
- Preserve old packet and actual human labels; create none for the real packet.
- User explicitly requests implementation and GitHub push; execute in this checkout to retain uncommitted historical prerequisites and datasets. Commit selected source prerequisites explicitly, leaving unrelated artifacts intact.

## Review Focus

- Imported notes beginning with spreadsheet formula characters must round-trip safely.
- Refresh/concurrent tabs must not silently replace a newer review.
- Same article's prior comparison exposure must remain visible on label edits.
- An audit cannot verify a new suggestion using an unrelated baseline path.
- Import failure midway must leave no partial human labels or audit revisions.

### Task 1: Versioned sampling, packet and archived evidence

**Files:** `review_sampling.py`, `review_packet.py`, `tests/integration/test_groq_review_packet.py` under the existing experiment package/tests.

**Interfaces:** `select_review_sample(rows, results, seed, evaluation_size=60, diagnostic_size=30, required_ids=())`; `create_review_packet(...)`; `load_review_packet(root, expected_manifest_id)`.

- [x] Write/run failing tests for 60+30 distinct deterministic members, all required cases, cell probabilities, source identities and blind whitelist.
- [x] Implement frame allocation, packet2 hashing, archived policy guide and evidence export using existing validation utilities.
- [x] Run packet tests; record source preservation baseline before real preparation.

### Task 2: Review schema and durable revision history

**Files:** `review_schema.py`, `review_store.py`, `tests/integration/test_groq_review_store.py`.

**Interfaces:** `validate_semantic(payload, reference)`; `ReviewStore(packet, workspace)` with save/current/history/import/export and separate audit saves.

- [x] Write/run failing tests for four memberships, primary/evidence conditions, provenance binding, stale saves, history after reopen, safe workspace paths and atomic imports.
- [x] Implement strict validation, transactional revision store, comparison exposure and typed CSV/JSONL transfer.
- [x] Run store tests including formula round-trips and rollback on later invalid rows.

### Task 3: Three-RQ metrics and offline CLI

**Files:** `review_evaluation.py`, `scripts/groq_review.py`, `tests/integration/test_groq_review_evaluation.py`.

**Interfaces:** `evaluate_review(packet, submissions, audits)` returns partitioned metrics; CLI `prepare`, `serve`, `export`, `import`, `evaluate`.

- [x] Write/run failing denominator tests including OUT, ambiguous, insufficient, pending and semantic-correct/unverified distinctions.
- [x] Implement null/UNASSESSED state, three approaches, 8-group primary metrics, membership behavior and correct additional coverage with per-reason RQ3.
- [x] Verify commands are offline and outputs cannot overlap historical inputs.

### Task 4: Local blind review and historical audit interface

**Files:** `review_server.py`, `review_static/index.html`, `review_static/app.js`, `review_static/style.css`, `tests/integration/test_groq_review_server.py`, `frontend/tests/groq-review.spec.ts`.

**Interfaces:** `create_review_app(packet, workspace, expected_manifest_id)`; loopback HTTP endpoints expose only blind reference until saved.

- [x] Write/run failing HTTP tests for blind leakage, post-submit compare/audit, no credential/source routes and localhost origin controls.
- [x] Implement navigation, persistent saves/resume, revision view, evidence selection, confidence/reviewer/time, separate audit and import/export/evaluation controls.
- [x] Run HTTP tests and real browser smoke in a synthetic workspace where possible.

### Task 5: Real packet, verification, handoff and GitHub

**Files:** `docs/groq-human-review-ui.md`, `reports/verification/groq-human-review-ui-20261031.md`, separate local data/report outputs.

- [x] Prepare real 90-article packet with validated source loader and preserved HR01–HR11; export blank labels and UNASSESSED metrics.
- [x] Run focused tests and full offline pytest; verify source hashes/table rows/ledger unchanged and no real labels.
- [x] Ask fresh reviewer to assess against this spec; address material findings with regression tests.
- [x] Produce documented PowerShell serve/evaluate commands, sampling counts, preservation audit and full technical handoff.
- [ ] Scan/stage only intended code/docs/tests/prerequisites, commit and push to the user-specified GitHub remote; verify remote commit.
