# Data Model: Corpus Builder and Annotation

**Plan:** [plans.md](plans.md)  
**Status:** Proposed implementation schema, not applied migrations.

## Shared Conventions

- UUID strings identify runs, sessions, submissions and samples. Wikipedia page/revision IDs are integers; Wikidata identities use QID strings and integer revision IDs.
- Timestamps are UTC ISO-8601. Snapshot, revision and operation timestamps are distinct fields.
- Canonical config serialization plus SHA-256 yields config/stage fingerprints; raw source hashes and extraction/content hashes are stored separately.
- Page identity is canonical `(wiki, page_id)`; one frozen corpus has one selected revision per article. A sentence key is `(corpus_version, page_id, revision_id, sent_id)`.
- Evidence text is exactly the stored normalized sentence. Browser-supplied source text is not authoritative.
- `primary_entity_type` is one of the eight agreed groups. Subtypes have canonical identity and a display name; one article may have several supported subtypes.

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
| `stage_results` | PK `(run_id, identity, stage, dependency_hash)`; pending/completed/failed, result hash/path/JSON, rejection reason, lease, attempts, error, next retry | Idempotent stage outcomes and checkpoints |
| `classification_results` | Article + classification fingerprint; primary group, subtype IDs, item/dependency revision IDs, property paths, outcome | Historical classification basis |
| `lead_results` | Article revision + extraction fingerprint; full prose/paragraphs/sentences, stats, retained link occurrences, outcome | Shared prose and graph extraction boundary |
| `pair_candidates` | Unique `(run_id, pair_kind, endpoint_a, endpoint_b, ranking_version)`; features, score, matching-rule/sentence provenance | Directed bridge pairs and canonical unordered comparison pairs |
| `selection_steps` | PK `(run_id, selection_version, step)`; added ordered IDs, marginal feature values/gain, selected-count state | Replayable greedy trace |
| `run_events` | PK event ID; run/stage, UTC, severity, message and counts | Diagnostic/progress history; not a semantic input |

Raw blobs reside in `data/raw/<site>/<revision_id>/<content_hash>.json.gz` or a documented equivalent. Write blob files atomically and verify hashes before completing stage references. An orphaned durable blob after interruption can be reused; a database record must not claim a nonexistent blob is complete.

### Builder States

Run lifecycle: `collecting -> processing -> selecting -> ready_to_freeze -> frozen`, with `interrupted`/`failed` outcomes recorded where appropriate. An interrupted run resumes under its original fingerprint.

Stage lifecycle: `pending -> completed` or `pending -> failed`; retryable failures return to pending through a recorded retry decision. A completed eligibility rejection stores `eligible=false` and reason rather than becoming an API failure. Missing supported history/classification/extraction can remain pending/unclassified with a specific cause.

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
| `classification_provenance` | Article, historical QID/revisions/property paths and embedded relevant archived metadata; optional raw-cache hashes for builder replay | Explain entity type/subtype assignment without depending on paths on the builder's machine |
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
