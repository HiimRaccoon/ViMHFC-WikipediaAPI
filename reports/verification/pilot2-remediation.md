# Pilot 100 round 2 — offline repair acceptance

Source run: `9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e`; snapshot `2026-10-25T03:00:00Z`; source config hash `c29c2a66f744704373dcbebb8294154a6dbd5361dbbf98cc2904125d54e3175b`. Source database, checkpoints, logs, audits and immutable raw objects were inspected locally. **No Wikipedia/Wikidata API request, real Pilot 100/1,000, Production or actual replay-run creation was performed during this repair.**

Outputs: [per-article audit](pilot2-remediation-audit.json), [preservation verification](pilot2-preservation.json), [ledger](pilot2-remediation-ledger.md), [exact ordered PowerShell handoff](../../docs/pilot100-replay.md). SPEC and PLAN were not changed.

## Root cause of all 43 truncated classifications

All 43 hit the configured depth-12 guard. There was no node-limit or maxlag/retry failure. Their 108 cutoff diagnostics comprise 43 distinct cutoff QIDs and 55 distinct immediate children; all 55 children were already in compatible historical raw cache. Twenty-six articles already contained configured roots, but still had required sibling branches. Starting properties for the cutoff paths: 86 P31, 22 P106, followed by P279.

These sibling paths enter general ontologies rather than closing at the known supported class: person/occupation -> organism/physical-object/endurant; creative work -> information-resource -> source/semantic role; collective organizations -> entities and collections. Frequent cutoffs include `Q141810807` (fusion), `Q141814105` (causal object), `Q2364925` (causal structure). The old acquisition returned only its depth-bounded subgraph and classification interpreted every remaining edge as missing traversal, even when another archived source acquisition had already cached the full dependency graph.

The revised algorithm retains **network max_depth=12**. It materializes known snapshot cache nodes directly, then walks their finite reachable graph to a fixed point, with cycle detection and an explicit 2,048-node local safety budget. It never requests an uncached ancestor beyond the network depth. All necessary P31/P106 sibling branches remain required; configured roots terminate their own ancestor branch only. Precedence and subtype decisions wait until the required closure is proved. Shortest supported paths and historical revision references remain recorded. No ontology classes, primary groups, subtypes, precedence or eligibility thresholds were added or broadened.

Selected-item metadata, content model, QID/site/cache-key identity, page ID, snapshot/cutoff, raw-object integrity and content SHA-256 are checked before cache reuse. The title/reference index is published atomically after validation, preventing a failed scan from leaving a partially trusted index. Invalid blobs/reference JSON become typed `invalid_historical_cache` failures instead of uncaught exceptions or live-data fallback.

### Retrospective outcomes, not a new pilot

| Original 43 truncated cases | New verified cached outcome |
|---|---:|
| Complete supported closure | 20 |
| Complete closure with no configured supported root | 17 |
| Missing required historical frontier | 6 |

Across all 100: **47 supported, 47 unclassified/no_supported_rule, 6 unresolved**. Closures for the previously truncated cases have shortest-path maximum depths 13–20; the largest diagnostic reachable graph has 241 nodes. This proves local cached closure where possible rather than increasing network traversal limits or automatically accepting root-containing articles.

