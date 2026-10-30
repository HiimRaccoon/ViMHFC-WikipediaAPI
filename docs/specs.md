# Feature Specification: Fixed Wikipedia Corpus and Vietnamese Fact-Checking Annotation

**Feature name**: `vietnamese-fact-checking-dataset`  
**Created**: 2026-10-14  
**Status**: Ready for implementation planning  
**Source of agreed requirements**: [docs.md](docs.md)  
**Feature directory**: `docs/`

This specification defines observable behavior and acceptance criteria for constructing a fixed evidence corpus and authoring a Vietnamese multi-hop fact-checking benchmark. It does not claim that the corpus, benchmark, or application already exists. Implementation design belongs in the subsequent plan.

## User Scenarios & Testing

### User Story 1 - Build and Freeze a Reproducible Evidence Corpus (Priority: P1)

As the corpus builder, I want to select 20,000 feasible Vietnamese Wikipedia articles from one fixed snapshot so that annotation and future Agentic RAG evaluation use the same reproducible evidence source.

**Why this priority**: All annotation and later retrieval depend on the corpus. Selecting articles independently of their useful partners would undermine the research goal.

**Independent Test**: Run corpus construction on snapshot-consistent source data, inspect classification and pair-selection records, and verify the final corpus count, extraction boundaries, provenance, and graph. Controlled fixtures can validate behavior; delivery of the research corpus requires real source data.

**Acceptance Scenarios**:

1. **Given** a run with a fixed UTC snapshot, **When** source revisions are selected, **Then** every Wikipedia and Wikidata revision used has a timestamp not later than the run's snapshot, and its provenance and raw data are retained.
2. **Given** an article with multiple introductory paragraphs, an infobox, and body headings, **When** its lead is extracted, **Then** all introductory prose before the first content heading is retained, non-prose elements and body content are excluded, and stored sentence IDs begin at zero without gaps.
3. **Given** eligible candidates and supported classifications, **When** candidates are ranked and selected, **Then** both article quality and bridge/comparison pair potential contribute to recorded decisions, and useful partners, diversity, soft quotas, and popularity bias are considered.
4. **Given** required primary historical Wikidata evidence is unavailable or a primary conflict remains undecidable, **When** classification is attempted, **Then** primary remains explicitly unresolved without live-data substitution. Missing optional subtype evidence alone does not reject a proven primary.
5. **Given** fewer than 20,000 eligible unique articles or invalid metadata/graph data, **When** freeze is requested, **Then** freeze fails with an actionable report without lowering quality or altering quotas silently.
6. **Given** exactly 20,000 eligible articles and passing integrity checks, **When** freeze succeeds, **Then** the full corpus and graph receive one corpus version and cannot be changed by annotation or later pipeline runs.
7. **Given** identical stored inputs and rule versions, **When** ranking and selection are replayed, **Then** scores, selected article identities, and decision ordering are reproducible.
8. **Given** the initial 80,000 Vietnamese Wikipedia candidates yield fewer than 20,000 eligible articles, **When** collection continues, **Then** additional candidates may be collected under the same run snapshot without lowering eligibility standards.
9. **Given** a historical revision contains lead-prose links, body-only links, and redirect targets, **When** the graph is constructed, **Then** only retained Full Lead prose links form normalized directed edges; the frozen internal graph contains only edges whose source and target belong to the selected corpus, while any recorded full-article links remain separate.

### User Story 2 - Resume Corpus Construction Without Losing Work (Priority: P1)

As the corpus builder, I want to stop and resume a large collection run without downloading valid data again, duplicating records, or mixing snapshots and rules.

**Why this priority**: The expected candidate pool is large and historical-source retrieval may be interrupted or expensive.

**Independent Test**: Interrupt processing, resume under the same configuration, retry failures, and compare records and source requests with an uninterrupted run.

**Acceptance Scenarios**:

1. **Given** a compatible checkpoint after completed batches, **When** the run resumes, **Then** completed valid work is reused, unfinished work continues, and no duplicate candidates or articles are created.
2. **Given** transient source failures, **When** retries occur, **Then** affected work remains distinguishable from completed work, retains the original snapshot, and exposes its processing status.
3. **Given** changed extraction, classification, filtering, ranking, or snapshot configuration, **When** resume is attempted against an incompatible checkpoint, **Then** the run stops with a clear explanation and instructions to create a new run.
4. **Given** a new run using compatible stored raw data, **When** changed rules are applied, **Then** only affected stages and downstream dependencies need reprocessing; the original run remains intact.
5. **Given** active processing, **When** progress is inspected, **Then** processed counts, speed, progress, and an ETA estimate are visible; an unavailable estimate is not presented as a reliable completion time.
6. **Given** a source host enters maxlag/Retry-After cooldown, **When** another candidate needs that host, **Then** it is deferred with a durable deadline rather than reported as a terminal source failure. Safe cached work may continue; no request bypasses the deadline.
7. **Given** deferred work becomes due, **When** the same bounded pilot resumes, **Then** it retries automatically within finite round/wait limits while preserving completed stages, immutable manifests and candidate uniqueness. Missing item hints may refresh after a controlled negative-cache TTL only before an association/manifest is pinned.
8. **Given** an invocation has visited its cohort, **When** it reports, **Then** attempted, processed, deferred and terminal source-failed counts are distinct. Invocation completion/exit 0 do not mean acceptance. Incomplete source coverage blocks the next cohort; recovery must not mutate published logical input.

### User Story 3 - Start and Share the Local Application (Priority: P1)

As an annotator, I want to start the application with one Windows command and use a supplied frozen corpus without rebuilding it.

**Why this priority**: Both annotators must be able to operate independently with identical evidence sources.

**Independent Test**: Start with valid and invalid corpus packages, restart with existing samples, and transfer the same frozen corpus to a second local installation.

**Acceptance Scenarios**:

1. **Given** a valid frozen corpus and installed prerequisites, **When** the user runs `start.ps1`, **Then** the local application starts and displays its access URL without running corpus construction.
2. **Given** missing prerequisites, an unfrozen corpus, an incorrect article count, or failed corpus validation, **When** startup is attempted, **Then** annotation does not start and the user receives clear corrective guidance.
3. **Given** an existing corpus and submitted samples, **When** the application restarts, **Then** stored corpus data, historical sessions, and submitted samples are preserved.
4. **Given** the source code and the same frozen corpus are copied to the second machine, **When** its user starts annotation, **Then** both machines expose identical corpus versions and sentence content while keeping sessions and samples independent.
5. **Given** the corpus is frozen and the local application is available, **When** internet access is disconnected, **Then** Random, discovery, annotation, Submit, and benchmark export continue using local data.

