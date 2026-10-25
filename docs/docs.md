# Vietnamese Multi-hop Fact-Checking: Agreed Requirements

This document summarizes the decisions made during the requirements interview. It records the agreed design, not completed implementation. Later decisions override conflicting statements in the original request.

## 1. Project Goal and Scope

Build a high-quality Vietnamese fact-checking benchmark based on a fixed Wikipedia summary corpus, then use that same corpus to develop and evaluate an Agentic RAG fact-checking system.

The immediate implementation scope is:

1. Build and freeze a corpus of 20,000 feasible Vietnamese Wikipedia articles.
2. Provide a local annotation application for human-authored claims.
3. Persist submitted samples and export the benchmark as JSONL.

The later Agentic RAG system will receive only a claim, retrieve from the frozen corpus, optionally expand retrieval through the stored graph, and predict `SUPPORTED`, `REFUTED`, or `NEI`. It must not access gold labels, annotated evidence, or live Wikipedia content during benchmark evaluation.

The corpus-building and annotation stages require no LLM API, Cross-Encoder, LangChain, LangGraph, or autonomous fact-checking agent.

## 2. Benchmark Targets

| Property | Agreed target |
| --- | --- |
| Total claims | 2,000 |
| SUPPORTED | 900 |
| REFUTED | 900 |
| NEI | 200 |
| Allowed hop counts | 2 or 3 |
| Approximate hop distribution | 95% 2-hop, 5% 3-hop |

Three-hop claims should have natural, necessary reasoning chains. Do not force the approximate 100-claim three-hop target by adding independent propositions or unnecessary factual steps. Four-hop and five-hop claims are out of scope.

All 200 NEI claims use the prescribed two-hop structure. The approximate three-hop subset therefore comes from SUPPORTED/REFUTED claims.

The maximum of five articles in the annotation workspace is a UI limit. It does not determine hop count or the required number of evidence sentences.

## 3. Fixed Evidence Scope

The complete set of 20,000 frozen Full Lead Sections defines the verification scope:

- `SUPPORTED`: sufficient evidence exists in this corpus to establish the claim.
- `REFUTED`: sufficient evidence exists in this corpus to contradict the claim.
- `NEI`: the required information cannot be established or contradicted after the humans perform the agreed search and review outside the application.

Missing a checkbox or failing one retrieval query is not a basis for NEI. Information available elsewhere on Wikipedia but absent from this corpus cannot be used as benchmark evidence.

Annotation and future Agentic RAG retrieval must use exactly the same frozen corpus and `corpus_version`.

## 4. Corpus Composition

Select 20,000 unique feasible articles using these soft allocation targets:

| Primary entity group | Target articles |
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

Each eligible article has exactly one proven `primary_entity_type` for quota accounting. Under Primary-First, Optional-Subtype, subtypes are optional; record Scientist, Footballer, Actor, University, Country, City, Film or Stadium only with positive historical paths. A proven primary may pass classification while subtype search is incomplete. Empty subtype is not proof that no subtype exists; record search status and completeness.

Primary classification uses historical Wikidata P31/P279 at the Fixed Snapshot; direct configured-root identity remains an explicit versioned mapping with a zero-property historical path. P106 alone must not infer Person. Person subtype discovery may additionally use P106/P279. Collect primary candidates deterministically under depth 12 and the 2,048-node union budget; preserve all sibling branches that could change precedence. An open primary branch blocks acceptance unless the proven winner has the first precedence among all configured groups, making every possible remaining candidate dominated. Record decision proof, source revisions/hashes and immutable role-aware manifest. Cache changes acquisition cost, not the frozen logical input. Unresolved optional subtype alone does not block eligibility; unresolved primary does.

Exclude redirect-only, disambiguation, list-only, and unhelpful summaries. Entity type alone is insufficient: prioritize useful factual prose and potential bridge or comparison partners.

Quotas are soft, but the frozen corpus must contain exactly 20,000 unique eligible articles. If targets cannot be reached, report actual counts and propose adjustments; do not silently lower quality, change quotas, or fill gaps with unsuitable articles.

## 5. Full Lead Section Extraction

For every article, extract all introductory prose from the beginning of the article up to the first content heading, including multiple paragraphs.

- Remove infoboxes, tables, navigation, footnotes, and other non-prose components.
- Do not include body sections after the first content heading.
- Do not impose a hard sentence-count limit on stored leads.
- Normalize Unicode and split the extracted prose into sentences.
- Assign stable, zero-based `sent_id` values within each stored article.
- Store `extraction_version`, `sentence_count`, `paragraph_count`, and lead length statistics.

