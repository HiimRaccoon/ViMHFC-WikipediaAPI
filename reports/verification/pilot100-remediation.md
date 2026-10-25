# Pilot 100 remediation — offline acceptance, 2026-10-25

Implemented the user's bounded repair request. **No Wikipedia/Wikidata API calls, real pilot, new real pipeline run, Pilot 1,000, Production or corpus freeze were performed.** The next real execution belongs to the user, using [the PowerShell handoff](../../docs/pilot100-replay.md).

Source run: `b0243673-5f0b-4ac6-b8b3-0ca7aa74ccc6`; archived snapshot `2026-10-25T03:00:00Z`; config hash `e6c0df8c87da2efdccd059be2cf44356b80f4b625b0cce935bd9689e6f131139`. Source metrics, raw cache and database were inspected locally. The separate [machine-readable retrospective audit](pilot100-remediation-audit.json) identifies every original unresolved article, all diagnostic argument forms, all classification outcomes and blocked paths.

## Extraction: all 65 original failures investigated

Original: 65 unresolved articles, 88 diagnostics, 55 distinct template/diagnostic names. A small English prefix list did not recognize Vietnamese infoboxes, hatnotes, maintenance/media/navigation components or `langx`. Unknown arguments inside an excluded infobox should never have been lead prose dependencies. The old source-level list-line regex could also remove template closing braces before AST parsing. `Warner Bros. Television` crossed a false sentence boundary; diagnostic `raw_hash` previously fingerprinted the JSON-encoded/preprocessed string instead of original source bytes.

Changes:

- Explicit reviewed Vietnamese infobox/hatnote aliases and non-prose categories; parse the original AST before excluding list nodes/lines. Unsupported content inside a valid excluded box does not quarantine authored prose or enter the graph.
- Strict local argument-only formatters for the observed `langx`, IPA, `nihongo`, `zh`/`lang-zh`, airport-code and quotation forms. Preserve authored strings and actual authored wikilinks; synthesize no implicit graph links. Reject unknown languages, extra/duplicate/missing arguments, invalid/oversized dates and malformed template delimiters.
- Versioned sentence abbreviations for company names; retain exact prose/sentence/link offsets. Original source SHA-256 is attached to diagnostics.
- Unknown generated factual forms or unverified topic components remain unresolved. No live expansion/template lookup, guessing, or silent deletion of factual clauses.

Retrospective extraction from the **same 100 archived source revisions**: **92 completed, 8 unresolved**, with 57 of the original 65 unresolved articles repaired. All 35 originally completed articles remain completed. This is an offline extractor observation, not a new live Pilot 100 or measured final eligibility.

| Still unresolved page ID | Article | Remaining obstacle |
|---|---|---|
| 20004132 | Embraer C-390 Millennium | `cvt`: generated conversion/rounding semantics |
| 19970897 | U-1058 (tàu ngầm Đức) | `sclass`: generated class/link semantics |
| 19961315 | U-1023 (tàu ngầm Đức) | `sclass`, `grt`: generated class/unit semantics |
| 19561728 | Hiệp hội các Trung tâm Khoa học-Công nghệ | Malformed/unparsed box markup and `USA`; cannot establish a safe prose boundary/output |
| 511644 | Tổ chức | `quản trị kinh doanh`: topic component not proven from archived template body |
| 16317 | Thương mại điện tử | `thương mại điện tử`: same limitation |
| 122844 | M'linh | `nhạc cụ dân gian việt nam`: same limitation |
| 677 | E | `nguyên âm tiếng việt`: same limitation |

## Classification: all 46 truncated cases and all 20 maxlag cases inspected

Old traversal explored ancestors beyond configured taxonomy roots and treated any outgoing edge at the depth limit as truncation, including cycles/already visited or already queued dependencies. Returning immediately at a cutoff also hid diagnostics for necessary siblings. Finding a root alone was insufficient because other P31/P106 branches could determine a higher-precedence group or a subtype.

The revised `qid-2` taxonomy defines each configured class as a terminal match **for its own ancestor branch**. Every other required instance/occupation branch remains necessary; precedence/subtypes are resolved only after those branches close. The 12-edge acquisition safety bound remains. Cutoff diagnostics are reconciled after the queue drains, so a dependency reached by another valid path is not falsely truncated. Required missing/future/invalid nodes and genuinely unvisited cutoff children keep the article pending, even if another branch contains a root. Acquisition and classification share the same child-selection helper.

| Original classification cohort | Retrospective outcome using compatible cached snapshot items |
|---|---|
| 46 `truncated_hierarchy` | 7 completed supported, 7 unclassified `no_supported_rule`, 32 still truncated |
| 27 of those with a configured root in the old closure | 7 completed, 20 still truncated because another required branch is open |
| 20 maxlag-pending | 19 lack a historical article-item association in available cache; 1 has a required historical dependency missing |
| All 100 | 24 supported, 24 no supported rule, 32 truncated, 20 incomplete cached source metadata |

These cached results are not promises of live yield. The residual 32 need investigation of the recorded paths/source model or another explicitly versioned classification change; they were not forced complete or relabelled unsupported to improve statistics. No taxonomy roots, precedence, quotas or eligibility thresholds were broadened.

