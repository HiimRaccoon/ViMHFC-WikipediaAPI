# Technical Review Handoff — Wikidata-first + Groq-assisted integration

Publication:01/11/2026. Experimental execution:31/10/2026.

**Implementation ready; live execution PARTIAL (`rate_limited`, HTTP429).**
Offline replay covers all985processed articles. Final observed states are
**431verified,48provisional,30abstained,0model-declared OUT,476unresolved**.
Original verified eligibility remains **162**; there are **16separate
provisional eligible candidates**. Human quality is **UNASSESSED**. No automatic
promotion, source recovery, Pilot10k or Production was performed.

The user's01/11follow-up explicitly authorizes source commit/push, superseding
the earlier no-commit/push instruction. Publication makes no additional Groq
requests and does not renew the expired live envelope.

## Implementation and frozen scope

Policy: `wikidata-first-groq-provisional-1`; model: `qwen/qwen3.8-27b`.
New modules are `integration_policy.py`, `integration_shadow.py`,
`integration_runner.py` and `integration_report.py` under
`backend/src/vimfc/experiments/groq_classification/`.
CLI: `scripts/groq_integration.py`; limits: `configs/groq_integration.json`.
Three integration test files and the
[operations guide](../../docs/groq-integration.md) accompany the implementation.

Original qid-5 verified primary wins before per-article cache/provider work.
Only original unclassified/unresolved articles with trusted retained Historical
Lead can receive suggestions. New results distinguish `wikidata_verified_primary`
from `llm_provisional_primary`; they never change source eligibility or evidence.
Human Review/UI is deferred and its previous artifacts remain untouched.

Source run: `80524a0f-87fe-4c01-af9f-dfa4b3951899`; cohort:
`08bc22892cb821f9b8f52ac6e18f903d5bbb77142b21b549f22bccf214ec676c`.
Snapshot remains `2026-10-25T03:00:00Z`, with unchanged
`qid-5 / lead-4 / quality-2.1 / rank-1`, inventory and quotas.

New dataset: `data/experiments/groq-integration-20261031/dataset`;
manifest **`c5c86653ccab73d97b201977d54f0b5a61aee3da7e57dd10471a9833a39b047c`**.
The existing Archive validates all985original checkpoints/raw/dependency inputs.
Of533completed extractions,2have empty retained leads: **531trusted leads**.
There are248trusted unclassified/unresolved inputs, of which98had legacy results.
The frozen300dataset and its original179responses/121pending remain unchanged.

## Offline and live coverage

| Metric /985processed | Offline cache-only replay | After bounded live attempt |
|---|---:|---:|
| Verified primary |431|431|
| Provisional primary |46|48|
| Abstained |28|30|
| Model-declared OUT |0|0|
| Unresolved |480|476|
| Assigned coverage including provisional |477/985 (48.43%)|479/985 (48.63%)|
| Verified coverage |431/985 (43.76%)|431/985 (43.76%)|
| Verified eligible |162|162|
| Separate provisional eligible candidates |15|16|
| Legacy cached responses reused |74|74|
| New-schema slots pending |174|170|

New responses were2provisional assignments and2abstentions: one AMBIGUOUS and
one INSUFFICIENT_EVIDENCE. The failed fifth chat remains unresolved/pending,
not an invented classification or a valid abstention. There are0invalid terminal
model outputs in the four successful responses.

Legacy reuse is46assignments plus28abstentions. Another24legacy predictions
require new subject/scope validation; Science is not accepted as a generic
fallback. Legacy abstentions retain unspecified membership instead of becoming
OUT. OUT0does not establish a validated ability to recognize out-of-taxonomy.

| Group | Verified | Provisional | Verified Extraction pass | Provisional Extraction pass | Verified Quality pass | Provisional Quality pass | Verified eligible | Provisional candidates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Person |234|0|149|0|60|0|60|0|
| Organization |55|4|33|4|26|3|26|3|
| Location |9|22|3|22|2|4|2|4|
| Event |10|8|4|8|3|4|3|4|
| Creative Work |73|5|57|5|46|2|46|2|
| Award / Competition |32|2|28|2|19|0|19|0|
| Landmark / Facility |15|7|7|7|6|3|6|3|
| Science / Technology |3|0|2|0|0|0|0|0|

Quality columns apply every saved criterion other than the explicit
verified-classification requirement; no threshold was lowered. Provisional
candidates are not added to the162verified eligible articles or accepted graph.
Subject title/first-sentence/quote checks and a conservative Science lexical
scope guard establish textual consistency, not semantic entailment. They may
reject valid aliases and cannot establish classification accuracy. Correct
Additional Coverage remains null without real quality adjudication.