The six unresolved articles are `20020318` (1ucid), `19482761` (Khánh (nghệ sĩ)), `19583257` (12 Gauge (rapper)), `19966548` (2pillz), `3700502` (K'Choi), `19575174` (2Mex). Each still needs `Q141438977` and `Q141445732`, whose required historical contents are absent from compatible cache. A known Human root does not prove the unresolved occupation branches have no more specific supported subtype.

These six receive `classification.outcome=unresolved`, reason `missing_or_invalid_historical_dependency`, paths/frontier QIDs and `retryable=false`. They remain ineligible. This is distinct from `unclassified/no_supported_rule`, which requires a closed graph. Node-budget exhaustion and invalid cache also have explicit terminal reasons. Only transient source failures remain retryable `pending`; stage completion means processing finished, not that classification is supported. A newly created run re-evaluates incomplete derived results rather than blindly inheriting an old unresolved result. Complete proven classifications may still be reused under matching dependencies.

## Extraction: preserve working behavior, repair demonstrated defects

All 100 original article revisions were re-extracted locally with raw SHA-256/cutoff checks. All 92 completed outputs pass full output-sentence coverage, exact sentence/link offsets, nonempty retained link spans, and original lead AST target checks after the repair. Of those 92, 91 retain the same output apart from the extraction version. One article contains demonstrated defects:

- `3148970`, **Thể thao điện tử**: the authored label `[[Garena Free Fire|: Free Fire]]` was recursively interpreted as a definition-list line. The literal `: Free Fire` disappeared and a zero-length graph occurrence was validated. Inline rendering now preserves list-like punctuation in labels, inline template arguments and inline HTML; document and quotation lists still exclude their links.
- The same article contains `TV&nbsp;That's Incredible!`. Paragraph normalization converted NBSP to a space while `link_text` retained NBSP, violating the exact text/span invariant. Labels are now taken from the normalized retained span, including NFC and whitespace handling. Unknown empty pipe labels are quarantined instead of becoming zero-width graph links.

Version is `lead-3`; sentence rules remain `vi-punct-2`. The eight unresolved articles remain unresolved: `20004132` (`cvt` conversions/rounding), `19970897` (`sclass`), `19961315` (`sclass`, `grt`), `19561728` (malformed markup/`USA`), `677` (`nguyên âm tiếng việt`), `122844` (`nhạc cụ dân gian việt nam`), `511644` (`quản trị kinh doanh`), `16317` (`thương mại điện tử`). Generated values and unverified topic-component outputs cannot be proved from the available historical data. No formatter was guessed, template expanded live, or factual clause silently removed to validate these articles. The malformed article's provisional rendering can change, but remains quarantined with its original diagnostic categories.

These automated checks establish the stated parser/provenance invariants; they do not replace human semantic review of the corpus.

## Pair generation and reporting

Controlled integration fixtures run the real extraction, classification, quality, graph, pair and ranking functions. They produce a directed lead Bridge A -> B with a relation signal and complementary facts, and a positive Comparison A/C sharing Scientist subtype and birth-year attributes. Body/infobox links, ineligible endpoints, ambiguous values, and broad-primary-group fallback are excluded. Reversed input produces the same manifest. Existing cap/ranking regression suites pass. `rank-1` and scoring weights were not changed.

Inspection of the actual **16 eligible articles from round 2** explains zero pairs without altering them: zero directed internal non-self lead edges; one self-link; 139 external/ineligible-target occurrences; two scalar comparison blocks, each with only one article (University/founded/year and Building/founded/year). There is no valid bridge edge or shared subtype/attribute block to feed pair generation. No pair was manufactured and no poor-selection conclusion follows from this small incomplete cohort.

Future reports add `performance.json` and `pair-audit.json` alongside existing metrics/template/classification audits, both in the latest report folder and immutable per-invocation `attempts/<attempt_id>/`. Pair audits distinguish actual edges, self-links, external endpoints, subtype/attribute blocks and generated/positive counts. Comparison blocking is shared by generator and audit to avoid divergent definitions. Classification audit includes terminal-unresolved counts and closure depth/frontier/budget details; performance separates unresolved classifications from ordinary quality rejections. Generic checkpoint and archived full-report export remain offline.

## Verification and operator handoff

Final full offline suite: **177 passed in 88.54 seconds, exit 0**. Command:

```powershell
$env:VIMFC_OFFLINE='1'
& '.\.tools\python312\python.exe' -m pytest --basetemp .tools/pilot2-fix/full-offline-final -q --tb=short 2>&1 |
    Tee-Object -FilePath .tools/pilot2-fix/tests-final.log
$TestExitCode = $LASTEXITCODE
```

Tests include the controlled 20,000-article freeze checks, legacy depth-rule compatibility, all-branch precedence/subtype/cycles, source request bounds, wrong snapshot/cache corruption/index atomicity, terminal states and cross-run reuse, extraction defects, pair fixtures, report exports and PowerShell logging. A compact fixture derived from 162 archived hierarchy items records original source hashes/revisions and projection provenance in `tests/fixtures/manifest.json`. After dropping irrelevant non-Vietnamese sitelinks from that fixture, all three archived-case tests were rechecked and pass.

One independent read-only review found untyped cache failures; corrupt-blob/reference regressions were observed failing, then fixed and included in the green full suite. No other Important/Critical finding remains open.

All four operator PowerShell blocks parse. The preparation/install/create/status/checkpoint-export flow was executed successfully against an isolated **fixture database** under `.tools/pilot2-fix/operator-smoke/`, not the actual data database. Native source stderr/exit handling remains covered by fake-executable tests. The current embedded runtime has been reinstalled offline with current code; the handoff repeats that installation for reproducibility.

Fingerprint verification preserves both actual runs' rows across 10 tables each and every existing report/log/context/attempt file: 6 files for the original run, 14 for round 2. The actual database contains exactly **two runs**, the pre-existing ones; no new actual replay run was created. New mutable defaults are `pipeline-3`, `qid-3`, `lead-3`; source bundles and old results were not changed. Fixed Snapshot, Full Lead, Lead-only Graph, eligibility/factual rules, exact 20,000 target and ranking remain unchanged.

The user's next action is the [ordered PowerShell handoff](../../docs/pilot100-replay.md), whose source report now points to round 2 and derives run/snapshot/config from its archive. Live performance, eligible counts and pairs await that user-run pilot. **STOP here; Pilot 1,000 and Production are not authorized.**
