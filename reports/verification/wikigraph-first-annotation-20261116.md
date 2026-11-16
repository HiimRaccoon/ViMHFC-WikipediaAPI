# WikiGraph-first Annotation Collection — 2026-11-16

## Scope and diagnosis

Source run: `80524a0f-87fe-4c01-af9f-dfa4b3951899`, frozen snapshot `2026-10-25T03:00:00Z`.

The previous builder called `annotation_edges.classify_links` without a `GraphRecoveryV2` authority. That classifier deliberately assigned legacy revision-backed resolutions `CANDIDATE_EDGE` with `MISSING_PROOF: historical_ownership`; only a matching, independently certified V2 resolution could become `VERIFIED_EDGE`. Thus zero verified links was expected from that invocation. It did not mean Wikipedia had zero hyperlinks.

The previous graph also used only `retained_lead_prose` links surviving sentence extraction and measured connectivity within the eligible cohort. Literal body links and targets without a saved per-title identity resolution could not contribute. Merely renaming the old 19 edges would not fix those restrictions.

The available October 1 dump contains page/category discovery metadata, not frozen October 25 article text, complete redirect contents or pagelinks. It is not used to certify October 25 structural edges. The implementation uses the existing read-only snapshot revision database, checked raw cache, historical title/move indexes and Integrity Registry. It makes no live request.

## Structural semantics and processing

`VERIFIED_STRUCTURAL_EDGE` means an explicit wikilink exists in checked source wikitext and resolves deterministically to a checked canonical namespace-0 article in the same snapshot. This is hyperlink provenance only: it carries no sentence evidence, entailment, factual correctness or human-gold status. Paths have `paths_are_claims=false`.

The pipeline is now:

1. Read checked source revisions and extract explicit links throughout their wikitext, independently of lead/sentence eligibility.
2. Load other locally available pinned snapshot revisions and redirect bytes; build a title index before filtering eligible articles.
3. Normalize NFC, whitespace, underscores and first-letter case; strip link fragments and optional leading colon. Numeric titles are title keys, never page-ID selectors.
4. Follow redirects to canonical articles with cycle detection. Preserve redirect chains in occurrence records. Check namespace-0 scope; colon titles need the existing exact namespace checkpoint. Exclude self-links after canonicalization. Deduplicate directed edges while retaining occurrence counts.
5. Form the feasible graph induced by the existing quality-eligible candidate articles. Run the existing connectivity-aware selector on that graph. Outside-pool nodes are not silently admitted and do not bridge an induced 2-hop path.

The adapter into existing connectivity math is private and transient. Source `extraction`, sentences, link spans, classification and quality records are returned unchanged. No structural link is written into sentence-level evidence fields. Existing strict/candidate helpers remain available for their legacy policy; the new builder uses the structural path.

Checkpoint binding is `annotation-builder-wikigraph-1`; edge policy `wikigraph-1`; ranking `rank-wikigraph-1`; manifest `annotation-collection-wikigraph-1`. Old V2 checkpoints cannot be resumed under the new semantics. The default target remains 20,000. Quality filters, entity metadata, 900 SUPPORTED / 900 REFUTED / 200 NEI policy, claim/evidence rules and annotation UI are unchanged.

## Files and functions changed

| File | Change |
|---|---|
| `backend/src/vimfc/pipeline/wikigraph.py` | `source_links`, `canonical_title`, `build_graph`, `archived_graph`, `graph_records`, `structural_metrics`, `select_structural`: checked offline canonical graph, reporting and private selection adapter |
| `backend/src/vimfc/pipeline/annotation_builder.py` | New checkpoint binding; retain structural source projection at intake; build snapshot graph before selection; report snapshot, feasible and selected graphs separately |
| `backend/src/vimfc/pipeline/annotation_edges.py` | `select_articles` accepts an explicit structural graph and routes it to structural selection; legacy behavior retained |
| `scripts/wikigraph-pilot.py` | Offline selection-100 verification on the existing archive; authenticated intake reuse, graph/selection replay and source preservation checks |
| `tests/unit/test_wikigraph.py` | Resolver, full-body extraction, deterministic graph, namespace, missing raw, distinct paths, selection and evidence preservation tests |
| `tests/integration/test_annotation_builder.py` | Assertions updated for the new versioned manifest and explicitly non-factual structural metrics; checkpoint repair controls retained |

## Verification

Focused offline command:

```powershell
.tools/python312/python.exe -B -m pytest tests/unit/test_wikigraph.py tests/unit/test_annotation_edges.py tests/integration/test_annotation_builder.py tests/unit/test_connectivity_rank.py tests/unit/test_hyperlink_ranking.py tests/integration/test_relaxed_pilot.py -q -p no:cacheprovider --basetemp=.tools/wikigraph-final-focused-3 --tb=short
```

**86 passed**, exit 0. Log: `.tools/wikigraph-final-focused.log`.