## Requests, usage, cost and checkpointing

Live invocation: `a858549ee4bf4954963c57971711fe8d`.
Six new physical attempts: **1model lookup,4successful chats,1HTTP429chat**.
No original verified-primary article was dispatched to Groq.

- Successful-response usage:4568prompt +633completion = **5201observed tokens**.
- Conservative charged accounting: **7547tokens**, including2346reserved for
  the429attempt whose actual usage is unknown. This is not7547observed tokens.
- The74legacy cache reuses incur0new requests/tokens; their old costs are not
  charged a second time.
- Paid-list-price equivalent for observed usage: **$0.0061864**, using the
  recorded31/10rates from [Groq model docs](https://console.groq.com/docs/models).
  This is not an invoice; actual billed cost is unknown. Estimated Free-plan
  spend0is conditional on the existing explicit Free-tier confirmation.

Envelope:61requests including metadata,100000tokens,900seconds,1attempt/key;
30RPM/1000RPD/8000TPM/200000TPD with lower provider headers taking precedence.
Elapsed execution before stop was11.91seconds. The actual provider429was
checkpointed and stopped the invocation; the one-attempt policy does not
automatically resubmit that failed request. Its membership remains unknown.

The authorization deadline has expired. **No online resume is authorized now.**
Offline receipt recovery remains possible without another POST. Do not delete
the shared meter, reset its clock or create a new manifest to reset allowance.

Original ledger remains byte/table unchanged. A fixed separate shared meter
at `data/experiments/groq-integration-rate-1/ledger.sqlite` starts from a
read-only SQLite backup, retains earlier rate windows, serializes integration
workers, and pins dataset/config/deadline. Main/WAL divergence checks refuse
detected legacy/source writes, including a WAL first created after the freeze.
They are not an atomic lock against unrelated external clients.

Raw receipts precede result publication. Successful HTTP can recover offline
after a publication crash and exhausted budgets. Invalid terminal outputs never
regenerate automatically. Reserved/uncertain attempts and known failed requests
require reconciliation/review. Error headers from model discovery retain lower
provider limits. CLI process success alone does not mean inference completion;
consult the recorded operations status.

## Source integrity and proposed Pilot1000

Original1000-slot cohort remains985processed,13deferred and2reviewed quarantine.
Processed classification remains431completed,332unclassified and222unresolved;
452extractions remain unresolved. Missing historical dependencies, no-supported-
rule and primary ambiguity cannot be repaired by a Groq text label. Known
historical SHA-1/refusal cases and Integrity Registry/approvals were not changed.

Proposed paired Pilot1000: freeze exactly the same ordered identities/snapshot,
with A=archivedqid-5 and B=the provisional wrapper. Use identical source,
extraction, quality, quotas and inventory; retain all15source-blocked slots in
both full-cohort denominators. Report985processed separately and stratify paired
coverage/candidate/cost changes by original outcome/group/reason. Pin a finite
authorization, shared meter, prompt/model/cache identities and an untouched
quality-evaluation subset. Do not interpret provisional gains as verified
eligibility, accuracy or permission for Production. **This design was not run.**

## Verification and preservation

The fresh independent review found and regression-tested recovery after an
exhausted request budget, publication-abort receipts, original WAL divergence,
new-manifest budget reset, absolute authorization deadline and failed model
lookup header persistence. Focused integration tests cover those cases offline,
alongside schema/subject/scope, cache identity and eligibility separation.

Fresh full offline acceptance: **822passed in346.52seconds, exit0**. It is recorded verbatim in
[offline-tests.txt](groq-integration-20261031/offline-tests.txt); the interrupted
31/10full run is not used as acceptance. No live call was made for publication.

[Preservation](groq-integration-20261031/preservation.json) confirms **10,223pinned
files unchanged** (about4.05GB), **21source tables/88,354rows unchanged**,
source/ledger integrity=ok, and original ledger **179results/188attempts**.
Original experiment121pending was not resumed. Snapshot/inventory/registry,
old source/pipeline code, cache and original experiment outputs remain unchanged;
volatile SQLite SHM is excluded from byte comparisons.

Aggregate [live summary](groq-integration-20261031/live-summary.json) and
[offline metrics](groq-integration-20261031/offline/metrics.json) accompany source.
Raw data, credentials, database/cache, local ledgers and unrelated old operational
reports are not published. No human gold, source recovery, Pilot10k or Production.