### User Story 4 - Discover Articles and Manage a Workspace (Priority: P1)

As an annotator, I want to explore related articles in a bounded workspace so that I can compose a claim from genuine frozen-source information.

**Why this priority**: Flexible evidence discovery is central to efficient human annotation.

**Independent Test**: Random an article, use each discovery mode from different workspace articles, add/remove candidates, and verify the corpus remains unchanged.

**Acceptance Scenarios**:

1. **Given** a newly opened page, **When** the annotator chooses Random, **Then** Article A is selected from the frozen corpus and shows its title and up to six initial sentences with stable IDs and checkboxes.
2. **Given** an article with more than six sentences, **When** Show More is used and later Show Less is used, **Then** remaining sentences can be inspected and selected without renumbering or losing existing selections.
3. **Given** any open article, **When** Hyperlink discovery is used, **Then** candidates correspond to real outgoing Full Lead Entity Links in the frozen Corpus-internal Graph and belong to the frozen corpus; body-only hyperlinks do not produce candidates.
4. **Given** a selected shared entity, **When** Shared Entity discovery is used, **Then** candidate Full Lead Sections link to the same identified entity; that entity may be outside the corpus, but candidates cannot be.
5. **Given** a supported subtype, **When** Same Entity Type discovery is used, **Then** candidates share that subtype, with no silent fallback to a broader primary group.
6. **Given** available candidates, **When** a candidate is selected manually or randomly, **Then** the article is added only if it is not already open and the workspace has fewer than five articles.
7. **Given** a removable article with selected evidence, **When** removal is requested, **Then** confirmation is required; cancellation preserves the draft, and acceptance removes only the article and its draft evidence.
8. **Given** unsaved workspace/annotation work, **When** Re-random is requested, **Then** confirmation precedes replacement of the workspace with a newly selected Article A; submitted samples remain intact.
9. **Given** no candidates or an operational failure, **When** discovery completes, **Then** no-results and error states are distinguishable and neither state fabricates articles or relationships.

### User Story 5 - Submit Human-authored Claims and Evidence (Priority: P1)

As an annotator, I want to enter a claim, label, and hop count and select evidence sentences, while retaining full authority over the annotation.

**Why this priority**: Human-created annotations are the benchmark's primary output.

**Independent Test**: Submit examples for all labels, including single-document evidence, and inspect persistence, validation, derived coverage, retry behavior, and form reset.

**Acceptance Scenarios**:

1. **Given** a nonempty claim, human-selected SUPPORTED/REFUTED, hop count 2 or 3, and at least two distinct checked sentences, **When** Submit succeeds, **Then** one independent sample is stored with those exact label/hop values and sentences in `gold_evidence`, while `partial_evidence` is empty.
2. **Given** the required two or more sentences belong to one article, **When** the sample is structurally valid, **Then** it is not rejected for lacking a second source article, and its coverage is `single_document`.
3. **Given** human-selected NEI, hop count 2, and exactly one checked sentence, **When** Submit succeeds, **Then** that sentence is stored in `partial_evidence` and `gold_evidence` is empty.
4. **Given** invalid fields, unsupported hop values, an invalid evidence reference, or a label-specific evidence-count violation, **When** Submit is attempted, **Then** errors are reported and the draft is preserved without changing the human's label or hop count.
5. **Given** a stored sample, **When** its form is reset after successful confirmation, **Then** claim, label, hop count, and checked sentences clear while articles, metadata, display state, and discovery context remain.
6. **Given** a storage failure, **When** Submit cannot persist the sample, **Then** the full draft remains available and the sample counter does not increase.
7. **Given** a persisted submission whose acknowledgement was lost, **When** the same submission is retried or double-clicked, **Then** it produces only one sample and returns the existing result.
8. **Given** evidence from two or more distinct article page IDs, **When** the sample is stored, **Then** coverage is `cross_document`; coverage derivation does not assign or alter label/hop count or assert reasoning validity.

### User Story 6 - Start New Sessions Without Restoring Drafts (Priority: P1)

As an annotator, I want each page opening to start a fresh workspace while retaining all submitted data.

**Independent Test**: Submit a sample, create an unsubmitted draft, refresh or close/reopen the page, and inspect current and historical sessions.

**Acceptance Scenarios**:

1. **Given** an active session with submitted samples and a draft, **When** the page refreshes or reopens, **Then** a new UUID session is created, the workspace and draft are empty, and previous submitted samples retain their original session IDs.
2. **Given** separate local machines, **When** each opens the application, **Then** sessions are independently identified without requiring synchronization or an annotator identifier.
3. **Given** samples in older sessions, **When** the new page shows its counter, **Then** only successful submissions in the current session are counted.

### User Story 7 - Export and Reuse Dataset Artifacts (Priority: P1)

As an annotator or researcher, I want consistent session exports and separately reusable corpus/graph artifacts without losing data or interrupting annotation.

**Independent Test**: Export a current session, export an older session through the command interface, parse the files, and compare their contents with stored samples and corpus provenance.

**Acceptance Scenarios**:

1. **Given** a current session with successful submissions, **When** Export Dataset is used, **Then** one UTF-8 JSONL record is written per sample from that session, with both evidence arrays and derived coverage metadata preserved.
2. **Given** open unselected articles and an unsubmitted draft, **When** export runs, **Then** neither drafts nor unselected articles appear in the output.
3. **Given** an empty current session, **When** the user views export, **Then** the button is disabled with an explanatory message rather than downloading an empty dataset.
4. **Given** a populated workspace, **When** export completes, **Then** data and workspace state remain unchanged and further annotation is possible.
5. **Given** a historical session ID, **When** the user lists sessions and exports that ID through a PowerShell-accessible command, **Then** the saved samples from that session remain retrievable after page refresh or application restart.
6. **Given** a frozen corpus, **When** corpus and graph artifacts are exported independently, **Then** all 20,000 articles, including never-annotated articles, remain represented and share the corpus version used by benchmark samples.

### Edge Cases

