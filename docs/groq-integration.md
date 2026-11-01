# Wikidata-first + Groq-assisted shadow integration

`wikidata-first-groq-provisional-1` wraps archived qid-5 decisions without
changing source runs, snapshot, inventory, historical evidence or eligibility.
Human Review is deferred; no UI work or human gold is part of this workflow.

## Routing

1. Return the original verified primary before per-article LLM cache/live work.
2. For unclassified/unresolved articles, require a trusted Historical Lead,
   checked through the existing read-only Archive and extraction replay.
3. Reuse cached responses only after article/input/model/prompt/schema checks.
   Legacy abstention means unspecified membership, not OUT. Legacy assigned
   results require cited sentence0/title linkage; Science needs the new scope check.
4. New responses distinguish IN, OUT, INSUFFICIENT and AMBIGUOUS; validate exact
   subject, supporting sentence IDs/quotes and technical scope for Science.
5. Set `llm_provisional_primary` separately from `wikidata_verified_primary`.
   Provisional candidates pass saved extraction/quality criteria other than the
   verified-classification prerequisite. They never become verified eligibility,
   accepted graph evidence or gold.

These are conservative textual consistency checks, not semantic entailment or
measured accuracy. They may reject valid aliases/topics. OUT is model-declared,
not verified taxonomy truth; human accuracy remains UNASSESSED.

## Offline commands

From `C:\ViFC_MultiHop_Data`, using the existing Python 3.12 environment:

```powershell
& .\.tools\python312\python.exe -B .\scripts\groq_integration.py prepare `
  --context .\data\experiments\dump-seed-only-20261028-084004-499522\run-context.json `
  --legacy-ledger .\api\groq-experiment-ledger.sqlite `
  --output .\data\experiments\groq-integration-NEW\dataset

& .\.tools\python312\python.exe -B .\scripts\groq_integration.py replay `
  --dataset .\data\experiments\groq-integration-20261031\dataset `
  --manifest-id c5c86653ccab73d97b201977d54f0b5a61aee3da7e57dd10471a9833a39b047c `
  --output .\reports\verification\groq-integration-NEW\offline
```

Both commands block outbound networking. Output directories must be new. Frozen
datasets, raw caches and ledgers remain local/ignored; a fresh clone needs those
preserved artifacts. A separately installed Python3.12 with the existing backend
dependencies can replace the local `.tools` interpreter.

## Finite live execution

The31/10/2026invocation used `qwen/qwen3.8-27b`, at most61HTTPattempts
(one model lookup and60chats),100000tokens,900seconds and1attempt/key.
It stopped at HTTP429; its absolute deadline has expired. **Do not resume
inference, reset the meter or change the dataset to obtain another allowance.**
The01/11/2026follow-up authorizes source publication only. No additional Groq
requests were made during publication.

Within an explicitly authorized active envelope, the CLI form is:

```powershell
& .\.tools\python312\python.exe -B .\scripts\groq_integration.py run `
  --dataset <frozen-dataset> --manifest-id <pinned-manifest> `
  --output <owned-live-output> --config .\configs\groq_integration.json `
  --env-file .\api\.env
```

`--resume` requires matching ownership/configuration. Saved successful HTTP
receipts recover offline before another model lookup/reservation, even after
budget/deadline expiry. Invalid terminal responses are not regenerated;
reserved/uncertain requests need reconciliation. The one-attempt policy never
silently retries a known failed POST; that request requires explicit review.

The fixed shared meter
`data/experiments/groq-integration-rate-1/ledger.sqlite` starts as a read-only
SQLite backup of the original ledger, carrying prior account windows forward.
Its owner pins dataset, limits and absolute deadline. New output paths or
manifests cannot reset the allowance. A lease serializes integration workers;
database/WAL divergence guards refuse detected source/legacy-worker changes.
They are not an atomic lock shared with unrelated external Groq clients.

Caps30RPM/1000RPD/8000TPM/200000TPD were checked against
[Groq documentation](https://console.groq.com/docs/rate-limits) on31/10/2026.
Lower provider headers and429win, including error headers from model discovery.
No fallback model or paid-tier action occurs. Live credentials are never logged.

## Reports and tests

Reports keep the985processed denominator, disclose the1000-slot cohort and
15source-blocked slots, and separate verified/provisional/abstained/model-OUT/
unresolved counts, eight-group coverage, extraction/quality passes and candidates.
Coverage is not accuracy or verified-primary growth. Read the operations
`status`; normal CLI return does not establish complete inference.

Provider usage is actual only where returned. Error reservations are not
observed tokens; cache reuse incurs no new charge. Recorded
[paid-list-price equivalents](https://console.groq.com/docs/models) are not
invoices. Free-plan spend0is conditional on the existing user's confirmation.

```powershell
& .\.tools\python312\python.exe -B -m pytest -q -p no:cacheprovider `
  tests/integration/test_groq_integration_policy.py `
  tests/integration/test_groq_integration_shadow.py `
  tests/integration/test_groq_integration_runner.py `
  --basetemp=.tools/groq-integration/focused-check --tb=short
```

Tests use synthetic data and cover routing, schema, cache identities, denominators,
crash recovery, source protection, deadlines, new-scope refusal, original WAL
writes and provider-error caps. See the
[Technical Review Handoff](../reports/verification/groq-integration-handoff-20261101.md)
for actual results, preservation and proposed same-cohort Pilot1000design.
