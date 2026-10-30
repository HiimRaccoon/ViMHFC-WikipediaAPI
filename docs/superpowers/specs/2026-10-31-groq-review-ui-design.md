# Local Groq human review and quality evaluation

The user requires an implemented, offline review workflow for the 179 saved
`qwen/qwen3.8-27b` responses, not additional inference. The deliverable is a
localhost interface ready for the user to label, with all human metrics
UNASSESSED until real submissions exist. The latest chat explicitly authorizes
commit/push to the existing GitHub remote, superseding the attachment's older
no-commit/push boundary. No other execution or promotion boundary changes.

## Design

Reuse the existing validated dataset loader, response validation, hashing, CSV
escaping and classification metrics. Preserve the old eleven-article packet.
Create a versioned packet with 60 deterministic stratified random evaluation
articles and 30 additional targeted diagnostics (90 distinct articles). Draw
evaluation first from all 179 valid responses using baseline outcome × Groq
group/abstention cells; allocate at least one per nonempty cell, then allocate
remaining slots by largest remaining population capacity. Each cell is a fixed
size simple random draw using seeded hash ranks; record n/N inclusion
probabilities and every frame identity. Required old HR cases and all control
disagreements must be in the union; diagnostic priority also covers Science,
missing rules/dependencies, ambiguity and abstentions. No diagnostic population
estimate; no extrapolation from the response frame to 300 or the corpus.

FastAPI serves bundled HTML/CSS/JavaScript at 127.0.0.1. The existing React/Vite
entry point is empty, so adding a build toolchain gives no reusable interface.
No new dependency is needed. The server cannot initiate external requests and
has no provider, credential or pipeline mutation interface. Packet artifacts
are hash-bound; runtime SQLite lives only in a new review workspace, never in
source/cache/registry/experiment inference directories. Reject unsafe paths,
foreign host/origin requests and stale revisions.

Blind endpoints expose title, retained lead/sentence IDs, article revision,
snapshot and input fingerprint only. No sampling metadata, old HR identity,
baseline, prediction, reason, ontology evidence or request key is exposed.
Saving a real semantic submission unlocks comparison and a separate evidence
audit for that article. UI displays previous exposure on later edits; every
save/import creates an immutable revision, including original timestamps,
server receipt time and import source. Concurrent edits use expected revision.

Semantic labels distinguish IN_TAXONOMY, OUT_OF_TAXONOMY,
INSUFFICIENT_EVIDENCE and AMBIGUOUS. IN requires an exact group and at least one
known integer sentence ID. Other memberships forbid primary labels. Require
confidence, reviewer and timezone-aware timestamp; uncertainty requires notes.
Only reviewed submissions are gold. JSONL/CSV use manifest-bound identity and a
strict whitelist, with import validation of the whole file before one atomic
transaction. No imports from comparison files or historical ChatGPT labels.

## Historical evidence

Archive the actual qid-5 configuration, roots/subtypes/precedence and classifier
implementation identities. Audit evidence comes from checksum-validated saved
process checkpoints and their dependency manifests, read through the existing
WAL-aware read-only Archive. Preserve original outcomes, paths, diagnostics,
frontier and revision/hash metadata; never rerun/promote classifications.
Completed original evidence verifies only its original primary, not a different
Groq suggestion. Missing rules are not proof of OUT; missing cache is not proof
of historical nonexistence. An audited suggestion is marked VERIFIED only if
the supplied historical policy evidence binds that exact group; otherwise use
NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE, CONFLICTING_EVIDENCE or NOT_AUDITED.
Save a separately attributed audit decision with cited available evidence refs.

## Evaluation

Report evaluation_random and diagnostic_targeted separately for qid-5,
Groq-only and Wikidata-first suggestions. RQ1 reports assigned/declined behavior
by all four human memberships, false inclusion among OUT, and operational
membership precision/recall on decided IN/OUT (decline is not explicit OUT).
RQ2 conditions accuracy and 8-group PR/F1/confusion on human IN only; abstention
is wrong on that denominator. Include all eight groups, support and small-sample
warnings, plus audit-tagged precedence errors. RQ3 conditions on non-abstaining
suggestions for original unclassified/unresolved, reports semantic correct,
wrong, undecidable and pending separately, historical verification separately,
and correct additional coverage with explicit denominators and per-reason
breakdowns. Pending human records never enter gold denominators. Do not weight
partially reviewed samples into population claims. Metrics and audit rates are
null/UNASSESSED without relevant reviewed records.

## Acceptance

Focused offline tests cover sampling determinism/coverage/probabilities,
tampering/source protection, four-way schema validation, typed evidence IDs,
blind HTTP responses, persistent save/resume/history, stale revisions,
atomic CSV/JSONL import/export and metrics denominators. Full pytest must pass.
Exercise the real localhost UI with browser automation if available, preserving
zero real labels using a separate synthetic workspace. Prepare the real packet,
record zero real submissions, hash/table preservation and unchanged 179 results,
188 attempts and 121 pending. Supply PowerShell commands, guide, manifest,
readiness outputs and a technical handoff. Review and scan selected Git content
before commit/push; do not publish credentials, runtime DBs or raw caches.
