# Pilot 100 remediation — offline implementation plan and ledger

User-authorized scope: diagnose all 65 unresolved extractions, 46 truncated hierarchies and 20 maxlag-pending cases; fix with offline fixtures; deliver exact PowerShell replay/logging commands. No source API calls, real pilot, 1,000 cohort, production, freeze or push in this work.

Source run: `b0243673-5f0b-4ac6-b8b3-0ca7aa74ccc6`, snapshot read from its archived configuration/report: `2026-10-25T03:00:00Z`. Its database rows and report files were fingerprinted before changes in ignored `.tools/pilot-fix/` and must remain unchanged.

The agreed spec/plan remains binding. This is a repair of existing builder flows; no new benchmark requirements, eligibility thresholds, group quotas, semantic validators or annotation workflows.

- [x] P0 Extraction: classify all archived diagnostics; test explicit non-prose aliases, strict argument-only language/quotation/code formatters, malformed/unknown factual forms and sentence-boundary regression; update versioned extraction rules while retaining thresholds.
- [x] P0 Classification: test root-bounded BFS with all necessary sibling branches, missing dependencies, subtype/precedence, depth/cycles; share traversal semantics with acquisition; batch known-revision contents and memoize hierarchy nodes.
- [x] Retry/request fan-out: test typed retryable errors/Retry-After deadlines, persisted source-failure cache, deterministic pending with no blind retry, successful retry/upserts, historical association reuse and deferred link resolution for ineligible articles.
- [x] Audits/CLI: preserve pending outcomes in reports, add classification audit and atomic report export; implement preparation of a new replay config/run from actual old metadata for user invocation; provide PowerShell logging with exit-code capture and exclusive invocation.
- [x] Acceptance: run offline regressions/full suite; replay archived extraction/classification locally without creating a real run or making network requests; verify old-run/report fingerprints; document residual unsupported forms and exact operator commands; STOP.

Initial evidence: 65 articles / 88 diagnostics / 55 distinct template-or-diagnostic names. Original classification has 46 truncated cases, 27 containing configured roots, and 20 maxlag cases. Historical request trace has aggregate endpoint/stage metrics but does not retain failed QID request context; this observability gap must be fixed rather than retroactively guessed.

Hypotheses, in priority order:
1. Explicit Vietnamese infobox/hatnote aliases are incorrectly treated as factual dependencies; localized exclusion rules should clear those diagnostics without deleting prose.
2. Argument-complete inline templates can be rendered locally, but unknown/ambiguous/generated-value forms must remain unresolved.
3. Traversal of ancestors beyond configured terminal roots causes avoidable cutoff/pending outcomes; stopping only that branch must retain unresolved necessary siblings.
4. Retryability/deadlines are lost between source, classification and process stages; typed propagation and shared failed-item deferral should reduce repeated work without suppressing eligible retries.
5. Cached historical item associations, canonical revision lookup aliases, content batching and eligibility-before-link-resolution can reduce requests without increasing concurrency or changing facts.

Acceptance: **142 offline tests passed, 66.39s, exit 0**. [Results and root causes](pilot100-remediation.md); [per-article retrospective audit](pilot100-remediation-audit.json); [ordered PowerShell handoff](../../docs/pilot100-replay.md).

Ruling: configured taxonomy roots close their own ancestor branches only; missing/cutoff siblings remain pending — preserves subtype/precedence accuracy rather than declaring the 27 root-containing cases complete — residual 32 truncated cases intentionally remain pending in cached analysis.

Ruling: unsupported topic components and generated factual forms stay unresolved without archived template-output proof — no live expansion or guessed formatter — eight articles remain unresolved offline.

Ruling: list filtering moves after original AST parsing — pre-parse line deletion can remove closing braces and leak non-prose — extra AST/list regressions pass and original raw diagnostics remain traceable.

Ruling: permanent content deferral applies only to revisions proven absent/hidden; valid siblings are processed and cached before raising — avoids request duplication without poisoning valid batch dependencies.

One fresh read-only review completed; all four findings verified/addressed. Whole-suite verification followed the fix pass. Operator preparation/create/status/checkpoint-export commands were also smoke-tested with a **separate fixture database** under `.tools/pilot-fix/operator-smoke/`; all PowerShell blocks parse. Actual database/report fingerprints remain unchanged and its run count is one. A new real replay run and its Pilot 100 remain for the user to invoke. No integration/push/next-pilot action is authorized by this completion.