- A candidate has multiple possible primary groups: apply recorded classification rules; unresolved or unsupported classification cannot silently pass as a known type.
- A required historical revision is unavailable: report pending/failed work and do not replace it with current content.
- A lead has fewer than six sentences: show all available sentences without artificial padding.
- A long lead is collapsed after selecting later sentences: selected evidence remains in the draft.
- Distinct articles each have `sent_id = 0`: sentence identity includes the source page/revision, so they are not treated as duplicates.
- A redirect or alias resolves to an already-open article: do not create a second workspace entry for that article.
- A shared entity is outside the corpus: use its stored identity for matching; do not fetch or add its article during annotation.
- A hyperlink appears only in the body or an excluded lead component: it may be retained as source/audit data but cannot enter the lead-based graph or establish summary factual evidence.
- The initial 80,000 candidates are insufficient: expand collection under the same snapshot without relaxing standards or freezing an undersized corpus.
- An article lacks a supported subtype or has no matching candidates: explain the absence; do not infer a subtype or broaden discovery silently.
- Five articles are open: deny a sixth addition without losing the existing workspace.
- Removal or Re-random confirmation is cancelled: preserve all current state.
- Label changes before Submit: evidence remains a draft and is classified into the appropriate array only using the final human-selected label; invalid counts require correction rather than silent trimming.
- Evidence comes from a different corpus version or no longer matches its stored source sentence: reject the invalid submission rather than repairing source content silently.
- A page closes before Submit confirmation: the draft is not restored, but any sample successfully persisted remains available through historical-session export.
- A repeated submission ID carries different content: report a conflict rather than overwrite the stored sample or silently accept changed data as a retry.
- An export fails: saved samples and workspace remain intact, and a retry is possible.
- A valid corpus misses individual soft quotas: report deviations; do not treat exact per-group quotas as equivalent to the hard total of 20,000.

## Requirements

### Scope and Research Boundaries

- **FR-001**: The system MUST support construction of one versioned, immutable evidence corpus and a local human annotation application using that corpus.
- **FR-002**: The corpus MUST contain exactly 20,000 unique eligible Vietnamese Wikipedia articles. The benchmark collection targets 2,000 human-authored claims: 900 SUPPORTED, 900 REFUTED, and 200 NEI, with approximately 95% two-hop and 5% three-hop claims.
- **FR-003**: Benchmark counts and the approximate hop distribution MUST be treated as research collection targets, not automatic label assignment, hop assignment, or submission quotas enforced across independent machines.
- **FR-004**: The full frozen corpus MUST define claim verification scope. Checked sentences and the currently open workspace MUST NOT redefine that scope.
- **FR-005**: The corpus-building and annotation stages MUST operate without an LLM or Cross-Encoder dependency. Future Agentic RAG implementation is outside this feature's scope.

### Snapshot, Extraction, and Classification

- **FR-006**: Each pipeline run MUST retain a unique run identity, a fixed UTC snapshot timestamp, configuration version, and versions of all rules used.
- **FR-007**: Source selection MUST use the latest available Wikipedia/Wikidata revisions not later than the run's snapshot. This includes Wikidata entities referenced during classification hierarchy traversal.
- **FR-008**: The system MUST preserve raw source data and provenance, including Wikipedia page/revision identities and timestamps, Wikidata identities/revisions/timestamps where used, and the basis for classification.
- Source-integrity rejection MUST retain the reported revision hash, available main-slot hash, computed exact UTF-8 hash, content model/serialization format and pinned revision identity in diagnostics. Reformatting equivalent JSON, substituting a slot hash or computing a new local checksum MUST NOT turn a failed revision-hash check into verified source input. A source mismatch is distinct from a missing dependency or transient API failure; unverified responses cannot be published as accepted raw artifacts or manifests.
- An explicitly reviewed integrity quarantine MAY refuse only its pinned revision/snapshot under a run/config-scoped, evidence-hashed operational approval. Affected candidates MUST supply no trusted quarantined-source content, classification, eligibility or graph/pair evidence, including on cache/checkpoint replay. Preserve their prior artifacts without reinterpreting them as current accepted evidence. Keep candidates in the cohort denominator; report quarantine separately from processed/deferred/source failures and keep integrity/data completeness and pilot acceptance false while it applies. Continue independent work only; every unreviewed integrity failure retains the hard stop. Quarantine never substitutes content, revisions or candidates.
- **FR-009**: Missing historical metadata MUST be reported by dependency role. Missing required primary evidence produces explicit unresolved primary classification; missing optional subtype evidence produces incomplete subtype search and MUST NOT alone reject a proven primary. Missing physical artifacts declared by a frozen manifest are input failures, never permission to replay a smaller graph. Unsupported type inference and live-data substitution are prohibited.
- **FR-010**: Full Lead Section extraction MUST retain all introductory prose before the first content heading, excluding infoboxes, tables, navigation, footnotes, and other non-prose elements. It MUST exclude body sections and impose no hard sentence-count limit.
- Verified infobox/hatnote/maintenance aliases MUST be identified as non-prose without leaking their text or links into evidence. An unknown content-producing template or ambiguous arguments MUST remain unresolved; no live expansion or silent factual omission is permitted.
- **FR-011**: The system MUST normalize Unicode, create a stable sentence sequence with zero-based contiguous IDs, and retain extraction version, sentence count, paragraph count, and lead length statistics.
- Explicit retained wikilink labels MUST retain their exact text/spans. Internal title punctuation MUST NOT fragment the label across sentences; punctuation serving as a sentence terminator at the end of a label must remain usable. Changed segmentation/extraction semantics require a new version/run, preserving prior sentence identities.
- **FR-012**: Each selected article MUST have exactly one positively proven historical primary entity group. Subtypes are OPTIONAL and MUST have explicit historical provenance when recorded. Primary-First, Optional-Subtype classification uses P31/P279 for primary; an item directly equal to a configured root retains its explicit historical identity mapping with a recorded zero-property path. P106 alone MUST NOT establish Person. Optional Person subtypes may additionally use P106/P279. Collect proven primary candidates under the versioned bounded policy and apply recorded precedence; do not select the first/nearest root. A required open primary branch that could change the winning group MUST keep primary unresolved/ambiguous. Accepting primary with open branches requires a recorded proof that they cannot outrank it. Missing/incomplete subtype closure alone MUST NOT reject primary. Record subtype search status distinctly from subtype existence; not found within policy does not assert no subtype exists. Eligibility additionally requires unchanged extraction, factual quality and article-kind rules.
- **FR-013**: The pipeline MUST exclude redirect-only, disambiguation, list-only, and unhelpful leads and assess useful factual content and partner potential rather than selecting by entity type alone.
- Factual-signal rules MUST be versioned and reproducible from exact retained sentence spans. Signals represent bounded potential, not human verification. New positive attributes require a local literal date/quantity/named value or explicit linked entity; bare predicates, negated/hypothetical assertions, identifier suffixes and unrelated quoted/title dates are not positive attribute evidence. Distinct start/end events and quantities MUST NOT masquerade as one compatible year attribute. Calendar-invalid complete dates cannot supply a valid date signal. No quality threshold, subtype requirement for Comparison, annotation policy or corpus/claim target is relaxed.
- Refinements within a quality-rule family MUST have an explicit revision in rules/results and fingerprints; absence retains the prior interpretation. Parenthetical years or shared coordinated objects may be used only under a documented bounded binding with exact source spans and positive/negative regression evidence. Explicitly reviewed source pilots MUST stop before further source work on serious integrity/checkpoint failure, including optional dependencies or target resolution; a partial receipt MUST NOT count as a completed pilot.
- **FR-014**: The pipeline MUST use the soft entity-group targets below and report deviations or insufficient eligible supply. It MUST NOT lower quality or change allocations silently.

