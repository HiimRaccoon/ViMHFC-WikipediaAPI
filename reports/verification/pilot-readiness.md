# Manual Pilot Start Gate — readiness report

Date: 2026-10-25 (Asia/Saigon).

**Status: builder implementation and offline fixture verification through T048 complete. STOP before T049.** The user has not requested real pilot execution. No real pilot, production acquisition or research corpus freeze ran. Passing tests does not release this gate or T097.

**Subsequent update (2026-10-25):** The paragraph above records the pre-execution readiness state. The user later authorized pilot 100 only; T049 has now completed with verified source/artifact checks. See the [pilot 100 assessment](../pilots/b0243673-5f0b-4ac6-b8b3-0ca7aa74ccc6/100/summary.md). No pilot 1,000/10,000, production or real freeze has been authorized or run.

## Verification

Final command, from the repository root:

```powershell
& .\.tools\python312\python.exe -m pytest --basetemp .tools/acceptance-final --junitxml .tools/acceptance-final.xml -q --tb=short
```

Result: **81 passed in 80.22 seconds**, exit 0, zero failures/errors/skips. Raw local output: `.tools/acceptance-final.log`; machine-readable results: `.tools/acceptance-final.xml` (ignored verification scratch). Tests deny real sockets and use synthetic sources; controlled 20,000-record publication fixtures are not a research corpus.

Other setup/package checks:

- Python 3.12.10; pinned backend dependencies clean-installed into an isolated target and imported successfully.
- Current backend built into a wheel, installed, and `python -m vimfc --help` succeeded. `python -m pip check`: no broken requirements.
- Node 24.14.0; clean locked npm install, TypeScript check and Vite 7.3.7 build succeeded.
- Vitest command is configured but explicitly reports no test files; no frontend feature/browser acceptance is claimed. Frontend is T002 setup only.
- Git diff whitespace check passed. `.agents`, data/DB/cache/raw/bulk outputs are ignored; curated verification and compact pilot summaries remain committable.

Story evidence: [setup](setup.md), [US1](us1.md), [US2](us2.md), [independent review and fixes](review.md), [execution ledger](implementation-ledger.md).

## Delivered boundary

T001–T014: setup, strict source/evidence value contracts, versioned config, run/stage/raw storage and CLI foundations.

T015–T038: historical acquisition/identity/Wikidata access, safe Full Lead extraction and sentence identity, quality/classification, sparse pair scoring, deterministic selection, reports, exact-count freeze and read-only package verification.

T039–T048: compatible checkpoints/reuse, leases and stale-completion fencing, bounded retry/progress, performance/template instrumentation and manual pilot/production controls, all exercised with fixtures.

Phase 3 is complete. Phase 4's implementation/tests are complete through T048, while its real-pilot tasks T049–T053 remain unchecked and blocked. Annotation API/UI/startup/export work in T054+ is not delivered by this gate.

## Remaining empirical risks and prerequisites

- Select the intended snapshot explicitly and configure a real Wikimedia contact/User-Agent before creating a live acquisition run. Defaults intentionally contain no invented contact.
- Real historical API/move coverage, supported template/classification yield, source dependency fan-out, source backoff and extraction completeness need the authorized 100 → 1,000 → 10,000 pilots. Conservative unresolved outcomes may require more candidates; quality is not relaxed.
- Production-scale memory/disk/throughput and actual 80,000-candidate ETA remain unmeasured. Deferred minor from review: repeated selected-set copies in marginal-gain computation should be measured before optimization. The implementation does not promise full-scale throughput.
- Human Valid 2-hop Yield remains unassessed. Pair scores never establish factual evidence, human labels or hops. The 20,000 real corpus and 2,000 human claims are not delivered.
- Second-machine/offline annotation acceptance belongs to later tasks. Source provenance fixtures do not prove real-world historical completeness.

The independent review found ten Important issues; regressions reproduced and fixed them. A second review was not run; final regression/full-suite evidence supports the fixes. No known Important finding from that review is left unresolved; the scalability minor is recorded above.

## Handoff

Remain stopped before T049 until the user explicitly requests real pilots. After the final pilot reports, stop separately at T097 for the user's production decision. Do not create approval records on the user's behalf.

Git refs were read-only in the sandbox, so branch creation failed. Changes remain uncommitted in the current workspace; no push or merge was attempted. No destructive cleanup or corpus overwrite was performed.
