# US1 — Builder fixture verification

Date: 2026-10-25. Scope: T015–T038 implementation and controlled fixtures only.

The backend now connects snapshot acquisition, historical identity/Wikidata classification, local AST extraction, quality filtering, pair ranking, deterministic selection, reporting, freeze and read-only verification through the documented CLI. This is software capability, not delivery of the real research corpus.

Verified behaviors:

- One-page historical enumeration at the fixed cutoff, known-revision batches (20 IDs with adaptive 413 splitting), timestamp/identity/content hashes, HTTP-200 maxlag, Retry-After and continuation through empty pages.
- Redirect cycle/depth/ambiguity handling and title ownership rewound using move evidence with the same recorded server horizon as the current-title lookup.
- Complete retained multi-paragraph lead, NFC/stable sentence IDs, abbreviations/initials/decimals/quotes, nested argument-only formatters and explicit unresolved factual templates. Body, table, infobox and reference links cannot become operational links.
- Historical sitelink/dependency paths, P31/P279/P106, overlapping groups, cycles, truncation, missing history and cache reuse. Unsupported classification remains pending/unclassified.
- Versioned quality thresholds/signals; years are bound to factual clauses. Pair candidates retain feature/rule/sentence provenance, directed bridge identities and capped compatible comparison partners; zero/negative scores remain represented.
- Deterministic pair/selection replay, ordered tie breaking, partner bundles, soft deficits, subtype novelty, hub penalties and insufficient supply without relaxing quality.
- Synthetic 19,999/20,000/20,001 publication boundaries. Only exactly 20,000 eligible unique records can publish. Frozen classification/quality rules replay; metadata, source revision, sentence and target inconsistencies are rejected.
- A standalone controlled 20,000-record SQLite fixture can be copied, verified and opened read-only without WAL/SHM sidecars. External lead targets remain stored, while internal edges have selected endpoints and exact retained occurrence provenance. Updating the outer checksum cannot hide inconsistent operational tables.

Evidence: full default offline pytest suite and focused selection/acquisition coverage; final aggregate command/result is recorded in `pilot-readiness.md`. Default tests block real socket connections. Fixtures and provenance/hashes are documented in `tests/fixtures/manifest.json`. Review/fix evidence is in `review.md`.

No real Wikipedia/Wikidata pilot, production request stream or research freeze ran. Heuristic pair scores are not measured human Valid 2-hop Yield. Annotation API/UI and independent artifact exports belonging to later tasks are not claimed complete.
