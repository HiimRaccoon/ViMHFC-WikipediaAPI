# Technical Review Handoff — Human Review UI, 31/10/2026

**REVIEW_READY: 90 articles** (60 evaluation_random +30 diagnostic_targeted).
**781 offline tests PASS; 3 Chrome browser tests PASS.** The actual packet has
**0 human labels, RQ1/RQ2/RQ3 UNASSESSED**. No Groq call or pending resume was
performed. The original experiment remains **179/300 responses: 151 predictions,
28 abstentions, 0 invalid; 121 pending**. No source/policy/prediction/corpus
promotion. The latest user instruction explicitly requests GitHub push and
supersedes the attachment's older no-commit/push clause.

## Start reviewing

From `C:\ViFC_MultiHop_Data`:

```powershell
& .\.tools\python312\python.exe -B .\scripts\groq_review.py serve `
  --profile .\configs\groq-review.local.json
```

Open **http://127.0.0.1:8765**. The local profile is already prepared. The smoke
server was stopped after verification; this command starts it when ready.
Ctrl+C stops it; saved reviews remain in the separate SQLite workspace. See the
[complete operations guide](../../docs/groq-human-review-ui.md) for schemas,
guideline, evidence audit, transfer, metrics, preparation and test commands.

The service uses existing FastAPI/uvicorn dependencies and serves HTML/CSS/JS;
the existing React entry point had no product interface to reuse. No frontend
build is needed to run review. Static assets are packaged with Python as well.
Host/origin checks and an outbound-socket/DNS guard keep the service local.
No API credentials are loaded by this workflow or exposed to the frontend.

## Packet and sampling

- Packet: `data/experiments/groq-review-20261031/packet`.
- Workspace: `data/experiments/groq-review-20261031/workspace`.
- Manifest: **`4aec69e7ea99315d7911592121d982c9da5fadbb9189a720f1de9098d5b76cd9`**.
- Frozen dataset manifest: `ece7a99e23bbd95488abd226e3cc83ff703e33fff23a01d5eaa8f1181ae2e161`.
- Seed: **20261031**. Existing dataset-v2 was not regenerated.
- Sampling manifest, exact IDs/probabilities and hashes:
  `packet/coordinator/manifest.json` (local data artifact, not published with source).
- [Sampling summary](groq-human-review-ui-20261031/sample-summary.json).

Evaluation is drawn first across all179saved valid responses using20nonempty
baseline-outcome × predicted-group/abstention cells. At least one per cell,
then fixed allocations by remaining cell capacity, followed by seeded ranks
within each cell; inclusion probability is exactly n/N. Diagnostics add30new
distinct cases after this draw; no random seed search or diagnostic replacement
of evaluation members. Both scopes remain separate in every score.

| Original qid-5 outcome | Evaluation | Additional diagnostics |
|---|---:|---:|
| completed / verified |32|4|
| unclassified |19|12|
| unresolved |9|14|
| **Total** |**60**|**30**|

| Groq group/outcome | Evaluation | Additional diagnostics |
|---|---:|---:|
| Person |11|0|
| Organization |3|2|
| Location |5|6|
| Event |3|5|
| Creative Work |12|2|
| Award / Competition |7|2|
| Landmark / Facility |3|6|
| Science / Technology |9|3|
| Abstention |7|4|

All HR01–HR11 are present in the union, including HR04 Di sản tư liệu thế giới,
HR05 Loại trừ xã hội, HR08 Dàn nhạc giao hưởng hiện đại, HR01 Mô tô nước tại SEA
Games2025, HR07 Station F and HR10 WrestleMania Backlash. All five original
verified-reference disagreements are included. Their presence identifies review
candidates, not confirmed errors. Members already drawn randomly remain in
evaluation; old HR IDs and partition/group/reason metadata never reach blind
article/session endpoints. The old11-article packet and editable labels remain
unchanged. No old ChatGPT label was imported as human ground truth.

The sample covers12Science/Technology suggestions and11abstentions. It does
not estimate population quality, whole300accuracy or production readiness.

## Implemented behavior

1. **BLIND semantic review:** exact title/Lead/sentence IDs and article revision
   provenance; four distinct memberships (IN, OUT, INSUFFICIENT, AMBIGUOUS),
   conditional primary/evidence requirements, confidence, notes, actual reviewer
   and timestamp. No system-label autofill. Own drafts survive refresh.
2. **Durable review:** Previous/Next/jump/Resume, SQLite transactions, immutable
   hash-chained semantic/audit revisions, receipt timestamps, stale-tab rejection
   and history display. Input/navigation lock prevents typing loss while Save
   is pending. Late comparison/navigation responses cannot cross article IDs.
3. **Post-save comparison:** Human vs Groq vs qid-5 unlocks only after a saved
   semantic submission for that article. Exposure persists through later edits
   and CSV/JSONL transfers; exposed edits are explicitly identified.
4. **Separate historical audit:** archived policy roots/subtypes/precedence,
   classifier source hashes, saved ontology paths/candidates, diagnostics,
   closure/frontier and dependency-node revision/hash metadata. Audit decisions
   are attributed, cite only supplied references and name the semantic revision
   viewed. A stale audit is rejected; a semantic edit invalidates previous audit
   use in current metrics until re-audited.
5. **Transfer:** strict manifest/article/fingerprint binding, typed evidence IDs,
   CSV UTF8-BOM/formula escaping and JSONL, validation before atomic import,
   preserved comparison exposure, new revisions on import, stale export rejection.
   Pending/empty fields do not create gold. Existing source/policy/packet paths
   and unowned workspace directories reject writes.

The authoritative precedence was copied from archived qid-5 configuration:
`Landmark / Facility > Award / Competition > Organization > Location > Creative Work > Person > Science / Technology > Event`.
Supported roots/subtypes are displayed verbatim. Existing prompt descriptions
are explanatory and do not create new ontology rules or a Science catch-all.

Historical statuses distinguish COMPLETE, ABSENT_SUPPORTED_RULE, AMBIGUOUS,
DEPENDENCY_UNAVAILABLE and NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE. COMPLETE binds
only the original archived primary; unrelated candidate paths cannot verify a
new suggestion. Missing cache never proves historical nonexistence. Semantic
correctness never changes verified-primary/eligibility/corpus state.

## Evaluation readiness

Prepared blank exports: `data/experiments/groq-review-20261031/blank-labels.csv`
and `blank-labels.jsonl`. These contain **no human gold**. Current
[metrics](groq-human-review-ui-20261031/unlabelled-evaluation/metrics.json) and
[evaluation report](groq-human-review-ui-20261031/unlabelled-evaluation/report.md)
are UNASSESSED, reviewed0/90, with RQ1/RQ2/RQ3=null in both partitions.

After actual review:

```powershell
& .\.tools\python312\python.exe -B .\scripts\groq_review.py evaluate `
  --profile .\configs\groq-review.local.json `
  --output .\reports\verification\groq-human-review-ui-20261031\scores-v1
