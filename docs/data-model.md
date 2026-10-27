# Data Model: Corpus Builder and Annotation

Refinement within quality-2 adds optional `quality_revision: quality-2.1` to rules/results/signals and an explicit `binding_reason` for the narrowly supported founding-year/shared-object cases. Absence retains the original quality-2 interpretation. pipeline-6.1/run fingerprints distinguish these semantics; source snapshot, qid-5 manifests and schema1 remain unchanged.

Current extraction defaults are `lead-4` / `vi-punct-3` and `quality-2` (`pipeline-6`); classification remains qid-5. No schema migration is needed. Versioned quality JSON adds `quality_version`; signals add sentence-relative `match_start`, `match_end`, `value_spans` (literal text, optional unit), and `date_spans` (literal text plus calendar-checked ISO date). `value_kind` may be `year`, `quantity`, `entity`, or `text`; text is not a resolved entity, and only year signals qualify for current Comparison blocks. Each span slices the stored sentence exactly. Full validation rules remain embedded in frozen metadata. Rules without `quality_version` replay quality-1 exactly; old JSON/annotation/export contracts remain readable and old sentence IDs are never remapped.

**Plan:** [plans.md](plans.md)  
**Status:** Builder classification below reflects implemented qid-5; existing corpus/annotation schema and legacy records remain compatible. No actual-data migration is performed by the offline implementation task.

## Shared Conventions

Discovery experiments add JSON artifacts, not a schema migration: `vimfc-discovery-inventory-1` pins source snapshot/rule hashes, canonical seeds, prior-run eligible-lead target proofs and omissions; `vimfc-discovery-cohort-1` pins policy/variant/input inventory, unique candidate order/origins, actual slot mix and shortfalls. A new run config carries `discovery.version/inventory_id/manifest_id/cohort_hash/namespace_policy`, while legacy configs omit it. Cohort cloning compatibility includes this discovery object. Candidate records add `discovery_version`, `discovery_slot`, `expected_strata`, `discovery_origins`, `discovery_candidate_variants` and explicit verified/unverified namespace status; these are acquisition metadata, never classification evidence. New discovery process results retain `discovery_source_scope`; its hash is part of outer process input identity. Colon-title scope receipts live in existing versioned stage_results (`verify_namespace`); missing/mismatching receipts block cache-only restore, no silent evidence revival.

Sampling quotas (eight unchanged values), actual qid-5 primary group and optional reporting group are separate. Location/Facility may aggregate only for reporting; Science/Technology has null core-report mapping and is explicitly shown separately. No primary/subtype/quality/freeze/annotation schema changes follow from this aggregation.

`vimfc-manual-review-pack-1` / `manual-pair-review-1` stores frame/sample hashes, inclusion probabilities/weights, frozen79 pair IDs, checked Full Lead documents/witnesses and empty human judgments. Unique dependency manifests are referenced by ID and exported alongside the packet; it is an external research artifact, not app-managed review state. Quarantine restore consumes immutable receipt JSON and leaves queue attempts/timestamps unchanged. Source exhaustion code `association_review_required` means manual association review, not no supported primary or proven absence of QID.

- UUID strings identify runs, sessions, submissions and samples. Wikipedia page/revision IDs are integers; Wikidata identities use QID strings and integer revision IDs.
- Timestamps are UTC ISO-8601. Snapshot, revision and operation timestamps are distinct fields.
- Canonical config serialization plus SHA-256 yields config/stage fingerprints; raw source hashes and extraction/content hashes are stored separately.
- Page identity is canonical `(wiki, page_id)`; one frozen corpus has one selected revision per article. A sentence key is `(corpus_version, page_id, revision_id, sent_id)`.
- Evidence text is exactly the stored normalized sentence. Browser-supplied source text is not authoritative.
- `primary_entity_type` is one of the eight agreed groups and is required for eligibility. Subtypes are optional, with canonical identity, display name and positive historical paths; one article may have several or none. Optional search incompleteness is distinct from unresolved primary.

