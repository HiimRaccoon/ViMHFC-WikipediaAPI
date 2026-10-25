# Pilot 100 round 2 — offline repair ledger

User-authorized scope: diagnose 43 truncated classifications and 8 unresolved extractions; verify pair fixtures; preserve fixed snapshot/cohort/eligibility; implement tested versioned repairs and hand off exact commands. No source API, real pilot, Production or actual run creation by the agent.

Source run: `9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e`, source report `reports/pilots/9c3ed5a4-f161-454c-8a3c-71fe69ed6c1e/100/metrics.json`. Snapshot/config come from the archived database/report. Both actual runs' rows and files were fingerprinted in `.tools/pilot2-fix/baseline.json` before changes.

- [x] Diagnose all 43 cases from SQLite checkpoint, logs, classification audit and compatible raw cache.
- [x] Implement/test versioned cached fixed-point closure while retaining depth-12 network acquisition; explicit terminal unresolved reasons and guarded node budget; preserve subtype/precedence and cache provenance.
- [x] Inspect all 8 extraction residuals and verify all 92 completed archived outputs, sentence/link coverage and controlled fixtures; change extraction only for demonstrated defects.
- [x] Verify directed lead bridges, comparison subtype/attribute blocks, ranking, linkage and negative cases with fixtures; explain current cohort's zero pairs without manufacturing candidates.
- [x] Complete full offline suite and one independent review; verify both old runs unchanged; add performance/pair audits and tested PowerShell handoff from round-2 metadata; STOP.

Evidence: 43 articles, 108 cutoff diagnostics, 43 distinct cutoff QIDs, 55 distinct immediate blocked children, all 55 already cached. Cutoff depth is 12; no existing node limit or API/retry error caused these outcomes. Branches enter general ontologies (EMMO/DOLCE, entities, roles, matter) from P31/P106; root detection works but cannot by itself close required siblings. 26 cases already contain configured roots.

Diagnostic fixed-point traversal of the cached historical graph (no network, no run writes) closes 37 cases (20 with supported roots, 17 with none); six require two uncached dependencies, `Q141438977` and `Q141445732`. Largest reachable closure: 241 nodes; shortest-path depth 13–20 for previously truncated cases. This is not a new pilot result or a guarantee of eligibility.

Design ruling: keep `max_depth=12` for network acquisition; allow a separately bounded, cycle-safe fixed-point walk over already verified snapshot cache, including longer paths only when every required dependency is present. This avoids raising API traversal limits or stopping at a root without checking siblings. Introduce versioned terminal `unresolved` classification for missing frontier/budget/invalid metadata; never call it supported or `no_supported_rule`. Transient acquisition remains retryable pending. New runs re-evaluate incomplete derived results; successful complete proofs may be reused. No SPEC/PLAN changes are needed for this bounded repair requested by the user.

Extraction ruling: preserve `lead-2` unless the archived inspection/fixtures expose a concrete renderer defect. Unknown generated-value templates and topic components without historical output proof remain quarantined. Pair generation rules/eligibility remain unchanged unless a fixture demonstrates a defect.

Confirmed extraction defects in archived article 3148970: inline `: Free Fire` was interpreted as a list and lost; NBSP/NFC/whitespace normalization left `link_text` inconsistent with its retained span. RED/GREEN regressions justify `lead-3`. Of the 92 completed outputs, 91 remain unchanged apart from version and one is repaired; all eight quarantined outputs remain unresolved.

Final cached analysis: 43 former truncated -> 20 supported, 17 proved no supported rule, 6 unresolved requiring Q141438977/Q141445732. All 100 -> 47 supported / 47 unclassified / 6 unresolved. These are retrospective source-cache results, not a real rerun or eligibility claim.

Acceptance: **177 offline tests passed in 88.54s, exit 0**; archived fixture compaction separately rechecked (3 passed). One independent review's cache failure finding was reproduced and fixed with typed errors, atomic index publication and blob/reference regressions. Pair fixtures positive; actual16 have no non-self internal edge or multi-article comparison block. Both actual runs' table/report fingerprints preserved; actual run count remains two. [Acceptance report](pilot2-remediation.md), [per-article audit](pilot2-remediation-audit.json), [operator commands](../../docs/pilot100-replay.md). Operator preparation/create/status/export were tested on a separate fixture database only. STOP; no new actual run/pilot/source call/Production.
