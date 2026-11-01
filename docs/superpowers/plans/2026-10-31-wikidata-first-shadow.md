# Wikidata-first + Groq-assisted shadow integration

**Goal:** Implement a separate experimental classification policy and replay all985
saved processed Pilot articles without changing any original source decision.
The user explicitly requests implementation and a bounded live experiment,
not further Human Review/UI. The original request prohibited commit/push;
the user's01/11/2026follow-up authorizes source commit and push to the existing
GitHub remote. Pilot/Production execution remains excluded.

## Design and constraints

- `wikidata-first-groq-provisional-1` wraps archived qid-5 decisions. Verified
  primary always wins before lead/cache/provider work. It never invokes Groq.
- Read985checksummed original checkpoints with the existing read-only Archive.
  Validate/replay completed retained leads with `_verified_input`; absent,
  provisional or unresolved extraction cannot trigger inference. Preserve all
  original classification/extraction/quality objects and hashes.
- New inference prompt/schema explicitly distinguishes IN, OUT, INSUFFICIENT,
  AMBIGUOUS, binds article title and supporting subject sentence/quote, and
  forbids Science/Technology as a fallback. Machine checks establish structural
  and textual consistency, never semantic truth or historical verification.
- Reuse legacy saved responses only with exact article/input/model/prompt/schema
  provenance. Legacy abstention has unspecified membership, never automatically
  OUT. Legacy assigned responses need cited first-sentence/title linkage;
  Science requires the new explicit technical-scope response. Unaccepted legacy
  responses are advisory records and may be reconsidered under a new prompt/key.
- Report verified, provisional, abstained, model-declared OUT and unresolved
  as exclusive operational states. OUT is an LLM disposition, not gold.
- Provisional eligible candidates pass all saved extraction/quality requirements
  except the explicit verified-classification requirement. Report them separately;
  verified eligibility remains the original162, with no accepted graph changes.
- Preserve the original Groq ledger byte-for-byte. A new ledger is initialized
  by SQLite read-only backup, carrying existing attempts/rate windows and results
  forward. New budgets use a new experiment scope, not the old300scope. Detect
  original-ledger changes before dispatch and refuse concurrent/divergent use.
- Live envelope:61HTTPrequests (one models lookup and at most60chat attempts),
  100000tokens,900seconds, max1attempt/key,30RPM/1000RPD/8000TPM/200000TPD,
  maxwait60seconds. Actual headers/429can only reduce limits. No next-day resume,
  cap reset, fallback model, invented usage or automatic regeneration of invalids.

## Task sequence

1. [x] Add failing tests for verified-first routing, strict new schema, legacy
   cache migration, four-way membership, subject/quote/Science guard, no promotion.
   Implement `integration_policy.py`.
2. [x] Add failing tests for full-cohort offline freeze, identity/tamper/output
   protection, independent quality gate and report denominators. Implement
   `integration_shadow.py` and prepare/replay/report CLI.
3. [x] Add failing tests for new durable request keys, checkpoint/resume,
   shared-day ledger clone, finite budgets, unknown HTTP outcome and cost/cache
   accounting. Implement `integration_runner.py`, bounded run command/config.
4. [x] Preserve source/cache/registry/inventory/oldexperiment hashes and tables;
   prepare985article dataset and offline shadow report. Verify before live calls.
5. [x] Run focused tests and one fresh independent code review. Execute authorized
   bounded live work, retaining per-request raw receipts before publication.
6. [x] Run full offline pytest and final preservation audit. Export Technical
   Review Handoff, source-integrity limitations and same-cohort Pilot1000design.
   Stop inference; publish source under the01/11follow-up authorization. Do not create human gold.

## Review focus

Verified short-circuit must precede every cache/live side effect; stale cache
identity must not migrate; model OUT must not become human gold; copied ledger
must not reset day usage; successful HTTP persisted before crash must replay
without another POST; failures must retain charged reservations and unknown
outcomes must block resume; candidate metrics must never combine provisional
and verified eligibility; invalid terminal outputs must not become pending
retry loops; source outputs cannot be adopted as integration output directories.