| Primary entity type | Soft target |
| --- | ---: |
| Person | 5,000 |
| Organization | 3,000 |
| Location | 3,000 |
| Event | 2,500 |
| Creative Work | 2,500 |
| Award / Competition | 1,500 |
| Landmark / Facility | 1,500 |
| Science / Technology | 1,000 |
| **Total** | **20,000** |

### Multi-hop Potential and Corpus Selection

- **FR-015**: The pipeline MUST assess bridge and comparison candidate pairs after article-quality filtering, in addition to independent article scores.
- **FR-016**: Bridge potential MUST favor entity relationships expressed in summary A and complementary factual information in summary B, without requiring subtype when both endpoints are eligible. Comparison candidates MUST share a positively supported subtype with historical provenance and compatible factual attributes; unknown subtype MUST NOT fall back to primary group. Existing ranking, caps and Lead-only Graph semantics remain in force.
- **FR-017**: Reranking MUST reward complementary information and penalize redundancy and shortcut-risk signals. Scores MUST be presented as multi-hop potential estimates, not semantic proof of valid claims.
- **FR-018**: Final selection MUST use deterministic, rule-based, graph-aware greedy selection that considers partner retention, internal connectivity, diversity, soft quotas, and bias toward popular/high-link-count entities.
- **FR-019**: Ranking scores, contributing signals, candidate pairs, rule versions, selection logs, and reproducible decision ordering MUST be retained. Same-input replay MUST reproduce the selected article identities and ranking/selection results.
- **FR-020**: Hyperlink collection SHOULD prefer the MediaWiki API and MUST use the selected historical Wikipedia revision. Full Article Hyperlinks, Full Lead Entity Links, the Corpus-internal Graph, and comparison associations MUST remain distinguishable. Only genuine links in retained Full Lead prose MAY form operational graph edges for connectivity, selection, Hyperlink discovery, and later graph expansion; body-only links and excluded components MUST NOT contribute such edges. Page identities and redirects MUST be normalized consistently with the snapshot, with resolution provenance retained, and edges MUST preserve direction A -> B. Once the 20,000-article selection is fixed, its internal lead-based edges MUST be filtered to in-corpus endpoints and stored in `corpus.sqlite` as part of the immutable frozen artifact. Full-article links, if retained, are source/audit data; comparison associations MUST NOT be recorded as Wikipedia hyperlinks. A hyperlink by itself MUST NOT be treated as factual evidence; Gold Evidence MUST come only from stored Full Lead sentences.
- **FR-021**: Corpus evaluation MUST report internal connectivity, bridge/comparison candidate counts, subtype coverage, and group counts. Valid 2-hop Yield MUST be supported as a human-reviewed evaluation outcome, without introducing an annotation-app review workflow.

### Processing Lifecycle and Freeze

- **FR-022**: Collection MUST target an initial pool of **80,000 Vietnamese Wikipedia candidates**. This is an initial target, not a hard cap or guaranteed feasible-article yield. If fewer than 20,000 articles meet the agreed standards, the pipeline MUST support additional collection under the same snapshot without lowering quality to satisfy the final count.
- **FR-023**: Processing MUST use bounded work, batching, and reusable stored results. Progress MUST distinguish visited/attempted candidates, terminally processed candidates, deferred work and terminal source failures, alongside speed and qualified ETA estimates. Host cooldown MUST defer network-dependent work while permitting safe cache-only work. Concurrency remains one request per host; `maxlag=5` and Retry-After MUST be honored.
- **FR-024**: Checkpoints MUST retain collected candidates, source metadata/content, leads, links, classifications, filtering/ranking results, and pending/completed/deferred/failed states. Versioned immutable classification dependency manifests MUST identify primary and optional subtype evidence, revisions/hashes, policy, completeness/frontiers and roles. Stage fingerprints MUST bind the actual manifest/rules; same materialized logical input MUST replay deterministically independently of physical cache warmth or processing order. Changed incompatible input requires a new run; historical classification semantics/records remain intact. Compact evidence is permitted only with source provenance and validated projection hashes; no validation may be bypassed. Scheduling metadata is operational and separate from logical classification input.
- **FR-025**: Compatible resume and retries MUST preserve snapshot/rule consistency, reuse valid work, and avoid duplicate records. Transient source errors MUST be deferred with source/reason and retry deadline, and retried automatically when due under finite invocation budgets. Negative item hints MUST NOT be permanent evidence; controlled refresh MUST preserve pinned associations/manifests. Reports MUST distinguish invocation completion, data completeness and human pilot acceptance; no scheduler or successful exit may grant acceptance or start another cohort. Historical attempts and pre-resume reports MUST remain available.

  Deferred-only resume MUST execute only due deferred workers, preserving terminal checkpoints and failures. Cached terminal evidence MUST pass offline identity/hash/manifest validation without source recovery before reuse. Rejected source hashes MUST remain rejected, with diagnostics distinguishing missing/mismatched response hashes from physical cache corruption. Performance attribution MUST follow the actual stage scope; resource measurement MUST include primary report export and disclose any excluded final telemetry flush.

  Wikidata deferred resume MUST check host readiness before entering workers. Health preflight MUST make no HTTP attempt during a recorded cooldown or another probe's lease, and at most one attempt per invocation. A successful probe is advisory, never proof of sustained health. A host circuit MUST pause known dependent workers after maxlag, persist its retry time/reason, and support guarded recovery after cooldown without requiring a different CLI mode. Host pauses MUST preserve candidate/stage checkpoints and prior data reports; operational health observations MUST NOT become classification evidence.
- **FR-026**: Incompatible resume MUST stop with clear guidance. New configuration/snapshot processing MUST use a new run identity and preserve prior runs, with compatible raw-source reuse and reprocessing of affected stages and dependencies only.
- **FR-027**: Freeze MUST succeed only with exactly 20,000 unique eligible articles and passing checks for metadata, revision consistency, extraction/sentence integrity, quality rules, and graph integrity.
- **FR-028**: Successful freeze MUST assign a corpus version and preserve the entire selected corpus and graph. Subsequent pipeline or annotation actions MUST NOT mutate or overwrite that corpus.

### Local Workspace and Discovery