### Versioned classification JSON

Legacy qid-1/2/3/4 retain `outcome`, `primary_entity_type`, `subtypes`, `provenance`, `reason`, `diagnostics` and their archived rules. qid-5 uses `PrimaryFirstResult`, extending these fields without adding fields to the legacy dataclass:

| Field | Contract |
|---|---|
| `primary_status` | classified / unclassified / unresolved |
| `primary_provenance`, `primary_candidates` | Historical P31/P279 paths and proven candidate groups |
| `primary_decision` | closed_primary_graph / precedence_dominates_open_branches / primary_ambiguity / primary_dependency_missing / historical_sitelink_mismatch / no_supported_rule |
| `subtype_status` | supported / undetermined_in_policy / incomplete / no_configured_subtype_found / not_attempted |
| `subtype_search_status` | complete / policy_limited / dependency_missing / not_run; supported subtype does not imply complete search |
| `subtype_provenance` | Positive subtype paths; Person may include P106/P279 |
| `outcome` | completed iff primary classified; unclassified only with closed unsupported primary graph; unresolved otherwise |
| `policy_version`, `manifest_id` | primary-first-1 and immutable logical input identity |
| `items`, `dependency_manifest` | Compact replay evidence and self-contained manifest; original revision/content/entity hashes and projection hashes retained |

`items` uses `evidence_projection=classification-evidence-1` for qid-5: id, historical viwiki sitelink and P31/P279/P106 claims plus revision/model/raw hash and `source_entity_hash`. Full raw objects remain in immutable source storage. Projection hash is pinned in manifest, full raw is validated before projection on materialization, and standalone package validation replays from projection. Zero supported subtypes is valid for a proven primary; do not synthesize an article_subtypes row or Comparison block from primary alone.

## 1. Builder Database: `data/pipeline.sqlite`

| Table | Key fields and constraints | Responsibility |
| --- | --- | --- |
| `pipeline_runs` | PK `pipeline_run_id`; snapshot, config/rule versions and hash, initial target 80000, run status, created/updated UTC | Immutable run configuration and lifecycle |
| `discovery_streams` | PK `(run_id, stream_id)`; origin, full continuation JSON, exhausted flag, stable ordering state | Resume listing without skipping/duplicating pages |
| `candidates` | PK `(run_id, canonical_page_id)` once resolved; discovery origin/order; source identity and unresolved lookup reference | Deduplicated manifest; provisional lookups do not masquerade as canonical pages |
| `candidate_lookups` | PK `(run_id, wiki, normalized_title)`; candidate mapping, resolution status/provenance | Track unresolved/alias discovery before canonical IDs are proven |
| `source_revisions` | Unique `(site, revision_id)`; page/item identity, revision timestamp, content model, source/local hashes, raw object path | Revision-addressed reusable acquisition |
| `snapshot_revision_refs` | PK `(site, identity, snapshot_timestamp_utc)`; verified selected revision or unresolved outcome | Avoid repeating historical revision lookup |
| `identity_resolutions` | Snapshot + lookup title; canonical page/title, resolution-chain JSON, status, source/log evidence | Historical redirect/title ownership proof |
| `stage_results` | PK `(run_id, identity, stage, dependency_hash)`; pending/completed/deferred/failed, result hash/path/JSON, rejection reason, lease, attempts, typed error JSON, next retry | Idempotent stage outcomes and checkpoints |
| `candidate_work` | PK `(run_id, page_id)`; processed/deferred/source_failed/quarantined_integrity, compact diagnostics, retry deadline, worker-entry count, update time | Durable operational queue, separate from classification manifests; quarantine has a case/review ID and no classification/evidence payload |
| `host_circuits` | PK site; state, consecutive_failures, next_retry_at, last observation/code/message, leased probe token/until | Operational host readiness; never corpus evidence or a classification dependency |
| `classification_results` | Article + classification fingerprint; primary group, subtype IDs, item/dependency revision IDs, property paths, outcome | Historical classification basis |
| `lead_results` | Article revision + extraction fingerprint; full prose/paragraphs/sentences, stats, retained link occurrences, outcome | Shared prose and graph extraction boundary |
| `pair_candidates` | Unique `(run_id, pair_kind, endpoint_a, endpoint_b, ranking_version)`; features, score, matching-rule/sentence provenance | Directed bridge pairs and canonical unordered comparison pairs |
| `selection_steps` | PK `(run_id, selection_version, step)`; added ordered IDs, marginal feature values/gain, selected-count state | Replayable greedy trace |
| `run_events` | PK event ID; run/stage, UTC, severity, message and counts | Diagnostic/progress history; not a semantic input |