Coverage includes normal links, numeric titles, redirect chains/cycles, missing targets, namespace filtering (including exact namespace proof for a colon title), fragments, self-links through aliases, ambiguous title owners, wrong snapshot, altered graph fingerprint, input-order determinism, body links, edge deduplication, two-hop counting excluding A→B→A, connectivity selection and unchanged evidence/metadata. Existing checkpoint interrupt, writer lock and Windows replace retry tests also pass.

## Pilot connectivity results

The final pilot selects **100 articles** from the existing 1,000-identity archive. Checked article intake was reused from a separate authenticated development checkpoint; the final invocation rebuilt graph and selection with the final implementation. No source acquisition was rerun. Both pilot outputs are isolated from the original V2 output.

| Metric | Previous V2 eligible graph | New feasible graph | New selected graph |
|---|---:|---:|---:|
| Vertices | 368 | 368 | 100 |
| Internal directed nonself edges | 19 candidate | 44 verified structural | 44 verified structural |
| Non-isolated vertices | 22 | 51 | 51 |
| Isolated vertices | 346 | 317 | 49 |
| Weak components including isolates | 350 | 329 | 61 |
| Largest weak component | 12 | 18 | 18 |
| Distinct structural A→B→C paths | 1 | 26 | 26 |

All 19 previous internal connections remain and 25 additional connections are available. The 368 eligible page IDs, per-article quality results, entity metadata and full sentence/lead extraction across all 1,000 records match the original pilot exactly (excluding the old derived edge annotations). Selection of 100 preserves all 44 feasible edges and 26 paths; it does not improve the available eligible supply or establish claim quality.

The checked cached snapshot graph contains **4,552 source pages**, including **854 redirects**, leaving **3,698 canonical vertices**, **37,686 unique directed nonself edges**, **3,414 non-isolated vertices**, **286 weak components**, largest component **3,412**, and **1,226,332 distinct structural two-hop paths**. Those global counts include pages outside the eligible cohort and are not eligible-corpus metrics.

Across canonical source wikitext there are **515,113 literal wikilink occurrences**:

| Occurrence outcome | Count |
|---|---:|
| `VERIFIED_STRUCTURAL_EDGE` | 65,546 |
| `target_not_in_frozen_cache` | 406,960 |
| `namespace_unverified` | 41,858 |
| `self_link` excluded | 749 |

These counts cover full wikitext across the checked cached snapshot, a wider scope than the old lead-only 4,252 occurrences. They must not be interpreted as a before/after occurrence-resolution rate on identical inputs. Namespace exclusions include prefixed targets without exact namespace-0 proof. There were **47 additional pinned pages unavailable** because they lacked a completed acquire checkpoint needed for exact namespace checking; they were excluded rather than guessed. No redirect cycle was encountered in the real cached graph; synthetic tests cover cycles.

The inherited graph-math field `outside_pool_link_occurrences` counts projected unique outgoing edges in this structural adapter; literal occurrence counts and failure reasons are in `structural_graph.occurrences` and `unresolved_reasons`.

Final manifest: `747494b2c4524fdddfc6caeb85e0710820555b1879564f8a67c5e215ba14410f`.
Graph ID: `cfe7fea3fd60dedff413da9b4fa63cda7c9907560b02086adf24dfb4cc911e6e`.

Artifacts:

- `.tools/wikigraph-pilot100-20261116-final/checkpoint.json`: final graph, occurrences, source bindings, articles and selection.
- `.tools/wikigraph-pilot100-20261116-final/verification.json`: final replay/preservation receipt and metrics.
- `.tools/wikigraph-pilot100-20261116-final/comparison.json`: assertions comparing all 1,000 original and new article records.
- `.tools/wikigraph-pilot100-20261116-final.log`: operator stdout/stderr.
- Reproduce the bounded offline verification with `.tools/python312/python.exe -B scripts/wikigraph-pilot.py`.

The final isolated build returned exit 0 with **100 selected**, **zero intake blockers**, `pilot_accepted=false`, identical rebuilt graph/selection/manifest on replay, and **12,305 preserved files with zero changed/missing files**. Preservation covers the source experiment directory, Integrity Registry directory, original V2 output and separate development intake output; credentials and volatile SQLite SHM are excluded by the existing preservation helper. The initial final build plus replay took 149.39s, excluding its initial preservation baseline scan. A subsequent replay with explicit pinned target revision/timestamp checks also passed (exit 0, 85.05s), reproducing the same graph ID, selection and manifest with the same preservation count. Its log is `.tools/wikigraph-pilot100-20261116-final-replay.log`.

## Remaining limits

This is a graph over the locally available checked portion of the frozen snapshot, not a complete viwiki graph. `target_not_in_frozen_cache` means no checked local title owner was available; it does not establish that the page was absent on Wikipedia. `namespace_unverified` records missing namespace proof; `self_link` is an intentional exclusion, not an unknown target. Source failures are reported separately under `unavailable_sources`.

Only explicit stored wikitext links are parsed; templates are not remotely expanded and rendered transclusion-only links are outside coverage. Repeated wikilinks can produce many occurrence records, so production memory/runtime has not been established. No production 20k run or corpus freeze was performed. The full repository suite, unrelated Groq cleanup and human claim/evidence quality were not checked in this change.
