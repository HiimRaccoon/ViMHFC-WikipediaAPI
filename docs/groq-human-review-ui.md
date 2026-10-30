# Local Human Review & Groq Quality Evaluation

Review the saved `qwen/qwen3.8-27b` suggestions against human semantic judgments
and the archived `qid-5` historical evidence. The UI runs entirely on localhost
using existing FastAPI/uvicorn dependencies and static HTML/CSS/JavaScript.
The React frontend was only a setup entry point; this interface needs no Node
build or new framework to run. Static assets are also included in Python wheels.

This workflow makes **no Groq requests**, does not resume the 121 pending slots,
and has no corpus/policy/eligibility/prediction mutation interface. Offline
network guards block outbound sockets and DNS; Windows asyncio's exact stdlib
socketpair caller is the only loopback connection exception. Historical reads
use the existing WAL-aware read-only Archive and raw hash validation.

## Start on Windows

From `C:\ViFC_MultiHop_Data`, with the existing Python 3.12 environment:

```powershell
& .\.tools\python312\python.exe -B .\scripts\groq_review.py serve `
  --profile .\configs\groq-review.local.json
```

Open **http://127.0.0.1:8765**. Ctrl+C stops the server. SQLite keeps all saved
labels/audits and revision history across restart; the next start resumes the
same workspace. Nothing invokes inference. `--port 8767` selects another local
port. The service binds only 127.0.0.1 and rejects foreign Host/Origin requests.
API credentials are neither needed nor loaded by the review workflow.

The local profile was prepared for this checkout and is git-ignored. For a new
checkout, restore the existing local frozen inputs/packet and copy
`configs/groq-review.example.json` to `configs/groq-review.local.json`, adjusting
paths and manifest to that packet. The repository deliberately excludes raw
caches, experiment data and human label databases. The portable `.tools` Python
is local; a separately installed Python 3.12 with `pip install -e ./backend`
can run the same commands with `python`.

## Review sample and preservation

The versioned packet is `data/experiments/groq-review-20261031/packet` with
manifest `4aec69e7ea99315d7911592121d982c9da5fadbb9189a720f1de9098d5b76cd9`.
The old eleven-case packet and its editable labels remain untouched.

- **60 evaluation_random**: fixed sample allocations across 20 nonempty
  baseline-outcome × Groq-group/abstention cells, then deterministic seeded
  within-cell draws. Each member has probability `sample_count/population_count`.
  Every cell is represented before the remaining slots are allocated by largest
  remaining cell population capacity. Seed: `20261031`.
- **30 diagnostic_targeted**: additional distinct cases, selected after the
  evaluation draw, covering required old HR cases/control disagreements first,
  followed by round-robin Science/reason/abstention risk cells.
- All HR01–HR11 and all five saved control disagreements occur in the union.
  Required cases selected randomly retain evaluation membership; their presence
  does not change the evaluation draw. Review IDs/order are newly shuffled R001+
  and do not expose old HR identities in the blind UI.

`coordinator/manifest.json` records complete frame IDs, allocation, inclusion
probabilities, input/source hashes, members and diagnostic reasons. Keep it,
comparison/evidence files and reports away from annotators until labels are
saved. The default UI does not expose these fields. Give external annotators
only `blind/` or a separate local UI workspace using the same immutable packet.

The random frame is the **179 responses**, not the original 300 slots or the
20,000-article target corpus. No outcome-weighted estimate is produced from
partial human review. Diagnostic metrics stay separate throughout.

## Semantic labeling

The interface initially shows only historical title, retained Lead/sentences,
article/revision timestamp/snapshot and input identity. Sentence checkboxes
select semantic evidence, not model evidence. No primary or membership is
prefilled from a system label.

| Membership | Primary | Semantic sentence IDs | Interpretation |
|---|---|---|---|
| IN_TAXONOMY | Exact one of eight groups | At least one known integer ID | Subject belongs to taxonomy |
| OUT_OF_TAXONOMY | null | Optional | Enough evidence; subject outside all eight groups |
| INSUFFICIENT_EVIDENCE | null | Optional | Supplied text cannot establish membership |
| AMBIGUOUS | null | Optional | Competing interpretations remain unresolved |

Every saved semantic payload contains `membership`, `primary_group`,
`evidence_sentence_ids`, `confidence` (`high/medium/low`), `notes`, `reviewer`,
and `review_timestamp` (ISO 8601 including timezone). Uncertainty/ambiguity needs
explanatory notes. IDs reject booleans, duplicates and unknown sentences. The
server separately records receipt time, input fingerprint and manifest.

The UI saves timestamp automatically at Submit and restores the reviewer's own
drafts from browser storage. Only Save/import creates actual review records.
SQLite stores immutable, hash-chained semantic and audit revisions; Save uses
the displayed revision to reject stale tabs. Inputs/navigation are locked while
saving. Previous/Next, jump-to-ID and Resume never auto-label another article.

`blind/INSTRUCTIONS.md` and `blind/taxonomy.json` include all eight groups,
archived supported root/subtype names and the exact precedence:

`Landmark / Facility > Award / Competition > Organization > Location > Creative Work > Person > Science / Technology > Event`.

The guide uses existing prompt explanations as descriptive text; the archived
roots/precedence remain authoritative. In particular Science / Technology is
not a catch-all for generic concepts. Applying precedence requires evidence for
multiple groups first. Judge the subject, not an entity merely mentioned in the
Lead. Do not use current Wikipedia or prior ChatGPT guesses as human gold.

## Separate historical evidence audit

After Save, **Mở Human vs Groq vs qid-5 & audit** unlocks comparison for that
article. Viewing it records permanent exposure; later semantic edits are
explicitly marked as having seen system labels. Export/import preserves that
flag. Such corrections should not be described as an independent first blind
judgment. The history panel retains the initial decision and later revisions.

Evidence includes the original classification, primary candidates, ontology
paths, diagnostics, closure/frontier, dependency manifest and node revision/hash
provenance, checked against archived source. `coordinator/policy-sources.json`
also records the classifier code read during preparation and its hashes.

| Original evidence availability | Meaning |
|---|---|
| COMPLETE | Saved original primary verified under archived policy |
| ABSENT_SUPPORTED_RULE | No supported mapping in the saved classification; not proof of OUT |
| AMBIGUOUS | Saved policy could not resolve primary ambiguity |
| DEPENDENCY_UNAVAILABLE | Required historical dependency unresolved |
| NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE | Available artifacts cannot establish verification |

Audit is a **separate attributed decision**: `VERIFIED`, `CONFLICTING_EVIDENCE`,
or `NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE`, with notes and cited available
`manifest:<id>` / `node:<qid>:<revision>` references. VERIFIED requires the exact
original completed historical primary and evidence references. A path for a
different candidate does not verify the suggested primary. This workflow does
not manufacture evidence to turn an unresolved original result into verified
coverage. Cache absence never establishes historical nonexistence.

Error tags include subject confusion, organization/facility, event/award,
location/organization, creative/science, precedence, outside taxonomy,
Science catch-all and other. They are entered by the auditor after comparison,
not inferred from a disagreement. Each audit names the semantic revision seen;
semantic edits invalidate its current use in metrics until re-audited. Stale
audit tabs are rejected. Browser audit drafts are keyed by semantic revision.

## CSV/JSONL transfer and metrics

UI export/import buttons support both formats. CLI examples:

```powershell
# Output must be new; use v2/v3 names for later exports.
& .\.tools\python312\python.exe -B .\scripts\groq_review.py export `
  --profile .\configs\groq-review.local.json --format jsonl `
  --output .\data\experiments\groq-review-20261031\human-labels-v1.jsonl