- **FR-029**: Annotation MUST use only the frozen local corpus and stored discovery information. Random, Re-random, discovery, and Add Article MUST NOT access live Wikipedia/Wikidata or add outside-corpus articles.
- **FR-030**: A new workspace MUST start empty; Random MUST select Article A from the corpus. The workspace MUST contain no more than five distinct articles.
- **FR-031**: Articles MUST show titles and numbered sentence checkboxes, initially displaying up to six sentences. Show More/Show Less MUST preserve sentence identities and checkbox selections.
- **FR-032**: Hyperlink discovery MUST return only corpus articles reached by genuine directed outgoing Full Lead Entity Links from the chosen workspace article in the stored Corpus-internal Graph. Links appearing only in the body or excluded components MUST NOT produce candidates.
- **FR-033**: Shared Entity discovery MUST match identified entities linked from Full Lead Sections. A shared entity may be outside the corpus; candidate source articles may not. Matching MUST NOT rely solely on similar names or body-only mentions.
- **FR-034**: Same Entity Type discovery MUST match the selected supported subtype, allow subtype selection when several apply, and avoid silent fallback to broader primary groups.
- **FR-035**: Discovery MUST work from any open article, provide candidate selection and Random Candidate, prevent duplicate additions, and distinguish empty results from failures without fabricating relationships.
- **FR-036**: Article A MUST have no Remove action. Other articles without checked evidence MUST be removable immediately; articles with checked evidence MUST require confirmation that removes only workspace state and associated draft evidence.
- **FR-037**: Re-random MUST confirm discarding unsubmitted work before resetting the workspace/draft around a new Article A. Neither Re-random nor Remove MUST affect submitted samples or source corpus data.

### Human Authority and Structural Validation

- **FR-038**: The annotation form MUST expose only claim, label, and hop count as human-entered fields. Source/evidence metadata MUST be populated from the corpus rather than manually typed.
- **FR-039**: The system MUST preserve human authority over label and hop count and MUST NOT infer, assign, correct, or overwrite either field.
- **FR-040**: Structural submission validation MUST require a nonempty claim, one allowed human-selected label, one allowed human-selected hop count, valid distinct selected sentence identities, and label-specific evidence counts. Errors MUST preserve the draft and explain the required correction.
- **FR-041**: SUPPORTED/REFUTED MUST accept hop count 2 or 3 with at least two distinct evidence sentences, including when all evidence comes from one article. Three-hop submissions MUST NOT automatically require three sentences or articles.
- **FR-042**: NEI MUST require human-selected hop count 2 and exactly one selected sentence. Its prescribed research structure is a verified first factual relationship and an insufficiently evidenced necessary second relationship.
- **FR-043**: Selected SUPPORTED/REFUTED sentences MUST populate `gold_evidence` with empty `partial_evidence`. Selected NEI evidence MUST populate `partial_evidence` with empty `gold_evidence`.
- **FR-044**: Evidence selections MUST use checkboxes with a light blue selected state and support check/uncheck actions. Text highlighting, offsets, unselected articles, and distractor contexts MUST NOT be required or exported.
- **FR-045**: Human reasoning assessment, NEI corpus search, and cross-review MUST remain external. The application MUST NOT introduce review workflows, review logs/status fields, or automatic semantic/multi-hop adjudication.

### Hop Count and Multi-hop Necessity Guideline

The user-facing annotation guidance MUST apply these definitions. They govern human judgement, not additional automatic semantic submission validation.

| Rule | Required interpretation |
| --- | --- |
| One hop | One verification step for an independent factual relationship or attribute genuinely necessary to determine the verdict |
| 2-hop Bridge | Verify X -> Y and Y -> Z |
| 2-hop Comparison | Verify A's attribute and B's attribute; the final comparison adds no hop |
| 3-hop | Verify three independent necessary factual relationships/attributes |
| Unrelated counts | Article count, sentence count, and retrieval-call count do not determine hop count |
| Inflated chains | Redundant relationships and independent propositions added to inflate hops do not count |
| Necessity | A sentence that alone determines the verdict invalidates the intended multi-hop sample; humans also check for an article directly resolving the claim without a reasoning chain |
| Single-document reasoning | Combining necessary factual steps within one article is allowed |
| NEI | Two-hop required structure, with one sentence verifying Hop 1 and insufficient information for Hop 2 |