For example, the Lionel Messi article includes introductory prose before the first body section, but excludes the infobox and the body section itself.

The UI initially displays six sentences per article. `Show More Sentences` / `Show Less` changes presentation only. All stored sentences remain available for annotation and Agentic RAG retrieval.

## 6. Fixed Snapshot and Reproducibility

Each pipeline run has a fixed `snapshot_timestamp_utc`.

### Wikipedia

- Select the latest article revision whose timestamp is not later than the snapshot timestamp.
- Extract leads and hyperlinks from that revision, rather than current live content.
- Store page ID, title, source URL, revision ID, revision timestamp, raw content, and metadata used.
- Resume operations and retries retain the original snapshot timestamp.

### Wikidata

- Apply the same snapshot timestamp to classification metadata, including P31, P279, and P106 where applicable.
- Use the latest eligible historical revision and retain its ID, timestamp, raw metadata, and classification basis.
- Apply this rule to referenced entities used when traversing the classification hierarchy as well.
- If required primary historical evidence is missing or conflicting, keep primary unresolved; reserve unclassified for a closed unsupported primary graph. Missing optional evidence records incomplete subtype search without rejecting a proven primary. Never substitute live metadata or infer absent subtype.
- Wikidata supports classification and corpus construction. It is never gold evidence for benchmark claims.

Store configuration and algorithm versions, classification rules, extraction rules, downloaded source data, and ranking/selection records. Changing the snapshot timestamp requires a new pipeline run.

The frozen corpus has its own `corpus_version` and is immutable during annotation or subsequent pipeline runs.

## 7. Corpus Selection Pipeline

The selection pipeline operates before annotation. Its candidate pairs support corpus selection; they are not the runtime Add Article candidate list.

### Candidate collection and filtering

The initial collection target is **80,000 Vietnamese Wikipedia candidates**, not a hard cap or a guarantee of feasible-article yield. If that pool does not supply 20,000 articles meeting the agreed standards, collect additional candidates without lowering quality to reach the final count.

Collect candidates across entity groups and subtypes, retrieve snapshot-consistent source data, classify entities, and filter lead quality before final selection. Keep Full Article Hyperlinks, Full Lead Entity Links, and the Corpus-internal Graph distinct as defined below.

### Wikipedia Hyperlink Graph

Prefer the MediaWiki API for collecting real Wikipedia hyperlinks. Links must come from the selected historical revision under the fixed snapshot, never from the current live article. The plan should investigate `action=parse&oldid=...&prop=links` as a possible collection endpoint and determine how to restrict extracted links to eligible Full Lead prose; an unrestricted full-article link list must not be treated as a lead-only graph.

- **Full Article Hyperlinks:** all recorded hyperlinks from the selected article revision. If retained, they are source/audit data; links appearing only in body sections or excluded components do not enter the lead-based graph.
- **Full Lead Entity Links:** real hyperlinks appearing in the retained Full Lead Section prose, excluding infoboxes, tables, navigation, footnotes, and body sections. Only these links may form the graph used for corpus connectivity, graph-aware selection, annotation Hyperlink discovery, and future graph expansion.
- **Corpus-internal Graph:** the directed Full Lead Entity Link edges whose normalized source and target articles both belong to the final frozen set of 20,000 articles.

Normalize page identities and redirects consistently with the snapshot, preserve the provenance of link resolution, and store directed edges A -> B using canonical source/target page identities. Shared Entity matching may retain lead link targets outside the corpus, but those targets do not become corpus-internal edges.

Once the final 20,000-article selection is fixed, filter its internal lead-based edges and persist them in `corpus.sqlite` as part of the frozen corpus artifact. Annotation must not rebuild the graph from live data or mutate the frozen artifact.

A hyperlink alone is not factual evidence. A body-only link cannot establish a factual relationship in the summary, and even a lead link is only a discovery/selection signal until humans select actual factual prose. Gold Evidence comes exclusively from stored Full Lead Section sentences.

Endpoint choices, batching, caching, link extraction/resolution mechanics, and performance design belong in the plan.

### Multi-hop-aware reranking

Score article pairs in addition to individual articles:

- **Bridge:** favor A–B pairs where summary A expresses an entity relationship involving B and summary B supplies complementary factual information.
- **Comparison:** favor articles with matching subtypes and compatible attributes expressed in both leads.
- Reward complementary information and reduce redundant pairs.
- Penalize signals suggesting that a single sentence or article might already be enough to establish the prospective verdict.

These scores estimate multi-hop potential. They do not prove claim validity, generate claims, assign labels, or determine hop counts.

### Graph-aware corpus selection

Use deterministic, rule-based, graph-aware greedy selection to retain useful bridge/comparison partners while supporting entity diversity and soft quotas.

- Evaluate connectivity within the selected corpus, not just a candidate's total outgoing links.
- Avoid overfavoring popular entities or articles with unusually large hyperlink counts.
- Use reproducible ordering and tie-breaking.
- Preserve ranking scores, contributing signals, candidate pairs, rules versions, and selection logs.

Comparison associations are selection metadata; they must not be represented as original Wikipedia hyperlinks.

### Corpus evaluation

Report internal connectivity, bridge/comparison candidate counts, subtype coverage, group counts, and Valid 2-hop Yield assessed through human review. Human quality assessment is performed outside the annotation application's review workflow.

Freeze only after the corpus contains 20,000 unique articles and passes checks for source metadata, extraction consistency, sentence IDs, quality rules, and graph integrity.

## 8. Pipeline Performance, Checkpoint, and Resume

- Process data in batches with caching and bounded concurrency.
- Do not download or recompute already valid results unnecessarily.
- Display progress, processed article counts, processing speed, and estimated time remaining.
- Save candidates, metadata, leads, hyperlinks, classifications, filtering results, and ranking results periodically.
- Track `pending`, `completed`, and `failed` processing states; support retries for API/network failures.
- Make processing idempotent to avoid duplicate records.
- Resume automatically from the nearest valid checkpoint after interruption.

If a configuration or extraction/filtering/ranking rule change makes a checkpoint incompatible:

1. Stop resume with a clear error and instructions for creating a new run.
2. Create a new `pipeline_run_id` with its configuration and algorithm versions.
3. Reuse raw data only when its snapshot/revision and format remain compatible.
4. Rerun affected stages and their downstream dependencies.
5. Preserve the previous run, checkpoints, and results.
6. Never overwrite an already frozen corpus.

## 9. Storage and Transfer

Use separate local SQLite databases:

| Database | Contents and behavior |
| --- | --- |
| `corpus.sqlite` | Frozen articles, full leads, sentences, entity metadata, graph, and corpus version; read-only during annotation |
| `annotations.sqlite` | Local sessions and submitted samples; writable and persistent |

Retain at least the following article information: `page_id`, `title`, `source_url`, `revision_id`, `revision_timestamp`, `primary_entity_type`, subtype information, Wikidata identifiers/revisions where available, full lead prose, sentences, outgoing hyperlinks, and extraction version.

Store normalized directed Full Lead Entity Link edges with source and target page identities, and persist the filtered Corpus-internal Graph in `corpus.sqlite`. Keep any retained Full Article Hyperlinks separate as source/audit data; body-only links must not enter the operational graph. Retain lead entity links to outside-corpus entities where needed for Shared Entity discovery.

The corpus, graph, and benchmark are separate artifacts linked by the same corpus version. Support independent corpus/graph export for reuse, while transferring `corpus.sqlite` is the agreed method for the second annotator.

The user builds and freezes the corpus once, then shares source code and the frozen corpus database with the second annotator. Each machine creates its own annotation database and sessions. No `annotator_id` field is required.

Use database indexes to support fast startup, article lookup, discovery, session sample counts, and exports.

## 10. Annotation Workspace and Discovery

Start with an empty workspace and let the annotator Random Article A from the frozen corpus. Up to four additional articles may be opened, for a maximum of five simultaneous articles.

After freeze, Random, Re-random, and all discovery modes use local data only. Do not retrieve new articles or updated summaries from live Wikipedia.

### Hyperlink

Find candidates through real directed Full Lead Entity Links in the stored Corpus-internal Graph from a selected workspace article. Body-only hyperlinks are excluded. Added articles must belong to the frozen corpus.

### Shared Entity

Find articles whose Full Lead Sections hyperlink to the same identified Wikipedia entity. The shared entity itself may be outside the corpus, but candidate articles added to the workspace must be inside it.

Do not match solely by similar names or use mentions that occur only in body sections.

### Same Entity Type

Use a specific shared subtype, such as Footballer or Film, rather than only a broad group such as Person or Creative Work. If several subtypes apply, the annotator selects one. Do not silently broaden the search when no candidates exist.