& .\.tools\python312\python.exe -B .\scripts\groq_review.py import `
  --profile .\configs\groq-review.local.json `
  --labels .\data\experiments\groq-review-20261031\human-labels-v1.jsonl

& .\.tools\python312\python.exe -B .\scripts\groq_review.py evaluate `
  --profile .\configs\groq-review.local.json `
  --output .\reports\verification\groq-human-review-ui-20261031\scores-v1
```

For CSV use `--format csv` and `.csv`. UTF-8 BOM and formula escaping protect
spreadsheet display; quoted/apostrophe-prefixed notes round-trip exactly.
Evidence ID lists and the optional audit payload are JSON inside CSV cells.
JSONL is the easiest format to edit with a text editor.

Transfer schema v2 pins `schema_version`, `manifest_id`, `review_id`, `page_id`,
article `revision_id`, `input_fingerprint`, `review_status`, `exported_revision`,
`comparison_exposed`, the semantic fields, optional `audit`, and
`exported_audit_revision`. To review a pending exported row, fill all semantic
fields and set `review_status=reviewed`. Pending rows keep null semantic/audit
fields and are never gold. Do not alter identities or exported revision tokens.
Foreign/duplicate IDs, extra columns, comparison files, invalid dates/flags and
stale revisions are rejected. All rows validate before one atomic transaction;
failure leaves no partial imports. Imports into a fresh workspace retain the
source exposure flag; imports into an existing workspace must match its current
revision. If stale, export current labels and reconcile manually.

Evaluation writes new `metrics.json` and `report.md` outputs:

- **RQ1** reports all four human membership behaviors, assigned labels on OUT,
  false inclusion among human OUT, and operational membership precision/recall
  on decided human IN/OUT. Abstain is not an explicit OUT classifier. Missing
  denominators yield null, not fabricated zero accuracy.
- **RQ2** conditions primary accuracy/precision/recall/F1/confusion on human IN
  only. Model abstain/unresolved is wrong on that denominator. Macro-F1 includes
  all eight groups with zero division=0; group supports and groups with support
  <5 are disclosed. OUT/uncertain/ambiguous never become primary-group errors.
- **RQ3** reports saved non-abstaining suggestions for original baseline gaps:
  semantic correct/wrong, undecidable and pending, historical audits/verified
  suggestions, semantic-correct without verification, and **Correct Additional
  Coverage** over reviewed articles and reviewed baseline gaps. It breaks down
  no_supported_rule, primary_ambiguity and primary_dependency_missing separately.
- qid-5 baseline, Groq-only and Wikidata-first suggestions have separate
  coverage and human metrics. Science probes/error tags are reported. Current
  metrics use latest attributed semantic revisions; prior/exposed decisions
  remain identifiable in history.

Both partitions are always separate. Without human submissions the status is
**UNASSESSED**, reviewed=0 and RQ1/RQ2/RQ3 are null. No quality conclusion follows
from dataset/UI readiness. Labels are evaluation-only, never corpus acceptance.

## Reproduce preparation and tests

Preparation validates the frozen 300 inputs through the existing full loader,
checks the 179 responses, binds old HR cases and rereads archived classifications
offline. Use a **new** packet output for another preparation:

```powershell
& .\.tools\python312\python.exe -B .\scripts\groq_review.py prepare `
  --dataset .\data\experiments\groq-classification-20261029-111240-537171\dataset-v2 `
  --results .\reports\verification\groq300-20261029-151008-991893\final\results.json `
  --previous-bundle .\data\experiments\groq-classification-20261029-111240-537171\human-review-20261030-153404-669815 `
  --output .\data\experiments\groq-review-NEW\packet --seed 20261031

& .\.tools\python312\python.exe -B -m pytest -q -p no:cacheprovider `
  tests/integration/test_groq_review_packet.py tests/integration/test_groq_review_store.py `
  tests/integration/test_groq_review_evaluation.py tests/integration/test_groq_review_server.py `
  tests/integration/test_groq_review_cli.py tests/integration/test_groq_human_review.py `
  --basetemp=.tools/groq-review-ui/focused-check --tb=short

& .\.tools\python312\python.exe -B -m pytest -q -p no:cacheprovider `
  --basetemp=.tools/groq-review-ui/full-check --tb=short

Push-Location frontend
npm.cmd run test:review
Pop-Location
```

The browser suite uses **synthetic controls in a separate workspace**, installed
Chrome and the existing Playwright dependency. `VIMFC_REVIEW_PYTHON` can select
another Python executable. It never loads the real review profile. Its shutdown
route exists only in the synthetic fixture, not in the real application.

See [technical handoff](../reports/verification/groq-human-review-ui-20261031.md)
for actual sample counts, acceptance and preservation evidence.