Raw blobs reside in `data/raw/<site>/<revision_id>/<content_hash>.json.gz` or a documented equivalent. Write blob files atomically and verify hashes before completing stage references. An orphaned durable blob after interruption can be reused; a database record must not claim a nonexistent blob is complete.

### Builder States

Run lifecycle: `collecting -> processing -> selecting -> ready_to_freeze -> frozen`, with `interrupted`/`failed` outcomes recorded where appropriate. An interrupted run resumes under its original fingerprint.

Stage lifecycle: `pending -> completed`, `pending -> deferred` for transient errors, or `pending -> failed` for terminal errors. Due deferred stages return to pending automatically under finite invocation budgets. Legacy failed rows with retry deadlines remain resumable. A completed eligibility rejection stores `eligible=false`, not an API failure. Depth/ambiguity unresolved is a terminal semantic result, not an automatic source retry. The additive queue table/error column preserve schema-1 records and manifest/config identities. Negative hint refresh uses a separate operational stage/24-hour TTL, preserving old null records.

Incompatible configuration creates a new run. Prior stage outputs may be referenced by the new run only if relevant dependency hashes match. Never mutate prior configuration or frozen artifacts.

### Builder Indexes

Index candidates by `(run_id, discovery_order)`, stage results by `(run_id, stage, status, next_retry_at)`, source references by `(site, identity, snapshot)`, classification by `(run_id, primary_group)`, and pair endpoints independently for adjacency queries. Use sparse partner records and bounded generation rather than a dense candidate matrix.

## 2. Frozen Database: `corpus.sqlite`

| Table | Key fields and constraints | Responsibility |
| --- | --- | --- |
| `corpus_meta` | One row; corpus version, schema version, snapshot, source run/config/rule versions, manifest hashes, frozen=true, article count | Package identity and startup validation |
| `articles` | PK page_id; unique dense ordinal 0..19999; snapshot title, revision ID/timestamp, source URL, lead prose, primary group, entity/QID metadata, extraction version and summary stats | All 20,000 selected articles |
| `paragraphs` | PK `(page_id, paragraph_id)`; normalized text and stable order | Complete multi-paragraph leads |
| `sentences` | PK `(page_id, sent_id)`; revision ID, paragraph reference, exact sentence text | Stable evidence lookup; contiguous sent_id sequence per article |
| `subtypes` | PK subtype_id; name and supported classification identity | Canonical subtype definitions |
| `article_subtypes` | PK `(page_id, subtype_id)`; classification provenance reference | Same Entity Type discovery |
| `entity_targets` | PK target_key; wiki/page identity where proven, canonical snapshot title, resolution provenance | Includes in-corpus and outside-corpus lead link targets |
| `lead_entity_links` | Source article, target_key, source sentence/paragraph, original link text/target/fragment, revision/extraction provenance | All retained real prose lead-link occurrences |
| `internal_edges` | PK `(source_page_id, target_page_id)`; both FKs to articles; source link provenance reference | Deduplicated directed graph derived solely from retained lead links |
| `classification_provenance` | Article, primary/subtype statuses/paths, immutable dependency manifest and compact evidence with source/projection hashes; legacy full metadata remains readable | Standalone primary/subtype replay without builder-machine paths; optional search incompleteness retained |
| `quality_report` | Versioned report fields and counts, assessment status/reference | Connectivity, pair/subtype/group coverage and external yield summary |