### Workspace behavior

- Allow discovery from any open article, not only Article A.
- Show a candidate list and a Random Candidate option.
- Prevent duplicate articles in the workspace.
- Distinguish no results from operational errors; do not fabricate articles or relationships.
- Article A has no Remove button; replace it through Re-random.
- Remove additional articles immediately if they have no selected evidence.
- If an article has selected evidence, confirm removal before removing it and its draft evidence.
- Removal affects only the current workspace, never the corpus, graph, or submitted samples.
- Removed articles remain eligible to be selected again.
- Re-random resets the current workspace/draft after confirmation when there is unsubmitted work, while preserving submitted samples.

## 11. Human Annotation Authority and Evidence

The annotation panel has only three human-entered fields: claim, label, and hop count. Evidence is selected through sentence checkboxes; article/source metadata is populated automatically.

Humans decide both `label` and `hop_count`. The system must never assign, repair, or overwrite either field. It may validate the submitted structure and report errors for the human to correct.

The two annotators discuss reasoning and perform cross-review outside the application. Do not implement a review workflow, NEI verification search workflow, or review logs in the annotation app. Do not export review features such as `review_status`.

### Hop Count and Multi-hop Necessity Guideline

**One hop is one verification step for an independent factual relationship or attribute that is genuinely necessary to determine the verdict.** Humans apply this definition when selecting `hop_count`.

- **2-hop Bridge:** X -> Y -> Z requires verification of two factual relationships: the relationship identifying Y from X, followed by the relationship connecting Y to Z.
- **2-hop Comparison:** verify the relevant attribute of A and the relevant attribute of B, then compare them. The final comparison operation does not add a hop.
- **3-hop:** verify three independent factual relationships or attributes, all necessary to determine the verdict.
- Hop count is not the number of source articles, evidence sentences, or retrieval calls.
- Do not count redundant relationships or independent propositions added only to inflate hop count.
- **Multi-hop Necessity:** if one sentence alone determines the verdict, the sample does not meet the multi-hop requirement, regardless of the number of selected sentences or articles. During external review, also check whether a single article directly resolves the claim without requiring a reasoning chain.
- A single article may still contain valid multi-hop evidence when establishing the verdict genuinely requires combining two or more necessary factual steps.
- **NEI exception:** `hop_count = 2` describes the structure required to verify the claim. Exactly one partial evidence sentence verifies Hop 1; the information needed for Hop 2 is insufficient within the fixed corpus. It does not describe two completed evidence-backed steps.

The following examples are hypothetical guideline illustrations, not real Wikipedia evidence or benchmark records. Assume no sentence or article directly states the final verdict unless the example explicitly says otherwise.

| Example | Claim and available facts | Guideline outcome |
| --- | --- | --- |
| Valid 2-hop Bridge | Claim: "The director of film X was born in Z." One sentence identifies Y as X's director; another establishes Y's birthplace as Z. | Verify X -> Y and Y -> Z. Both facts are necessary: 2 hops. |
| Valid 2-hop Comparison | Claim: "Organization A was founded before organization B." Separate sentences give A's and B's founding years. | Verify both founding-year attributes: 2 hops. Comparing the years adds no hop. |
| Valid 3-hop Bridge | Claim: "The university attended by the director of film X was founded in year T." Sentences identify director Y, Y's university U, and U's founding year T. | Verify X -> Y, Y -> U, and U -> T: 3 necessary hops. |
| Valid Single-document Multi-hop | In one lead, a sentence identifies Y as X's director and another gives Y's birthplace as Z. Neither sentence directly states where X's director was born. | Combining both relationships is necessary: valid 2-hop, with `single_document` coverage. |
| Invalid sentence shortcut | A sentence directly says "The director of film X was born in Z." An annotator also selects another related sentence. | The first sentence alone establishes the verdict; the extra selection does not make the sample multi-hop. |
| Invalid article shortcut | Evidence is selected from two articles, but one article directly states the complete fact asserted by the claim. | Two source articles do not establish multi-hop necessity; the direct answer is a shortcut. |
| Invalid inflated count | A two-hop comparison is marked 3-hop because the final comparison is counted, or a bridge claim adds an unrelated fact about Y. | Neither the comparison operation nor an unnecessary proposition adds a factual verification hop. |
| Invalid REFUTED shortcut | A compound claim incorrectly identifies W as X's director and adds a fact about W. One sentence naming Y as X's director already contradicts the claim. | One sentence determines REFUTED; the additional clause does not create a necessary second hop. |
| Valid prescribed NEI structure | Claim: "The director of film X was born in Z." Exactly one partial sentence establishes director Y; humans find insufficient corpus information to establish or contradict Y's birthplace. | Human-selected 2-hop structure with Hop 1 verified and Hop 2 incomplete; retain the existing external NEI review process. |