```

Use new output directories for later evaluations. UI evaluation uses the same
pure metrics engine. It compares qid-5 baseline, Groq-only and Wikidata-first
suggestions with explicit reviewed-case denominators:

- RQ1: all four membership behaviors; operational IN/OUT precision/recall and
  false inclusion among human OUT. Declining is not explicit OUT detection.
- RQ2: primary metrics/confusion only on human IN; model abstention is wrong on
  that denominator. All8groups enter Macro-F1 with support/zero division rules.
- RQ3: semantic correct/wrong/undecidable/pending gap suggestions, separately
  audited historical verification, semantic-correct-but-unverified counts and
  **Correct Additional Coverage** over reviewed articles and reviewed baseline
  gaps, with per-reason breakdowns.
- Science probes, manually audited error tags, original coverage and groups
  with human support<5 are visible. No pooled diagnostic/evaluation headline,
  no population estimate and no production quality conclusion.

The Groq response schema intentionally cannot distinguish OUT from insufficient
or ambiguous evidence when it abstains; evaluation discloses that limitation.
The existing unresolved policy evidence is not sufficient to manufacture new
historically verified labels. Latest semantic labels drive current metrics;
initial blind decisions and exposure remain available in revision history.

## Acceptance and preservation

| Check | Fresh result |
|---|---|
| Full offline pytest |**781passed in340.19s**, exit0|
| Focused review/new+legacy packet suite |**59passed in3.22s**, exit0|
| Chrome browser E2E, synthetic workspace |**3passed in7.3s**, exit0|
| Frontend typecheck / build |Both exit0|
| Actual90-article Chrome GET-only smoke |BLIND_UI_READY,0pageerrors,0humanlabels|
| Source preservation |**10,190pinned files unchanged**, about2.56GB|
| Source database |**21tables/88,354rows unchanged**, integrity=ok|
| Groq ledger |All tables unchanged;179results/188attempts; integrity=ok|

[Preservation audit](groq-human-review-ui-20261031/preservation.json),
[real browser readiness](groq-human-review-ui-20261031/browser-readiness.json),
[BLIND UI screenshot](groq-human-review-ui-20261031/blind-ui-ready.png).

Pinned scope: pre-existing Groq experiment/dataset/inference and old packet,
final response exports, source raw cache, source DB/WAL/context, integrity
registry, source/pipeline code and API ledger. Volatile SQLite SHM is excluded.
This is a fresh10,190-file audit, not a claim of rehashing every historical file
elsewhere in the repo. Existing985processed/162eligible source decisions,
qid-5/lead-4/quality-2.1/rank-1, snapshot/quotas and accepted graph were not changed.

Tests cover BLIND leakage, schema/membership distinctions, typed evidence IDs,
source identities/tampering, saved history/reopen, concurrent saves/audits,
stale/foreign/duplicate imports and atomicity, exact CSV note roundtrips,
comparison exposure, no-gold/denominators and historical verification boundaries.
Browser tests use synthetic fixtures only; the real-packet smoke uses GETs only.

Fresh independent review found five important issues (stale imports, stale
semantic-view audits, exposure loss on transfer, late comparison responses and
typing during Save). Reproductions were captured and regression-tested; all five
were corrected before acceptance. Audit drafts were also retained by semantic
revision. No unresolved material review finding remains.

Local logs: `.tools/groq-review-ui/{focused-final,full-acceptance,browser-final}.log`
and matching `.exit-code.txt`; preparation and source preservation baseline are
in the same task-local directory. Published concise test logs accompany this
handoff. No credentials occur in selected source/report outputs.

## Files and GitHub scope

New experiment modules: `review_sampling.py`, `review_packet.py`,
`review_prepare.py`, `review_schema.py`, `review_store.py`,
`review_evaluation.py`, `review_offline.py`, `review_server.py` and
`review_static/{index.html,style.css,app.js}`. CLI: `scripts/groq_review.py`.
Added5pytest files, a synthetic browser fixture,3Playwright tests and a review
Playwright configuration. Added the operations guide/example profile, design,
implementation plan and this handoff. Python package data includes static UI;
README links the workflow; local profiles/temp outputs are ignored.

This checkout already contained uncommitted source/test/docs changes for Groq
and historical source tooling. They are included as explicit prerequisites in
the GitHub source snapshot so imports/tests remain reproducible after checkout;
the task did not edit historical pipeline/source behavior. Unrelated existing
operational report directories stay local. Raw data, source DB/cache, API ledger,
credentials, machine-local review profile and actual review workspace are not
published. The packet remains ready in this local checkout.

**Stop after handoff and push.** Await real human submissions. No auto-labeling,
Groq inference,121pending resume, Pilot/Production or automatic promotion.