Full Article Hyperlinks, if retained, are a separate audit table or builder artifact. They are never joined into operational internal edges unless an independently retained lead-link occurrence proves the same link belongs to eligible prose. Comparison pairs remain builder selection metadata, not hyperlink edges.

Store all selected-source lead link targets for Shared Entity matching, including those outside the selected 20,000. `internal_edges` contains only selected endpoints and cannot represent such external targets.

### Frozen Invariants and Indexes

- Exactly 20,000 unique article IDs and one selected revision each, with revision timestamps at/before snapshot.
- Every article has complete source/extraction metadata and a supported primary group.
- Every sentence ID sequence is zero-based/contiguous, belongs to its article revision, and appears in the retained lead.
- Every internal edge has both endpoints in articles and provenance in retained lead prose; no body-only/infobox/table links contribute.
- Index articles by dense ordinal and primary group; sentences by page/sentence; article_subtypes by `(subtype_id, page_id)`; lead links by `(target_key, source_page_id)` and source; internal edges by source and by target.
- `mode=ro` connections; no annotation migrations target this database. First publication validates integrity/foreign keys and final file hashing after clean closure.

## 3. Annotation Database: `annotations.sqlite`

| Table | Key fields and constraints | Responsibility |
| --- | --- | --- |
| `annotation_meta` | One row; schema version and bound corpus_version | Reject accidental switching of existing samples to a different corpus |
| `sessions` | PK session_id; corpus_version and created_at UTC | Independent page-open sessions |
| `samples` | PK id; unique submission_id; FK session; corpus_version, claim, strict label, strict hop_count, reasoning_chain=null, created_at, coverage and source_article_count | Durable benchmark values and retry identity |
| `sample_evidence` | PK `(sample_id, evidence_role, page_id, revision_id, sent_id)`; ordinal, exact title/URL/text, role gold/partial | Source-authoritative evidence snapshot |
| `submission_receipts` | PK submission_id; canonical payload_digest, sample_id | Exact replay vs changed-payload conflict |

Sample insert, evidence insert, coverage derivation result, and receipt creation share one transaction. Label-specific count constraints are enforced by the service and meaningful database checks where applicable. Annotations and receipts cannot overwrite an existing sample to make a retry succeed.

### Annotation Invariants

| Label | Human hop_count | Evidence | Coverage |
| --- | --- | --- | --- |
| SUPPORTED / REFUTED | 2 or 3 | At least two distinct gold sentences; no partial evidence | One distinct source page = single_document; otherwise cross_document |
| NEI | 2 | Exactly one partial sentence; no gold evidence | single_document, describing observed partial evidence only |

There is no minimum article-count constraint, semantic sufficiency checker, automatic hop rule, review status, annotator ID, stored workspace draft, or non-null generated reasoning chain.

Index samples by `(session_id, created_at, id)`; submission IDs are unique. Session counter and export read committed samples only.

The public JSONL schema remains exactly [specs.md](specs.md#benchmark-record-contract). Derived database columns serialize under `metadata`; internal receipt/submission fields do not appear in benchmark exports. Both evidence arrays always exist.

## 4. Artifact Manifest and Transfer

`manifest.json` includes corpus version, snapshot, schema/rule/config versions, selected-count/group summary, source/content/selection digests, frozen status, and final database SHA-256. Do not place the final file hash inside the same file being hashed.

Builder raw sources, candidate/pair/selection logs, and historical resolver provenance support replay on the builder machine. The portable corpus package contains enough stored articles, graph, entity metadata and manifest to serve both annotation and future retrieval without network access.

Separate `corpus.jsonl` and `graph.jsonl` exports carry corpus version and stable identities. All 20,000 articles are exported regardless of annotation use; graph exports contain internal lead-only directed edges. The SQLite corpus package is the agreed transfer path, so this phase does not introduce an unrelated generic database-import feature.