These are human annotation and external review guidelines, not automatic semantic validation. Backend validation remains structural and must not infer or change `hop_count` or `label`.

### Cross-document Priority and Document Coverage

Prioritize claims whose necessary Gold Evidence spans at least two distinct Wikipedia articles, to evaluate Agentic RAG retrieval and evidence combination across documents. This is a research preference, not a new submission constraint or numerical quota.

Single-document Multi-hop remains allowed when two or more factual steps are genuinely necessary. The existing SUPPORTED/REFUTED validation still requires at least two distinct evidence sentences, with no minimum number of source articles. Selecting two sentences from two articles does not automatically establish 2-hop reasoning; humans must still apply Multi-hop Necessity.

Automatically derive sample-level document coverage from the distinct `page_id` values in the saved evidence arrays:

- `single_document`: evidence has one distinct source article.
- `cross_document`: evidence has at least two distinct source articles.
- For SUPPORTED/REFUTED, count sources in `gold_evidence`; `partial_evidence` remains empty.
- For NEI, count sources in `partial_evidence`; its prescribed one-sentence structure yields `single_document`. This describes only the observed partial evidence, not coverage of the missing Hop 2 or a complete proof of NEI.
- Ignore open workspace articles without selected evidence. Multiple sentences from the same `page_id` count as one document.

Persist `metadata.document_coverage` and `metadata.source_article_count` with each submitted sample and export them in JSONL for separate Agentic RAG analysis. Compute these descriptive values from evidence metadata only; do not add human input fields, change evidence objects, infer reasoning validity, or assign/modify the human's label or hop count.

### SUPPORTED / REFUTED

- Human-selected hop count is 2 or 3.
- Select at least two distinct evidence sentences.
- Evidence may come from one or multiple articles; there is no minimum article count.
- Three-hop samples do not automatically require three sentences.
- Selected sentences populate `gold_evidence`; `partial_evidence` is empty.
- Humans are responsible for ensuring that the evidence is necessary and sufficient for the verdict and reasoning steps.

### NEI

- Human-selected hop count must be 2.
- Select exactly one evidence sentence establishing the factual relationship in Hop 1.
- Hop 2 requires information that is insufficient to support or refute the claim within the fixed corpus.
- The selected sentence is partial evidence, not evidence establishing the full claim.
- `gold_evidence` is empty; the selected sentence populates `partial_evidence`.
- No zero-evidence NEI or alternative NEI structure is allowed.
- Human search and review are required before confirming NEI, but are handled outside the application.

### Evidence UI

Render each sentence with its zero-based ID and a checkbox. Selected sentences have a light blue background. Allow checking and unchecking multiple sentences without text-span highlighting or offset annotation.

Only checked sentences are saved as evidence. Open articles without selected evidence are not included as benchmark context or distractors.

## 12. Submit and Form Reset

On Submit, validate required fields, allowed values, evidence counts, and evidence identities against the frozen corpus. Save the exact human-entered label and hop count, automatically placing selected sentences into the appropriate evidence array.

Generate a sample UUID and UTC timestamp, associate the sample with the current session and corpus version, and persist it in SQLite.

In the same successful submission, derive and persist document coverage metadata from the saved evidence as specified above. This introduces no additional evidence-count or article-count validation and no automatic semantic or hop-count inference.

Use an internal `submission_id` for idempotency:

- Double-clicks and retries for the same submission must not create duplicate samples.
- A retry returns the result already persisted for that submission.
- A subsequent successful annotation submission uses a new ID and creates an independent sample.

Only after the backend confirms successful persistence:

- Clear the claim.
- Reset label and hop count to unselected.
- Uncheck all evidence sentences.

Preserve open articles, summaries, sentence IDs, metadata, Show More/Show Less state, and the current discovery structure. If Submit fails, preserve the entire draft for retry.

## 13. Session Lifecycle

- Each page opening or refresh creates a new UUID session ID.
- Reset workspace and all unsubmitted annotation state; do not restore drafts.
- Start again from Random Article.
- Keep all previously submitted samples and their original session IDs.
- Each machine generates sessions independently; no shared session counter or synchronization is required.
- The displayed sample counter counts successfully submitted samples in the current session.