- **FR-046**: Guidance MUST explain all interpretations above and include valid bridge/comparison/three-hop/single-document examples and invalid shortcut/inflated-count examples. The hypothetical illustrations in [docs.md](docs.md#hop-count-and-multi-hop-necessity-guideline) are the agreed basis; real benchmark evidence must come from the frozen corpus.
- **FR-047**: Cross-document claims with necessary Gold Evidence from at least two articles MUST be prioritized as a research preference, without rejecting valid single-document multi-hop or imposing an additional document-count validation/quota.
- **FR-048**: Two sentences from two articles MUST NOT be treated as automatic proof of two-hop reasoning. Human Multi-hop Necessity assessment remains required.

### Derived Coverage, Persistence, and Sessions

- **FR-049**: Each saved sample MUST include automatically derived `metadata.source_article_count` and `metadata.document_coverage`, counting distinct source page IDs in its stored evidence, excluding unselected workspace articles.
- **FR-050**: One distinct source article MUST yield `single_document`; at least two MUST yield `cross_document`. SUPPORTED/REFUTED use gold evidence sources; NEI uses its single partial evidence source, describing observed evidence only rather than the missing Hop 2.
- **FR-051**: Coverage derivation MUST NOT modify evidence object fields, add human-input fields, infer reasoning validity, or change human label/hop count.
- **FR-052**: Successful Submit MUST persist an independent sample with a UUID, current session identity, corpus version, UTC creation timestamp, human annotations, selected evidence, and derived coverage before acknowledging success.
- **FR-053**: An internal submission identity MUST make double-clicks and same-submission retries idempotent. A new annotation submission MUST receive a new identity; retries MUST NOT overwrite existing samples.
- **FR-054**: Only after confirmed persistence MUST the form clear claim, label, hop count, and selected evidence. It MUST retain articles, metadata, display state, and discovery context. Failed submissions MUST retain the draft.
- **FR-055**: Every page opening or refresh MUST create a new UUID session and empty workspace/draft, without restoring previous unsubmitted data. Successfully submitted samples MUST retain their original session identity across refreshes and restarts.
- **FR-056**: Session IDs MUST be generated independently on each machine without coordination or annotator identifiers. The displayed counter MUST count successful samples in the current session only.

### Export, Transfer, and Operation

- **FR-057**: Export Dataset MUST export all and only successful samples from the current session as UTF-8 JSONL, one complete record per line, preserving both evidence arrays, source metadata, corpus version, UTC timestamp, and derived coverage.
- **FR-058**: Export MUST leave saved data and workspace unchanged and permit further annotation. An empty current session MUST disable export with an explanation.
- **FR-059**: The UI MUST have no Export by Date or date filter. Session-based export filenames MUST be used; `VIMFC_SESSION_001.jsonl` is illustrative, not a shared-machine numbering requirement.
- **FR-060**: PowerShell-accessible commands MUST list saved sessions and export a specified historical session, including after refresh or application restart.
- **FR-061**: Corpus and graph MUST be independently exportable with the same corpus version and include all 20,000 selected articles, even those never used for annotation.
- **FR-062**: Transfer of source code and the frozen corpus package MUST enable independent annotation on the second machine without rerunning collection. Frozen source data MUST be read-only during annotation and separate from writable session/sample storage.
- **FR-063**: Windows startup through `start.ps1` MUST check prerequisites and corpus validity, refuse invalid/unfrozen corpora, display the local URL after success, and never invoke corpus construction automatically.
- **FR-064**: The UI MUST use Vietnamese instructions, controls, and errors while retaining the literal label values and export field names. Its layout MUST provide the agreed header, article workspace, and annotation panel without extra human metadata entry.
- **FR-065**: User documentation MUST explain Windows installation, separate corpus build/resume/freeze, corpus transfer, startup, session export/recovery, and the distinction between lost drafts and durable submitted samples.
- **FR-066**: Before implementation, research MUST be recorded in `research_vimqa.md`, distinguishing ViMQA's original collection approach from this fact-checking adaptation, including its requested data-collection, interface, reasoning-type, and benchmark-setting sections.

### Benchmark Record Contract

The record contract below is an interchange requirement, not a database or endpoint design. Every sample contains the same top-level fields.

| Field | Required meaning |
| --- | --- |
| `id` | Sample UUID |
| `session_id` | UUID of the session in which the sample was submitted |
| `corpus_version` | Identity of the frozen evidence corpus |
| `claim` | Human-entered Vietnamese claim |
| `label` | Human-selected SUPPORTED, REFUTED, or NEI |
| `hop_count` | Human-selected 2 or 3; NEI requires 2 |
| `gold_evidence` | Selected complete evidence for SUPPORTED/REFUTED; empty for NEI |
| `partial_evidence` | Exactly one selected Hop 1 sentence for NEI; empty otherwise |
| `metadata.document_coverage` | Derived `single_document` or `cross_document` |
| `metadata.source_article_count` | Count of distinct observed evidence source page IDs |
| `reasoning_chain` | Null; no automatically generated gold reasoning chain |
| `created_at` | UTC creation timestamp in ISO-8601 format |

Each evidence object, in either array, MUST retain exactly the agreed fields: `article_title`, `page_id`, `revision_id`, `source_url`, `sent_id`, and `evidence_text`. No review metadata or annotator identifier is included. Internal submission identities do not require an added benchmark field.

### Key Entities

- **Pipeline Run**: Fixed snapshot, configuration/rule versions, processing state, source inputs, and associated checkpoints and selection records.
- **Source Revision**: Wikipedia or Wikidata identity, revision/timestamp, raw content/metadata, and recorded provenance.
- **Candidate Article**: An article under collection, classification, filtering, or ranking, with quality signals and explicit processing status.
- **Frozen Article**: Selected page identity, full lead, stable sentences, entity metadata, source provenance, extraction version, and summary statistics.
- **Candidate Pair**: Bridge/comparison partners, observed supporting signals, pair scores, and rule versions; not a human-annotated claim or invented graph edge.
- **Full Article Hyperlinks**: Historical-revision hyperlinks across the article; if retained, source/audit data separate from the lead-based operational graph.
- **Full Lead Entity Link**: A real hyperlink in retained introductory prose, with snapshot-consistent source/target identity and redirect-resolution provenance; targets may be outside the corpus for Shared Entity matching.
- **Corpus-internal Graph Edge**: A normalized directed Full Lead Entity Link with both endpoints in the final 20,000-article corpus, stored with the frozen artifact for discovery and future graph expansion.
- **Frozen Corpus**: Exactly 20,000 immutable selected articles plus versioned graph and metadata, independently reusable on both machines.
- **Session**: Local UUID created for a page opening/refresh, associated with successfully submitted samples.
- **Workspace Draft**: Up to five open articles, display/discovery state, human field values, and temporary evidence selections; discarded on page reopening/refresh.
- **Submitted Sample**: Durable benchmark record preserving the human's values, frozen-source evidence, session/corpus identity, timestamp, and derived document coverage.

## Success Criteria

### Measurable Outcomes

- **SC-001**: A completed corpus contains exactly 20,000 unique eligible articles; all carry the required source, classification, extraction, and sentence metadata, and group totals/deviations are reported.
- **SC-002**: Every source revision used is at or before the fixed run snapshot; annotation exposes zero live Wikipedia/Wikidata requests after corpus freeze.
- **SC-003**: All stored lead sentences are traceable to their recorded article revisions; all article sentence sequences are zero-based and contiguous; every internal graph edge is a directed, normalized hyperlink from retained Full Lead prose at the recorded revision with both endpoints in the frozen corpus. Graph integrity checks pass, and zero body-only links enter the operational graph.
- **SC-004**: Replaying ranking/selection against identical stored inputs and rule versions yields identical selected article identities and scoring/decision results. Compatible interrupted runs produce no duplicate records and reuse completed valid work.
- **SC-005**: All three discovery modes satisfy their stated relationship rules, return only addable in-corpus articles, and enforce zero duplicate entries and a maximum of five open articles.
- **SC-006**: Valid structural submissions for all labels are persisted without label/hop modification, including SUPPORTED/REFUTED using one source article; every NEI submission has exactly one partial sentence and hop count 2.
- **SC-007**: All repeated requests for one submission identity yield one stored sample; confirmed samples survive refresh/restart, while unsubmitted drafts do not reappear.
- **SC-008**: Exported line counts equal successful sample counts for the selected session. Every line parses as one record with the required contract, matching stored evidence and derived coverage, with zero drafts, unselected contexts, or cross-session records.
- **SC-009**: Two local installations using the same transferred frozen corpus expose identical corpus versions and article/sentence data while maintaining independent session/sample stores.
- **SC-010**: The ordinary startup command performs no corpus collection; it permits annotation only for a valid frozen corpus and gives actionable feedback for invalid prerequisites or corpus data.
- **SC-011**: Corpus quality reporting includes connectivity, bridge/comparison counts, subtype coverage, and externally human-assessed Valid 2-hop Yield. When assessed, report the valid and reviewed counts and their ratio; if no review has occurred, report it as unassessed rather than claim a measured yield.
- **SC-012**: Research collection can produce records supporting the 2,000-claim target and separate single-document/cross-document analysis. Completion of the human-authored benchmark and its label distribution is a research milestone, not an automatic software acceptance result.

## Assumptions and Dependencies

1. [docs.md](docs.md) and the user's latest confirmed decisions are the authority for scope. This specification formalizes them without introducing new research quotas or semantic validation.
2. Human annotators own claim truth assessment, factual-step counting, multi-hop necessity, and external NEI review. The software checks structural validity only.
3. Availability of historical Wikipedia/Wikidata source revisions is an external dependency. Missing data can delay collection; it does not justify live-data substitution or relaxed provenance rules.
4. The initial 80,000-candidate target is not a hard cap; additional candidates may be needed to obtain 20,000 eligible articles. Entity quotas remain soft and the approximate 5% three-hop distribution remains a research target; none permits relaxing article quality.
5. Pair scores, filtering thresholds, classification precedence, checkpoint frequency, endpoint choices, batch sizes, caching, concurrency bounds, and performance baselines belong in the plan as versioned, reviewable choices. The plan should investigate `action=parse&oldid=...&prop=links` for historical hyperlink collection while ensuring full-article responses do not become lead-only graph edges without scope filtering. This specification does not finalize that endpoint or invent unsupported numerical guarantees or a minimum Valid 2-hop Yield.
6. Local speed and startup/query efficiency must be measured on the intended Windows environment during planning/verification. Research collection is network-dependent; annotation after freeze is not.
7. The agreed React/TypeScript/Vite, Python/FastAPI, SQLite split (`corpus.sqlite` and `annotations.sqlite`), and PowerShell delivery choices remain constraints from [docs.md](docs.md); their implementation design belongs in the plan.
8. The repository currently uses `docs/specs.md` as the requested specification location rather than an initialized `.specify` feature tree.

## Out of Scope

- Implementing or evaluating the future Agentic RAG system in this feature.
- Live or outside-corpus article discovery during annotation; body-section or infobox evidence.
- Four-hop/five-hop claims, automatic claim generation, label/hop inference, and fabricated reasoning chains.
- App-managed human review, NEI verification search workflow/logs, review-status exports, or annotator IDs.
- Mandatory cross-document validation, automatic single-hop shortcut adjudication, or an enforced cross-document quota.
- Draft restoration, date-based dataset export, Docker deployment, or rebuilding the corpus at annotation startup.
- New train/dev/test split policies, centralized multi-machine annotation, and automatic aggregation of the two annotators' exports; these were not agreed requirements.

## Discovery experiment / lifecycle refinement (2026-10-27)

This approved implementation refines FR-019/020/022/025/026 and SC-004/011 without changing qid-5, lead-4, quality-2.1, rank-1, Fixed Snapshot, eligibility, Comparison subtype matching or research targets.

- Valid reviewed quarantine MUST restore from its exact immutable receipt and matching run-scoped approval without worker entry, source request, queue write or attempt increment. Invalid/missing/conflicting review/receipt MUST stop. Quarantine remains non-evidence and blocks integrity completeness/acceptance.
- Negative pageprops observations MUST have a finite automatic budget and explicit historical-association review state; missing hints or absence from a partial historical cache MUST NOT establish QID nonexistence or justify a guessed QID. Existing TTLs and positive historical checks remain required.
- Discovery semantics MUST be independently versioned and use separate run/cohort manifests; changed discovery identity MUST NOT silently inherit a previous cohort. Canonical dedup MUST preserve origins and exact input/output ordering. Physical cache/worker timing MUST NOT choose candidate membership.
- Stratified experiments MAY balance the eight existing sampling strata using unchanged soft quotas. Sampling origin, proven primary entity group and reporting aggregation MUST be separate. Reporting MAY aggregate Location + Landmark/Facility; Science/Technology stays explicitly unmapped/separate rather than being forced into Creative Work. All eight original quotas still sum20,000; the six aggregate core groups do not silently absorb the remaining science quota.
- Hyperlink-aware candidate expansion MUST originate in checked eligible completed Full Lead seeds with historical target identity, revision/hash and exact retained-link sentence provenance. Body links, inferred title matches and comparison associations MUST NOT become hyperlink evidence. Unknown identity/namespace/eligibility remains explicit; source failures are not replaced to fill the candidate denominator.
- Controlled discovery experiments MUST declare frozen prior-run seed/frontier inventories, variant and unique-candidate budgets, caps/fallbacks/shortfalls, source and cache-baseline scopes. Unknown inputs MUST NOT be counted as ineligible or as complete yield. Expanding a later universe requires new declared input identity; no automatic live-adaptive expansion is authorized by an offline result.
- A79-pair external review package MUST retain29 Bridge census +50 stratified Comparison samples, inclusion probabilities/weights, full lead sentences, revision/raw/manifest/link/value provenance, heuristic risks and blank human judgments. Pair counts/scores MUST NOT be reported as measured Valid2-hop Yield; app annotation/export contracts are unchanged.

Operational commands and exact boundaries: [discovery-experiments.md](discovery-experiments.md). This refinement does not authorize API work or a pilot during implementation/offline verification.

## Seed-only collection decision (2026-10-28)

The user selected **Stratified Seed-only** as the sole forward corpus-discovery strategy. Legacy/Hybrid artifacts and readers remain for reproducibility; no new comparative arms are required. This decision does not change qid-5, lead-4, quality-2.1, rank-1, snapshot2026-10-25T03:00:00Z, ontology depth12, eligibility thresholds, eight sampling quotas totaling20000, six core reporting groups or separate Science/Technology reporting.

- A wide inventory MUST retain60000–100000 unique ns0 page identities, listing-response hashes, category/exploration origins, observation timestamps, continuation and explicit shortfall/cost. It MUST NOT acquire historical dependency closures for every inventory member. Discovery metadata is current observation, never primary classification, historical namespace proof or graph evidence.
- `seed-only-2` uses deterministic weighted soft quotas plus20% exploration slots with explicit exhausted-bucket fallback. Sampling strata are hints; they MUST NOT become classification labels. Missing strata remain unassigned; duplicate page identities preserve all observed origins. Changed/conflicting observed titles require review.
- Freeze inventory identity, sampling policy/config hash and exact ordered1000-cohort before pilots. Current listing title changes MUST NOT override historical page identity; historical intake and namespace checks remain mandatory. Old historical-cohort strict canonical-title guards remain unchanged.
- Bounded discovery checkpoints MUST retain charged request attempts, cursors and host cooldown across restart. Frozen inventories MUST reject mutation; extending the universe requires a new freeze/run identity.
- An explicit `vimfc-exploratory-progression-1` permission MAY permit only100→1000 on a seed-only-2 run with an independently replayed100-prefix. It MUST pin run/config/snapshot/full-cohort/prior-report identity and exact observed missing-hint pages/reviewed refusal. Unknown failures, unreviewed critical defects or transient source frontiers MUST block progression. This permission MUST NOT set data_complete, integrity_complete or pilot_accepted and MUST NOT open10000/Production gates.
- Missing QID hints retain TTL/finite-observation lifecycle and historical association checks. Exact reviewed source refusal never creates trusted raw, manifest, classification, eligibility or graph evidence; every other integrity failure still stops immediately.

Operational entry point: [seed-only.md](seed-only.md). The current authorization covers finite listing acquisition and staged100/1000 only; no10000/Production or automatic unbounded retries.

### Inventory source reliability (2026-10-28)

Acquisition MUST preserve cumulative physical-attempt reservations, cursors, successful response hashes, identities and origins across HTTP failures/restart. One listing query uses one HTTP attempt; a new503 or unexpected source/integrity/circuit/budget stop MUST end the authorized recovery invocation. Exponential backoff with bounded jitter sets a persistent host retry deadline for a later explicitly authorized recovery; it does not silently retry503. Retry-After MUST NOT be shortened, even above the local backoff cap. Host-wide pacing/cooldown and a process-aware checkpoint lease MUST prevent concurrent workers and source probes during pause.

New receipts MUST distinguish observed HTTP status/endpoint/safe request context, allowlisted response headers, body fingerprint and transport exceptions; HTTP503 alone MUST NOT be called rate limiting or attributed to Wikimedia/proxy without evidence. Cookies/tokens/contact credentials MUST NOT be logged. RAM/CPU/runtime and failure receipts MUST be published on aborted acquisition; unavailable/corrupt checkpoint status MUST remain explicit rather than erase telemetry.

Inventory publication MUST use the acquisition lease, validate captured provenance, stage checksummed artifacts before sealing, and recover missing aliases without network. A minimum60000 snapshot MAY be frozen only with explicit permission and successful integrity checks; target shortfall, source stop and acceptance remain separate. An active lease/circuit or unknown exception MUST NOT authorize freeze. No source switch or dump merge is implied by a dumps proposal.
# Official dump candidate inventory (dump-inventory-1)

Seed-only remains the sole corpus Discovery method. A new dump-inventory-1 input may use only completed official viwiki page/linktarget/categorylinks metadata, pinned by date, source URLs, sizes and official SHA-1, with local SHA-256. Candidate source date is not historical evidence at the fixed snapshot. Keep seed-only-2/qid-5/lead-4/quality-2.1/rank-1 and all eight sampling quotas unchanged.

Importer must stream without executing SQL, validate schema/IDs/ns/redirects/title conflicts and join categorylinks.cl_target_id to namespace14 linktarget.lt_id. Binary sortkeys must not be decoded as title text. Metadata category hints do not prove primary group/subtype. Only unique ns0 nonredirect identities can enter the inventory; historical intake must independently prove existence/revision/ns/Wikidata at 2026-10-25T03:00:00Z before corpus evidence is admitted.

Downloads must retain bounded attempts/byte reservations/cooldown through restart, validate complete compressed artifacts before import, and publish atomically. File imports are transactional and idempotent; partial imports and conflicting identities cannot be sealed. Freeze requires >=60,000 verified identities and deterministic replay, with immutable manifest and provenance. Existing API inventory and historical runs must be preserved. Inventory completeness does not imply article eligibility or pilot acceptance. No dump acquisition command starts a pilot.
## Additive reviewed integrity refusals

Single-revision refusal approvals remain immutable and readable. A versioned refusal-set envelope may add separately evidenced approvals for the exact same run/config/snapshot. Activation appends missing component events; all subsequent invocations must present the complete active component set. Removing an active component or falling back to an older single approval is rejected. Existing quarantine case IDs/receipts must retain their original component identity and restore without worker calls or attempt increments.

Each component binds exact revision/identity/timestamp/snapshot and captured response/content hashes; supplied RCA pointers must verify. Refusal is not content validation or pilot acceptance. No trusted raw, classification, eligibility, published article manifest or graph evidence may use a refused dependency. Unknown integrity failures remain hard stops, including optional subtype branches. Additions do not change qid-5/lead-4/quality-2.1/rank-1 or fixed cohort/quotas; quarantine keeps denominators and incomplete gates honest.

## Integrity blocker registry (operational version 1)

An explicitly installed `vimfc-integrity-registry-1` sidecar MUST be bound to run/config/snapshot and remain outside trusted raw. Canonical append-only observation/disposition events MUST hash-chain and match every main-database run-event anchor. Missing files, altered evidence/authorization, rollback and unanchored tails MUST fail closed. Exact selected dependencies MUST be checked before cache/network/materialization; known unknowns MUST block preflight. Registry observations MUST distinguish unknown integrity, reviewed refusal, verified recovery, source unavailability, semantic unresolved and missing historical dependency. Counts are observations, not candidate counts.

Only explicit exact approval/evidence/authorization may transition an unknown mismatch to reviewed refusal. Verified recovery MUST prove an exact payload matching the original source SHA-1 and still cannot bypass ordinary source validation. New unknown mismatches MUST stop and preserve the original exception even if diagnostic persistence fails. No registry event grants execution/progression authority or changes research semantics, accepted raw or completeness flags. Cross-database append/anchor interruption requires reviewed repair; it MUST NOT be silently truncated or reset. See [source-integrity.md](source-integrity.md).

## Groq-assisted classification experiment

Groq suggestions MUST remain separate from qid-5 verified primary, human review and unresolved states. Assisted mode preserves a completed verified primary without inference; explicit blind controls MAY call Groq for evaluation but MUST NOT alter source labels, eligibility or graph. Only independently checked historical retained lead/title/sentence IDs enter prompts; baseline labels, strata and gold stay local. Precedence is copied from the archived repository rules, never invented by the LLM.

Inputs/manifests/config/prompt versions and API responses MUST be frozen/provenance-bound. Request/token reservations, shared pacing/cooldown and successful HTTP checkpoints MUST survive restart without duplicate valid inference. Unknown in-flight outcomes fail closed. Generation requires actual local credentials, authenticated model availability and explicit user confirmation via `GROQ_FREE_TIER_CONFIRMED=true` in local dotenv or environment. No evidence JSON, screenshot or image checksum is required. This user confirmation is not provider billing attestation; neither model listing nor rate headers prove billing tier. No automatic upgrade, fallback model/mode, unbounded retries or next-day execution. Baseline agreement is not human accuracy; no human gold means no Accuracy/F1 claim. See [groq-classification.md](groq-classification.md).