All 20 original source failures have maxlag messages (lag values approximately 5.03–10.4 seconds). The old archive lacks the failed request's QID and per-request context: messages repeat, but that does not establish which QIDs failed. The audit retains the exact article IDs/reasons; it does not invent missing request identities. New failures preserve code, site, stage, attempts, titles/pageids/revids, cutoff, retryability, retry deadline and partial closure.

## Requests, retry and checkpoints

The original 3,956 physical requests break down exactly as follows:

| Stage | Requests | Main causes |
|---|---:|---|
| Discovery and historical identity | 645 | 414 revisions, 199 move-log queries, 32 category/allpages calls |
| Classification | 2,076 | 1,976 Wikidata revision calls, including retries; 100 current pageprops hints |
| Retained-link identity dependencies | 1,235 | 789 revisions, 392 move-log queries, 54 other identity queries |
| Total | 3,956 | 103 errors, 83 retries, 475 seconds backoff |

Optimizations preserve serial host concurrency:

- Memoize unique snapshot QIDs across articles; batch sibling revision **contents**, at most 20 known revisions per call. Revision selection remains a per-identity historical cutoff lookup rather than an unsafe multi-title history query.
- Reuse verified historical sitelink associations before current pageprops; cache pageprops only as a hint and always validate the historical sitelink. Cache canonical page-ID aliases for title-selected revisions at the same cutoff.
- Persist failed item/revision lookups and host Retry-After deadlines across source/client restart. Keep maxlag=5, at most five attempts and server Retry-After; never bypass cooldown with `retry`.
- Cache proven permanently unavailable content separately from transient failures; process valid batch siblings before reporting missing content, without poisoning those siblings.
- Preserve typed retry metadata through classification/process/stage rows. Structural pending has no automatic retry timer. Eligible transient retries update the current run's classification row; old `INSERT OR IGNORE` could leave a stale pending audit after recovery.
- Apply the unchanged quality rules before resolving expensive target histories. Keep authored link occurrences on ineligible articles, but defer target requests. Only eligible articles with resolved historical identity processing enter pair/selection work.

Fixture measurements establish batching and duplicate-request avoidance: one root plus three siblings uses one root-content request plus one three-item content batch; repeating its closure issues no source requests. Repeated failed QID lookup and a restarted client's different-QID lookup during host cooldown issue no additional physical requests. These are controlled observations, not live runtime projections.

Every completed invocation now retains `attempts/<attempt_id>/metrics.json` plus both audits; the latest root report is still convenient for inspection/export. Stage reuse and raw-cache reuse are reported separately. A new-rule replay with archived raw cache does not receive a cold Production ETA. Reports include pending classifications; generic read-only report/status do not require the old rule files to remain unchanged.

## Verification and review

Fresh final offline command:

```powershell
$env:VIMFC_OFFLINE='1'
& '.\.tools\python312\python.exe' -m pytest --basetemp .tools/pilot-fix/full-offline-final -q --tb=short 2>&1 |
    Tee-Object -FilePath .tools/pilot-fix/offline-tests.log
$TestExitCode = $LASTEXITCODE
$TestExitCode | Set-Content .tools/pilot-fix/offline-tests.exit-code.txt
```

Result: **142 passed in 66.39 seconds, exit 0**. Includes offline unit/integration fixtures, checkpoint/retry/upsert behavior, source maxlag/HTTP/Retry-After cases, boundary/cycle/subtype tests, new-run compatibility, 20,000-article controlled freeze validation and PowerShell tests for merged output, exit 7, launch failure and rejection of a 1,000 context. The socket-denial fixture remains enabled. No frontend behavior was changed.

The handoff's preparation block was executed against a separate controlled fixture database under `.tools/pilot-fix/operator-smoke/`, including an offline backend reinstall and actual CLI prepare/create/status/checkpoint-export. Exit 0; the archived fixture cohort was copied to a distinct fixture UUID. All four PowerShell blocks parse successfully. The real data database was not used for this smoke test.

An independent read-only review found malformed/oversized template input, unavailable-content caching and a potentially stale PowerShell launch exit code. These were addressed with regressions; valid mixed-batch siblings received an additional regression. No known Critical/Important review finding remains open.

Version bundle: pipeline `pipeline-2`, extraction `lead-2`, sentence `vi-punct-2`, classification `qid-2`; ranking unchanged. Original `min_sentences=2`, `min_tokens=40`, factual signal rules, roots/precedence, Fixed Snapshot, retained-prose-only graph and exact frozen target 20,000 are unchanged.

Before/after immutable fingerprints match for the original run across 9 tables: pipeline_runs 1, candidates 100, stage_results 600, lead_results 100, classification_results 100, pair_candidates 0, selection_steps 5, run_events 101, pilot_reports 1. All 6 original report files also match their original SHA-256. The actual database still contains **only the original single pipeline run**; no replay run was created by the agent.

The user's next action is [the ordered PowerShell handoff](../../docs/pilot100-replay.md). Final real yield, request reduction and runtime await that run. Pair quality and readiness for Pilot 1,000 remain unassessed while extraction/classification are incomplete. **Stop after handing over commands.**