Restarting the application preserves the corpus, stored sessions, and submitted samples. This persistence does not restore the old page session or its draft.

## 14. Benchmark JSONL Schema and Export

All records contain both evidence arrays. Both arrays use identical evidence metadata:

Each record also contains sample-level `metadata.document_coverage` and `metadata.source_article_count`, derived from its evidence source page IDs. Evidence object fields and the human-entered label/hop count remain unchanged.

```json
{
  "id": "sample-uuid",
  "session_id": "session-uuid",
  "corpus_version": "frozen-corpus-version",
  "claim": "Vietnamese claim entered by the human",
  "label": "SUPPORTED",
  "hop_count": 2,
  "gold_evidence": [
    {
      "article_title": "Source article title",
      "page_id": 123,
      "revision_id": 456,
      "source_url": "Source URL",
      "sent_id": 0,
      "evidence_text": "Selected sentence text"
    },
    {
      "article_title": "Source article title",
      "page_id": 123,
      "revision_id": 456,
      "source_url": "Source URL",
      "sent_id": 1,
      "evidence_text": "Another selected sentence"
    }
  ],
  "partial_evidence": [],
  "metadata": {
    "document_coverage": "single_document",
    "source_article_count": 1
  },
  "reasoning_chain": null,
  "created_at": "2026-10-14T00:00:00Z"
}
```

This example illustrates structure only; real source metadata and evidence must come from the frozen corpus. Do not generate a reasoning chain and treat it as human gold annotation. No review metadata or annotator identifier is exported.

Provide one UI button, **Export Dataset**:

- Export all successfully submitted samples belonging to the current `session_id`.
- Write UTF-8 JSONL with one complete sample per line.
- Include only selected evidence, never drafts, unselected articles, or distractors.
- Preserve claim, human label/hop count, evidence metadata, corpus version, and timestamp.
- Preserve derived sample-level document coverage metadata for separate single-document and cross-document analysis.
- Use a session-based filename, for example `VIMFC_SESSION_001.jsonl`.
- Export does not delete data, reset the workspace, or interrupt annotation.
- Disable export when the current session has no submitted samples and show an explanatory message.

There is no Export by Date feature or date-filter UI. Timestamps remain UTC in storage.

Provide PowerShell-accessible commands to list stored sessions and export an older session by its UUID, since reopening or refreshing the page creates a new current session.

## 15. Stack, UI Language, and Windows Startup

- Frontend: React, TypeScript, Vite.
- Backend: Python, FastAPI.
- Storage: SQLite.
- Source collection: MediaWiki and Wikidata APIs during corpus construction only.
- UI: Vietnamese instructions, buttons, and errors; keep `SUPPORTED`, `REFUTED`, `NEI`, and JSON field names unchanged.
- Layout: header with session ID, sample counter, Re-random and Export; article workspace; annotation panel.

Use PowerShell on Windows, without Docker:

- `start.ps1` starts backend and frontend with one command.
- Check environment, dependencies, databases, and frozen corpus validity; provide clear errors or setup instructions.
- Start annotation only when the corpus version, 20,000 articles, metadata, and graph pass validation.
- Run corpus construction through a separate command; never rebuild it automatically at application startup.
- Preserve existing corpus and annotation data on restart.
- Display the local URL after successful startup.
- Provide a README covering Windows installation, corpus build/resume/freeze, transfer to the second machine, startup, and export commands.

## 16. Research Reference and Superseded Requirements

Before implementation, study [ViMQA](https://aclanthology.org/2022.lrec-1.700.pdf), particularly Section 2, Figures 2–3, Table 3, and Gold-only/Distractor settings. Document its original methodology and this project's adaptations separately in `docs/research_vimqa.md`.

The following original requirements have been superseded:

- Hop counts 2–5: replaced by 2 or 3 only.
- Six entity groups: replaced by the eight-group allocation above.
- First paragraph only: replaced by the full introductory prose section.
- Export by Date: removed.
- App-managed review statuses/workflows: removed; human review occurs externally.
- Draft restoration: rejected; new page loads start empty with a new session.
- Minimum two evidence articles: rejected; SUPPORTED/REFUTED require at least two distinct sentences, which may belong to the same article.
- Live Wikipedia discovery during annotation: replaced by local frozen-corpus discovery.
